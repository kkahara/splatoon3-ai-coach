"""Idempotent snapshot backfill for retained analysis directories."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from public_site.job_trace import read_job_trace, read_llm_calls, scalar_provenance
from public_site.models import Submission
from public_site.store import PublicStore
from public_site.telemetry import TelemetryStore
from splatoon3_ai_coach.coach.artifacts import coach_prototype_dir


class _Writer(Protocol):
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
        """Persist one allowlisted snapshot."""


def backfill_retained(store: PublicStore, writer: _Writer) -> dict[str, int]:
    """Rewrite safe snapshots for retained submissions that still have artifacts.

    Expired submissions are not opened. Missing directories are counted and
    skipped. Coaching is not re-run.
    """
    updated = 0
    skipped_expired = 0
    skipped_missing = 0
    for submission in store.list_submissions():
        if submission.status == "expired":
            skipped_expired += 1
            continue
        analysis = _analysis_dir(submission)
        if analysis is None:
            skipped_missing += 1
            continue
        _write(writer, submission.id, analysis)
        updated += 1
    return {
        "updated": updated,
        "skipped_expired": skipped_expired,
        "skipped_missing": skipped_missing,
    }


def _analysis_dir(submission: Submission) -> Path | None:
    if not submission.analysis_dir:
        return None
    path = Path(submission.analysis_dir)
    return path if path.is_dir() else None


def _write(writer: _Writer, submission_id: str, analysis: Path) -> None:
    trace = read_job_trace(analysis)
    calls = read_llm_calls(coach_prototype_dir(analysis) / "llm_metrics.jsonl")
    writer.update_artifacts(
        submission_id,
        stage_seconds=trace.timing,
        llm=trace.llm_metrics,
        provenance=trace.operational_provenance(),
        scalar=scalar_provenance(trace),
        llm_calls=calls,
    )


def backfill_store(store: PublicStore, telemetry: TelemetryStore) -> dict[str, int]:
    """Backfill using the live telemetry writer."""
    return backfill_retained(store, telemetry)
