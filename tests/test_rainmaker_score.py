"""Rainmaker development counts, team mapping and shared progress evidence."""

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.analysis.rainmaker_objective_progress import (
    rainmaker_objective_observations,
)
from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import RainmakerScoreConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    GameStateSnapshot,
    MatchIntroReading,
    TimerReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.rainmaker_score import (
    RainmakerScoreDetector,
    associate_rainmaker_scores,
)
from splatoon3_ai_coach.vision.rainmaker_score_fusion import fuse_rainmaker_score
from splatoon3_ai_coach.vision.state import fuse_game_state
from splatoon3_ai_coach.vision.team_color_calibration import TeamColorCalibrationResult


@pytest.mark.parametrize(
    "name,expected",
    [("ja_172", [58, 55]), ("en_120", [66]), ("en_200", [48, 7]), ("en_190", [])],
)
def test_development_counts(name: str, expected: list[int]) -> None:
    """Read English/Japanese palettes; an open-map frame has no counts."""
    image = np.zeros((1080, 1920, 3), np.uint8)
    image[:280] = cv2.imread(str(PROJECT_ROOT / f"tests/fixtures/rainmaker/{name}.png"))
    reading, _ = RainmakerScoreDetector(RainmakerScoreConfig()).detect(image, 172)
    assert ([p.value for p in reading.pods] if reading else []) == expected


def test_color_binding_fusion_and_progress() -> None:
    """Opposite screen positions map by calibrated team color; 72→68 yields +4."""
    image = np.zeros((1080, 1920, 3), np.uint8)
    image[:280] = cv2.imread(str(PROJECT_ROOT / "tests/fixtures/rainmaker/ja_172.png"))
    reading, _ = RainmakerScoreDetector(RainmakerScoreConfig()).detect(image, 172)
    assert reading is not None
    result = DetectorResult(
        id="rain-cue",
        detector_name="rainmaker_score",
        detector_version="test",
        confidence=0.9,
        reading=reading,
    )
    calibration = TeamColorCalibrationResult(
        ally_h=83,
        opponent_h=2,
        separation_degrees=162,
        calibrated_at=10,
        accepted_frame_ids=("ready-frame",),
    )
    association = associate_rainmaker_scores(result, calibration)
    frame = VisionFrameResult(
        frame_id="f172",
        timestamp=172,
        source="cadence",
        detections=[result],
        rainmaker_score_association=association,
    )
    values = fuse_rainmaker_score(frame, "rainmaker", "in_match", False)
    assert values["rainmaker_ally_remaining"] == 55
    assert values["rainmaker_opponent_remaining"] == 58
    assert associate_rainmaker_scores(result, None) is None
    frame.rainmaker_score_association = None
    assert "rainmaker_ally_remaining" not in fuse_rainmaker_score(
        frame, "rainmaker", "in_match", False
    )
    assert fuse_rainmaker_score(frame, "tower_control", "in_match", False) == {}
    assert fuse_rainmaker_score(frame, "rainmaker", "in_match", True) == {}
    snapshots = [
        GameStateSnapshot(
            timestamp=t,
            match_phase="in_match",
            rainmaker_ally_remaining=v,
            rainmaker_score_evidence_ids=[f"count-{t}"],
        )
        for t, v in [(1, 72), (2, 68), (10, 65), (11, 70)]
    ]
    obs = rainmaker_objective_observations(snapshots, max_gap_seconds=2)
    assert obs[1].normalized_progress_amount == 4
    assert obs[1].progress_state_from_previous == "progress"
    assert obs[1].objective_evidence_ids == ["count-2"]
    assert obs[2].normalized_progress_amount is None
    assert obs[3].normalized_progress_amount is None
    restored = GameStateSnapshot.model_validate_json(
        GameStateSnapshot(timestamp=172, **values).model_dump_json()
    )
    assert restored.rainmaker_ally_remaining == 55
    assert restored.rainmaker_score_association.calibration_frame_ids == ["ready-frame"]
    frame.rainmaker_score_association = association
    frame.detections.extend(
        [
            DetectorResult(
                id="intro",
                detector_name="match_intro",
                detector_version="test",
                confidence=1,
                reading=MatchIntroReading(
                    battle_mode_id="rainmaker", battle_mode_template_score=1
                ),
            ),
            DetectorResult(
                id="timer",
                detector_name="timer",
                detector_version="test",
                confidence=1,
                reading=TimerReading(display="2:50", seconds_remaining=170),
            ),
        ]
    )
    config = load_config(PROJECT_ROOT / "configs/default.yaml")
    fused = fuse_game_state([frame], config.vision.timer, config.vision.state_fusion)[0]
    assert fused.rainmaker_ally_remaining == 55
    assert fused.source_frame.frame_id == "f172"
    assert fused.rainmaker_score_evidence_ids == ["rain-cue"]
