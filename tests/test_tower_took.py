"""Factual Tower acquisition observations and control-state transitions."""

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    MatchIntroReading,
    TimerReading,
    TowerControlCue,
    TowerControlReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.state import fuse_game_state
from splatoon3_ai_coach.vision.tower_control_state import TowerControlMessageFuser
from splatoon3_ai_coach.vision.tower_took import collect_took_observations


def _frame(time: float, cue_type: str | None = None) -> VisionFrameResult:
    """Create a Tower cue frame with stable source and detector identities."""
    detections = []
    if cue_type is not None:
        team = "ally" if cue_type == "ally_took" else "opponent"
        detections.append(
            DetectorResult(
                id=f"cue-{time}",
                detector_name="tower_control",
                detector_version="test",
                confidence=0.99,
                reading=TowerControlReading(
                    timestamp=time,
                    control_cue=TowerControlCue(
                        cue_type=cue_type, team=team, template_score=0.99, language="en"
                    ),
                ),
            )
        )
    return VisionFrameResult(
        frame_id=f"frame-{time}", timestamp=time, source="cadence", detections=detections
    )


def test_took_observation_directly_establishes_control_from_unknown() -> None:
    """A valid direct acquisition cue needs no preceding returning state."""
    source = _frame(10, "ally_took")
    observation = collect_took_observations([source])[0]
    state, evidence = TowerControlMessageFuser().step(
        source, "tower_control", "in_match", took_observation=observation
    )
    assert state == "ally_control"
    assert evidence is not None
    assert evidence.video_time == 10
    assert evidence.detector_evidence_id == "cue-10"
    assert evidence.took_observation is not None
    assert evidence.took_observation.cue == "ally_took_tower"
    assert evidence.took_observation.samples[0].cue.language == "en"


def test_took_observation_replaces_stale_control_without_loss_cue() -> None:
    """A later explicit acquisition corrects held control despite a missed loss."""
    opponent, ally = _frame(1, "opponent_took"), _frame(5, "ally_took")
    observations = collect_took_observations([opponent, ally])
    fuser = TowerControlMessageFuser()
    assert fuser.step(
        opponent, "tower_control", "in_match", took_observation=observations[0]
    )[0] == "opponent_control"
    state, evidence = fuser.step(
        ally, "tower_control", "in_match", took_observation=observations[1]
    )
    assert state == "ally_control"
    assert evidence is not None and evidence.video_time == 5


def test_repeated_took_samples_update_provenance_not_transition() -> None:
    """One displayed cue yields one held state with growing direct support."""
    first, repeated = _frame(1, "opponent_took"), _frame(1.5, "opponent_took")
    observation = collect_took_observations([first, repeated])[0]
    first_only = observation.model_copy(
        update={"samples": observation.samples[:1], "last_video_time": 1}
    )
    fuser = TowerControlMessageFuser()
    assert fuser.step(
        first, "tower_control", "in_match", took_observation=first_only
    )[0] == "opponent_control"
    state, evidence = fuser.step(
        repeated, "tower_control", "in_match", took_observation=observation
    )
    assert state == "opponent_control"
    assert evidence is not None and evidence.video_time == 1
    assert evidence.took_observation is not None
    assert [sample.evidence_id for sample in evidence.took_observation.samples] == [
        "cue-1",
        "cue-1.5",
    ]


def test_snapshot_fusion_does_not_manufacture_control_without_took_cue() -> None:
    """Scoreless frames without a direct acquisition message retain unknown."""
    intro = VisionFrameResult(
        frame_id="intro",
        timestamp=0,
        source="cadence",
        detections=[
            DetectorResult(
                id="intro",
                detector_name="match_intro",
                detector_version="test",
                confidence=1,
                reading=MatchIntroReading(
                    stage_id="makomart", battle_mode_id="tower_control",
                    stage_template_score=1, battle_mode_template_score=1,
                ),
            )
        ],
    )
    blank = _frame(1)
    for item in (intro, blank):
        item.detections.append(
            DetectorResult(
                id=f"timer-{item.timestamp}",
                detector_name="timer",
                detector_version="test",
                confidence=1,
                reading=TimerReading(display="2:05", seconds_remaining=125),
            )
        )
    config = load_config(PROJECT_ROOT / "configs/default.yaml")
    snapshots = fuse_game_state(
        [intro, blank], config.vision.timer, config.vision.state_fusion
    )
    assert snapshots[-1].tower_control_state == "unknown"
    assert snapshots[-1].tower_control_evidence is None
