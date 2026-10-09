"""Safe, read-only projections of public-job timing and artifact provenance."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from splatoon3_ai_coach.coach.artifacts import (
    ArtifactError,
    RunProvenance,
    coach_inputs_dir,
    coach_prototype_dir,
    read_coaching_index_or_none,
    read_prototype_index,
)
from splatoon3_ai_coach.vision.models import VisionManifest

_OTHER_TOLERANCE_SECONDS = 0.05
PROVENANCE_SCHEMA_VERSION = 1
PROVENANCE_READER_VERSION = "job-trace-1"
LLM_CALL_SCHEMA_VERSION = 1
LLM_CALL_READER_VERSION = "llm-calls-1"
LlmDisposition = Literal["requested_succeeded", "requested_failed", "skipped"]
_DETECTOR_SUFFIX = "_detector_seconds"
_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:+@-]{0,127}$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TopLevelTiming(_StrictModel):
    """Measured wall time for the worker's four top-level stages."""

    analyze: float | None = Field(default=None, ge=0)
    coach_inputs: float | None = Field(default=None, ge=0)
    coach_prototype: float | None = Field(default=None, ge=0)
    frames: float | None = Field(default=None, ge=0)


class DiagnosticTiming(_StrictModel):
    """Diagnostic decomposition; it is never used to calculate ``other``."""

    vision_decode: float | None = Field(default=None, ge=0)
    vision_detectors: float | None = Field(default=None, ge=0)
    fusion: float | None = Field(default=None, ge=0)
    analyze_non_vision: float | None = None


class JobTiming(_StrictModel):
    """Lifecycle durations plus top-level and diagnostic stage timing."""

    end_to_end: float | None = Field(default=None, ge=0)
    upload_validation: float | None = Field(default=None, ge=0)
    queue_wait: float | None = Field(default=None, ge=0)
    processing: float | None = Field(default=None, ge=0)
    other: float | None = None
    timing_status: Literal["consistent", "timing_inconsistent"] = "consistent"
    top_level: TopLevelTiming = Field(default_factory=TopLevelTiming)
    diagnostic: DiagnosticTiming = Field(default_factory=DiagnosticTiming)


class JobTimestamps(_StrictModel):
    """Optional lifecycle boundaries used for wall-time calculations."""

    created_at: datetime | None = None
    queued_at: datetime | None = None
    processing_started_at: datetime | None = None
    completed_at: datetime | None = None


class VisionProvenance(_StrictModel):
    """Allowlisted analysis identity fields safe for operational traces."""

    analysis_id: str = Field(max_length=128, pattern=_SAFE_ID.pattern)
    pipeline_version: str = Field(max_length=128, pattern=_SAFE_ID.pattern)
    package_version: str = Field(max_length=128, pattern=_SAFE_ID.pattern)
    git_sha: str | None = Field(default=None, max_length=64, pattern=r"^[0-9a-f]+$")
    vision_config_sha256: str = Field(max_length=128, pattern=_SAFE_ID.pattern)
    refused_vision_config_sha256: str | None = Field(
        default=None, max_length=128, pattern=_SAFE_ID.pattern
    )


class SafeRunProvenance(_StrictModel):
    """Minimal, allowlisted LLM run identity."""

    provider: str = Field(max_length=128, pattern=_SAFE_LABEL.pattern)
    model: str = Field(max_length=128, pattern=_SAFE_LABEL.pattern)
    llm_view_sha256: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    importance_config_hash: str | None = Field(
        default=None, min_length=12, max_length=12, pattern=r"^[0-9a-f]{12}$"
    )


class OperationalProvenance(_StrictModel):
    """Version and model identity persisted in operational telemetry."""

    vision: VisionProvenance | None = None
    coaching_evidence_version: int | None = None
    llm_runs: list[SafeRunProvenance] = Field(default_factory=list)


class LlmMetricsSummary(_StrictModel):
    """Aggregate of allowlisted ``llm_metrics.jsonl`` values."""

    runs: int = Field(ge=0)
    successes: int = Field(ge=0)
    failures: int = Field(ge=0)
    skipped: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    input_tokens_est: int | None = Field(default=None, ge=0)
    output_tokens_est: int | None = Field(default=None, ge=0)
    providers: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)


class ScalarProvenance(_StrictModel):
    """Indexed identity columns. ``snapshot_at`` is assigned by the database."""

    pipeline_version: str | None = None
    package_version: str | None = None
    evidence_version: int | None = None
    vision_config_hash: str | None = None
    importance_config_hash: str | None = Field(
        default=None, min_length=12, max_length=12, pattern=r"^[0-9a-f]{12}$"
    )
    llm_provider: str | None = None
    llm_model: str | None = None
    snapshot_schema_version: int = PROVENANCE_SCHEMA_VERSION
    snapshot_reader_version: str = PROVENANCE_READER_VERSION


