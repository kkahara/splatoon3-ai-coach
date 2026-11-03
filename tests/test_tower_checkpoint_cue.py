"""Development checkpoint-passed text cue observations."""

import cv2
import numpy as np

from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.tower_checkpoint_cue import CheckpointPassedDetector
from splatoon3_ai_coach.vision.tower_checkpoint_state import (
    CheckpointObservation,
    build_checkpoint_state,
)

CALIBRATION = PROJECT_ROOT / "calibration/tower_control/en"
DEVELOPMENT = PROJECT_ROOT / "analysis/development/tower_control/en"


def frame(name: str) -> np.ndarray:
    """Restore the original position of a development message crop."""
    crop = cv2.imread(str(DEVELOPMENT / name))
    assert crop is not None
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    image[250:380, 300:1600] = crop
    return image


def test_positive_passed_cue_and_at_checkpoint_negative() -> None:
    """The cue template must distinguish this development at-checkpoint banner."""
    detector = CheckpointPassedDetector(CALIBRATION / "passed_checkpoint_template.png")
    reading, confidence = detector.detect(frame("passed_development.png"), 170.5)
    assert reading is not None and reading.timestamp == 170.5
    assert confidence >= 0.93
    assert detector.detect(frame("at_checkpoint_development.png"), 167.5) == (None, 0.0)
    assert detector.detect(np.zeros((1080, 1920, 3), dtype=np.uint8)) == (None, 0.0)


def test_detected_cue_supports_operator_attributed_clear() -> None:
    """The detected cue clears only after a caller supplies team and checkpoint."""
    detector = CheckpointPassedDetector(CALIBRATION / "passed_checkpoint_template.png")
    reading, _ = detector.detect(frame("passed_development.png"), 170.5)
    assert reading is not None
    state = build_checkpoint_state(
        [
            CheckpointObservation(
                video_time=reading.timestamp,
                frame_id="development-frame-170.5",
                marker_evidence_id="operator-marker-attribution",
                team="ally",
                checkpoint=1,
                identity_evidence_ids=["operator-checkpoint-attribution"],
                passed_evidence_id="development-passed-cue",
            )
        ]
    )
    assert state.ally[0].status == "cleared"
    assert state.ally[0].support[0].video_time == 170.5
