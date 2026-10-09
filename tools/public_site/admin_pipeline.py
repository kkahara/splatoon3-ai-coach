"""Pipeline and failure queries. Populations come from ``admin_analytics``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from public_site.admin_analytics import (
    AnalyticsWindow,
    bucket_literal,
    executions_in_window_cte,
    sample_label,
    select_scatter,
    terminal_rates,
    window_bounds,
)
from public_site.telemetry import Diagnostic, ErrorCode, RunStatus, Stage

_FAILURE_LIMIT = 50


def pipeline_report(
    conn,
    *,
    window: AnalyticsWindow,
    status: str | None,
    pipeline_version: str | None,
    vision_config: str | None,
    slow_seconds: float,
    queue_depth: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate executions in one run window. Queue depth is current only."""
    moment = now or datetime.now(UTC)
    start, end = window_bounds(window, now=moment)
    params = _params(start, end, status, pipeline_version, vision_config)
    rates = _rates(conn, params)
    processing = _duration_summary(conn, params, "processing_seconds")
    queue_wait = _duration_summary(conn, params, "queue_wait_seconds")
    points = _scatter_points(conn, params)
    return {
        "window": window.value,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "population": "executions_in_window",
        "rates": rates,
        "in_flight": _count(
            conn, params, "status NOT IN ('completed', 'failed', 'interrupted')"
        ),
        "processing": processing,
        "queue_wait": queue_wait,
        "current_queue_depth": queue_depth,
        "stages": _stages(conn, params),
        "diagnostics": _diagnostics(conn, params),
        "series": _series(conn, params, window, start, end),
        "versions": _versions(conn, params),
        "scatter": select_scatter(
            points,
            p99=processing["p99_seconds"],
            slow_seconds=slow_seconds,
        ),
    }


