"""Coaching, LLM, and product queries keep the shared populations."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from public_site.accounts import AccountStore
from public_site.admin_analytics import AnalyticsWindow
from public_site.admin_observatory import (
    coaching_report,
    llm_report,
    product_report,
    versions_report,
)
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


def test_coaching_uses_latest_completed_after_the_global_choice(analytics_conn) -> None:
    submission = uuid.uuid4().hex
    _insert(
        analytics_conn,
        submission_id=submission,
        attempt=1,
        status="completed",
        created_at=datetime(2026, 9, 30, tzinfo=UTC),
        coaching={"snapshot_schema_version": 1, "candidate": 4},
    )
    _insert(
        analytics_conn,
        submission_id=submission,
        attempt=2,
        status="completed",
        created_at=datetime(2026, 10, 2, tzinfo=UTC),
        coaching={"snapshot_schema_version": 1, "candidate": 9},
    )

    report = coaching_report(
        analytics_conn,
        window=AnalyticsWindow.D7,
        filters={},
        now=datetime(2026, 10, 1, tzinfo=UTC),
    )

    assert report["population"] == "latest_completed_run_per_submission"
    assert report["runs"] == 0
    assert report["selection"]["candidate"] == 0


def test_coaching_reports_mixed_snapshot_versions(analytics_conn) -> None:
    moment = datetime(2026, 10, 8, tzinfo=UTC)
    _insert(
        analytics_conn,
        submission_id=uuid.uuid4().hex,
        attempt=1,
        status="completed",
        created_at=moment - timedelta(hours=2),
        coaching={"snapshot_schema_version": 1, "candidate": 2, "selected": 1},
    )
    _insert(
        analytics_conn,
        submission_id=uuid.uuid4().hex,
        attempt=1,
        status="completed",
        created_at=moment - timedelta(hours=1),
        coaching={"snapshot_schema_version": 2, "candidate": 3, "selected": 3},
    )

    report = coaching_report(
        analytics_conn, window=AnalyticsWindow.H24, filters={}, now=moment
    )

    assert report["runs"] == 2
    assert report["selection"]["candidate"] == 5
    assert report["snapshot_versions"] == [
        {"snapshot_schema_version": "1", "runs": 1},
        {"snapshot_schema_version": "2", "runs": 1},
    ]


def test_versions_keep_execution_and_output_samples(analytics_conn) -> None:
    moment = datetime(2026, 10, 8, tzinfo=UTC)
    _insert(
        analytics_conn,
        submission_id=uuid.uuid4().hex,
        attempt=1,
        status="completed",
        created_at=moment - timedelta(hours=1),
        pipeline_version="pipeline-a",
        coaching={
            "snapshot_schema_version": 1,
            "recommendation": 2,
            "blocked": 1,
            "no_recommendation": 0,
            "not_reviewed": 1,
        },
    )

    report = versions_report(
        analytics_conn, window=AnalyticsWindow.H24, filters={}, now=moment
    )
    row = report["rows"][0]

    assert report["population_executions"] == "executions_in_window"
    assert report["population_output"] == "latest_completed_run_per_submission"
    assert row["n"] == 1
    assert row["sample"] == "insufficient"
    assert row["output_sample"] == "insufficient"
    assert row["recommendations_per_submission"] == 2
    assert row["blocked_rate"] == 0.25
    assert row["snapshot_coverage"] == 1


def test_unknown_model_cost_is_null(analytics_conn) -> None:
    moment = datetime(2026, 10, 8, tzinfo=UTC)
    run_id = _insert(
        analytics_conn,
        submission_id=uuid.uuid4().hex,
        attempt=1,
        status="completed",
        created_at=moment - timedelta(hours=1),
    )
    analytics_conn.execute(
        """
        INSERT INTO llm_call_metrics (
            run_id, ordinal, provider, model, disposition,
            input_tokens_est, output_tokens_est,
            snapshot_schema_version, snapshot_reader_version
        )
        VALUES (
            %s, 0, 'mystery', 'unlisted-model', 'requested_succeeded',
            100, 40, 1, 'llm-calls-1'
        )
        """,
        (run_id,),
    )

    report = llm_report(
        analytics_conn, window=AnalyticsWindow.H24, filters={}, now=moment
    )

    assert report["population"] == "executions_in_window"
    assert report["calls"] == 1
    assert report["estimated_cost"] is None


def test_account_cards_do_not_require_a_job(analytics_conn) -> None:
    moment = datetime(2099, 1, 1, tzinfo=UTC)
    user_id = str(uuid.uuid4())
    analytics_conn.execute(
        """
        INSERT INTO users (
            id, email, name, password_hash, created_at, verified_at, last_seen_at
        )
        VALUES (%s, %s, 'Observatory', 'hash', %s, %s, %s)
        """,
        (
            user_id,
            f"{user_id}@example.test",
            moment - timedelta(hours=2),
            moment - timedelta(hours=2),
            moment - timedelta(hours=1),
        ),
    )

    report = product_report(
        analytics_conn,
        window=AnalyticsWindow.H24,
        account_retention_days=365,
        now=moment,
    )

    assert report["accounts"]["new"]["n"] == 1
    assert report["accounts"]["newly_verified"]["n"] == 1
    assert report["accounts"]["authenticated_active"]["n"] == 1
    assert report["accounts"]["registered"]["window"] == "all_time"
    assert report["accounts"]["submitting"] == {"n": 0, "label": "submitting"}
    assert report["requests"]["n"] == 0


def test_product_censors_a_submission_newer_than_the_horizon(analytics_conn) -> None:
    moment = datetime(2099, 6, 1, tzinfo=UTC)
    user_id = str(uuid.uuid4())
    analytics_conn.execute(
        """
        INSERT INTO users (id, email, name, password_hash, created_at)
        VALUES (%s, %s, 'Recent', 'hash', %s)
        """,
        (user_id, f"{user_id}@example.test", moment - timedelta(days=1)),
    )
    _insert(
        analytics_conn,
        submission_id=uuid.uuid4().hex,
        attempt=1,
        status="completed",
        created_at=moment - timedelta(days=1),
        user_id=user_id,
    )

    report = product_report(
        analytics_conn,
        window=AnalyticsWindow.H24,
        account_retention_days=365,
        now=moment,
    )
    week = next(
        row for row in report["observed_resubmission"] if row["horizon_days"] == 7
    )

    assert week["eligible_n"] == 0
    assert week["rate"] is None
    assert week["coverage_note"]


def _insert(
    conn,
    *,
    submission_id: str,
    attempt: int,
    status: str,
    created_at: datetime,
    user_id: str | None = None,
    coaching: dict | None = None,
    pipeline_version: str | None = None,
) -> str:
    run_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO job_runs (
            run_id, submission_id, attempt, user_id, is_guest,
            language, status, created_at, coaching_metrics, pipeline_version
        )
        VALUES (%s, %s, %s, %s, %s, 'en', %s, %s, %s, %s)
        """,
        (
            run_id,
            submission_id,
            attempt,
            user_id,
            user_id is None,
            status,
            created_at,
            None if coaching is None else Jsonb(coaching),
            pipeline_version,
        ),
    )
    return run_id
