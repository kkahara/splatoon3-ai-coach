"""Persisted models for screenshot-derived Review evidence."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class SlotState(StrEnum):
    """State visible for one cursor-roster slot."""

    alive = "alive"
    dead = "dead"
    unknown = "unknown"


class KnowledgeScope(StrEnum):
    """Temporal scope of an observation."""

    cursor_state = "cursor_state"
    match_history = "match_history"


class CursorObservation(BaseModel):
    """Confidence and location of the timeline cursor."""

    x: float | None = None
    x_norm: float | None = Field(default=None, ge=0, le=1)
    confidence: float = Field(default=0, ge=0, le=1)
    runner_up_margin: float = Field(default=0, ge=0, le=1)
    source: str = "timeline_graph"


class ScrollDirection(StrEnum):
    """Direction of the Review clock between clocked frames."""

    forward = "forward"
    backward = "backward"
    unknown = "unknown"


class EvidenceField(BaseModel):
    """One per-second evidence value. ``None`` means unknown, never a guess."""

    value: int | str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    source_sample_id: str | None = None
    source_sample_ids: list[str] = Field(default_factory=list)
    reason: str | None = None


class PenaltyField(EvidenceField):
    """A displayed penalty value. The number has no unit."""

    pill_visible: bool | None = None


class FrameProvenance(BaseModel):
    """Source of one clocked timeline frame. Video time is not the game clock."""

    sample_id: str
    source_video_time: float | None = None
    frame_index: int | None = None
    source_pts: int | None = None
    decoded_clock: str | None = None
    clock_confidence: float = Field(default=0, ge=0, le=1)
    scroll_direction: ScrollDirection = ScrollDirection.unknown


class CursorScoreObservation(BaseModel):
    """Remaining and penalty reads for the two team rows at the cursor.

    ``top`` and ``bottom`` are filled only after color identifies the team row.
    A missing value is unknown. Penalty ``0`` is a confident observation that
    the identified label's own pill is absent.
    """

    top_remaining: int | None = None
    top_remaining_confidence: float = Field(default=0, ge=0, le=1)
    top_remaining_reason: str | None = None
    bottom_remaining: int | None = None
    bottom_remaining_confidence: float = Field(default=0, ge=0, le=1)
    bottom_remaining_reason: str | None = None
    top_penalty: int | None = None
    top_penalty_confidence: float = Field(default=0, ge=0, le=1)
    top_penalty_pill_visible: bool | None = None
    top_penalty_reason: str | None = None
    bottom_penalty: int | None = None
    bottom_penalty_confidence: float = Field(default=0, ge=0, le=1)
    bottom_penalty_pill_visible: bool | None = None
    bottom_penalty_reason: str | None = None


class CursorSlotObservation(BaseModel):
    """One player's visible state at a cursor time."""

    slot_id: str
    team_row: Literal["top", "bottom"] | None = None
    state: SlotState = SlotState.unknown
    confidence: float = Field(default=0, ge=0, le=1)
    sample_id: str
    elapsed_seconds: int | None = None
    knowledge_scope: KnowledgeScope = KnowledgeScope.cursor_state


class ReviewTimelineSample(BaseModel):
    """One source screenshot and all observations extracted from it."""

    sample_id: str
    capture_index: int
    image_path: str
    image_sha256: str
    source_view: Literal["timeline"] = "timeline"
    elapsed_seconds: int | None = None
    displayed_clock: str | None = None
    clock_source: str | None = None
    clock_status: str = "unreadable"
    clock_confidence: float = Field(default=0, ge=0, le=1)
    cursor: CursorObservation = Field(default_factory=CursorObservation)
    slots: list[CursorSlotObservation] = Field(default_factory=list)
    score: CursorScoreObservation = Field(default_factory=CursorScoreObservation)
    scroll_direction: ScrollDirection = ScrollDirection.unknown
    source_video_time: float | None = None
    frame_index: int | None = None
    source_pts: int | None = None
    warnings: list[str] = Field(default_factory=list)


