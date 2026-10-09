"""Shared analytics definitions for the admin observatory.

Every v2 population, rate, and sampling rule lives here. Routes consume these
helpers instead of restating them.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
MIN_COMPARISON_N = 10
SCATTER_CAP = 500
RESUBMISSION_NOTE = (
    "Observed resubmission counts registered accounts with a later linked "
    "submission. Telemetry history starts at a known date, and expiry unlinks "
    "older runs."
)


class AnalyticsWindow(StrEnum):
    """Closed run and account window names."""

    H24 = "24h"
    D7 = "7d"
    D30 = "30d"


_WINDOW_DELTA = {
    AnalyticsWindow.H24: timedelta(hours=24),
    AnalyticsWindow.D7: timedelta(days=7),
    AnalyticsWindow.D30: timedelta(days=30),
}
_BUCKET = {
    AnalyticsWindow.H24: "hour",
    AnalyticsWindow.D7: "day",
    AnalyticsWindow.D30: "day",
}
SampleLabel = Literal["sufficient", "insufficient"]


def window_bounds(window: AnalyticsWindow, *, now: datetime) -> tuple[datetime, datetime]:
    """Return the half-open ``[start, end)`` interval ending at ``now``."""
    return now - _WINDOW_DELTA[window], now


def bucket_literal(window: AnalyticsWindow) -> str:
    """Return the ``date_trunc`` unit for a window. Only ``hour`` or ``day``."""
    return _BUCKET[window]


def require_label(value: str | None) -> str | None:
    """Accept an empty filter or one safe label. Reject SQL metacharacters."""
    if value is None or value == "":
        return None
    if _SAFE_LABEL.fullmatch(value) is None:
        raise ValueError("invalid filter")
    return value


def sample_label(n: int) -> SampleLabel:
    """Mark comparison groups below the shared minimum sample size."""
    return "sufficient" if n >= MIN_COMPARISON_N else "insufficient"


def terminal_rates(*, completed: int, failed: int, interrupted: int) -> dict[str, Any]:
    """Rate each terminal outcome over ``completed + failed + interrupted``.

    ``failure_rate`` is ``failed / terminal_runs``. Interrupted stays its own
    rate. Callers report in-flight runs separately; they are not a denominator.
    """
    terminal = completed + failed + interrupted

    def rate(count: int) -> float | None:
        return None if terminal == 0 else count / terminal

    return {
        "completed": completed,
        "failed": failed,
        "interrupted": interrupted,
        "terminal_runs": terminal,
        "completed_rate": rate(completed),
        "failed_rate": rate(failed),
        "interrupted_rate": rate(interrupted),
        "failure_rate": rate(failed),
    }


def executions_in_window_cte() -> str:
    """Every run whose ``created_at`` falls in the half-open window."""
    return """
    executions_in_window AS (
        SELECT job_runs.*
        FROM job_runs
        WHERE job_runs.created_at >= %(window_start)s
          AND job_runs.created_at < %(window_end)s
    )
    """


def latest_completed_run_per_submission_cte() -> str:
    """Latest completed attempt globally, then restricted to the window.

    Filtering completed runs to the window before choosing the latest attempt
    is a different query and must not be used here.
    """
    return """
    latest_completed_globally AS (
        SELECT DISTINCT ON (submission_id) job_runs.*
        FROM job_runs
        WHERE status = 'completed'
        ORDER BY submission_id, attempt DESC
    ),
    latest_completed_run_per_submission AS (
        SELECT latest_completed_globally.*
        FROM latest_completed_globally
        WHERE created_at >= %(window_start)s
          AND created_at < %(window_end)s
    )
    """


def population_sql(name: Literal["executions", "latest_completed"]) -> str:
    """Select one named population. ``name`` is a closed identifier."""
    if name == "executions":
        cte = executions_in_window_cte()
        source = "executions_in_window"
    else:
        cte = latest_completed_run_per_submission_cte()
        source = "latest_completed_run_per_submission"
    return f"""
        WITH {cte}
        SELECT submission_id, attempt, run_id::text AS run_id, status, created_at
        FROM {source}
        ORDER BY submission_id, attempt
    """


def account_created_sql() -> str:
    """New accounts by ``users.created_at``. This is not a run population."""
    return """
        SELECT count(*) AS n
        FROM users
        WHERE created_at >= %(window_start)s
          AND created_at < %(window_end)s
    """


def account_verified_sql() -> str:
    """Newly verified accounts by ``users.verified_at``."""
    return """
        SELECT count(*) AS n
        FROM users
        WHERE verified_at >= %(window_start)s
          AND verified_at < %(window_end)s
    """


def account_active_sql() -> str:
    """Authenticated-active accounts by ``users.last_seen_at``."""
    return """
        SELECT count(*) AS n
        FROM users
        WHERE last_seen_at >= %(window_start)s
          AND last_seen_at < %(window_end)s
    """


def observed_resubmission_sql() -> str:
    """Censor on the first linked submission, then count a later submission."""
    return """
        WITH first_submission AS (
            SELECT DISTINCT ON (user_id)
                user_id, submission_id, created_at AS first_at
            FROM job_runs
            WHERE user_id IS NOT NULL AND attempt = 1
            ORDER BY user_id, created_at ASC
        ),
        eligible AS (
            SELECT user_id, submission_id, first_at
            FROM first_submission
            WHERE first_at <= %(cutoff)s - %(horizon)s
        )
        SELECT
            count(*) AS eligible_n,
            count(*) FILTER (
                WHERE EXISTS (
                    SELECT 1
                    FROM job_runs later
                    WHERE later.user_id = eligible.user_id
                      AND later.submission_id <> eligible.submission_id
                      AND later.attempt = 1
                      AND later.created_at > eligible.first_at
                      AND later.created_at <= eligible.first_at + %(horizon)s
                )
            ) AS resubmitted_n
        FROM eligible
    """


def observed_resubmission(
    row: dict[str, Any] | None,
    *,
    horizon: timedelta,
    account_retention_days: int,
) -> dict[str, Any]:
    """Apply the retention and small-sample rules to one cohort row."""
    days = horizon.days
    if account_retention_days < days:
        return _resubmission(None, None, None, available=False)
    eligible = 0 if row is None else int(row["eligible_n"])
    resubmitted = 0 if row is None else int(row["resubmitted_n"])
    rate = None if eligible < MIN_COMPARISON_N else resubmitted / eligible
    return _resubmission(eligible, resubmitted, rate, available=True)


def _resubmission(
    eligible: int | None,
    resubmitted: int | None,
    rate: float | None,
    *,
    available: bool,
) -> dict[str, Any]:
    return {
        "eligible_n": eligible,
        "resubmitted_n": resubmitted,
        "rate": rate,
        "available": available,
        "coverage_note": RESUBMISSION_NOTE,
    }


def select_scatter(
    points: list[dict[str, Any]],
    *,
    p99: float | None,
    slow_seconds: float,
    cap: int = SCATTER_CAP,
) -> dict[str, Any]:
    """Keep failures and outliers first, then fill with a hash of ``run_id``."""
    exceptional = [point for point in points if _exceptional(point, p99, slow_seconds)]
    ordinary = [point for point in points if point not in exceptional]
    ranked = sorted(exceptional, key=_exceptional_key)
    if len(ranked) > cap:
        chosen = ranked[:cap]
        mode = "exceptional_overflow"
    else:
        rest = sorted(ordinary, key=lambda point: _run_hash(str(point["run_id"])))
        chosen = ranked + rest[: cap - len(ranked)]
        mode = "complete" if len(points) <= cap else "exceptional_first"
    return {
        "total": len(points),
        "shown": len(chosen),
        "exceptional_total": len(exceptional),
        "mode": mode,
        "points": chosen,
    }


def _exceptional(point: dict[str, Any], p99: float | None, slow_seconds: float) -> bool:
    status = point["status"]
    if status in {"failed", "interrupted"} or point["timing_inconsistent"]:
        return True
    processing = float(point["processing_seconds"])
    if processing > slow_seconds:
        return True
    return p99 is not None and processing > p99


def _exceptional_key(point: dict[str, Any]) -> tuple[int, float, str]:
    status = point["status"]
    if status == "failed":
        group = 0
    elif status == "interrupted":
        group = 1
    elif point["timing_inconsistent"]:
        group = 2
    else:
        group = 3
    processing = -float(point["processing_seconds"]) if group == 3 else 0.0
    return (group, processing, str(point["run_id"]))


def _run_hash(run_id: str) -> str:
    return hashlib.sha256(run_id.encode("utf-8")).hexdigest()
