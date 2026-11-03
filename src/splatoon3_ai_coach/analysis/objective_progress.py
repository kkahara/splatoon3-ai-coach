"""Mode-neutral sparse objective progress evidence for coaching.

Adapters translate a mode's fused objective state into fresh observations.
This module deliberately knows nothing about counters, checkpoints, or any
particular ranked mode.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.vision.models import PlayerLifecycle

ObjectiveTeam = Literal["ally", "opponent"]
ProgressState = Literal["progress", "no_progress", "unknown"]


class ObjectiveProgressObservation(BaseModel):
    """One fresh mode-adapter objective observation at a video time."""

    game_mode_id: str
    team: ObjectiveTeam
    video_time: float = Field(ge=0)
    value_kind: str
    value_label: str
    original_value: float | int | None = None
    progress_state_from_previous: ProgressState = "unknown"
    normalized_progress_amount: float | None = None
    objective_evidence_ids: list[str] = Field(default_factory=list)


class ObjectiveTemporalSample(BaseModel):
    """Objective evidence paired with already-fused roster and lifecycle state."""

    objective: ObjectiveProgressObservation
    ally_alive_count: int | None = Field(default=None, ge=0, le=4)
    opponent_alive_count: int | None = Field(default=None, ge=0, le=4)
    roster_reliable: bool = False
    roster_evidence_ids: list[str] = Field(default_factory=list)
    player_lifecycle: PlayerLifecycle = "unknown"
    lifecycle_evidence_ids: list[str] = Field(default_factory=list)


class ObjectiveProgressEvidence(BaseModel):
    """Window-bounded temporal objective samples; no continuous state implied."""

    observations: list[ObjectiveTemporalSample] = Field(default_factory=list)
    max_gap_seconds: float = Field(ge=0)
