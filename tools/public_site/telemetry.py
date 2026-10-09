"""Privacy-bounded operational telemetry for public coaching jobs."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any, Literal

import psycopg
from loguru import logger
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from public_site import errors

_SCHEMA_LOCK = 8_273_341_903
_SCALAR_COLUMNS = (
    "pipeline_version",
    "package_version",
    "evidence_version",
    "vision_config_hash",
    "importance_config_hash",
    "llm_provider",
    "llm_model",
    "snapshot_schema_version",
    "snapshot_reader_version",
)
_CALL_COLUMNS = (
    "ordinal",
    "provider",
    "model",
    "disposition",
    "latency_ms",
    "input_tokens_est",
    "output_tokens_est",
    "snapshot_schema_version",
    "snapshot_reader_version",
)


class RunStatus(StrEnum):
    """Persisted execution states."""

    SUBMITTED = "submitted"
    VALIDATING = "validating"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class EventKind(StrEnum):
    """Append-only job event vocabulary."""

    SUBMITTED = "submitted"
    UPLOAD_CONFIRMED = "upload_confirmed"
    VALIDATION_STARTED = "validation_started"
    VALIDATION_FINISHED = "validation_finished"
    QUEUED = "queued"
    STAGE_STARTED = "stage_started"
    STAGE_FINISHED = "stage_finished"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    EXPIRED = "expired"


class Stage(StrEnum):
    """Top-level worker stages."""

    ANALYZE = "analyze"
    COACH_INPUTS = "coach_inputs"
    COACH_PROTOTYPE = "coach_prototype"
    FRAMES = "frames"


class ErrorCode(StrEnum):
    """Sanitized public-site failure codes."""

    ANALYSIS_FAILED = "analysis_failed"
    COACHING_FAILED = "coaching_failed"
    INTERRUPTED = "interrupted"
    PROBE_REJECTED = "probe_rejected"
    UPLOAD_MISSING = "upload_missing"
    UPLOAD_MISMATCH = "upload_mismatch"
    UPLOAD_UNFINISHED = "upload_unfinished"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class CoachingOutcome(StrEnum):
    """Result classification of a completed run. Never a failure category."""

    COACHED = "coached"
    NO_DEATHS = "no_deaths"


class Diagnostic(StrEnum):
    """Non-sensitive failure categories."""

    NONZERO_EXIT = "nonzero_exit"
    MISSING_ARTIFACT = "missing_artifact"
    SOURCE_MISSING = "source_missing"
    PROBE_REJECTED = "probe_rejected"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class EmptyDetail(BaseModel):
    """An event with no additional data."""

    model_config = ConfigDict(extra="forbid")


class StageFinishedDetail(BaseModel):
    """Allowlisted stage completion facts."""

    model_config = ConfigDict(extra="forbid")

    exit_code: int
    seconds: float = Field(ge=0)


class FailureDetail(BaseModel):
    """Allowlisted failure facts."""

    model_config = ConfigDict(extra="forbid")

    error_code: ErrorCode
    diagnostic: Diagnostic


class AuditRoleDetail(BaseModel):
    """Allowlisted administrator role-change facts."""

    model_config = ConfigDict(extra="forbid")

    via: Literal["cli", "admin"]
    role_before: Literal["user", "admin"]
    role_after: Literal["user", "admin"]


_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS job_runs (
        run_id uuid PRIMARY KEY,
        submission_id text NOT NULL,
        attempt integer NOT NULL CHECK (attempt > 0),
        user_id uuid REFERENCES users (id) ON DELETE SET NULL,
        is_guest boolean NOT NULL,
        language text NOT NULL CHECK (language IN ('en', 'ja')),
        status text NOT NULL CHECK (
            status IN (
                'submitted', 'validating', 'queued', 'running',
                'completed', 'failed', 'interrupted'
            )
        ),
        created_at timestamptz NOT NULL DEFAULT now(),
        upload_confirmed_at timestamptz,
        queued_at timestamptz,
        started_at timestamptz,
        finished_at timestamptz,
        failed_stage text CHECK (
            failed_stage IS NULL OR failed_stage IN (
                'analyze', 'coach_inputs', 'coach_prototype', 'frames'
            )
        ),
        error_code text,
        diagnostic text,
        video_seconds double precision CHECK (video_seconds IS NULL OR video_seconds >= 0),
        size_bytes bigint CHECK (size_bytes IS NULL OR size_bytes >= 0),
        width integer CHECK (width IS NULL OR width >= 0),
        height integer CHECK (height IS NULL OR height >= 0),
        stage_seconds jsonb,
        llm jsonb,
        provenance jsonb,
        UNIQUE (submission_id, attempt)
    )
    """,
    "CREATE INDEX IF NOT EXISTS job_runs_submission ON job_runs (submission_id)",
    "CREATE INDEX IF NOT EXISTS job_runs_created ON job_runs (created_at DESC, submission_id)",
    """
    CREATE TABLE IF NOT EXISTS job_events (
        id bigserial PRIMARY KEY,
        run_id uuid NOT NULL REFERENCES job_runs (run_id) ON DELETE CASCADE,
        at timestamptz NOT NULL DEFAULT now(),
        kind text NOT NULL CHECK (
            kind IN (
                'submitted', 'upload_confirmed', 'validation_started',
                'validation_finished', 'queued', 'stage_started',
                'stage_finished', 'completed', 'failed', 'interrupted', 'expired'
            )
        ),
        stage text CHECK (
            stage IS NULL OR stage IN (
                'analyze', 'coach_inputs', 'coach_prototype', 'frames'
            )
        ),
        detail jsonb NOT NULL DEFAULT '{}'::jsonb,
        CHECK (
            (kind IN ('stage_started', 'stage_finished') AND stage IS NOT NULL)
            OR
            (kind NOT IN ('stage_started', 'stage_finished') AND stage IS NULL)
        )
    )
    """,
    "CREATE INDEX IF NOT EXISTS job_events_run ON job_events (run_id, at, id)",
    "CREATE INDEX IF NOT EXISTS job_events_at ON job_events (at, id)",
    "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS pipeline_version text",
    "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS package_version text",
    "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS evidence_version integer",
    "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS vision_config_hash text",
    "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS importance_config_hash text",
    "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS llm_provider text",
    """
    ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS coaching_outcome text CHECK (
        coaching_outcome IS NULL OR coaching_outcome IN ('coached', 'no_deaths')
    )
    """,
    "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS llm_model text",
    "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS snapshot_schema_version integer",
    "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS snapshot_reader_version text",
    "ALTER TABLE job_runs ADD COLUMN IF NOT EXISTS snapshot_at timestamptz",
    "ALTER TABLE job_runs DROP CONSTRAINT IF EXISTS job_runs_importance_hash_hex",
    """
    ALTER TABLE job_runs ADD CONSTRAINT job_runs_importance_hash_hex
        CHECK (
            importance_config_hash IS NULL
            OR importance_config_hash ~ '^[0-9a-f]{12}$'
        )
    """,
    "CREATE INDEX IF NOT EXISTS job_runs_pipeline ON job_runs (pipeline_version, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS job_runs_vision_config ON job_runs (vision_config_hash)",
    """
    CREATE TABLE IF NOT EXISTS llm_call_metrics (
        run_id uuid NOT NULL REFERENCES job_runs (run_id) ON DELETE CASCADE,
        ordinal integer NOT NULL CHECK (ordinal >= 0),
        provider text,
        model text,
        disposition text NOT NULL CHECK (
            disposition IN ('requested_succeeded', 'requested_failed', 'skipped')
        ),
        latency_ms integer CHECK (latency_ms IS NULL OR latency_ms >= 0),
        input_tokens_est integer CHECK (
            input_tokens_est IS NULL OR input_tokens_est >= 0
        ),
        output_tokens_est integer CHECK (
            output_tokens_est IS NULL OR output_tokens_est >= 0
        ),
        snapshot_schema_version integer NOT NULL,
        snapshot_reader_version text NOT NULL,
        snapshot_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (run_id, ordinal)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS admin_audit_log (
        id bigserial PRIMARY KEY,
        admin_user_id uuid REFERENCES users (id) ON DELETE SET NULL,
        action text NOT NULL CHECK (action IN ('grant_admin', 'revoke_admin')),
        target_type text NOT NULL CHECK (target_type = 'user'),
        target_id uuid NOT NULL,
        at timestamptz NOT NULL DEFAULT now(),
        metadata jsonb NOT NULL
    )
    """,
)