class TimelineEventMarker(BaseModel):
    """Conservative team-row marker from the relatively static event lane."""

    marker_id: str
    team_row: Literal["top", "bottom", "unknown"] = "unknown"
    kind: Literal["death", "special", "unknown"] = "unknown"
    x_norm: float = Field(ge=0, le=1)
    event_elapsed_min: int | None = None
    event_elapsed_max: int | None = None
    elapsed_estimate: float | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    supporting_sample_ids: list[str] = Field(default_factory=list)
    cursor_obscured_sample_ids: list[str] = Field(default_factory=list)
    knowledge_scope: KnowledgeScope = KnowledgeScope.match_history
    player_slot_id: str | None = None
    killer_slot_id: None = None


class AxisCalibrationEvidence(BaseModel):
    """Linear timeline-axis fit and its observed residuals."""

    slope: float
    intercept: float
    point_count: int = Field(ge=2)
    # Clocked samples excluded as misreads before the accepted fit.
    rejected_point_count: int = Field(default=0, ge=0)
    mean_absolute_error_seconds: float = Field(ge=0)
    max_absolute_error_seconds: float = Field(ge=0)

    def elapsed_at(self, x_norm: float) -> float:
        """Estimate elapsed time at a normalized full-frame x coordinate."""
        return self.slope * x_norm + self.intercept


def project_elapsed_second(estimate: float) -> int:
    """Map a timeline estimate to the integer game-clock second that contains it.

    Nearest integer, with halves moving away from zero. This is not Python's
    banker's ``round`` (``12.5`` would become ``12`` there, and ``13`` here).
    """
    if estimate >= 0:
        return int(estimate + 0.5)
    return int(estimate - 0.5)


class TeamIdentity(BaseModel):
    """Top/bottom team colors plus an optional ally-row interpretation."""

    top_color: float | None = Field(default=None, ge=0, lt=180)
    bottom_color: float | None = Field(default=None, ge=0, lt=180)
    ally_row: Literal["top", "bottom"] | None = None
    source: Literal["gameplay_color", "cli", "unknown"] = "unknown"


class ControlSegment(BaseModel):
    """Run of static timeline control evidence."""

    start_elapsed: float
    end_elapsed: float
    controller: Literal["top", "bottom", "neutral", "unknown"]
    confidence: float = Field(ge=0, le=1)


class SpecialUseEvent(BaseModel):
    """One event-lane special activation estimate.

    ``supporting_sample_ids`` are frames whose clear (not cursor-covered)
    observation supports ``special_id``. ``cursor_obscured_sample_ids`` are
    frames where the cursor covered this position; they are neither positive
    nor negative evidence.
    """

    slot_id: str
    team_row: Literal["top", "bottom"]
    special_id: str
    elapsed_estimate: float
    x_norm: float = Field(ge=0, le=1)
    center_x: float
    score: float = Field(ge=0, le=1)
    runner_up_score: float = Field(default=0, ge=0, le=1)
    runner_up_margin: float = Field(ge=0, le=1)
    template_id: str | None = None
    runner_up_template_id: str | None = None
    row_id: int | None = Field(default=None, ge=0, le=7)
    left_x_norm: float | None = Field(default=None, ge=0, le=1)
    right_x_norm: float | None = Field(default=None, ge=0, le=1)
    timestamp_anchor: Literal["center", "left", "right"] = "center"
    scale: float | None = None
    supporting_sample_ids: list[str] = Field(default_factory=list)
    cursor_obscured_sample_ids: list[str] = Field(default_factory=list)


