"""Development checkpoint fusion with same-frame provenance and team checks."""

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import (
    TowerCheckpointLocation,
    TowerControlDetectorConfig,
)
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.models import DetectorResult, VisionFrameResult
from splatoon3_ai_coach.vision.team_color_calibration import TeamColorCalibrationResult
from splatoon3_ai_coach.vision.tower_checkpoint_fusion import TowerCheckpointFuser
from splatoon3_ai_coach.vision.tower_control import TowerControlDetector
from splatoon3_ai_coach.vision.tower_team import resolve_tower_team

pytestmark = pytest.mark.skip(
    reason="Historical marker-position checkpoint fusion is no longer active"
)

CALIBRATION = PROJECT_ROOT / "calibration/tower_control"
DEVELOPMENT = PROJECT_ROOT / "analysis/development/tower_control"
CONFIG = TowerControlDetectorConfig(
    template_dir=PROJECT_ROOT / "calibration/templates",
    checkpoint_cue_template_dir=CALIBRATION,
)


def development_frame(time: float) -> VisionFrameResult:
    """Run the actual Tower detector on a saved MakoMart development crop."""
    crop = cv2.imread(str(DEVELOPMENT / f"makomart_candidate_{int(time)}_5.png"))
    assert crop is not None
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    image[95:315, 450:1470] = crop
    if time in (167.5, 170.5):
        filename = (
            "at_checkpoint_development.png" if time == 167.5 else "passed_development.png"
        )
        message = cv2.imread(str(DEVELOPMENT / "en" / filename))
        assert message is not None
        image[250:380, 300:1600] = message
    reading, confidence = TowerControlDetector(CONFIG).detect(image, time)
    assert reading is not None
    result = DetectorResult(
        id=f"det-{time}",
        detector_name="tower_control",
        detector_version="test",
        confidence=confidence,
        reading=reading,
    )
    calibration = TeamColorCalibrationResult(
        ally_h=10, opponent_h=143, separation_degrees=47, calibrated_at=8
    )
    return VisionFrameResult(
        frame_id=f"frame-{time}",
        timestamp=time,
        source="cadence",
        detections=[result],
        tower_team=resolve_tower_team(result, calibration, "tower_control", CONFIG),
    )


def fuser() -> TowerCheckpointFuser:
    """Use the operator-confirmed MakoMart ally checkpoint 1 location."""
    return TowerCheckpointFuser(
        [
            TowerCheckpointLocation(
                stage_id="makomart", team="ally", checkpoint=1, lane_position=582 / 960
            )
        ],
        stage_id="makomart",
    )


def test_real_development_cue_clears_confirmed_checkpoint() -> None:
    """Real frame detection clears only with prior and current matching team/position."""
    tracker = fuser()
    before, passed = development_frame(169.5), development_frame(170.5)
    assert (
        before.tower_team is not None and before.tower_team.control_presentation == "ally"
    )
    assert (
        passed.tower_team is not None and passed.tower_team.control_presentation == "ally"
    )
    _, state = tracker.step(before, "tower_control", "in_match", False)
    assert state is not None and state.ally[0].status == "unknown"
    identity, state = tracker.step(passed, "tower_control", "in_match", False)
    assert identity == 1 and state is not None and state.ally[0].status == "cleared"
    evidence = state.ally[0].support[0]
    assert evidence.frame_id == "frame-170.5"
    assert evidence.identity_frame_id == "frame-169.5"
    assert evidence.evidence_ids == ["det-170.5", "det-169.5"]


def test_real_development_at_cue_establishes_uncleared_then_clear() -> None:
    """The operator-confirmed at-cue supports uncleared before the passed cue."""
    tracker = fuser()
    at, before, passed = (development_frame(t) for t in (167.5, 169.5, 170.5))
    assert at.tower_team is not None and at.tower_team.control_presentation == "ally"
    identity, state = tracker.step(at, "tower_control", "in_match", False)
    assert identity == 1 and state is not None and state.ally[0].status == "uncleared"
    assert state.ally[0].support[0].frame_id == "frame-167.5"
    tracker.step(before, "tower_control", "in_match", False)
    _, state = tracker.step(passed, "tower_control", "in_match", False)
    assert state is not None and state.ally[0].status == "cleared"


