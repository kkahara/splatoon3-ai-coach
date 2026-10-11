"""Coaching, version, LLM, and product aggregates for the admin observatory."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from public_site.admin_analytics import (
    AnalyticsWindow,
    account_active_sql,
    account_created_sql,
    account_verified_sql,
    bucket_literal,
    executions_in_window_cte,
    latest_completed_run_per_submission_cte,
    observed_resubmission,
    observed_resubmission_sql,
    sample_label,
    terminal_rates,
    window_bounds,
)
from public_site.coaching_metrics import PublicCounts, collapse_public_counts
from public_site.llm_pricing import PRICING_VERSION, estimated_call_cost

_PUBLIC = ("recommendation", "no_recommendation", "blocked", "not_reviewed")
_SELECTION = ("candidate", "positive_score", "selected")
_LLM = ("requested_succeeded", "requested_failed", "not_requested")
_VALIDATION = (
    "validated",
    "contains_unverified_wording",
    "blocked_unsupported_claim",
    "not_validated",
)
_COUNTS = (
    *_SELECTION,
    *_LLM,
    "unselected_requested",
    *_VALIDATION,
    *_PUBLIC,
    "review_missing",
)
_VERSION_FILTER = """
    AND (%(pipeline_version)s::text IS NULL OR {alias}.pipeline_version = %(pipeline_version)s)
    AND (%(evidence_version)s::text IS NULL
         OR {alias}.evidence_version::text = %(evidence_version)s)
    AND (%(vision_config)s::text IS NULL OR {alias}.vision_config_hash = %(vision_config)s)
    AND (%(importance_config)s::text IS NULL
         OR {alias}.importance_config_hash = %(importance_config)s)
    AND (%(provider)s::text IS NULL OR {alias}.llm_provider = %(provider)s)
    AND (%(model)s::text IS NULL OR {alias}.llm_model = %(model)s)
"""
_GROUP = """
    pipeline_version, evidence_version, vision_config_hash,
    importance_config_hash, llm_provider, llm_model
"""
_HORIZONS = (1, 7, 30)


def coaching_report(
    conn,
    *,
    window: AnalyticsWindow,
    filters: dict[str, str | None],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Sum coaching snapshots over the latest completed run of each submission."""
    params = _params(window, filters, now)
    rows = conn.execute(_coaching_sql(), params).fetchall()
    return _coaching_payload(window, rows)


