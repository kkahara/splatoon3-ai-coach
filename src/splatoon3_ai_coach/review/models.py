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
    confidence: float = Field(default=0, ge=0, le=1)
    runner_up_margin: float = Field(default=0, ge=0, le=1)
    source: str = "timeline_graph"


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
    warnings: list[str] = Field(default_factory=list)


class TimelineEventMarker(BaseModel):
    """Conservative team-row marker from the relatively static event lane."""

    marker_id: str
    team_row: Literal["top", "bottom", "unknown"] = "unknown"
    kind: Literal["death", "special", "unknown"] = "unknown"
    x_norm: float = Field(ge=0, le=1)
    event_elapsed_min: int | None = None
    event_elapsed_max: int | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    supporting_sample_ids: list[str] = Field(default_factory=list)
    knowledge_scope: KnowledgeScope = KnowledgeScope.match_history
    player_slot_id: None = None
    killer_slot_id: None = None


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

    @property
    def onset_is_exact(self) -> bool:
        """Return whether the evidence identifies an exact onset second."""
        return (
            self.last_alive_elapsed is not None
            and self.first_dead_elapsed is not None
            and self.last_alive_elapsed == self.first_dead_elapsed
        )


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


class ReviewTimelineDataset(BaseModel):
    """Deterministic screenshot-derived Review evidence artifact."""

    schema_version: str = "1.0"
    source_recording_id: str | None = None
    regulation_seconds: int = Field(default=300, ge=0)
    samples: list[ReviewTimelineSample] = Field(default_factory=list)
    death_episodes: list[PlayerDeathEpisodeEvidence] = Field(default_factory=list)
    match_history_events: list[TimelineEventMarker] = Field(default_factory=list)
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

