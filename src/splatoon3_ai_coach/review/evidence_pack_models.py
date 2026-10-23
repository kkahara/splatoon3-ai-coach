"""Persisted models for ``review_evidence.json`` (contract: ``REVIEW_EVIDENCE_PACK.md``).

Review clock only. Inclusion in the pack never authorizes an evidence type for
ScenarioContext or coaching; a downstream consumer needs its own allowlist.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.review.models import AxisCalibrationEvidence, SlotState
from splatoon3_ai_coach.review.special_fusion import SpecialUseFact

PACK_FILENAME = "review_evidence.json"
PACK_SCHEMA_VERSION = "1.0"

ValidationStatus = Literal[
    "validated", "limited", "experimental", "not_applicable", "unavailable"
]
LimitationCode = Literal[
    "no_acquisition_provenance",
    "gate_run_before_freeze",
    "disconnected_slot_reads_alive",
    "slot_state_runs_not_gated",
    "slot_state_at_use_inherits_lamp_limits",
    "special_type_identity_partial",
    "special_facts_refused",
    "absence_not_evidence",
    "bottom_row_hue_zero_recall",
    "hue_not_measured",
    "hue_range_uncharacterized",
    "side_unknown",
    "side_from_unvalidated_calibration",
    "side_operator_declared",
    "score_reader_not_gated",
    "controller_without_sample_provenance",
    "battle_mode_unknown",
    "battle_mode_not_objective",
    "coverage_incomplete",
    "non_monotonic_check_unverified",
    "axis_unavailable",
]


class Limitation(BaseModel):
    """A closed-vocabulary limitation code plus a human-readable detail."""

    code: LimitationCode
    detail: str


class EvidenceBlock(BaseModel):
    """Validation status shared by every per-type block."""

    validation_status: ValidationStatus
    validation_basis: str | None = None
    limitations: list[Limitation] = Field(default_factory=list)


class SampleRef(BaseModel):
    """One timeline sample: the link from a fact to a frame of the artifact."""

    sample_id: str
    capture_index: int
    image_name: str
    image_sha256: str
    displayed_clock: str | None = None
    remaining_second: int | None = None
    clock_status: str
    source_video_time: float | None = None
    frame_index: int | None = None
    source_pts: int | None = None


class AcquisitionRecord(BaseModel):
    """Capture-package provenance, supplied by the caller; Review never reads packages."""

    package_id: str
    source_type: str
    acquisition_method: str
    language: str
    artifact_id: str
    artifact_path: str
    artifact_video_identity: str
    artifact_hash_verified: bool
    import_dir_in_package: str
    gate_contract: str
    gate_contract_status_at_run: str
    gate_automatic_verdict: str
    gate_verdict: str
    gate_checked_at: str
    gate_result_sha256: str
    gate_git_sha: str | None = None
    battle_mode_id: str | None = None
    battle_mode_source: Literal["capture_gate_intro_identity"] | None = None
    limitations: list[Limitation] = Field(default_factory=list)


class PackSource(BaseModel):
    """The import the pack was built from."""

    dataset_file: str
    dataset_sha256: str
    dataset_schema_version: str
    manifest_file: str | None = None
    manifest_sha256: str | None = None
    source_video_identity: str | None = None
    source_recording_id: str | None = None
    battle_mode_id: str | None = None
    regulation_seconds: int
    detector_versions: dict[str, str] = Field(default_factory=dict)


class SpecialUseEntry(BaseModel):
    """One ``SpecialUseFact`` plus the samples whose glyph supports it."""

    fact_id: str
    fact: SpecialUseFact
    source_sample_ids: list[str] = Field(default_factory=list)
    cursor_obscured_sample_ids: list[str] = Field(default_factory=list)


class SpecialUseBlock(EvidenceBlock):
    """SPECIAL_USE facts (timing frozen; type identity partly experimental)."""

    seconds_per_px: float | None = None
    entries: list[SpecialUseEntry] = Field(default_factory=list)


class SlotStateRun(BaseModel):
    """Consecutive observed seconds with one lamp state; never bridged across gaps.

    ``remaining_start`` is the earlier (larger) remaining second.
    """

    fact_id: str
    slot_id: str
    team_row: Literal["top", "bottom"] | None = None
    state: SlotState
    remaining_start: int
    remaining_end: int
    second_count: int = Field(ge=1)
    min_confidence: float = Field(ge=0, le=1)
    source_sample_ids: list[str] = Field(default_factory=list)


class SlotStateBlock(EvidenceBlock):
    """Roster-lamp runs per slot. ``alive`` only means no X marker was observed."""

    validated_scope: Literal["x_marker_detection"] = "x_marker_detection"
    state_semantics: dict[str, str] = Field(
        default_factory=lambda: {
            "alive": "no_x_marker_observed",
            "dead": "x_marker_observed",
            "unknown": "lamp_unreadable",
        }
    )
    connection_state: Literal["not_observed"] = "not_observed"
    gap_seconds: list[int] = Field(default_factory=list)
    runs: list[SlotStateRun] = Field(default_factory=list)


class RowRecall(BaseModel):
    """Death-glyph recall status for one team row. Absence is never evidence."""

    team_row: Literal["top", "bottom"]
    row_hue: float | None = None
    recall_status: Literal["partial", "known_zero_recall", "unknown"]
    absence_is_evidence: Literal[False] = False
    observed_glyph_count: int = Field(default=0, ge=0)
    diagnostic_lamp_onset_count: int | None = Field(default=None, ge=0)


class DeathMarkerEntry(BaseModel):
    """One death glyph in the event lane, on the remaining clock."""

    fact_id: str
    marker_id: str
    team_row: Literal["top", "bottom", "unknown"]
    player_slot_id: str | None = None
    remaining_estimate: float | None = None
    remaining_min: int | None = None
    remaining_max: int | None = None
    confidence: float = Field(ge=0, le=1)
    source_sample_ids: list[str] = Field(default_factory=list)
    cursor_obscured_sample_ids: list[str] = Field(default_factory=list)


class DeathMarkerBlock(EvidenceBlock):
    """Death glyphs with per-row recall status."""

    row_recall: list[RowRecall] = Field(default_factory=list)
    markers: list[DeathMarkerEntry] = Field(default_factory=list)


class ObservedValue(BaseModel):
    """A value read at one remaining second; empty sources mean none were recorded."""

    value: int | str
    confidence: float = Field(ge=0, le=1)
    source_sample_ids: list[str] = Field(default_factory=list)


class ScoreSecond(BaseModel):
    """Observed counts, penalties and control band at one remaining second."""

    fact_id: str
    remaining_second: int
    top_remaining: ObservedValue | None = None
    bottom_remaining: ObservedValue | None = None
    top_penalty: ObservedValue | None = None
    bottom_penalty: ObservedValue | None = None
    controller: ObservedValue | None = None


class ScoreControlBlock(EvidenceBlock):
    """Mode-gated objective counts and control band (no gate exists yet)."""

    battle_mode_id: str | None = None
    seconds: list[ScoreSecond] = Field(default_factory=list)


class CoverageBlock(EvidenceBlock):
    """Which remaining seconds were observed."""

    first_remaining: int | None = None
    last_remaining: int | None = None
    expected_second_count: int = 0
    observed_second_count: int = 0
    complete: bool = False
    missing_seconds: list[int] = Field(default_factory=list)
    duplicate_seconds: list[int] = Field(default_factory=list)
    disagreement_seconds: list[int] = Field(default_factory=list)
    unreadable_sample_count: int = 0
    non_monotonic_capture_indexes: list[int] = Field(default_factory=list)


class TeamIdentityBlock(EvidenceBlock):
    """Row hues and the ally-row assignment with its source."""

    top_hue: float | None = None
    bottom_hue: float | None = None
    ally_row: Literal["top", "bottom"] | None = None
    side_source: Literal[
        "gameplay_color",
        "gameplay_color_roster_gated",
        "cli",
        "unknown",
    ] = "unknown"
    side_provenance: Literal[
        "roster_gated_calibration",
        "operator_declared",
        "unknown",
    ] = "unknown"
    side_evidence_ids: list[str] = Field(default_factory=list)
    side_evidence_times: list[float] = Field(default_factory=list)
    side_provenance_reason: str | None = None


class ReviewEvidencePack(BaseModel):
    """Per-import evidence pack on the Review remaining clock."""

    schema_version: str = PACK_SCHEMA_VERSION
    clock: Literal["review_remaining"] = "review_remaining"
    consumption: Literal["not_authorized_by_inclusion"] = "not_authorized_by_inclusion"
    source: PackSource
    acquisition: AcquisitionRecord | None = None
    limitations: list[Limitation] = Field(default_factory=list)
    axis: AxisCalibrationEvidence | None = None
    samples: list[SampleRef] = Field(default_factory=list)
    special_uses: SpecialUseBlock
    slot_states: SlotStateBlock
    death_markers: DeathMarkerBlock
    score_control: ScoreControlBlock
    coverage: CoverageBlock
    team_identity: TeamIdentityBlock