def failures_page(
    conn,
    *,
    window: AnalyticsWindow,
    filters: dict[str, str | None],
    cursor_at: datetime | None,
    cursor_id: str | None,
    limit: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return one bounded page of execution failures."""
    params = _failure_params(window, filters, now)
    params.update(cursor_at=cursor_at, cursor_id=cursor_id, limit=limit + 1)
    rows = conn.execute(_failure_sql(_FAILURE_WHERE), params).fetchall()
    more = len(rows) > limit
    return {"items": [_failure_row(row) for row in rows[:limit]], "more": more}


def failures_summary(
    conn,
    *,
    window: AnalyticsWindow,
    filters: dict[str, str | None],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Group execution failures without returning individual prose."""
    params = _failure_params(window, filters, now)
    sql = f"""
        WITH {executions_in_window_cte()}
        SELECT COALESCE(error_code, 'unknown') AS error_code,
               COALESCE(diagnostic, 'unknown') AS diagnostic,
               COALESCE(failed_stage, 'none') AS failed_stage,
               count(*) AS n
        FROM executions_in_window
        WHERE {_FAILURE_WHERE}
        GROUP BY 1, 2, 3
        ORDER BY n DESC, error_code
        LIMIT {_FAILURE_LIMIT}
    """
    rows = conn.execute(sql, params).fetchall()
    return {"groups": [{**row, "n": int(row["n"])} for row in rows]}


def _params(
    start: datetime,
    end: datetime,
    status: str | None,
    pipeline_version: str | None,
    vision_config: str | None,
) -> dict[str, Any]:
    return {
        "window_start": start,
        "window_end": end,
        "status": status,
        "pipeline_version": pipeline_version,
        "vision_config": vision_config,
    }


def _measured_cte() -> str:
    return f"""
        {executions_in_window_cte()},
        filtered AS (
            SELECT *
            FROM executions_in_window
            WHERE (%(status)s::text IS NULL OR status = %(status)s)
              AND (%(pipeline_version)s::text IS NULL
                   OR pipeline_version = %(pipeline_version)s)
              AND (%(vision_config)s::text IS NULL
                   OR vision_config_hash = %(vision_config)s)
        ),
        measured AS (
            SELECT filtered.*,
                {_seconds("started_at", "finished_at")} AS processing_seconds,
                {_seconds("queued_at", "started_at")} AS queue_wait_seconds,
                COALESCE(
                    stage_seconds->>'timing_status' = 'timing_inconsistent',
                    false
                ) AS timing_inconsistent
            FROM filtered
        )
    """


def _seconds(start: str, end: str) -> str:
    return f"""
        CASE WHEN {start} IS NOT NULL AND {end} IS NOT NULL AND {end} >= {start}
             THEN EXTRACT(EPOCH FROM ({end} - {start}))
             ELSE NULL END
    """


def _rates(conn, params: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute(
        f"""
        WITH {_measured_cte()}
        SELECT
            count(*) FILTER (WHERE status = 'completed') AS completed,
            count(*) FILTER (WHERE status = 'failed') AS failed,
            count(*) FILTER (WHERE status = 'interrupted') AS interrupted
        FROM measured
        """,
        params,
    ).fetchone()
    return terminal_rates(
        completed=int(row["completed"]),
        failed=int(row["failed"]),
        interrupted=int(row["interrupted"]),
    )


def _count(conn, params: dict[str, Any], predicate: str) -> int:
    row = conn.execute(
        f"WITH {_measured_cte()} SELECT count(*) AS n FROM measured WHERE {predicate}",
        params,
    ).fetchone()
    return int(row["n"])


def _duration_summary(conn, params: dict[str, Any], column: str) -> dict[str, Any]:
    if column not in {"processing_seconds", "queue_wait_seconds"}:
        raise ValueError(column)
    row = conn.execute(
        f"""
        WITH {_measured_cte()}
        SELECT
            percentile_cont(0.5) WITHIN GROUP (ORDER BY {column}) AS median_seconds,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY {column}) AS p95_seconds,
            percentile_cont(0.99) WITHIN GROUP (ORDER BY {column}) AS p99_seconds,
            count({column}) AS n
        FROM measured
        """,
        params,
    ).fetchone()
    return {
        "median_seconds": _float(row["median_seconds"]),
        "p95_seconds": _float(row["p95_seconds"]),
        "p99_seconds": _float(row["p99_seconds"]),
        "n": int(row["n"]),
    }


def _stages(conn, params: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        WITH {_measured_cte()}
        SELECT events.stage,
               count(*) AS jobs,
               count(*) FILTER (WHERE (events.detail->>'exit_code')::int <> 0) AS nonzero_exits,
               count(DISTINCT measured.run_id) FILTER (
                   WHERE measured.status IN ('failed', 'interrupted')
                     AND measured.failed_stage = events.stage
               ) AS terminal_failures,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY (events.detail->>'seconds')::float
               ) AS median_seconds,
               percentile_cont(0.95) WITHIN GROUP (
                   ORDER BY (events.detail->>'seconds')::float
               ) AS p95_seconds
        FROM job_events events
        JOIN measured ON measured.run_id = events.run_id
        WHERE events.kind = 'stage_finished'
        GROUP BY events.stage
        ORDER BY events.stage
        """,
        params,
    ).fetchall()
    return [_stage_row(row) for row in rows]


def _stage_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": row["stage"],
        "jobs": int(row["jobs"]),
        "nonzero_exits": int(row["nonzero_exits"]),
        "terminal_failures": int(row["terminal_failures"]),
        "median_seconds": _float(row["median_seconds"]),
        "p95_seconds": _float(row["p95_seconds"]),
    }


def _diagnostics(conn, params: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute(
        f"""
        WITH {_measured_cte()}
        SELECT
            {_median_path("vision_decode")} AS decode_median,
            {_median_path("vision_detectors")} AS detectors_median,
            {_median_path("fusion")} AS fusion_median,
            {_median_path("analyze_non_vision")} AS analyze_non_vision_median,
            count(*) FILTER (
                WHERE stage_seconds #>> '{{diagnostic,vision_decode}}' IS NULL
            ) AS missing_breakdown
        FROM measured
        """,
        params,
    ).fetchone()
    return {
        "decode_median": _float(row["decode_median"]),
        "detectors_median": _float(row["detectors_median"]),
        "fusion_median": _float(row["fusion_median"]),
        "analyze_non_vision_median": _float(row["analyze_non_vision_median"]),
        "missing_breakdown": int(row["missing_breakdown"]),
    }


def _median_path(name: str) -> str:
    return (
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY "
        f"(stage_seconds #>> '{{diagnostic,{name}}}')::float)"
    )


def _series(
    conn,
    params: dict[str, Any],
    window: AnalyticsWindow,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    unit = bucket_literal(window)
    rows = conn.execute(
        f"""
        WITH {_measured_cte()}
        SELECT date_trunc('{unit}', created_at AT TIME ZONE 'UTC')
                   AT TIME ZONE 'UTC' AS bucket,
               count(*) AS jobs,
               count(*) FILTER (WHERE status = 'completed') AS completed,
               count(*) FILTER (WHERE status = 'failed') AS failed,
               count(*) FILTER (WHERE status = 'interrupted') AS interrupted,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY processing_seconds)
                   AS processing_median,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY queue_wait_seconds)
                   AS queue_wait_median
        FROM measured
        GROUP BY 1
        ORDER BY 1
        """,
        params,
    ).fetchall()
    found = {row["bucket"]: row for row in rows}
    return [
        _series_point(bucket, found.get(bucket)) for bucket in _buckets(start, end, unit)
    ]


def _series_point(bucket: datetime, row: dict[str, Any] | None) -> dict[str, Any]:
    completed = 0 if row is None else int(row["completed"])
    failed = 0 if row is None else int(row["failed"])
    interrupted = 0 if row is None else int(row["interrupted"])
    rates = terminal_rates(completed=completed, failed=failed, interrupted=interrupted)
    return {
        "bucket": bucket.isoformat(),
        "jobs": 0 if row is None else int(row["jobs"]),
        "processing_median": None if row is None else _float(row["processing_median"]),
        "queue_wait_median": None if row is None else _float(row["queue_wait_median"]),
        "failure_rate": rates["failure_rate"],
        "completed_rate": rates["completed_rate"],
        "interrupted_rate": rates["interrupted_rate"],
    }


def _buckets(start: datetime, end: datetime, unit: str) -> list[datetime]:
    step = timedelta(hours=1) if unit == "hour" else timedelta(days=1)
    cursor = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    if unit == "day":
        cursor = cursor.replace(hour=0)
    found: list[datetime] = []
    while cursor < end:
        found.append(cursor)
        cursor += step
    return found


def _versions(conn, params: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        WITH {_measured_cte()}
        SELECT COALESCE(pipeline_version, 'unknown') AS pipeline_version,
               COALESCE(vision_config_hash, 'unknown') AS vision_config_hash,
               count(*) AS n,
               count(*) FILTER (WHERE status = 'completed') AS completed,
               count(*) FILTER (WHERE status = 'failed') AS failed,
               count(*) FILTER (WHERE status = 'interrupted') AS interrupted,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY processing_seconds)
                   AS median_processing
        FROM measured
        GROUP BY 1, 2
        ORDER BY n DESC, pipeline_version
        LIMIT 50
        """,
        params,
    ).fetchall()
    return [_version_row(row) for row in rows]


