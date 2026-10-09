"""Bounded, server-authorized administrator API."""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as ApiPath
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from public_site.accounts import Account, AccountStore
from public_site.admin_analytics import AnalyticsWindow, require_label
from public_site.admin_pipeline import (
    failure_filter,
    failures_page,
    failures_summary,
    pipeline_report,
)
from public_site.settings import PublicSettings
from public_site.store import PublicStore
from public_site.telemetry import Diagnostic, ErrorCode, RunStatus, Stage, TelemetryStore

_LIMIT_DEFAULT = 50
_LIMIT_MAX = 200


class PageCursor(BaseModel):
    """Opaque deterministic pagination position."""

    model_config = ConfigDict(extra="forbid")

    created_at: datetime
    item_id: str = Field(min_length=1, max_length=64)


def register_admin_routes(
    app: FastAPI,
    *,
    settings: PublicSettings,
    accounts: AccountStore | None,
    telemetry: TelemetryStore | None,
    store: PublicStore,
    require_admin: Callable[[Request, AccountStore | None], Account],
) -> None:
    """Register the read-only v1 administrator API."""

    def database(request: Request) -> str:
        require_admin(request, accounts)
        if telemetry is None:
            raise HTTPException(status_code=503, detail="admin telemetry unavailable")
        return telemetry.database_url

    @app.get("/api/admin/overview")
    def overview(request: Request) -> dict[str, Any]:
        return _overview(database(request), store, settings.admin_slow_seconds)

    @app.get("/api/admin/requests")
    def requests_page(
        request: Request,
        status: RunStatus | None = None,
        user: str | None = Query(default=None, max_length=36),
        q: str | None = Query(default=None, max_length=200),
        cursor: str | None = Query(default=None, max_length=500),
        limit: int = Query(default=_LIMIT_DEFAULT, ge=1, le=_LIMIT_MAX),
    ) -> dict[str, Any]:
        return _requests_page(
            database(request),
            status=status,
            user=user,
            query=_query(q),
            cursor=_decode_cursor(cursor),
            limit=limit,
        )

    @app.get("/api/admin/requests/{submission_id}")
    def request_detail(
        request: Request,
        submission_id: str = ApiPath(pattern=r"^[0-9a-f]{32}$"),
    ) -> dict[str, Any]:
        payload = _request_detail(database(request), submission_id)
        submission = store.get(submission_id)
        expired_event = any(
            event["kind"] == "expired"
            for run in payload["runs"]
            for event in run["events"]
        )
        payload["submission_state"] = (
            "expired"
            if expired_event
            or (submission is not None and submission.status == "expired")
            else "retained"
        )
        return payload

    @app.get("/api/admin/users")
    def users_page(
        request: Request,
        q: str | None = Query(default=None, max_length=200),
        cursor: str | None = Query(default=None, max_length=500),
        limit: int = Query(default=_LIMIT_DEFAULT, ge=1, le=_LIMIT_MAX),
    ) -> dict[str, Any]:
        return _users_page(
            database(request),
            query=_query(q),
            cursor=_decode_cursor(cursor),
            limit=limit,
        )

    @app.get("/api/admin/users/{user_id}")
    def user_detail(
        request: Request,
        user_id: str = ApiPath(min_length=36, max_length=36),
    ) -> dict[str, Any]:
        return _user_detail(database(request), user_id)

    @app.get("/api/admin/pipeline")
    def pipeline(
        request: Request,
        window: AnalyticsWindow = AnalyticsWindow.H24,
        status: RunStatus | None = None,
        pipeline_version: str | None = Query(default=None, max_length=128),
        vision_config: str | None = Query(default=None, max_length=128),
    ) -> dict[str, Any]:
        url = database(request)
        with _conn(url) as conn:
            return pipeline_report(
                conn,
                window=window,
                status=None if status is None else status.value,
                pipeline_version=_label(pipeline_version),
                vision_config=_label(vision_config),
                slow_seconds=settings.admin_slow_seconds,
                queue_depth=store.count_status("queued"),
            )

    @app.get("/api/admin/failures")
    def failures(
        request: Request,
        window: AnalyticsWindow = AnalyticsWindow.H24,
        status: RunStatus | None = None,
        stage: Stage | None = None,
        error_code: ErrorCode | None = None,
        diagnostic: Diagnostic | None = None,
        pipeline_version: str | None = Query(default=None, max_length=128),
        evidence_version: str | None = Query(default=None, max_length=16),
        vision_config: str | None = Query(default=None, max_length=128),
        importance_config: str | None = Query(default=None, max_length=12),
        provider: str | None = Query(default=None, max_length=128),
        model: str | None = Query(default=None, max_length=128),
        cursor: str | None = Query(default=None, max_length=500),
        limit: int = Query(default=_LIMIT_DEFAULT, ge=1, le=_LIMIT_MAX),
    ) -> dict[str, Any]:
        position = _decode_cursor(cursor)
        filters = failure_filter(
            status=status, stage=stage, error_code=error_code, diagnostic=diagnostic
        )
        filters.update(
            pipeline_version=_label(pipeline_version),
            evidence_version=_label(evidence_version),
            vision_config=_label(vision_config),
            importance_config=_label(importance_config),
            provider=_label(provider),
            model=_label(model),
        )
        with _conn(database(request)) as conn:
            page = failures_page(
                conn,
                window=window,
                filters=filters,
                cursor_at=None if position is None else position.created_at,
                cursor_id=None if position is None else position.item_id,
                limit=limit,
            )
        page["next_cursor"] = _failure_cursor(page)
        return page

    @app.get("/api/admin/failures/summary")
    def failure_groups(
        request: Request,
        window: AnalyticsWindow = AnalyticsWindow.H24,
        status: RunStatus | None = None,
        stage: Stage | None = None,
        error_code: ErrorCode | None = None,
        diagnostic: Diagnostic | None = None,
        pipeline_version: str | None = Query(default=None, max_length=128),
        evidence_version: str | None = Query(default=None, max_length=16),
        vision_config: str | None = Query(default=None, max_length=128),
        importance_config: str | None = Query(default=None, max_length=12),
        provider: str | None = Query(default=None, max_length=128),
        model: str | None = Query(default=None, max_length=128),
    ) -> dict[str, Any]:
        filters = failure_filter(
            status=status, stage=stage, error_code=error_code, diagnostic=diagnostic
        )
        filters.update(
            pipeline_version=_label(pipeline_version),
            evidence_version=_label(evidence_version),
            vision_config=_label(vision_config),
            importance_config=_label(importance_config),
            provider=_label(provider),
            model=_label(model),
        )
        with _conn(database(request)) as conn:
            return failures_summary(conn, window=window, filters=filters)