class PlayerDeathEpisodeEvidence(BaseModel):
    """A bounded death/recovery interval inferred from cursor samples."""

    slot_id: str
    team_row: Literal["top", "bottom"] | None = None
    last_alive_elapsed: int | None = None
    first_dead_elapsed: int | None = None
    first_dead_sample_id: str | None = None
    last_dead_elapsed: int | None = None
    next_alive_elapsed: int | None = None
    recovery_sample_id: str | None = None
    candidate_marker_ids: list[str] = Field(default_factory=list)
    death_elapsed_estimate: float | None = None
    respawn_seconds_min: float | None = Field(default=None, ge=0)
    respawn_seconds_max: float | None = Field(default=None, ge=0)
    warnings: list[str] = Field(default_factory=list)

    @property
    def onset_is_exact(self) -> bool:
        """Return whether the evidence identifies an exact onset second."""
        return (
            self.last_alive_elapsed is not None
            and self.first_dead_elapsed is not None
            and self.last_alive_elapsed == self.first_dead_elapsed
        )


class ReviewSecondState(BaseModel):
    """Canonical evidence for one integer Review game-clock second.

    ``elapsed_second`` is the timeline clock second, not video time.
    """

    elapsed_second: int
    slots: list[CursorSlotObservation] = Field(default_factory=list)
    top_alive_count: EvidenceField = Field(default_factory=EvidenceField)
    bottom_alive_count: EvidenceField = Field(default_factory=EvidenceField)
    top_remaining: EvidenceField = Field(default_factory=EvidenceField)
    bottom_remaining: EvidenceField = Field(default_factory=EvidenceField)
    top_penalty: PenaltyField = Field(default_factory=PenaltyField)
    bottom_penalty: PenaltyField = Field(default_factory=PenaltyField)
    controller: EvidenceField = Field(default_factory=EvidenceField)
    special_ids: list[str] = Field(default_factory=list)
    death_marker_ids: list[str] = Field(default_factory=list)
    provenance: list[FrameProvenance] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CoverageReport(BaseModel):
    """Coverage diagnostics for the supplied screenshot sweep."""

    sample_count: int
    clocked_sample_count: int
    first_elapsed_seconds: int | None = None
    last_elapsed_seconds: int | None = None
    missing_elapsed_seconds: list[int] = Field(default_factory=list)
    duplicate_elapsed_seconds: list[int] = Field(default_factory=list)
    non_monotonic_capture_indexes: list[int] = Field(default_factory=list)
    unreadable_sample_ids: list[str] = Field(default_factory=list)
    expected_seconds: list[int] = Field(default_factory=list)
    observed_seconds: list[int] = Field(default_factory=list)
    complete: bool = False
    disagreement_seconds: list[int] = Field(default_factory=list)


class ReviewTimelineDataset(BaseModel):
    """Deterministic screenshot-derived Review evidence artifact."""

    schema_version: str = "1.2"
    source_recording_id: str | None = None
    battle_mode_id: str | None = None
    regulation_seconds: int = Field(default=300, ge=0)
    samples: list[ReviewTimelineSample] = Field(default_factory=list)
    second_states: list[ReviewSecondState] = Field(default_factory=list)
    death_episodes: list[PlayerDeathEpisodeEvidence] = Field(default_factory=list)
    match_history_events: list[TimelineEventMarker] = Field(default_factory=list)
    axis_calibration: AxisCalibrationEvidence | None = None
    team_identity: TeamIdentity = Field(default_factory=TeamIdentity)
    control_segments: list[ControlSegment] = Field(default_factory=list)
    special_events: list[SpecialUseEvent] = Field(default_factory=list)
    coverage: CoverageReport
    warnings: list[str] = Field(default_factory=list)
    detector_versions: dict[str, str] = Field(default_factory=dict)

    def cursor_state_at(self, elapsed_seconds: int) -> list[CursorSlotObservation]:
        """Return states from the sample at the requested cursor time only."""
        for sample in self.samples:
            if sample.elapsed_seconds == elapsed_seconds:
                return sample.slots
        return []

    def history_events_before(self, elapsed_seconds: int) -> list[TimelineEventMarker]:
        """Return match-history markers at or before an explicit cutoff."""
        return [
            marker
            for marker in self.match_history_events
            if marker.event_elapsed_max is not None
            and marker.event_elapsed_max <= elapsed_seconds
        ]