def test_checkpoint_cues_do_not_change_tower_score_observations() -> None:
    """Enabling cue recognition does not reinterpret the score pods."""
    crop = cv2.imread(str(DEVELOPMENT / "makomart_candidate_167_5.png"))
    message = cv2.imread(str(DEVELOPMENT / "en/at_checkpoint_development.png"))
    assert crop is not None and message is not None
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    image[95:315, 450:1470] = crop
    image[250:380, 300:1600] = message
    enabled, _ = TowerControlDetector(CONFIG).detect(image, 167.5)
    disabled_config = CONFIG.model_copy(update={"checkpoint_cue_template_dir": None})
    disabled, _ = TowerControlDetector(disabled_config).detect(image, 167.5)
    assert enabled is not None and disabled is not None
    assert enabled.checkpoint_at_cue_score is not None
    assert enabled.score_pods == disabled.score_pods


def test_cue_without_matching_prior_or_with_map_overlay_does_not_clear() -> None:
    """Cue alone, missing team, or interrupted observations are insufficient."""
    tracker = fuser()
    before, passed = development_frame(169.5), development_frame(170.5)
    _, state = tracker.step(passed, "tower_control", "in_match", False)
    assert state is not None and state.ally[0].status == "unknown"
    tracker.step(before, "tower_control", "in_match", False)
    tracker.step(before, "tower_control", "in_match", True)
    _, state = tracker.step(passed, "tower_control", "in_match", False)
    assert state is not None and state.ally[0].status == "unknown"


def test_wrong_stage_or_changed_team_cannot_clear() -> None:
    """A stage mismatch or change of team invalidates a checkpoint association."""
    before, passed = development_frame(169.5), development_frame(170.5)
    tracker = TowerCheckpointFuser(fuser().locations, stage_id="museum_dalfonsino")
    tracker.step(before, "tower_control", "in_match", False)
    _, state = tracker.step(passed, "tower_control", "in_match", False)
    assert state is not None and state.ally[0].status == "unknown"

    tracker = fuser()
    tracker.step(before, "tower_control", "in_match", False)
    assert passed.tower_team is not None
    passed.tower_team.control_presentation = "opponent"
    _, state = tracker.step(passed, "tower_control", "in_match", False)
    assert state is not None and state.ally[0].status == "unknown"


def test_cleared_state_is_persistent_and_snapshot_is_copied() -> None:
    """Later missing evidence preserves the clear without editing older snapshots."""
    tracker = fuser()
    before, passed = development_frame(169.5), development_frame(170.5)
    _, earlier = tracker.step(before, "tower_control", "in_match", False)
    tracker.step(passed, "tower_control", "in_match", False)
    empty = passed.model_copy(update={"tower_team": None, "detections": []})
    _, later = tracker.step(empty, "tower_control", "in_match", False)
    assert earlier is not None and earlier.ally[0].status == "unknown"
    assert later is not None and later.ally[0].status == "cleared"


def test_all_six_configured_makomart_locations_bind_to_correct_team_and_number() -> None:
    """Calibrated positions map only to their declared team and attack order."""
    config = load_config(PROJECT_ROOT / "configs/default.yaml")
    locations = config.vision.tower_control.checkpoint_locations
    assert len(locations) == 6
    for location in locations:
        tracker = TowerCheckpointFuser(locations, stage_id="makomart")
        before, passed = development_frame(169.5), development_frame(170.5)
        for frame in (before, passed):
            assert frame.tower_team is not None
            frame.tower_team.control_presentation = location.team
            reading = frame.detections[0].reading
            assert hasattr(reading, "marker") and reading.marker is not None
            reading.marker.lane_position = location.lane_position
        tracker.step(before, "tower_control", "in_match", False)
        identity, state = tracker.step(passed, "tower_control", "in_match", False)
        assert identity == location.checkpoint and state is not None
        assert getattr(state, location.team)[location.checkpoint - 1].status == "cleared"