def _overview(database_url: str, store: PublicStore, slow_seconds: int) -> dict[str, Any]:
    with _conn(database_url) as conn:
        totals = conn.execute(
            """
            SELECT
                count(*) FILTER (WHERE attempt = 1) AS requests,
                count(*) FILTER (WHERE status = 'completed') AS completed,
                count(*) FILTER (WHERE status IN ('failed', 'interrupted')) AS failed,
                count(DISTINCT user_id) FILTER (WHERE user_id IS NOT NULL) AS active_users,
                count(*) FILTER (WHERE upload_confirmed_at IS NOT NULL) AS uploads,
                percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY extract(epoch FROM (finished_at - created_at))
                ) FILTER (WHERE finished_at IS NOT NULL) AS median_job_elapsed,
                percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY extract(epoch FROM (finished_at - started_at))
                ) FILTER (WHERE finished_at IS NOT NULL AND started_at IS NOT NULL)
                    AS median_processing
            FROM job_runs
            WHERE created_at >= now() - interval '24 hours'
            """
        ).fetchone()
        new_users = conn.execute(
            """
            SELECT count(*) AS count FROM users
            WHERE created_at >= now() - interval '24 hours'
            """
        ).fetchone()["count"]
        volume = conn.execute(
            """
            WITH hours AS (
                SELECT generate_series(
                    date_trunc('hour', now()) - interval '23 hours',
                    date_trunc('hour', now()),
                    interval '1 hour'
                ) AS hour
            )
            SELECT hours.hour, count(job_runs.run_id) AS count
            FROM hours
            LEFT JOIN job_runs
                ON job_runs.attempt = 1
                AND job_runs.created_at >= hours.hour
                AND job_runs.created_at < hours.hour + interval '1 hour'
            GROUP BY hours.hour ORDER BY hours.hour
            """
        ).fetchall()
        stages = conn.execute(
            """
            SELECT e.stage,
                   count(*) AS count,
                   percentile_cont(0.5) WITHIN GROUP (
                       ORDER BY (e.detail->>'seconds')::double precision
                   ) AS median_seconds,
                   percentile_cont(0.95) WITHIN GROUP (
                       ORDER BY (e.detail->>'seconds')::double precision
                   ) AS p95_seconds
            FROM job_events e
            WHERE e.kind = 'stage_finished'
              AND e.at >= now() - interval '24 hours'
            GROUP BY e.stage ORDER BY e.stage
            """
        ).fetchall()
        recent = _recent_problems(conn, slow_seconds)
    return {
        "window": "last_24_hours",
        "requests": int(totals["requests"] or 0),
        "completed": int(totals["completed"] or 0),
        "failed": int(totals["failed"] or 0),
        "active_users": int(totals["active_users"] or 0),
        "new_users": int(new_users or 0),
        "uploads_confirmed": int(totals["uploads"] or 0),
        "processing_now": store.count_status("processing"),
        "queued_now": store.count_status("queued"),
        "median_job_elapsed_seconds": _float(totals["median_job_elapsed"]),
        "median_processing_seconds": _float(totals["median_processing"]),
        "volume": [
            {"hour": _stamp(row["hour"]), "count": int(row["count"])} for row in volume
        ],
        "stages": [_stage_stat(row) for row in stages],
        "recent_problems": recent,
    }


