"""PostgreSQL operational telemetry invariants."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from public_site.accounts import AccountStore
from public_site.job_trace import LlmCallRecord, ScalarProvenance
from public_site.telemetry import (
    CoachingOutcome,
    Diagnostic,
    ErrorCode,
    RunStatus,
    Stage,
    TelemetryStore,
)


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
def telemetry() -> TelemetryStore:
    url = _database_url()
    AccountStore(url).ensure_schema()
    store = TelemetryStore(url)
    store.ensure_schema()
    with psycopg.connect(url) as conn:
        conn.execute("TRUNCATE admin_audit_log, llm_call_metrics, job_events, job_runs")
    yield store
    with psycopg.connect(url) as conn:
        conn.execute("TRUNCATE admin_audit_log, llm_call_metrics, job_events, job_runs")


def test_initial_run_and_submitted_event_are_atomic_and_idempotent(
    telemetry: TelemetryStore,
) -> None:
    submission_id = uuid.uuid4().hex
    first = telemetry.open_initial_run(submission_id, user_id=None, language="en")
    second = telemetry.open_initial_run(submission_id, user_id=None, language="en")

    assert first is not None
    assert second is None
    with psycopg.connect(telemetry.database_url) as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM job_runs WHERE submission_id = %s",
                (submission_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                """
            SELECT count(*) FROM job_events e
            JOIN job_runs r USING (run_id)
            WHERE r.submission_id = %s AND e.kind = 'submitted'
            """,
                (submission_id,),
            ).fetchone()[0]
            == 1
        )


def test_stage_events_repeat_but_terminal_status_cannot_be_overwritten(
    telemetry: TelemetryStore,
) -> None:
    submission_id = uuid.uuid4().hex
    telemetry.open_initial_run(submission_id, user_id=None, language="ja")
    telemetry.upload_confirmed(submission_id)
    telemetry.validation_started(submission_id)
    telemetry.queued(
        submission_id,
        video_seconds=300,
        size_bytes=100,
        width=1920,
        height=1080,
    )
    telemetry.started(submission_id)
    telemetry.stage_started(submission_id, Stage.ANALYZE)
    telemetry.stage_started(submission_id, Stage.ANALYZE)
    telemetry.stage_finished(submission_id, Stage.ANALYZE, exit_code=0, seconds=1.25)
    assert telemetry.completed(submission_id)
    assert not telemetry.failed(
        submission_id,
        stage=Stage.ANALYZE,
        error_code=ErrorCode.ANALYSIS_FAILED,
        diagnostic=Diagnostic.NONZERO_EXIT,
    )

    with psycopg.connect(telemetry.database_url) as conn:
        row = conn.execute(
            "SELECT status FROM job_runs WHERE submission_id = %s",
            (submission_id,),
        ).fetchone()
        assert row[0] == RunStatus.COMPLETED
        starts = conn.execute(
            """
            SELECT count(*) FROM job_events e
            JOIN job_runs r USING (run_id)
            WHERE r.submission_id = %s
              AND e.kind = 'stage_started' AND e.stage = 'analyze'
            """,
            (submission_id,),
        ).fetchone()[0]
        assert starts == 2


def test_failed_at_stage_records_run_stage_and_stageless_event(
    telemetry: TelemetryStore,
) -> None:
    submission_id = uuid.uuid4().hex
    telemetry.open_initial_run(submission_id, user_id=None, language="en")
    telemetry.upload_confirmed(submission_id)
    telemetry.validation_started(submission_id)
    telemetry.queued(
        submission_id,
        video_seconds=300,
        size_bytes=100,
        width=1920,
        height=1080,
    )
    telemetry.started(submission_id)
    telemetry.stage_started(submission_id, Stage.COACH_INPUTS)
    assert telemetry.failed(
        submission_id,
        stage=Stage.COACH_INPUTS,
        error_code=ErrorCode.COACHING_FAILED,
        diagnostic=Diagnostic.NONZERO_EXIT,
    )

    with psycopg.connect(telemetry.database_url) as conn:
        run = conn.execute(
            "SELECT status, failed_stage FROM job_runs WHERE submission_id = %s",
            (submission_id,),
        ).fetchone()
        assert run == (RunStatus.FAILED, Stage.COACH_INPUTS)
        event = conn.execute(
            """
            SELECT e.stage FROM job_events e
            JOIN job_runs r USING (run_id)
            WHERE r.submission_id = %s AND e.kind = 'failed'
            """,
            (submission_id,),
        ).fetchone()
        assert event == (None,)


def test_completed_records_the_coaching_outcome(telemetry: TelemetryStore) -> None:
    submission_id = uuid.uuid4().hex
    telemetry.open_initial_run(submission_id, user_id=None, language="en")
    telemetry.upload_confirmed(submission_id)
    telemetry.validation_started(submission_id)
    telemetry.queued(
        submission_id,
        video_seconds=300,
        size_bytes=100,
        width=1920,
        height=1080,
    )
    telemetry.started(submission_id)
    assert telemetry.completed(submission_id, outcome=CoachingOutcome.NO_DEATHS)

    with psycopg.connect(telemetry.database_url) as conn:
        row = conn.execute(
            """
            SELECT status, coaching_outcome, error_code, failed_stage
            FROM job_runs WHERE submission_id = %s
            """,
            (submission_id,),
        ).fetchone()
    assert row == (RunStatus.COMPLETED, CoachingOutcome.NO_DEATHS, None, None)


def test_expiry_removes_identity_but_keeps_measurements(
    telemetry: TelemetryStore,
) -> None:
    user_id = str(uuid.uuid4())
    email = f"sensitive-{uuid.uuid4().hex}@example.test"
    with psycopg.connect(telemetry.database_url) as conn:
        conn.execute(
            """
            INSERT INTO users
                (id, email, name, password_hash, verified_at, created_at, role)
            VALUES (%s, %s, %s, 'hash', now(), now(), 'user')
            """,
            (user_id, email, "Sensitive Name"),
        )
    submission_id = uuid.uuid4().hex
    telemetry.open_initial_run(submission_id, user_id=user_id, language="en")
    telemetry.validation_started(submission_id)
    telemetry.queued(
        submission_id,
        video_seconds=123,
        size_bytes=456,
        width=1280,
        height=720,
    )

    assert telemetry.unlink_user(submission_id)
    with psycopg.connect(telemetry.database_url) as conn:
        run = conn.execute(
            """
            SELECT user_id, video_seconds, size_bytes, stage_seconds, llm, provenance
            FROM job_runs WHERE submission_id = %s
            """,
            (submission_id,),
        ).fetchone()
        payloads = conn.execute(
            """
            SELECT detail::text FROM job_events e
            JOIN job_runs r USING (run_id)
            WHERE r.submission_id = %s
            """,
            (submission_id,),
        ).fetchall()
    assert run[0] is None
    assert run[1:3] == (123, 456)
    all_text = " ".join(str(value) for value in (*run, *payloads))
    assert email not in all_text
    assert "Sensitive Name" not in all_text


def test_database_rejects_invalid_vocabularies(telemetry: TelemetryStore) -> None:
    run_id = telemetry.open_initial_run(uuid.uuid4().hex, user_id=None, language="en")
    assert run_id
    with psycopg.connect(telemetry.database_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "UPDATE job_runs SET status = 'surprising' WHERE run_id = %s",
                (run_id,),
            )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                """
                INSERT INTO job_events (run_id, kind, stage)
                VALUES (%s, 'submitted', 'analyze')
                """,
                (run_id,),
            )


def test_snapshot_keeps_12_hex_hash_and_replaces_call_rows(
    telemetry: TelemetryStore,
) -> None:
    submission_id = uuid.uuid4().hex
    telemetry.open_initial_run(submission_id, user_id=None, language="en")
    telemetry.update_artifacts(
        submission_id,
        scalar=ScalarProvenance(importance_config_hash="abc123def456"),
        llm_calls=[
            LlmCallRecord(ordinal=0, disposition="skipped", provider="cursor", model="m"),
            LlmCallRecord(ordinal=1, disposition="requested_succeeded", latency_ms=8),
        ],
    )
    telemetry.update_artifacts(
        submission_id,
        llm_calls=[
            LlmCallRecord(ordinal=0, disposition="requested_failed", latency_ms=2)
        ],
    )
    with psycopg.connect(telemetry.database_url) as conn:
        stored = conn.execute(
            """
            SELECT importance_config_hash, snapshot_schema_version, snapshot_at
            FROM job_runs WHERE submission_id = %s
            """,
            (submission_id,),
        ).fetchone()
        calls = conn.execute(
            """
            SELECT ordinal, disposition
            FROM llm_call_metrics
            JOIN job_runs USING (run_id)
            WHERE submission_id = %s
            ORDER BY ordinal
            """,
            (submission_id,),
        ).fetchall()
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                """
                UPDATE job_runs SET importance_config_hash = %s
                WHERE submission_id = %s
                """,
                ("a" * 64, submission_id),
            )
    assert stored[0] == "abc123def456"
    assert stored[1] == 1
    assert stored[2] is not None
    assert calls == [(0, "requested_failed")]


def test_concurrent_schema_setup_is_safe(telemetry: TelemetryStore) -> None:
    stores = [TelemetryStore(telemetry.database_url) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda item: item.ensure_schema(), stores))
