"""Capture package manifest and quality-gate result models.

A capture package records how artifacts were acquired (source, method,
operator attestations) and whether each satisfies its capture contract. It
says nothing about gameplay meaning; evidence comes from the existing
pipelines run on the stored artifacts.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from splatoon3_ai_coach.config.models import VisionLanguage
from splatoon3_ai_coach.media.video import VideoStreamProperties

CAPTURE_PACKAGE_FILENAME = "capture_package.json"
CAPTURE_PACKAGE_SCHEMA_VERSION = 1
ARTIFACTS_DIRNAME = "artifacts"
QUALITY_DIRNAME = "quality"
TIMELINE_CAPTURE_V1 = "timeline-capture-v1"

_REVIEW_CODE = re.compile(r"^[A-Z0-9]{4}(?:-[A-Z0-9]{4}){3}$")


class SourceType(StrEnum):
    """Where the match recording came from (acquisition origin)."""

    UPLOADED_VIDEO = "uploaded_video"
    REVIEW_CODE = "review_code"


class AcquisitionMethod(StrEnum):
    """Who or what produced the artifacts."""

    UPLOAD = "upload"
    MANUAL = "manual"
    SCLC = "sclc"


class ArtifactKind(StrEnum):
    """What view of the match an artifact shows."""

    GAMEPLAY = "gameplay"
    TIMELINE = "timeline"
    TOP_DOWN = "top_down"


class ArtifactStatus(StrEnum):
    """Role of an artifact within its package."""

    REQUIRED = "required"
    OPTIONAL = "optional"
    EXPERIMENTAL = "experimental"


class ContractStatus(StrEnum):
    """``draft`` contracts may still change; ``frozen`` ones never change meaning."""

    DRAFT = "draft"
    FROZEN = "frozen"


class CheckLayer(StrEnum):
    """What a gate check is about."""

    TECHNICAL = "technical"
    CONTENT = "content"
    PROVENANCE = "provenance"


class CheckMethod(StrEnum):
    """How a check is decided."""

    AUTOMATIC = "automatic"
    MANUAL = "manual"


class CheckStatus(StrEnum):
    """Outcome of one check; ``unknown`` and ``not_run`` need an attestation."""

    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_RUN = "not_run"


class Verdict(StrEnum):
    """Contract verdict for one artifact."""

    PASS = "pass"
    FAIL = "fail"
    INCOMPLETE = "incomplete"


def utc_now() -> str:
    """UTC timestamp to the second, ISO 8601."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalize_review_code(raw: str) -> str:
    """``XXXX-XXXX-XXXX-XXXX`` (upper case); raises ``ValueError`` otherwise."""
    chars = re.sub(r"[^A-Z0-9]", "", raw.upper())
    code = "-".join(chars[i : i + 4] for i in range(0, len(chars), 4))
    if not _REVIEW_CODE.fullmatch(code):
        raise ValueError("review code must be 16 letters/digits (XXXX-XXXX-XXXX-XXXX)")
    return code


class CaptureArtifact(BaseModel):
    """One stored, byte-identical acquired recording."""

    artifact_id: str
    kind: ArtifactKind
    status: ArtifactStatus
    contract: str | None = None
    path: str
    original_filename: str
    video_identity: str
    size_bytes: int = Field(gt=0)
    properties: VideoStreamProperties
    playback_rate_declared: str | None = None
    # Measured later from the recording; never assumed from the declared setting.
    effective_playback_rate: float | None = None
    added_at: str
    note: str | None = None


class ManualAttestation(BaseModel):
    """Operator confirmation for a check automation could not decide."""

    artifact_id: str
    check_id: str
    status: Literal[CheckStatus.PASS, CheckStatus.FAIL]
    note: str
    recorded_at: str


class GateSummary(BaseModel):
    """Latest gate run for one artifact; the full result lives in ``result_path``."""

    artifact_id: str
    contract: str
    contract_status: ContractStatus
    result_path: str
    automatic_verdict: Verdict
    verdict: Verdict
    checked_at: str


class CapturePackage(BaseModel):
    """``capture_package.json``: acquisition provenance for one match."""

    schema_version: Literal[1] = CAPTURE_PACKAGE_SCHEMA_VERSION
    package_id: str
    source_type: SourceType
    acquisition_method: AcquisitionMethod
    review_code: str | None = None
    language: VisionLanguage
    created_at: str
    note: str | None = None
    artifacts: list[CaptureArtifact] = Field(default_factory=list)
    attestations: list[ManualAttestation] = Field(default_factory=list)
    gates: list[GateSummary] = Field(default_factory=list)

    @field_validator("review_code")
    @classmethod
    def _normal_code(cls, value: str | None) -> str | None:
        return None if value is None else normalize_review_code(value)

    @model_validator(mode="after")
    def _source_rules(self) -> CapturePackage:
        if self.source_type is SourceType.REVIEW_CODE:
            if self.review_code is None:
                raise ValueError("review_code packages need a review code")
            if self.acquisition_method is AcquisitionMethod.UPLOAD:
                raise ValueError("review_code packages are acquired manually or by sclc")
        elif self.acquisition_method is not AcquisitionMethod.UPLOAD:
            raise ValueError("uploaded_video packages use acquisition_method upload")
        return self

    def artifact(self, artifact_id: str) -> CaptureArtifact | None:
        """The artifact with this id, if stored."""
        return next((a for a in self.artifacts if a.artifact_id == artifact_id), None)


class GateCheck(BaseModel):
    """One contract check and the measurements behind it."""

    check_id: str
    layer: CheckLayer
    method: CheckMethod
    status: CheckStatus
    required: bool = True
    detail: str = ""
    values: dict[str, Any] = Field(default_factory=dict)


class QualityGateResult(BaseModel):
    """Automatic gate output for one artifact. Never edited after it is written."""

    contract: str
    contract_status: ContractStatus
    artifact_id: str
    video_identity: str
    checked_at: str
    package_version: str
    git_sha: str | None = None
    checks: list[GateCheck]
    # Measurements that never gate a draft contract (continuity, overtime, timing).
    observations: dict[str, Any] = Field(default_factory=dict)
    automatic_verdict: Verdict


def derive_verdict(
    checks: Sequence[GateCheck], attestations: Sequence[ManualAttestation] = ()
) -> Verdict:
    """Fail on any failing required check; pass only when every one passes.

    An attestation decides only an ``unknown`` or ``not_run`` check. It never
    overrides an automatic pass or fail. The latest attestation per check wins.
    """
    attested = {item.check_id: item.status for item in attestations}
    statuses = []
    for check in checks:
        if not check.required:
            continue
        status = check.status
        if status in (CheckStatus.UNKNOWN, CheckStatus.NOT_RUN):
            status = attested.get(check.check_id, status)
        statuses.append(status)
    if CheckStatus.FAIL in statuses:
        return Verdict.FAIL
    if all(status is CheckStatus.PASS for status in statuses):
        return Verdict.PASS
    return Verdict.INCOMPLETE
