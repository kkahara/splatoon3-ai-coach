"""PostgreSQL operational telemetry invariants."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from public_site.accounts import AccountStore
from public_site.coaching_metrics import CoachingSnapshot
from public_site.job_trace import LlmCallRecord, ScalarProvenance
from public_site.telemetry import (
    CoachingOutcome,
    Diagnostic,
    ErrorCode,
    GameLanguage,
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
    first = telemetry.open_initial_run(submission_id, user_id=None)
    second = telemetry.open_initial_run(submission_id, user_id=None)

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
    telemetry.open_initial_run(submission_id, user_id=None)
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
    assert telemetry.completed(submission_id, game_language=GameLanguage.JA)
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
    telemetry.open_initial_run(submission_id, user_id=None)
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
    telemetry.open_initial_run(submission_id, user_id=None)
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
    assert telemetry.completed(
        submission_id, game_language=GameLanguage.EN, outcome=CoachingOutcome.NO_DEATHS
    )

    with psycopg.connect(telemetry.database_url) as conn:
        row = conn.execute(
            """
            SELECT status, coaching_outcome, error_code, failed_stage, game_language
            FROM job_runs WHERE submission_id = %s
            """,
            (submission_id,),
        ).fetchone()
    assert row == (RunStatus.COMPLETED, CoachingOutcome.NO_DEATHS, None, None, "en")


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
    telemetry.open_initial_run(submission_id, user_id=user_id)
    telemetry.validation_started(submission_id)
    telemetry.queued(
        submission_id,
        video_seconds=123,
        size_bytes=456,
        width=1280,
        height=720,
    )
    telemetry.update_artifacts(
        submission_id,
        coaching=CoachingSnapshot(candidate=2, selected=1),
        record_coaching=True,
    )

    assert telemetry.unlink_user(submission_id)
    with psycopg.connect(telemetry.database_url) as conn:
        run = conn.execute(
            """
            SELECT user_id, video_seconds, size_bytes, stage_seconds, llm,
                   provenance, coaching_metrics
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
    assert run[6]["candidate"] == 2
    assert "snapshot_at" in run[6]
    all_text = " ".join(str(value) for value in (*run, *payloads))
    assert email not in all_text
    assert "Sensitive Name" not in all_text


def test_database_rejects_invalid_vocabularies(telemetry: TelemetryStore) -> None:
    run_id = telemetry.open_initial_run(uuid.uuid4().hex, user_id=None)
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
    telemetry.open_initial_run(submission_id, user_id=None)
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


def test_coaching_snapshot_replaces_without_touching_provenance(
    telemetry: TelemetryStore,
) -> None:
    submission_id = uuid.uuid4().hex
    telemetry.open_initial_run(submission_id, user_id=None)
    telemetry.update_artifacts(
        submission_id,
        scalar=ScalarProvenance(importance_config_hash="abc123def456"),
        coaching=CoachingSnapshot(candidate=1),
        record_coaching=True,
    )
    telemetry.update_artifacts(
        submission_id,
        coaching=CoachingSnapshot(candidate=4, selected=2),
        record_coaching=True,
    )
    with psycopg.connect(telemetry.database_url) as conn:
        row = conn.execute(
            """
            SELECT snapshot_schema_version, snapshot_reader_version,
                   importance_config_hash, coaching_metrics
            FROM job_runs WHERE submission_id = %s
            """,
            (submission_id,),
        ).fetchone()
    assert row[0] == 1
    assert row[1] == "job-trace-1"
    assert row[2] == "abc123def456"
    assert row[3]["candidate"] == 4
    assert row[3]["selected"] == 2
    assert row[3]["snapshot_schema_version"] == 1


def test_concurrent_schema_setup_is_safe(telemetry: TelemetryStore) -> None:
    stores = [TelemetryStore(telemetry.database_url) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda item: item.ensure_schema(), stores))


def _running(telemetry: TelemetryStore) -> str:
    submission_id = uuid.uuid4().hex
    telemetry.open_initial_run(submission_id, user_id=None)
    telemetry.validation_started(submission_id)
    telemetry.queued(
        submission_id, video_seconds=300, size_bytes=100, width=1920, height=1080
    )
    telemetry.started(submission_id)
    return submission_id


def _run_row(telemetry: TelemetryStore, submission_id: str) -> tuple:
    with psycopg.connect(telemetry.database_url) as conn:
        return conn.execute(
            """
            SELECT status, game_language, game_language_required, error_code
            FROM job_runs WHERE submission_id = %s
            """,
            (submission_id,),
        ).fetchone()


@pytest.mark.parametrize("language", [GameLanguage.EN, GameLanguage.JA])
def test_successful_run_records_the_detected_game_language(
    telemetry: TelemetryStore, language: GameLanguage
) -> None:
    submission_id = _running(telemetry)
    assert _run_row(telemetry, submission_id)[1] is None
    assert telemetry.game_language_detected(submission_id, language)
    assert _run_row(telemetry, submission_id)[1] == language.value
    assert telemetry.completed(submission_id, game_language=language)
    assert _run_row(telemetry, submission_id) == (
        RunStatus.COMPLETED,
        language.value,
        True,
        None,
    )