class LlmCallRecord(_StrictModel):
    """One provider call. Scenario ids and error text are not fields."""

    ordinal: int = Field(ge=0)
    provider: str | None = None
    model: str | None = None
    disposition: LlmDisposition
    latency_ms: int | None = Field(default=None, ge=0)
    input_tokens_est: int | None = Field(default=None, ge=0)
    output_tokens_est: int | None = Field(default=None, ge=0)
    snapshot_schema_version: int = LLM_CALL_SCHEMA_VERSION
    snapshot_reader_version: str = LLM_CALL_READER_VERSION


class JobTrace(_StrictModel):
    """Allowlisted operational trace assembled without mutating artifacts."""

    timing: JobTiming
    vision: VisionProvenance | None = None
    coaching_evidence_version: int | None = None
    llm_metrics: LlmMetricsSummary | None = None
    run_provenance: list[SafeRunProvenance] = Field(default_factory=list)

    def operational_provenance(self) -> OperationalProvenance:
        """Return the compact provenance document persisted in telemetry."""
        return OperationalProvenance(
            vision=self.vision,
            coaching_evidence_version=self.coaching_evidence_version,
            llm_runs=self.run_provenance,
        )


def compute_timing(
    timestamps: JobTimestamps,
    top_level: TopLevelTiming | None = None,
    diagnostic: DiagnosticTiming | None = None,
) -> JobTiming:
    """Calculate lifecycle timing, preserving materially negative ``other``."""
    stages = top_level or TopLevelTiming()
    processing = _span(timestamps.processing_started_at, timestamps.completed_at)
    other = _other(processing, stages)
    status = "timing_inconsistent" if other is not None and other < 0 else "consistent"
    return JobTiming(
        end_to_end=_span(timestamps.created_at, timestamps.completed_at),
        upload_validation=_span(timestamps.created_at, timestamps.queued_at),
        queue_wait=_span(timestamps.queued_at, timestamps.processing_started_at),
        processing=processing,
        other=other,
        timing_status=status,
        top_level=stages,
        diagnostic=diagnostic or DiagnosticTiming(),
    )


def read_job_trace(
    analysis_dir: Path,
    *,
    timestamps: JobTimestamps | None = None,
    top_level: TopLevelTiming | None = None,
) -> JobTrace:
    """Read an analysis directory into a safe operational trace."""
    manifest = _read_manifest(analysis_dir / "vision_manifest.json")
    diagnostic = _diagnostic(manifest, top_level)
    return JobTrace(
        timing=compute_timing(
            timestamps or JobTimestamps(), top_level=top_level, diagnostic=diagnostic
        ),
        vision=_vision_provenance(manifest),
        coaching_evidence_version=_evidence_version(analysis_dir),
        llm_metrics=read_llm_metrics(
            coach_prototype_dir(analysis_dir) / "llm_metrics.jsonl"
        ),
        run_provenance=_run_provenance(analysis_dir),
    )


def read_llm_metrics(path: Path) -> LlmMetricsSummary | None:
    """Summarize allowlisted numeric/categorical metrics; never return errors."""
    if not path.is_file():
        return None
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not rows:
        return LlmMetricsSummary(runs=0, successes=0, failures=0, skipped=0, latency_ms=0)
    successful = sum(row.get("success") is True for row in rows)
    return LlmMetricsSummary(
        runs=len(rows),
        successes=successful,
        failures=sum(row.get("success") is False for row in rows),
        skipped=sum(row.get("skipped_llm") is True for row in rows),
        latency_ms=sum(_nonnegative_int(row.get("latency_ms")) or 0 for row in rows),
        input_tokens_est=_optional_sum(rows, "input_tokens_est"),
        output_tokens_est=_optional_sum(rows, "output_tokens_est"),
        providers=_strings(rows, "provider"),
        models=_strings(rows, "model"),
    )


def _span(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    seconds = (end - start).total_seconds()
    return seconds if seconds >= 0 else None


def _other(processing: float | None, stages: TopLevelTiming) -> float | None:
    values = [
        stages.analyze,
        stages.coach_inputs,
        stages.coach_prototype,
        stages.frames,
    ]
    if processing is None or any(value is None for value in values):
        return None
    other = processing - sum(value for value in values if value is not None)
    if -_OTHER_TOLERANCE_SECONDS <= other < 0:
        return 0.0
    return other


def _read_manifest(path: Path) -> VisionManifest | None:
    if not path.is_file():
        return None
    try:
        return VisionManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValidationError):
        return None


