"""Checkpoint state only changes from attributed, explicit observations."""

import pytest
from pydantic import ValidationError

from splatoon3_ai_coach.vision.tower_checkpoint_state import (
    CheckpointObservation,
    build_checkpoint_state,
)


def observation(time: float, **changes: object) -> CheckpointObservation:
    """Create an attributed development observation with explicit IDs."""
    values = {
        "video_time": time,
        "frame_id": f"frame-{time}",
        "marker_evidence_id": f"marker-{time}",
        "team": "ally",
        "checkpoint": 1,
        "identity_evidence_ids": [f"identity-{time}"],
    }
    values.update(changes)
    return CheckpointObservation.model_validate(values)


def test_passed_cue_clears_once_and_contradiction_does_not_revert() -> None:
    """A later conflicting active reading is retained but never reverses a clear."""
    state = build_checkpoint_state(
        [
            observation(3, uncleared_evidence_id="uncleared"),
            observation(4, passed_evidence_id="passed"),
            observation(5, uncleared_evidence_id="uncleared-contradiction"),
        ]
    )
    checkpoint = state.ally[0]
    assert checkpoint.status == "cleared"
    assert checkpoint.support[-1].frame_id == "frame-4"
    assert checkpoint.support[-1].evidence_ids == ["marker-4", "identity-4", "passed"]
    assert checkpoint.contradictions[0].frame_id == "frame-5"
    assert all(entry.status == "unknown" for entry in state.opponent)
    assert state.ally[1].status == "unknown"


def test_absent_observations_do_not_clear() -> None:
    """Absence never becomes evidence of a clear."""
    state = build_checkpoint_state(
        [
            observation(1, uncleared_evidence_id="uncleared"),
            observation(2),
            observation(3),
        ]
    )
    assert state.ally[0].status == "uncleared"
    assert len(state.ally[0].support) == 1


def test_unattributed_and_unproven_observations_are_rejected() -> None:
    """A caller must supply team, checkpoint identity, and source evidence."""
    with pytest.raises(ValidationError):
        observation(1, team="unknown", passed_evidence_id="cue")
    with pytest.raises(ValidationError):
        observation(1, identity_evidence_ids=[], passed_evidence_id="cue")
    with pytest.raises(ValidationError):
        observation(1, uncleared_evidence_id="")


def test_clear_can_be_first_observation_and_is_team_specific() -> None:
    """A direct cue may establish clear without a previously seen ring."""
    state = build_checkpoint_state(
        [observation(9, team="opponent", checkpoint=2, passed_evidence_id="cue")]
    )
    assert state.opponent[1].status == "cleared"
    assert state.ally[1].status == "unknown"