def test_game_language_is_written_only_while_running(telemetry: TelemetryStore) -> None:
    submission_id = uuid.uuid4().hex
    telemetry.open_initial_run(submission_id, user_id=None)
    assert not telemetry.game_language_detected(submission_id, GameLanguage.EN)
    assert _run_row(telemetry, submission_id)[1] is None
    with pytest.raises(ValueError):
        telemetry.game_language_detected(submission_id, "unknown")


def test_language_detection_failure_is_a_failed_run_without_a_language(
    telemetry: TelemetryStore,
) -> None:
    submission_id = _running(telemetry)
    assert telemetry.failed(
        submission_id,
        stage=Stage.ANALYZE,
        error_code=ErrorCode.GAME_LANGUAGE_UNDETERMINED,
        diagnostic=Diagnostic.NONZERO_EXIT,
    )
    assert _run_row(telemetry, submission_id) == (
        RunStatus.FAILED,
        None,
        True,
        ErrorCode.GAME_LANGUAGE_UNDETERMINED,
    )
    assert not telemetry.completed(submission_id, game_language=GameLanguage.EN)
    with psycopg.connect(telemetry.database_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "UPDATE job_runs SET game_language = 'en' WHERE submission_id = %s",
                (submission_id,),
            )


def test_database_rejects_unknown_and_completed_without_a_language(
    telemetry: TelemetryStore,
) -> None:
    submission_id = _running(telemetry)
    with psycopg.connect(telemetry.database_url) as conn:
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "UPDATE job_runs SET game_language = 'unknown' WHERE submission_id = %s",
                (submission_id,),
            )
        with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
            conn.execute(
                "UPDATE job_runs SET status = 'completed' WHERE submission_id = %s",
                (submission_id,),
            )


def test_run_creation_takes_no_submitted_language() -> None:
    import inspect

    params = inspect.signature(TelemetryStore.open_initial_run).parameters
    assert "language" not in params
    assert "game_language" not in params


def _scratch_database(name: str) -> str:
    url = _database_url()
    parts = urlsplit(url)
    admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, ""))
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {name}")
        conn.execute(f"CREATE DATABASE {name}")
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, ""))


def _job_runs_shape(url: str) -> tuple[dict, set[str]]:
    with psycopg.connect(url) as conn:
        columns = {
            name: (nullable, default)
            for name, nullable, default in conn.execute(
                """
                SELECT column_name, is_nullable, column_default
                FROM information_schema.columns WHERE table_name = 'job_runs'
                """
            ).fetchall()
        }
        constraints = {
            row[0]
            for row in conn.execute(
                """
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'job_runs'::regclass AND contype = 'c'
                """
            ).fetchall()
        }
    return columns, constraints


def test_fresh_schema_has_only_detected_game_language() -> None:
    url = _scratch_database("coach_test_fresh_language")
    AccountStore(url).ensure_schema()
    TelemetryStore(url).ensure_schema()
    TelemetryStore(url).ensure_schema()
    columns, constraints = _job_runs_shape(url)
    language_checks = [n for n in constraints if n.startswith("job_runs_game_language")]
    assert language_checks == ["job_runs_game_language_check"]
    assert "language" not in columns
    assert columns["game_language"][0] == "YES"
    assert columns["game_language_required"] == ("NO", "true")
    assert {
        "job_runs_completed_has_game_language",
        "job_runs_language_failure_has_no_language",
    } <= constraints
    assert not any(name.startswith("job_runs_language_check") for name in constraints)


def test_migration_drops_supplied_language_and_exempts_only_legacy_rows() -> None:
    url = _scratch_database("coach_test_language_migration")
    AccountStore(url).ensure_schema()
    store = TelemetryStore(url)
    store.ensure_schema()
    legacy_id = uuid.uuid4().hex
    with psycopg.connect(url) as conn:
        conn.execute(
            """
            ALTER TABLE job_runs
                DROP CONSTRAINT job_runs_completed_has_game_language,
                DROP CONSTRAINT job_runs_language_failure_has_no_language,
                DROP COLUMN game_language,
                DROP COLUMN game_language_required,
                ADD COLUMN language text NOT NULL DEFAULT 'en'
                    CHECK (language IN ('en', 'ja'))
            """
        )
        conn.execute(
            """
            INSERT INTO job_runs
                (run_id, submission_id, attempt, is_guest, language, status, user_id)
            VALUES (%s, %s, 1, true, 'ja', 'completed', NULL)
            """,
            (str(uuid.uuid4()), legacy_id),
        )
    store.ensure_schema()
    columns, _ = _job_runs_shape(url)
    assert "language" not in columns
    assert columns["game_language_required"] == ("NO", "true")
    assert _run_row(store, legacy_id) == (RunStatus.COMPLETED, None, False, None)
    assert store.unlink_user(legacy_id)
    fresh = uuid.uuid4().hex
    store.open_initial_run(fresh, user_id=None)
    assert _run_row(store, fresh)[2] is True
    store.ensure_schema()
    assert _run_row(store, legacy_id)[2] is False