def _recent_problems(conn, slow_seconds: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        WITH durations AS (
            SELECT r.*,
                   extract(epoch FROM (r.finished_at - r.created_at)) AS job_elapsed,
                   extract(epoch FROM (r.finished_at - r.started_at)) AS processing,
                   COALESCE((
                       SELECT sum((e.detail->>'seconds')::double precision)
                       FROM job_events e
                       WHERE e.run_id = r.run_id AND e.kind = 'stage_finished'
                   ), 0) AS measured
            FROM job_runs r
            WHERE r.finished_at IS NOT NULL
        )
        SELECT *, processing - measured AS other_seconds
        FROM durations
        WHERE status IN ('failed', 'interrupted')
           OR job_elapsed > %s
           OR processing - measured < -0.05
        ORDER BY finished_at DESC, run_id DESC
        LIMIT 50
        """,
        (slow_seconds,),
    ).fetchall()
    return [_problem(row, slow_seconds) for row in rows]


def _requests_page(
    database_url: str,
    *,
    status: RunStatus | None,
    user: str | None,
    query: str | None,
    cursor: PageCursor | None,
    limit: int,
) -> dict[str, Any]:
    clauses = ["r.attempt = 1"]
    params: list[Any] = []
    if status is not None:
        clauses.append("r.status = %s")
        params.append(status.value)
    if user:
        clauses.append("r.user_id = %s")
        params.append(_uuid(user))
    if query:
        clauses.append("(r.submission_id ILIKE %s OR u.email ILIKE %s)")
        like = f"%{query}%"
        params.extend([like, like])
    if cursor:
        clauses.append("(r.created_at, r.submission_id) < (%s, %s)")
        params.extend([cursor.created_at, cursor.item_id])
    params.append(limit + 1)
    sql = f"""
        SELECT r.*, u.email,
               EXISTS (
                   SELECT 1 FROM job_events x
                   WHERE x.run_id = r.run_id AND x.kind = 'expired'
               ) AS expired,
               COALESCE((
                   SELECT sum((e.detail->>'seconds')::double precision)
                   FROM job_events e
                   WHERE e.run_id = r.run_id AND e.kind = 'stage_finished'
               ), 0) AS measured
        FROM job_runs r
        LEFT JOIN users u ON u.id = r.user_id
        WHERE {" AND ".join(clauses)}
        ORDER BY r.created_at DESC, r.submission_id DESC
        LIMIT %s
    """
    with _conn(database_url) as conn:
        rows = conn.execute(sql, params).fetchall()
    more = len(rows) > limit
    selected = rows[:limit]
    return {
        "items": [_request_row(row) for row in selected],
        "next_cursor": _next_cursor(selected, more, "submission_id"),
    }


def _request_detail(database_url: str, submission_id: str) -> dict[str, Any]:
    with _conn(database_url) as conn:
        rows = conn.execute(
            """
            SELECT r.*, u.email
            FROM job_runs r
            LEFT JOIN users u ON u.id = r.user_id
            WHERE r.submission_id = %s
            ORDER BY r.attempt
            """,
            (submission_id,),
        ).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="not found")
        runs = []
        for row in rows:
            events = conn.execute(
                """
                SELECT id, at, kind, stage, detail
                FROM job_events WHERE run_id = %s
                ORDER BY at, id
                """,
                (row["run_id"],),
            ).fetchall()
            runs.append(_detail_run(row, events))
    return {
        "submission_id": submission_id,
        "user_email": rows[0]["email"],
        "runs": runs,
    }


def _users_page(
    database_url: str,
    *,
    query: str | None,
    cursor: PageCursor | None,
    limit: int,
) -> dict[str, Any]:
    clauses = ["TRUE"]
    params: list[Any] = []
    if query:
        clauses.append("(u.email ILIKE %s OR u.name ILIKE %s)")
        like = f"%{query}%"
        params.extend([like, like])
    if cursor:
        clauses.append("(u.created_at, u.id::text) < (%s, %s)")
        params.extend([cursor.created_at, cursor.item_id])
    params.append(limit + 1)
    sql = f"""
        SELECT u.id, u.email, u.name, u.role, u.created_at, u.last_seen_at,
               count(DISTINCT r.submission_id) AS requests,
               count(DISTINCT r.submission_id) FILTER (
                   WHERE r.status IN ('failed', 'interrupted')
               ) AS failed
        FROM users u
        LEFT JOIN job_runs r ON r.user_id = u.id AND r.attempt = 1
        WHERE {" AND ".join(clauses)}
        GROUP BY u.id
        ORDER BY u.created_at DESC, u.id::text DESC
        LIMIT %s
    """
    with _conn(database_url) as conn:
        rows = conn.execute(sql, params).fetchall()
    more = len(rows) > limit
    selected = rows[:limit]
    return {
        "items": [_user_row(row) for row in selected],
        "next_cursor": _next_cursor(selected, more, "id"),
    }


def _user_detail(database_url: str, user_id: str) -> dict[str, Any]:
    checked = _uuid(user_id)
    with _conn(database_url) as conn:
        row = conn.execute(
            """
            SELECT u.id, u.email, u.name, u.role, u.created_at, u.verified_at,
                   u.last_login_at, u.last_seen_at,
                   count(DISTINCT r.submission_id) AS requests,
                   count(DISTINCT r.submission_id) FILTER (
                       WHERE r.status = 'completed'
                   ) AS completed,
                   count(DISTINCT r.submission_id) FILTER (
                       WHERE r.status IN ('failed', 'interrupted')
                   ) AS failed,
                   COALESCE(sum(r.video_seconds) FILTER (WHERE r.attempt = 1), 0)
                       AS video_seconds
            FROM users u
            LEFT JOIN job_runs r ON r.user_id = u.id
            WHERE u.id = %s
            GROUP BY u.id
            """,
            (checked,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return {
        **_user_row(row),
        "verified": row["verified_at"] is not None,
        "last_login_at": _stamp(row["last_login_at"]),
        "completed": int(row["completed"]),
        "video_minutes": round(float(row["video_seconds"] or 0) / 60, 1),
    }


def _detail_run(row: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    measured = sum(
        float(event["detail"].get("seconds", 0))
        for event in events
        if event["kind"] == "stage_finished"
    )
    timing = _timing(row, measured)
    stages = [
        {
            "stage": stage.value,
            "seconds": sum(
                float(event["detail"].get("seconds", 0))
                for event in events
                if event["kind"] == "stage_finished" and event["stage"] == stage.value
            ),
        }
        for stage in Stage
    ]
    return {
        "run_id": str(row["run_id"]),
        "attempt": row["attempt"],
        "outcome": row["status"],
        "created_at": _stamp(row["created_at"]),
        "started_at": _stamp(row["started_at"]),
        "finished_at": _stamp(row["finished_at"]),
        "failed_stage": row["failed_stage"],
        "error_code": row["error_code"],
        "diagnostic": row["diagnostic"],
        "video_seconds": row["video_seconds"],
        "timing_coverage": _timing_coverage(timing),
        "pipeline_version": row.get("pipeline_version"),
        "importance_config_hash": row.get("importance_config_hash"),
        "snapshot_schema_version": row.get("snapshot_schema_version"),
        "snapshot_reader_version": row.get("snapshot_reader_version"),
        "snapshot_at": _stamp(row.get("snapshot_at")),
        **timing,
        "stages": stages,
        "breakdown": (row["stage_seconds"] or {}).get("diagnostic", {}),
        "llm": row["llm"],
        "provenance": row["provenance"],
        "events": [
            {
                "at": _stamp(event["at"]),
                "kind": event["kind"],
                "stage": event["stage"],
                "detail": event["detail"],
            }
            for event in events
        ],
    }


def _request_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "submission_id": row["submission_id"],
        "status": row["status"],
        "submission_state": "expired" if row["expired"] else "retained",
        "user_email": row["email"],
        "created_at": _stamp(row["created_at"]),
        "video_seconds": row["video_seconds"],
        "failed_stage": row["failed_stage"],
        **_timing(row, float(row["measured"] or 0)),
    }


def _timing(row: dict[str, Any], measured: float) -> dict[str, Any]:
    job_elapsed = _between(row["created_at"], row["finished_at"])
    upload_validation = _between(row["created_at"], row["queued_at"])
    queue_wait = _between(row["queued_at"], row["started_at"])
    processing = _between(row["started_at"], row["finished_at"])
    other = None if processing is None else processing - measured
    status = None
    if other is not None:
        if -0.05 <= other < 0:
            other = 0.0
        status = "timing_inconsistent" if other < -0.05 else "consistent"
    return {
        "job_elapsed_seconds": job_elapsed,
        "upload_validation_seconds": upload_validation,
        "queue_wait_seconds": queue_wait,
        "processing_seconds": processing,
        "other_seconds": other,
        "timing_status": status,
    }


def _problem(row: dict[str, Any], slow_seconds: int) -> dict[str, Any]:
    inconsistent = float(row["other_seconds"]) < -0.05
    if inconsistent:
        kind = "timing_inconsistent"
    elif row["status"] in {"failed", "interrupted"}:
        kind = "failed"
    else:
        kind = "slow"
    return {
        "submission_id": row["submission_id"],
        "kind": kind,
        "status": row["status"],
        "failed_stage": row["failed_stage"],
        "error_code": row["error_code"],
        "job_elapsed_seconds": _float(row["job_elapsed"]),
        "processing_seconds": _float(row["processing"]),
        "other_seconds": _float(row["other_seconds"]),
        "slow_threshold_seconds": slow_seconds,
        "finished_at": _stamp(row["finished_at"]),
    }


def _stage_stat(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": row["stage"],
        "count": int(row["count"]),
        "median_seconds": _float(row["median_seconds"]),
        "p95_seconds": _float(row["p95_seconds"]),
    }


def _user_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "created_at": _stamp(row["created_at"]),
        "last_seen_at": _stamp(row["last_seen_at"]),
        "requests": int(row["requests"]),
        "failed": int(row["failed"]),
    }


def _next_cursor(rows: list[dict[str, Any]], more: bool, id_key: str) -> str | None:
    if not more or not rows:
        return None
    row = rows[-1]
    return _encode_cursor(
        PageCursor(created_at=row["created_at"], item_id=str(row[id_key]))
    )


def _encode_cursor(cursor: PageCursor) -> str:
    raw = cursor.model_dump_json().encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str | None) -> PageCursor | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = base64.urlsafe_b64decode(padded.encode())
        return PageCursor.model_validate_json(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="invalid cursor") from exc


def _timing_coverage(timing: dict[str, Any]) -> list[str]:
    names = {
        "queue_wait_seconds": "queue_wait",
        "processing_seconds": "processing",
        "other_seconds": "other",
    }
    return [label for key, label in names.items() if timing.get(key) is not None]


def _label(value: str | None) -> str | None:
    try:
        return require_label(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid filter") from exc


def _failure_cursor(page: dict[str, Any]) -> str | None:
    if not page.pop("more", False) or not page["items"]:
        return None
    last = page["items"][-1]
    created = datetime.fromisoformat(last["created_at"])
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return _encode_cursor(PageCursor(created_at=created, item_id=last["run_id"]))


def _query(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _uuid(value: str) -> str:
    try:
        import uuid

        return str(uuid.UUID(value))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid id") from exc


def _between(start: datetime | None, finish: datetime | None) -> float | None:
    if start is None or finish is None:
        return None
    return (finish - start).total_seconds()


def _stamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def _conn(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, row_factory=dict_row)