def _vision_provenance(manifest: VisionManifest | None) -> VisionProvenance | None:
    if manifest is None:
        return None
    analysis = manifest.analysis
    try:
        return VisionProvenance(
            **analysis.model_dump(
                include={
                    "analysis_id",
                    "pipeline_version",
                    "package_version",
                    "git_sha",
                    "vision_config_sha256",
                    "refused_vision_config_sha256",
                }
            )
        )
    except ValidationError:
        return None


def _diagnostic(
    manifest: VisionManifest | None, top_level: TopLevelTiming | None
) -> DiagnosticTiming:
    timing = None if manifest is None else manifest.timing
    if timing is None:
        return DiagnosticTiming()
    raw = timing.model_dump()
    detectors = sum(
        float(value) for name, value in raw.items() if name.endswith(_DETECTOR_SUFFIX)
    )
    analyze = None if top_level is None else top_level.analyze
    non_vision = None if analyze is None else analyze - timing.total_seconds
    return DiagnosticTiming(
        vision_decode=timing.decode_seconds,
        vision_detectors=detectors,
        fusion=timing.temporal_seconds,
        analyze_non_vision=non_vision,
    )


def _evidence_version(analysis_dir: Path) -> int | None:
    try:
        index = read_coaching_index_or_none(coach_inputs_dir(analysis_dir))
    except (OSError, ArtifactError):
        return None
    return None if index is None else index.evidence_version


def _run_provenance(analysis_dir: Path) -> list[SafeRunProvenance]:
    try:
        index = read_prototype_index(coach_prototype_dir(analysis_dir))
    except (OSError, ArtifactError):
        return []
    if index is None:
        return []
    found: list[SafeRunProvenance] = []
    for entry in index.entries:
        for output in entry.outputs:
            item: RunProvenance | None = output.provenance
            if (
                item is None
                or not _safe_label(item.provider)
                or not _safe_label(item.model)
            ):
                continue
            found.append(
                SafeRunProvenance(
                    provider=item.provider,
                    model=item.model,
                    llm_view_sha256=_safe_hash(item.llm_view_sha256),
                    importance_config_hash=_importance_hash(item.importance_config_hash),
                )
            )
    return found


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return int(value)


def _optional_sum(rows: list[dict[str, Any]], key: str) -> int | None:
    values = [_nonnegative_int(row.get(key)) for row in rows]
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _strings(rows: list[dict[str, Any]], key: str) -> list[str]:
    return sorted(
        {
            value
            for row in rows
            if isinstance((value := row.get(key)), str) and _safe_label(value)
        }
    )


def _safe_label(value: str) -> bool:
    return _SAFE_LABEL.fullmatch(value) is not None


def read_llm_calls(path: Path) -> list[LlmCallRecord] | None:
    """Read call rows, dropping scenario ids, errors, and other prose.

    A missing file is an empty snapshot. Unreadable JSON returns ``None`` so
    the caller can leave the previous snapshot in place.
    """
    if not path.is_file():
        return []
    try:
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return [
        _call_record(ordinal, row)
        for ordinal, row in enumerate(rows)
        if isinstance(row, dict)
    ]


def scalar_provenance(trace: JobTrace) -> ScalarProvenance:
    """Project the primary safe identity fields for indexed columns."""
    vision = trace.vision
    primary = trace.run_provenance[0] if trace.run_provenance else None
    importance = next(
        (
            item.importance_config_hash
            for item in trace.run_provenance
            if item.importance_config_hash
        ),
        None,
    )
    return ScalarProvenance(
        pipeline_version=None if vision is None else vision.pipeline_version,
        package_version=None if vision is None else vision.package_version,
        evidence_version=trace.coaching_evidence_version,
        vision_config_hash=None if vision is None else vision.vision_config_sha256,
        importance_config_hash=importance,
        llm_provider=None if primary is None else primary.provider,
        llm_model=None if primary is None else primary.model,
    )


def _call_record(ordinal: int, row: dict[str, Any]) -> LlmCallRecord:
    provider = row.get("provider")
    model = row.get("model")
    return LlmCallRecord(
        ordinal=ordinal,
        provider=provider
        if isinstance(provider, str) and _safe_label(provider)
        else None,
        model=model if isinstance(model, str) and _safe_label(model) else None,
        disposition=_disposition(row),
        latency_ms=_nonnegative_int(row.get("latency_ms")),
        input_tokens_est=_nonnegative_int(row.get("input_tokens_est")),
        output_tokens_est=_nonnegative_int(row.get("output_tokens_est")),
    )


def _disposition(row: dict[str, Any]) -> LlmDisposition:
    if row.get("skipped_llm") is True:
        return "skipped"
    if row.get("success") is False:
        return "requested_failed"
    return "requested_succeeded"


def _safe_hash(value: str | None) -> str | None:
    if value is None or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        return None
    return value


def _importance_hash(value: str | None) -> str | None:
    if value is None or re.fullmatch(r"[0-9a-f]{12}", value) is None:
        return None
    return value