def _version_row(row: dict[str, Any]) -> dict[str, Any]:
    n = int(row["n"])
    rates = terminal_rates(
        completed=int(row["completed"]),
        failed=int(row["failed"]),
        interrupted=int(row["interrupted"]),
    )
    return {
        "pipeline_version": row["pipeline_version"],
        "vision_config_hash": row["vision_config_hash"],
        "n": n,
        "sample": sample_label(n),
        "failure_rate": rates["failure_rate"],
        "completed_rate": rates["completed_rate"],
        "interrupted_rate": rates["interrupted_rate"],
        "median_processing": _float(row["median_processing"]),
    }


def _scatter_points(conn, params: dict[str, Any]) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"""
        WITH {_measured_cte()}
        SELECT run_id::text AS run_id, submission_id, status,
               processing_seconds, video_seconds, timing_inconsistent
        FROM measured
        WHERE processing_seconds IS NOT NULL AND video_seconds IS NOT NULL
        """,
        params,
    ).fetchall()
    return [
        {
            "run_id": row["run_id"],
            "submission_id": row["submission_id"],
            "status": row["status"],
            "processing_seconds": float(row["processing_seconds"]),
            "video_seconds": float(row["video_seconds"]),
            "timing_inconsistent": bool(row["timing_inconsistent"]),
        }
        for row in rows
    ]