def versions_report(
    conn,
    *,
    window: AnalyticsWindow,
    filters: dict[str, str | None],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compare execution health with coaching output for the same version key."""
    params = _params(window, filters, now)
    executions = conn.execute(_execution_versions_sql(), params).fetchall()
    output = conn.execute(_output_versions_sql(), params).fetchall()
    return {
        "population_executions": "executions_in_window",
        "population_output": "latest_completed_run_per_submission",
        "window": window.value,
        "rows": _merge_versions(executions, output),
    }


def llm_report(
    conn,
    *,
    window: AnalyticsWindow,
    filters: dict[str, str | None],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate stored LLM calls for executions in the window."""
    moment = now or datetime.now(UTC)
    start, end = window_bounds(window, now=moment)
    params = _params(window, filters, moment)
    totals = conn.execute(_llm_totals_sql(), params).fetchone()
    groups = conn.execute(_llm_groups_sql(), params).fetchall()
    series = conn.execute(_llm_series_sql(window), params).fetchall()
    return _llm_payload(window, start, end, totals, groups, series)


def product_report(
    conn,
    *,
    window: AnalyticsWindow,
    account_retention_days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Account cards, submission output, requests, and observed resubmission."""
    moment = now or datetime.now(UTC)
    params = _params(window, {}, moment)
    return {
        "window": window.value,
        "accounts": _accounts(conn, params),
        "submissions": _submission_output(conn, params),
        "requests": _request_count(conn, params),
        "observed_resubmission": _resubmission(conn, moment, account_retention_days),
    }


def _params(
    window: AnalyticsWindow,
    filters: dict[str, str | None],
    now: datetime | None,
) -> dict[str, Any]:
    start, end = window_bounds(window, now=now or datetime.now(UTC))
    params: dict[str, Any] = {"window_start": start, "window_end": end}
    for name in (
        "pipeline_version",
        "evidence_version",
        "vision_config",
        "importance_config",
        "provider",
        "model",
    ):
        params[name] = filters.get(name)
    return params


def _coaching_sql() -> str:
    clause = _VERSION_FILTER.format(alias="latest_completed_run_per_submission")
    return f"""
        WITH {latest_completed_run_per_submission_cte()}
        SELECT coaching_metrics
        FROM latest_completed_run_per_submission
        WHERE TRUE
        {clause}
    """


def _coaching_payload(window: AnalyticsWindow, rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = dict.fromkeys(_COUNTS, 0)
    versions: Counter[str] = Counter()
    missing = 0
    by_type: dict[str, PublicCounts] = {}
    by_factor: dict[str, PublicCounts] = {}
    for row in rows:
        document = row["coaching_metrics"]
        if not isinstance(document, dict):
            missing += 1
            continue
        _add_document(totals, versions, by_type, by_factor, document)
    return {
        "population": "latest_completed_run_per_submission",
        "window": window.value,
        "runs": len(rows),
        "missing_snapshot": missing,
        "snapshot_versions": _version_mix(versions),
        "selection": {name: totals[name] for name in _SELECTION},
        "llm_disposition": {name: totals[name] for name in _LLM},
        "unselected_requested": totals["unselected_requested"],
        "validation": {name: totals[name] for name in _VALIDATION},
        "public": {name: totals[name] for name in _PUBLIC},
        "review_missing": totals["review_missing"],
        "by_candidate_type": _map_rows(_collapse(by_type)),
        "by_factor": _map_rows(_collapse(by_factor)),
    }


def _add_document(
    totals: dict[str, int],
    versions: Counter[str],
    by_type: dict[str, PublicCounts],
    by_factor: dict[str, PublicCounts],
    document: dict[str, Any],
) -> None:
    versions[str(document.get("snapshot_schema_version"))] += 1
    for name in _COUNTS:
        totals[name] += _int(document.get(name))
    _add_map(by_type, document.get("by_candidate_type"))
    _add_map(by_factor, document.get("by_factor"))


def _add_map(totals: dict[str, PublicCounts], incoming: object) -> None:
    if not isinstance(incoming, dict):
        return
    for key, counts in incoming.items():
        if not isinstance(key, str) or not isinstance(counts, dict):
            continue
        row = totals.setdefault(key, PublicCounts())
        for name in _PUBLIC:
            setattr(row, name, getattr(row, name) + _int(counts.get(name)))


def _collapse(counts: dict[str, PublicCounts]) -> dict[str, PublicCounts]:
    return collapse_public_counts(counts)


def _map_rows(counts: dict[str, PublicCounts]) -> list[dict[str, Any]]:
    return [{"key": key, **value.model_dump()} for key, value in counts.items()]


def _version_mix(versions: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"snapshot_schema_version": version, "runs": count}
        for version, count in sorted(versions.items())
    ]


def _execution_versions_sql() -> str:
    clause = _VERSION_FILTER.format(alias="executions_in_window")
    return f"""
        WITH {executions_in_window_cte()}
        SELECT {_GROUP},
               count(*) AS n,
               count(*) FILTER (WHERE status = 'completed') AS completed,
               count(*) FILTER (WHERE status = 'failed') AS failed,
               count(*) FILTER (WHERE status = 'interrupted') AS interrupted,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY {_processing_seconds()}
               ) AS median_processing
        FROM executions_in_window
        WHERE TRUE
        {clause}
        GROUP BY {_GROUP}
    """


def _output_versions_sql() -> str:
    clause = _VERSION_FILTER.format(alias="latest_completed_run_per_submission")
    public_units = " + ".join(
        f"coalesce((coaching_metrics->>'{name}')::int, 0)" for name in _PUBLIC
    )
    return f"""
        WITH {latest_completed_run_per_submission_cte()}
        SELECT {_GROUP},
               count(*) AS completed_submissions,
               count(*) FILTER (WHERE coaching_metrics IS NOT NULL) AS snapshotted,
               coalesce(sum((coaching_metrics->>'recommendation')::int), 0) AS recommendations,
               coalesce(sum((coaching_metrics->>'blocked')::int), 0) AS blocked,
               coalesce(sum({public_units}), 0) AS public_units
        FROM latest_completed_run_per_submission
        WHERE TRUE
        {clause}
        GROUP BY {_GROUP}
    """


def _processing_seconds() -> str:
    return """
        CASE WHEN started_at IS NOT NULL AND finished_at IS NOT NULL
                  AND finished_at >= started_at
             THEN EXTRACT(EPOCH FROM (finished_at - started_at))
             ELSE NULL END
    """


def _merge_versions(
    executions: list[dict[str, Any]], output: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    output_by_key = {_version_key(row): row for row in output}
    rows = [
        _version_row(row, output_by_key.get(_version_key(row))) for row in executions
    ]
    rows.sort(key=lambda row: (-row["n"], row["pipeline_version"] or ""))
    return rows[:50]


def _version_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["pipeline_version"],
        row["evidence_version"],
        row["vision_config_hash"],
        row["importance_config_hash"],
        row["llm_provider"],
        row["llm_model"],
    )


def _version_row(execution: dict[str, Any], output: dict[str, Any] | None) -> dict[str, Any]:
    n = int(execution["n"])
    rates = terminal_rates(
        completed=int(execution["completed"]),
        failed=int(execution["failed"]),
        interrupted=int(execution["interrupted"]),
    )
    completed = 0 if output is None else int(output["completed_submissions"])
    units = 0 if output is None else int(output["public_units"])
    recommendations = 0 if output is None else int(output["recommendations"])
    blocked = 0 if output is None else int(output["blocked"])
    snapshotted = 0 if output is None else int(output["snapshotted"])
    return {
        "pipeline_version": execution["pipeline_version"],
        "evidence_version": execution["evidence_version"],
        "vision_config_hash": execution["vision_config_hash"],
        "importance_config_hash": execution["importance_config_hash"],
        "provider": execution["llm_provider"],
        "model": execution["llm_model"],
        "n": n,
        "sample": sample_label(n),
        "failure_rate": rates["failure_rate"],
        "interrupted_rate": rates["interrupted_rate"],
        "median_processing_seconds": _float(execution["median_processing"]),
        "completed_submissions": completed,
        "output_sample": sample_label(completed),
        "recommendations_per_submission": _ratio(recommendations, completed),
        "blocked_rate": _ratio(blocked, units),
        "snapshot_coverage": _ratio(snapshotted, completed),
    }


def _llm_totals_sql() -> str:
    return f"""
        WITH {_llm_calls_cte()}
        SELECT
            count(*) AS calls,
            count(*) FILTER (WHERE disposition <> 'skipped') AS requested,
            count(*) FILTER (WHERE disposition = 'requested_succeeded') AS succeeded,
            count(*) FILTER (WHERE disposition = 'requested_failed') AS failed,
            count(*) FILTER (WHERE disposition = 'skipped') AS skipped,
            coalesce(sum(input_tokens_est), 0) AS input_tokens_est,
            coalesce(sum(output_tokens_est), 0) AS output_tokens_est,
            percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE disposition <> 'skipped' AND latency_ms IS NOT NULL)
                AS median_latency_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE disposition <> 'skipped' AND latency_ms IS NOT NULL)
                AS p95_latency_ms,
            count(latency_ms) FILTER (WHERE disposition <> 'skipped') AS latency_n
        FROM llm_calls
    """


def _llm_groups_sql() -> str:
    return f"""
        WITH {_llm_calls_cte()}
        SELECT provider, model, disposition,
               count(*) AS calls,
               coalesce(sum(input_tokens_est) FILTER (
                   WHERE input_tokens_est IS NOT NULL AND output_tokens_est IS NOT NULL
               ), 0) AS priced_input_tokens,
               coalesce(sum(output_tokens_est) FILTER (
                   WHERE input_tokens_est IS NOT NULL AND output_tokens_est IS NOT NULL
               ), 0) AS priced_output_tokens,
               count(*) FILTER (
                   WHERE input_tokens_est IS NOT NULL AND output_tokens_est IS NOT NULL
               ) AS token_rows
        FROM llm_calls
        GROUP BY provider, model, disposition
    """


def _llm_series_sql(window: AnalyticsWindow) -> str:
    unit = bucket_literal(window)
    return f"""
        WITH {_llm_calls_cte()}
        SELECT date_trunc('{unit}', created_at AT TIME ZONE 'UTC')
                   AT TIME ZONE 'UTC' AS bucket,
               count(*) AS calls,
               count(*) FILTER (WHERE disposition = 'requested_succeeded') AS succeeded,
               count(*) FILTER (WHERE disposition = 'requested_failed') AS failed
        FROM llm_calls
        GROUP BY 1
        ORDER BY 1
    """


def _llm_calls_cte() -> str:
    return f"""
        {executions_in_window_cte()},
        llm_calls AS (
            SELECT calls.*, runs.created_at
            FROM llm_call_metrics calls
            JOIN executions_in_window runs ON runs.run_id = calls.run_id
            WHERE (%(provider)s::text IS NULL OR calls.provider = %(provider)s)
              AND (%(model)s::text IS NULL OR calls.model = %(model)s)
              AND (%(pipeline_version)s::text IS NULL
                   OR runs.pipeline_version = %(pipeline_version)s)
              AND (%(evidence_version)s::text IS NULL
                   OR runs.evidence_version::text = %(evidence_version)s)
              AND (%(vision_config)s::text IS NULL
                   OR runs.vision_config_hash = %(vision_config)s)
              AND (%(importance_config)s::text IS NULL
                   OR runs.importance_config_hash = %(importance_config)s)
        )
    """


def _llm_payload(
    window: AnalyticsWindow,
    start: datetime,
    end: datetime,
    totals: dict[str, Any],
    groups: list[dict[str, Any]],
    series: list[dict[str, Any]],
) -> dict[str, Any]:
    priced = _price_groups(groups)
    calls = int(totals["calls"])
    return {
        "population": "executions_in_window",
        "window": window.value,
        "pricing_version": PRICING_VERSION,
        "calls": calls,
        "requested": int(totals["requested"]),
        "succeeded": int(totals["succeeded"]),
        "failed": int(totals["failed"]),
        "skipped": int(totals["skipped"]),
        "latency_ms": {
            "median": _float(totals["median_latency_ms"]),
            "p95": _float(totals["p95_latency_ms"]),
            "n": int(totals["latency_n"]),
        },
        "input_tokens_est": int(totals["input_tokens_est"]),
        "output_tokens_est": int(totals["output_tokens_est"]),
        "estimated_cost": priced["estimated_cost"],
        "priced_call_share": _ratio(priced["priced_calls"], calls),
        "providers": priced["providers"],
        "models": priced["models"],
        "series": _series(window, start, end, series),
    }


def _price_groups(groups: list[dict[str, Any]]) -> dict[str, Any]:
    priced_calls = 0
    costs: list[float] = []
    providers: dict[str, dict[str, Any]] = {}
    models: dict[tuple[str, str], dict[str, Any]] = {}
    for row in groups:
        cost = _group_cost(row)
        if cost is not None:
            priced_calls += int(row["token_rows"])
            costs.append(cost)
        _accumulate(providers, row["provider"] or "unknown", row, cost)
        _accumulate(models, (row["provider"] or "unknown", row["model"] or "unknown"), row, cost)
    return {
        "priced_calls": priced_calls,
        "estimated_cost": None if not costs else sum(costs),
        "providers": _group_rows(providers),
        "models": [
            {"provider": key[0], "model": key[1], **value}
            for key, value in models.items()
        ],
    }


def _group_cost(row: dict[str, Any]) -> float | None:
    if int(row["token_rows"]) == 0:
        return None
    return estimated_call_cost(
        row["provider"],
        row["model"],
        int(row["priced_input_tokens"]),
        int(row["priced_output_tokens"]),
    )


def _accumulate(groups: dict, key, row: dict[str, Any], cost: float | None) -> None:
    item = groups.setdefault(
        key,
        {"calls": 0, "estimated_cost": None, "priced_calls": 0},
    )
    item["calls"] += int(row["calls"])
    item[row["disposition"]] = item.get(row["disposition"], 0) + int(row["calls"])
    if cost is None:
        return
    item["priced_calls"] += int(row["token_rows"])
    item["estimated_cost"] = (item["estimated_cost"] or 0) + cost


def _group_rows(groups: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"provider": key, **value} for key, value in sorted(groups.items())]


def _series(
    window: AnalyticsWindow,
    start: datetime,
    end: datetime,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    found = {row["bucket"]: row for row in rows}
    return [
        {
            "bucket": bucket.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "calls": 0 if found.get(bucket) is None else int(found[bucket]["calls"]),
            "succeeded": 0 if found.get(bucket) is None else int(found[bucket]["succeeded"]),
            "failed": 0 if found.get(bucket) is None else int(found[bucket]["failed"]),
        }
        for bucket in _buckets(start, end, bucket_literal(window))
    ]


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


def _accounts(conn, params: dict[str, Any]) -> dict[str, Any]:
    registered = conn.execute("SELECT count(*) AS n FROM users").fetchone()
    submitting = conn.execute(
        """
        SELECT count(DISTINCT user_id) AS n
        FROM job_runs
        WHERE user_id IS NOT NULL
          AND created_at >= %(window_start)s
          AND created_at < %(window_end)s
        """,
        params,
    ).fetchone()
    return {
        "new": _account_card(conn, account_created_sql(), params, "account"),
        "newly_verified": _account_card(conn, account_verified_sql(), params, "account"),
        "authenticated_active": _account_card(conn, account_active_sql(), params, "account"),
        "registered": {"n": int(registered["n"]), "window": "all_time"},
        "submitting": {"n": int(submitting["n"]), "label": "submitting"},
    }


def _account_card(conn, sql: str, params: dict[str, Any], window: str) -> dict[str, Any]:
    return {"n": int(conn.execute(sql, params).fetchone()["n"]), "window": window}


def _submission_output(conn, params: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute(
        f"""
        WITH {latest_completed_run_per_submission_cte()}
        SELECT
            count(*) FILTER (WHERE is_guest) AS guest,
            count(*) FILTER (WHERE NOT is_guest) AS account,
            coalesce(sum(video_seconds), 0) AS video_seconds
        FROM latest_completed_run_per_submission
        """,
        params,
    ).fetchone()
    thresholds = conn.execute(
        f"""
        WITH {latest_completed_run_per_submission_cte()},
        counts AS (
            SELECT user_id, count(*) AS n
            FROM latest_completed_run_per_submission
            WHERE user_id IS NOT NULL
            GROUP BY user_id
        )
        SELECT
            count(*) FILTER (WHERE n >= 1) AS at_least_1,
            count(*) FILTER (WHERE n >= 2) AS at_least_2,
            count(*) FILTER (WHERE n >= 5) AS at_least_5
        FROM counts
        """,
        params,
    ).fetchone()
    return {
        "population": "latest_completed_run_per_submission",
        "guest": int(row["guest"]),
        "account": int(row["account"]),
        "processed_video_hours": round(float(row["video_seconds"]) / 3600, 3),
        "users_with_completed_submissions": {
            "at_least_1": int(thresholds["at_least_1"]),
            "at_least_2": int(thresholds["at_least_2"]),
            "at_least_5": int(thresholds["at_least_5"]),
        },
    }


def _request_count(conn, params: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute(
        f"""
        WITH {executions_in_window_cte()}
        SELECT count(*) AS n FROM executions_in_window
        """,
        params,
    ).fetchone()
    return {"population": "executions_in_window", "n": int(row["n"])}


def _resubmission(
    conn, cutoff: datetime, account_retention_days: int
) -> list[dict[str, Any]]:
    rows = []
    for days in _HORIZONS:
        horizon = timedelta(days=days)
        found = conn.execute(
            observed_resubmission_sql(),
            {"cutoff": cutoff, "horizon": horizon},
        ).fetchone()
        measured = observed_resubmission(
            found, horizon=horizon, account_retention_days=account_retention_days
        )
        rows.append({"horizon_days": days, **measured})
    return rows


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator
