"""Executable definitions for the admin observatory populations."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg.rows import dict_row
from public_site.accounts import AccountStore
from public_site.admin_analytics import (
    MIN_COMPARISON_N,
    AnalyticsWindow,
    account_active_sql,
    account_created_sql,
    account_verified_sql,
    observed_resubmission,
    observed_resubmission_sql,
    population_sql,
    sample_label,
    select_scatter,
    terminal_rates,
)
from public_site.admin_pipeline import pipeline_report
from public_site.telemetry import TelemetryStore


def _database_url() -> str:
    url = os.environ.get("PUBLIC_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("PUBLIC_DATABASE_URL is not set")
    parts = urlsplit(url)
    admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, ""))
    test_url = urlunsplit((parts.scheme, parts.netloc, "/coach_test", parts.query, ""))
    with psycopg.connect(admin, autocommit=True) as conn:
        found = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            ("coach_test",),
        ).fetchone()
        if found is None:
            conn.execute("CREATE DATABASE coach_test")
    return test_url


@pytest.fixture
def analytics_conn():
    url = _database_url()
    AccountStore(url).ensure_schema()
    TelemetryStore(url).ensure_schema()
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("TRUNCATE admin_audit_log, llm_call_metrics, job_events, job_runs")
        yield conn
        conn.rollback()


def test_latest_completed_is_chosen_before_the_window(analytics_conn) -> None:
    submission = uuid.uuid4().hex
    _insert(
        analytics_conn,
        submission_id=submission,
        attempt=1,
        status="completed",
        created_at=datetime(2026, 9, 30, tzinfo=UTC),
    )
    _insert(
        analytics_conn,
        submission_id=submission,
        attempt=2,
        status="completed",
        created_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    window = {
        "window_start": datetime(2026, 9, 24, tzinfo=UTC),
        "window_end": datetime(2026, 10, 1, tzinfo=UTC),
    }

    latest = analytics_conn.execute(population_sql("latest_completed"), window).fetchall()
    executions = analytics_conn.execute(population_sql("executions"), window).fetchall()

    assert latest == []
    assert [row["attempt"] for row in executions] == [1]


def test_failed_retry_keeps_the_earlier_completed_run(analytics_conn) -> None:
    submission = uuid.uuid4().hex
    _insert(
        analytics_conn,
        submission_id=submission,
        attempt=1,
        status="completed",
        created_at=datetime(2026, 9, 30, tzinfo=UTC),
    )
    _insert(
        analytics_conn,
        submission_id=submission,
        attempt=2,
        status="failed",
        created_at=datetime(2026, 10, 2, tzinfo=UTC),
    )
    window = {
        "window_start": datetime(2026, 9, 24, tzinfo=UTC),
        "window_end": datetime(2026, 10, 3, tzinfo=UTC),
    }

    latest = analytics_conn.execute(population_sql("latest_completed"), window).fetchall()
    executions = analytics_conn.execute(population_sql("executions"), window).fetchall()

    assert [row["attempt"] for row in latest] == [1]
    assert [row["attempt"] for row in executions] == [1, 2]


def test_run_window_is_half_open(analytics_conn) -> None:
    start = datetime(2026, 9, 24, tzinfo=UTC)
    end = datetime(2026, 10, 1, tzinfo=UTC)
    _insert(
        analytics_conn,
        submission_id=uuid.uuid4().hex,
        attempt=1,
        status="completed",
        created_at=start,
    )
    _insert(
        analytics_conn,
        submission_id=uuid.uuid4().hex,
        attempt=1,
        status="completed",
        created_at=end,
    )

    rows = analytics_conn.execute(
        population_sql("executions"),
        {"window_start": start, "window_end": end},
    ).fetchall()

    assert [row["created_at"] for row in rows] == [start]


def test_account_windows_do_not_require_a_job_run(analytics_conn) -> None:
    start = datetime(2026, 9, 24, tzinfo=UTC)
    end = datetime(2026, 10, 1, tzinfo=UTC)
    user_id = str(uuid.uuid4())
    analytics_conn.execute(
        """
        INSERT INTO users (
            id, email, name, password_hash, created_at, verified_at, last_seen_at
        )
        VALUES (%s, %s, 'Contract', 'hash', %s, %s, %s)
        """,
        (user_id, f"{user_id}@example.test", start, start, start),
    )
    params = {"window_start": start, "window_end": end}

    created = analytics_conn.execute(account_created_sql(), params).fetchone()["n"]
    verified = analytics_conn.execute(account_verified_sql(), params).fetchone()["n"]
    active = analytics_conn.execute(account_active_sql(), params).fetchone()["n"]
    runs = analytics_conn.execute(population_sql("executions"), params).fetchall()

    assert created >= 1
    assert verified >= 1
    assert active >= 1
    assert runs == []
    analytics_conn.execute("DELETE FROM users WHERE id = %s", (user_id,))


def test_resubmission_censors_accounts_newer_than_the_horizon(analytics_conn) -> None:
    cutoff = datetime(2026, 10, 9, tzinfo=UTC)
    horizon = timedelta(days=7)
    old_user = str(uuid.uuid4())
    new_user = str(uuid.uuid4())
    _user(analytics_conn, old_user)
    _user(analytics_conn, new_user)
    _insert(
        analytics_conn,
        submission_id="a" * 32,
        attempt=1,
        status="completed",
        created_at=cutoff - horizon,
        user_id=old_user,
    )
    _insert(
        analytics_conn,
        submission_id="b" * 32,
        attempt=1,
        status="completed",
        created_at=cutoff - horizon + timedelta(days=1),
        user_id=old_user,
    )
    _insert(
        analytics_conn,
        submission_id="c" * 32,
        attempt=1,
        status="completed",
        created_at=cutoff - horizon + timedelta(seconds=1),
        user_id=new_user,
    )

    row = analytics_conn.execute(
        observed_resubmission_sql(), {"cutoff": cutoff, "horizon": horizon}
    ).fetchone()
    censored = observed_resubmission(row, horizon=horizon, account_retention_days=365)
    unavailable = observed_resubmission(row, horizon=horizon, account_retention_days=6)

    assert row["eligible_n"] == 1
    assert row["resubmitted_n"] == 1
    assert censored["rate"] is None
    assert censored["eligible_n"] < MIN_COMPARISON_N
    assert unavailable["available"] is False
    assert unavailable["rate"] is None


def test_pipeline_failure_rate_uses_terminal_runs(analytics_conn) -> None:
    now = datetime.now(UTC)
    for index, status in enumerate(("completed", "failed", "interrupted", "running")):
        _insert(
            analytics_conn,
            submission_id=f"{index:032x}",
            attempt=1,
            status=status,
            created_at=now - timedelta(minutes=5),
        )
    report = pipeline_report(
        analytics_conn,
        window=AnalyticsWindow.H24,
        status=None,
        pipeline_version=None,
        vision_config=None,
        slow_seconds=600,
        queue_depth=4,
        now=now,
    )
    assert report["rates"]["failure_rate"] == pytest.approx(1 / 3)
    assert report["rates"]["interrupted_rate"] == pytest.approx(1 / 3)
    assert report["in_flight"] == 1
    assert report["current_queue_depth"] == 4
    assert sum(point["jobs"] for point in report["series"]) == 4


def test_terminal_failure_rate_excludes_interruptions_and_in_flight() -> None:
    rates = terminal_rates(completed=1, failed=1, interrupted=1)
    assert rates["terminal_runs"] == 3
    assert rates["failure_rate"] == pytest.approx(1 / 3)
    assert rates["interrupted_rate"] == pytest.approx(1 / 3)
    assert rates["failed_rate"] == rates["failure_rate"]


def test_scatter_keeps_exceptional_runs_ahead_of_ordinary_ones() -> None:
    points = [
        _point("ordinary", "completed", 10, False),
        _point("failed", "failed", 1, False),
        _point("interrupted", "interrupted", 1, False),
        _point("inconsistent", "completed", 1, True),
        _point("outlier", "completed", 100, False),
    ]
    selected = select_scatter(points, p99=50, slow_seconds=1000, cap=4)
    overflow = select_scatter(points, p99=50, slow_seconds=1000, cap=2)

    assert [point["run_id"] for point in selected["points"]] == [
        "failed",
        "interrupted",
        "inconsistent",
        "outlier",
    ]
    assert selected["mode"] == "exceptional_first"
    assert [point["run_id"] for point in overflow["points"]] == ["failed", "interrupted"]
    assert overflow["mode"] == "exceptional_overflow"


def test_small_samples_are_labeled() -> None:
    assert sample_label(9) == "insufficient"
    assert sample_label(10) == "sufficient"


def test_population_sql_is_shared_rather_than_inlined() -> None:
    root = Path(__file__).resolve().parents[1] / "tools" / "public_site"
    admin = (root / "admin.py").read_text(encoding="utf-8")
    pipeline = (root / "admin_pipeline.py").read_text(encoding="utf-8")
    analytics = (root / "admin_analytics.py").read_text(encoding="utf-8")
    assert "DISTINCT ON (submission_id)" not in admin
    assert "DISTINCT ON (submission_id)" not in pipeline
    assert "DISTINCT ON (submission_id)" in analytics
    assert "executions_in_window_cte" in pipeline
    for sql in (account_created_sql(), account_verified_sql(), account_active_sql()):
        assert "job_runs" not in sql


def _insert(
    conn,
    *,
    submission_id: str,
    attempt: int,
    status: str,
    created_at: datetime,
    user_id: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO job_runs (
            run_id, submission_id, attempt, user_id, is_guest,
            language, status, created_at
        )
        VALUES (%s, %s, %s, %s, %s, 'en', %s, %s)
        """,
        (
            str(uuid.uuid4()),
            submission_id,
            attempt,
            user_id,
            user_id is None,
            status,
            created_at,
        ),
    )


def _user(conn, user_id: str) -> None:
    conn.execute(
        """
        INSERT INTO users (id, email, name, password_hash, created_at)
        VALUES (%s, %s, 'Contract', 'hash', %s)
        """,
        (user_id, f"{user_id}@example.test", datetime(2026, 1, 1, tzinfo=UTC)),
    )


def _point(run_id: str, status: str, processing: float, inconsistent: bool) -> dict:
    return {
        "run_id": run_id,
        "status": status,
        "processing_seconds": processing,
        "video_seconds": 30,
        "timing_inconsistent": inconsistent,
    }