_FAILURE_WHERE = """
    status IN ('failed', 'interrupted')
    AND (%(status)s::text IS NULL OR status = %(status)s)
    AND (%(stage)s::text IS NULL OR failed_stage = %(stage)s)
    AND (%(error_code)s::text IS NULL OR error_code = %(error_code)s)
    AND (%(diagnostic)s::text IS NULL OR diagnostic = %(diagnostic)s)
    AND (%(pipeline_version)s::text IS NULL OR pipeline_version = %(pipeline_version)s)
    AND (%(evidence_version)s::text IS NULL OR evidence_version::text = %(evidence_version)s)
    AND (%(vision_config)s::text IS NULL OR vision_config_hash = %(vision_config)s)
    AND (%(importance_config)s::text IS NULL
         OR importance_config_hash = %(importance_config)s)
    AND (%(provider)s::text IS NULL OR llm_provider = %(provider)s)
    AND (%(model)s::text IS NULL OR llm_model = %(model)s)
    AND (%(cursor_at)s::timestamptz IS NULL
         OR (created_at, run_id::text) < (%(cursor_at)s, %(cursor_id)s))
"""


def _failure_params(
    window: AnalyticsWindow,
    filters: dict[str, str | None],
    now: datetime | None,
) -> dict[str, Any]:
    start, end = window_bounds(window, now=now or datetime.now(UTC))
    params: dict[str, Any] = {
        "window_start": start,
        "window_end": end,
        "cursor_at": None,
        "cursor_id": None,
    }
    for key in (
        "status",
        "stage",
        "error_code",
        "diagnostic",
        "pipeline_version",
        "evidence_version",
        "vision_config",
        "importance_config",
        "provider",
        "model",
    ):
        params[key] = filters.get(key)
    return params


def _failure_sql(where: str) -> str:
    return f"""
        WITH {executions_in_window_cte()}
        SELECT run_id::text AS run_id, submission_id, attempt, status,
               created_at, failed_stage, error_code, diagnostic,
               pipeline_version, evidence_version, vision_config_hash,
               importance_config_hash, llm_provider, llm_model
        FROM executions_in_window
        WHERE {where}
        ORDER BY created_at DESC, run_id::text DESC
        LIMIT %(limit)s
    """


def _failure_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "submission_id": row["submission_id"],
        "attempt": row["attempt"],
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
        "failed_stage": row["failed_stage"],
        "error_code": row["error_code"],
        "diagnostic": row["diagnostic"],
        "pipeline_version": row["pipeline_version"],
        "evidence_version": row["evidence_version"],
        "vision_config_hash": row["vision_config_hash"],
        "importance_config_hash": row["importance_config_hash"],
        "provider": row["llm_provider"],
        "model": row["llm_model"],
    }


def _float(value: Any) -> float | None:
    return None if value is None else float(value)


def failure_filter(
    *,
    status: RunStatus | None,
    stage: Stage | None,
    error_code: ErrorCode | None,
    diagnostic: Diagnostic | None,
) -> dict[str, str | None]:
    """Closed enums only. Label filters are added by the route after validation."""
    return {
        "status": None if status is None else status.value,
        "stage": None if stage is None else stage.value,
        "error_code": None if error_code is None else error_code.value,
        "diagnostic": None if diagnostic is None else diagnostic.value,
    }