class TelemetryStore:
    """PostgreSQL telemetry store. Public-site callers own best-effort handling."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def ensure_schema(self) -> None:
        """Create telemetry tables under a transaction-scoped advisory lock."""
        with self._conn() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK,))
            for statement in _SCHEMA:
                conn.execute(statement)

    def open_initial_run(
        self,
        submission_id: str,
        *,
        user_id: str | None,
        language: str,
    ) -> str | None:
        """Atomically create attempt one and its sole submitted event."""
        run_id = str(uuid.uuid4())
        with self._conn() as conn:
            row = conn.execute(
                """
                INSERT INTO job_runs
                    (run_id, submission_id, attempt, user_id, is_guest, language, status)
                VALUES (%s, %s, 1, %s, %s, %s, 'submitted')
                ON CONFLICT (submission_id, attempt) DO NOTHING
                RETURNING run_id
                """,
                (run_id, submission_id, user_id, user_id is None, language),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "INSERT INTO job_events (run_id, kind) VALUES (%s, 'submitted')",
                (run_id,),
            )
        return run_id

    def upload_confirmed(self, submission_id: str) -> bool:
        """Record confirmation without changing the run status."""
        return self._event(
            submission_id,
            EventKind.UPLOAD_CONFIRMED,
            set_clause="upload_confirmed_at = COALESCE(upload_confirmed_at, now())",
            allowed=(RunStatus.SUBMITTED,),
        )

    def validation_started(self, submission_id: str) -> bool:
        """Move a submitted run into validation."""
        return self._transition(
            submission_id,
            allowed=(RunStatus.SUBMITTED,),
            status=RunStatus.VALIDATING,
            kind=EventKind.VALIDATION_STARTED,
        )

    def queued(
        self,
        submission_id: str,
        *,
        video_seconds: float,
        size_bytes: int,
        width: int,
        height: int,
    ) -> bool:
        """Record validated video facts and queue the run."""
        with self._conn() as conn:
            run = self._latest(conn, submission_id)
            if run is None or run["status"] != RunStatus.VALIDATING:
                return False
            conn.execute(
                """
                INSERT INTO job_events (run_id, kind)
                VALUES (%s, 'validation_finished'), (%s, 'queued')
                """,
                (run["run_id"], run["run_id"]),
            )
            updated = conn.execute(
                """
                UPDATE job_runs
                SET status = 'queued', queued_at = now(), video_seconds = %s,
                    size_bytes = %s, width = %s, height = %s
                WHERE run_id = %s AND status = 'validating'
                """,
                (video_seconds, size_bytes, width, height, run["run_id"]),
            )
            if updated.rowcount != 1:
                raise RuntimeError("telemetry queue transition lost")
        return True

    def started(self, submission_id: str) -> bool:
        """Move a queued run into worker execution."""
        return self._transition(
            submission_id,
            allowed=(RunStatus.QUEUED,),
            status=RunStatus.RUNNING,
            kind=None,
            timestamp_column="started_at",
        )

    def stage_started(self, submission_id: str, stage: Stage) -> bool:
        """Append one stage-start event for the running attempt."""
        return self._event(
            submission_id,
            EventKind.STAGE_STARTED,
            stage=stage,
            allowed=(RunStatus.RUNNING,),
        )

    def stage_finished(
        self,
        submission_id: str,
        stage: Stage,
        *,
        exit_code: int,
        seconds: float,
    ) -> bool:
        """Append one allowlisted stage-finish event."""
        detail = StageFinishedDetail(exit_code=exit_code, seconds=seconds)
        return self._event(
            submission_id,
            EventKind.STAGE_FINISHED,
            stage=stage,
            detail=detail,
            allowed=(RunStatus.RUNNING,),
        )

    def completed(
        self,
        submission_id: str,
        *,
        outcome: CoachingOutcome = CoachingOutcome.COACHED,
    ) -> bool:
        """Finish a running attempt successfully with its result classification."""
        return self._transition(
            submission_id,
            allowed=(RunStatus.RUNNING,),
            status=RunStatus.COMPLETED,
            kind=EventKind.COMPLETED,
            timestamp_column="finished_at",
            outcome=CoachingOutcome(outcome),
        )

    def failed(
        self,
        submission_id: str,
        *,
        stage: Stage | None,
        error_code: ErrorCode,
        diagnostic: Diagnostic,
    ) -> bool:
        """Fail a pre-worker or running attempt without reopening terminal work."""
        detail = FailureDetail(error_code=error_code, diagnostic=diagnostic)
        return self._transition(
            submission_id,
            allowed=(RunStatus.SUBMITTED, RunStatus.VALIDATING, RunStatus.RUNNING),
            status=RunStatus.FAILED,
            kind=EventKind.FAILED,
            timestamp_column="finished_at",
            stage_value=stage,
            detail=detail,
        )

    def interrupted(self, submission_id: str) -> bool:
        """Mark startup-recovered validation or processing as interrupted."""
        detail = FailureDetail(
            error_code=ErrorCode.INTERRUPTED,
            diagnostic=Diagnostic.INTERRUPTED,
        )
        return self._transition(
            submission_id,
            allowed=(RunStatus.VALIDATING, RunStatus.RUNNING),
            status=RunStatus.INTERRUPTED,
            kind=EventKind.INTERRUPTED,
            timestamp_column="finished_at",
            detail=detail,
        )

    def unlink_user(self, submission_id: str) -> bool:
        """Remove account linkage from all attempts and append expiry to the latest."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                UPDATE job_runs SET user_id = NULL
                WHERE submission_id = %s
                RETURNING run_id, attempt
                """,
                (submission_id,),
            ).fetchall()
            if not rows:
                return False
            latest = max(rows, key=lambda row: row["attempt"])
            conn.execute(
                "INSERT INTO job_events (run_id, kind) VALUES (%s, 'expired')",
                (latest["run_id"],),
            )
        return True

    def update_artifacts(
        self,
        submission_id: str,
        *,
        stage_seconds: BaseModel | None = None,
        llm: BaseModel | None = None,
        provenance: BaseModel | None = None,
        scalar: BaseModel | None = None,
        llm_calls: list[BaseModel] | None = None,
    ) -> bool:
        """Persist allowlisted summaries and replace that run's call rows."""
        if _nothing_to_write(stage_seconds, llm, provenance, scalar, llm_calls):
            return False
        with self._conn() as conn:
            run = self._latest(conn, submission_id)
            if run is None:
                return False
            self._write_documents(conn, run["run_id"], stage_seconds, llm, provenance)
            if scalar is not None:
                self._write_scalar(conn, run["run_id"], scalar)
            if llm_calls is not None:
                self._replace_calls(conn, run["run_id"], llm_calls)
        return True

    @staticmethod
    def _write_documents(conn, run_id, stage_seconds, llm, provenance) -> None:
        values = {
            "stage_seconds": _json(stage_seconds),
            "llm": _json(llm),
            "provenance": _json(provenance),
        }
        present = {name: value for name, value in values.items() if value is not None}
        if not present:
            return
        assignments = [f"{name} = %s" for name in present]
        conn.execute(
            f"UPDATE job_runs SET {', '.join(assignments)} WHERE run_id = %s",  # noqa: S608
            (*present.values(), run_id),
        )

    @staticmethod
    def _write_scalar(conn, run_id, scalar: BaseModel) -> None:
        payload = scalar.model_dump(mode="json")
        columns = [name for name in _SCALAR_COLUMNS if name in payload]
        assignments = [f"{name} = %s" for name in columns]
        assignments.append("snapshot_at = now()")
        conn.execute(
            f"UPDATE job_runs SET {', '.join(assignments)} WHERE run_id = %s",  # noqa: S608
            (*(payload[name] for name in columns), run_id),
        )

    @staticmethod
    def _replace_calls(conn, run_id, calls: list[BaseModel]) -> None:
        conn.execute("DELETE FROM llm_call_metrics WHERE run_id = %s", (run_id,))
        for call in calls:
            payload = call.model_dump(mode="json")
            columns = [name for name in _CALL_COLUMNS if name in payload]
            names = ", ".join(["run_id", *columns])
            placeholders = ", ".join(["%s"] * (len(columns) + 1))
            conn.execute(
                f"INSERT INTO llm_call_metrics ({names}) VALUES ({placeholders})",  # noqa: S608
                (run_id, *(payload[name] for name in columns)),
            )

    def audit_role(
        self,
        *,
        target_id: str,
        before: Literal["user", "admin"],
        after: Literal["user", "admin"],
        via: Literal["cli", "admin"] = "cli",
        admin_user_id: str | None = None,
    ) -> None:
        """Record one allowlisted role change."""
        action = "grant_admin" if after == "admin" else "revoke_admin"
        detail = AuditRoleDetail(via=via, role_before=before, role_after=after)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO admin_audit_log
                    (admin_user_id, action, target_type, target_id, metadata)
                VALUES (%s, %s, 'user', %s, %s)
                """,
                (admin_user_id, action, target_id, Jsonb(detail.model_dump(mode="json"))),
            )

    def _transition(
        self,
        submission_id: str,
        *,
        allowed: tuple[RunStatus, ...],
        status: RunStatus,
        kind: EventKind | None,
        timestamp_column: str | None = None,
        stage_value: Stage | None = None,
        detail: BaseModel | None = None,
        outcome: CoachingOutcome | None = None,
    ) -> bool:
        with self._conn() as conn:
            run = self._latest(conn, submission_id)
            if run is None or run["status"] not in allowed:
                return False
            clauses = ["status = %s"]
            params: list[Any] = [status.value]
            if timestamp_column:
                clauses.append(f"{timestamp_column} = now()")
            if outcome is not None:
                clauses.append("coaching_outcome = %s")
                params.append(outcome.value)
            if status in {RunStatus.FAILED, RunStatus.INTERRUPTED}:
                clauses.extend(
                    ["failed_stage = %s", "error_code = %s", "diagnostic = %s"]
                )
                failure = FailureDetail.model_validate(detail)
                params.extend(
                    [
                        None if stage_value is None else stage_value.value,
                        failure.error_code.value,
                        failure.diagnostic.value,
                    ]
                )
            params.extend([run["run_id"], [item.value for item in allowed]])
            result = conn.execute(
                f"""
                UPDATE job_runs SET {", ".join(clauses)}
                WHERE run_id = %s AND status = ANY(%s)
                """,  # noqa: S608
                params,
            )
            if result.rowcount != 1:
                return False
            if kind is not None:
                # job_events allows a stage only on stage_started/stage_finished;
                # the failed stage lives on job_runs.failed_stage.
                self._insert_event(conn, run["run_id"], kind, None, detail)
        return True

    def _event(
        self,
        submission_id: str,
        kind: EventKind,
        *,
        stage: Stage | None = None,
        detail: BaseModel | None = None,
        allowed: tuple[RunStatus, ...],
        set_clause: str | None = None,
    ) -> bool:
        with self._conn() as conn:
            run = self._latest(conn, submission_id)
            if run is None or run["status"] not in allowed:
                return False
            if set_clause is not None:
                conn.execute(
                    f"UPDATE job_runs SET {set_clause} WHERE run_id = %s",  # noqa: S608
                    (run["run_id"],),
                )
            self._insert_event(conn, run["run_id"], kind, stage, detail)
        return True

    @staticmethod
    def _insert_event(conn, run_id, kind, stage, detail) -> None:
        payload = EmptyDetail() if detail is None else detail
        conn.execute(
            """
            INSERT INTO job_events (run_id, kind, stage, detail)
            VALUES (%s, %s, %s, %s)
            """,
            (
                run_id,
                kind.value,
                None if stage is None else stage.value,
                Jsonb(payload.model_dump(mode="json")),
            ),
        )

    @staticmethod
    def _latest(conn, submission_id: str) -> dict[str, Any] | None:
        return conn.execute(
            """
            SELECT run_id, attempt, status
            FROM job_runs
            WHERE submission_id = %s
            ORDER BY attempt DESC
            LIMIT 1
            FOR UPDATE
            """,
            (submission_id,),
        ).fetchone()

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)


def best_effort(action: str, operation, *args, **kwargs) -> Any:
    """Run one whole telemetry transaction without affecting public behavior."""
    try:
        return operation(*args, **kwargs)
    except Exception:
        logger.exception("telemetry operation failed: {}", action)
        return None


def classify_error(message: str) -> tuple[ErrorCode, Diagnostic]:
    """Map public messages to closed, non-sensitive telemetry values."""
    mapping = {
        errors.ANALYSIS_FAILED: (ErrorCode.ANALYSIS_FAILED, Diagnostic.NONZERO_EXIT),
        errors.COACHING_FAILED: (ErrorCode.COACHING_FAILED, Diagnostic.NONZERO_EXIT),
        errors.INTERRUPTED: (ErrorCode.INTERRUPTED, Diagnostic.INTERRUPTED),
        errors.UPLOAD_MISSING: (ErrorCode.UPLOAD_MISSING, Diagnostic.SOURCE_MISSING),
        errors.UPLOAD_MISMATCH: (ErrorCode.UPLOAD_MISMATCH, Diagnostic.SOURCE_MISSING),
        errors.UPLOAD_UNFINISHED: (
            ErrorCode.UPLOAD_UNFINISHED,
            Diagnostic.SOURCE_MISSING,
        ),
        errors.EXPIRED: (ErrorCode.EXPIRED, Diagnostic.UNKNOWN),
    }
    if message in {
        errors.PROBE_READ,
        errors.PROBE_CONTAINER,
        errors.PROBE_STREAM,
        errors.PROBE_DURATION,
        errors.PROBE_DIMENSIONS,
        errors.PROBE_SIZE,
    }:
        return ErrorCode.PROBE_REJECTED, Diagnostic.PROBE_REJECTED
    return mapping.get(message, (ErrorCode.UNKNOWN, Diagnostic.UNKNOWN))


def _json(value: BaseModel | None) -> Jsonb | None:
    return None if value is None else Jsonb(value.model_dump(mode="json"))


def _nothing_to_write(*values: Any) -> bool:
    return all(value is None for value in values)
