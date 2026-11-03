"""Conservative checkpoint state from attributed, observed Tower HUD cues.

This module accepts observations from a caller. It does not detect the HUD,
infer checkpoint identity, use Tower scores, or infer an exact clear time.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Team = Literal["ally", "opponent"]
CheckpointStatus = Literal["unknown", "uncleared", "cleared"]


class CheckpointObservation(BaseModel):
    """Same-frame checkpoint and optional passed-cue evidence with provenance."""

    video_time: float = Field(ge=0)
    frame_id: str = Field(min_length=1)
    marker_evidence_id: str = Field(min_length=1)
    team: Team
    checkpoint: int = Field(ge=1, le=3)
    identity_evidence_ids: list[str] = Field(min_length=1)
    identity_frame_id: str | None = None
    identity_video_time: float | None = Field(default=None, ge=0)
    uncleared_evidence_id: str | None = None
    passed_evidence_id: str | None = None

    @model_validator(mode="after")
    def validate_evidence(self) -> "CheckpointObservation":
        """Require nonempty source IDs for any supplied observation."""
        if any(not evidence for evidence in self.identity_evidence_ids):
            raise ValueError("identity evidence IDs must be nonempty")
        if self.passed_evidence_id == "":
            raise ValueError("passed evidence ID must be nonempty")
        if self.uncleared_evidence_id == "":
            raise ValueError("uncleared evidence ID must be nonempty")
        return self


class CheckpointEvidence(BaseModel):
    """Observation supporting an accepted state or a contradiction."""

    video_time: float
    frame_id: str
    evidence_ids: list[str]
    identity_frame_id: str | None = None
    identity_video_time: float | None = Field(default=None, ge=0)


class CheckpointEntry(BaseModel):
    """Monotonic checkpoint status and the observations supporting it."""

    status: CheckpointStatus = "unknown"
    support: list[CheckpointEvidence] = Field(default_factory=list)
    contradictions: list[CheckpointEvidence] = Field(default_factory=list)


class TowerCheckpointState(BaseModel):
    """Separate match-level checkpoint statuses for both attacking teams."""

    ally: list[CheckpointEntry] = Field(
        default_factory=lambda: [CheckpointEntry() for _ in range(3)],
        min_length=3,
        max_length=3,
    )
    opponent: list[CheckpointEntry] = Field(
        default_factory=lambda: [CheckpointEntry() for _ in range(3)],
        min_length=3,
        max_length=3,
    )


def build_checkpoint_state(
    observations: list[CheckpointObservation],
) -> TowerCheckpointState:
    """Build monotonic match state from already attributed same-frame evidence.

    The passed cue is the sole clear signal. Uncleared requires its own direct
    evidence. Missing or absent observations do nothing.
    The cue time is its observation time, not an exact gameplay clear time.
    """
    state = TowerCheckpointState()
    for observation in sorted(observations, key=lambda item: item.video_time):
        entry = getattr(state, observation.team)[observation.checkpoint - 1]
        _apply_observation(entry, observation)
    return state


def _apply_observation(
    entry: CheckpointEntry, observation: CheckpointObservation
) -> None:
    """Apply one observation without reverting a cleared checkpoint."""
    evidence_ids = [observation.marker_evidence_id, *observation.identity_evidence_ids]
    if observation.passed_evidence_id:
        evidence = CheckpointEvidence(
            video_time=observation.video_time,
            frame_id=observation.frame_id,
            evidence_ids=list(
                dict.fromkeys([*evidence_ids, observation.passed_evidence_id])
            ),
            identity_frame_id=observation.identity_frame_id,
            identity_video_time=observation.identity_video_time,
        )
        entry.status = "cleared"
        entry.support.append(evidence)
    elif observation.uncleared_evidence_id:
        evidence = CheckpointEvidence(
            video_time=observation.video_time,
            frame_id=observation.frame_id,
            evidence_ids=list(
                dict.fromkeys([*evidence_ids, observation.uncleared_evidence_id])
            ),
        )
        if entry.status == "cleared":
            entry.contradictions.append(evidence)
        else:
            entry.status = "uncleared"
            entry.support.append(evidence)
