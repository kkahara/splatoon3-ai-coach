"""Explicit Tower control messages and their persistent factual state."""

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.models import TowerControlDetectorConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    TowerControlCue,
    TowerControlReading,
    TowerMarkerReading,
    TowerTeamObservation,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.tower_control import TowerControlDetector
from splatoon3_ai_coach.vision.tower_control_messages import TowerControlMessageDetector
from splatoon3_ai_coach.vision.tower_control_state import TowerControlMessageFuser
from splatoon3_ai_coach.vision.tower_fusion import fuse_tower_observation

CONTROL_DIR = PROJECT_ROOT / "calibration/tower_control/en/control"
DEVELOPMENT = PROJECT_ROOT / "analysis/development/tower_control"
DEVELOPMENT_CONTROL = DEVELOPMENT / "en/control"


def frame(time: float, cue: TowerControlCue | None = None) -> VisionFrameResult:
    """Wrap one direct cue with canonical frame and detector identities."""
    reading = TowerControlReading(timestamp=time, control_cue=cue)
    return VisionFrameResult(
        frame_id=f"frame-{time}",
        timestamp=time,
        source="cadence",
        source_frame_index=100,
        detections=[
            DetectorResult(
                id=f"result-{time}",
                detector_name="tower_control",
                detector_version="test",
                confidence=0.99,
                reading=reading,
            )
        ],
    )


@pytest.mark.parametrize(
    ("cue_type", "team", "expected"),
    [
        ("ally_took", "ally", "ally_control"),
        ("opponent_took", "opponent", "opponent_control"),
        ("ally_lost", "ally", "returning"),
    ],
)
def test_four_message_types_set_state(cue_type: str, team: str, expected: str) -> None:
    """Each explicit cue sets the intended state with direct provenance."""
    cue = TowerControlCue.model_validate(
        {"cue_type": cue_type, "team": team, "template_score": 0.99}
    )
    fuser = TowerControlMessageFuser()
    state, evidence = fuser.step(frame(5, cue), "tower_control", "in_match")
    assert state == expected and evidence is not None
    assert evidence.video_time == 5 and evidence.source_frame.frame_id == "frame-5"
    assert evidence.detector_evidence_id == "result-5" and evidence.cue == cue


def test_cue_sequence_is_persistent_and_does_not_use_marker_or_score() -> None:
    """A loss over a map followed by opponent acquisition updates only from cues."""
    fuser = TowerControlMessageFuser()
    took = TowerControlCue(cue_type="ally_took", team="ally", template_score=0.99)
    lost = TowerControlCue(cue_type="ally_lost", team="ally", template_score=0.99)
    opponent = TowerControlCue(
        cue_type="opponent_took", team="opponent", template_score=0.99
    )
    assert fuser.step(frame(1, took), "tower_control", "in_match")[0] == "ally_control"
    state, held = fuser.step(frame(2), "tower_control", "in_match")
    assert state == "ally_control" and held is not None and held.video_time == 1
    assert fuser.step(frame(3, lost), "tower_control", "in_match")[0] == "returning"
    assert (
        fuser.step(frame(4, opponent), "tower_control", "in_match")[0]
        == "opponent_control"
    )
    assert fuser.step(frame(5), "tower_control", "post_match") == ("unknown", None)


def test_historical_marker_cannot_enter_active_tower_state() -> None:
    """Legacy marker and team metadata are ignored when replaying old frames."""
    old = frame(1)
    reading = old.detections[0].reading
    assert isinstance(reading, TowerControlReading)
    reading.marker = TowerMarkerReading(
        center_x=0.5, lane_position=0.5, bbox=(0.4, 0.1, 0.6, 0.2), usable=True
    )
    old.tower_team = TowerTeamObservation(
        result_id="result-1", control_presentation="ally"
    )
    assert fuse_tower_observation(old, "tower_control", "in_match") == (
        [],
        None,
        "unknown",
        [],
    )
    assert TowerControlMessageFuser().step(old, "tower_control", "in_match") == (
        "unknown",
        None,
    )


def test_score_observation_is_independent_of_message_detection() -> None:
    """Enabling message templates must not alter the observed Tower score pods."""
    crop = cv2.imread(
        str(DEVELOPMENT / "makomart_candidate_167_5.png")
    )
    assert crop is not None
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    image[95:315, 450:1470] = crop
    base = TowerControlDetectorConfig(template_dir=PROJECT_ROOT / "calibration/templates")
    enabled = base.model_copy(
        update={"checkpoint_cue_template_dir": CONTROL_DIR.parent.parent}
    )
    before, _ = TowerControlDetector(base, language="en").detect(image, 167.5)
    after, _ = TowerControlDetector(enabled, language="en").detect(image, 167.5)
    assert before is not None and after is not None
    assert before.score_pods and before.score_pods == after.score_pods


@pytest.mark.parametrize(
    ("image_name", "cue_type"),
    [
        ("ally_took_dev.png", "ally_took"),
        ("ally_lost_dev.png", "ally_lost"),
        ("opponent_took_dev.png", "opponent_took"),
    ],
)
def test_development_message_examples(image_name: str, cue_type: str) -> None:
    """Whole-message detection returns the known cue for each development image."""
    crop = cv2.imread(str(DEVELOPMENT_CONTROL / image_name))
    assert crop is not None
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    image[250:380, 300:1600] = crop
    detector = TowerControlMessageDetector(CONTROL_DIR)
    cue = detector.detect(image)
    assert cue is not None and cue.cue_type == cue_type
    tower = TowerControlDetector(
        TowerControlDetectorConfig(
            template_dir=PROJECT_ROOT / "calibration/templates",
            checkpoint_cue_template_dir=PROJECT_ROOT / "calibration/tower_control",
        ),
        language="en",
    )
    reading, _ = tower.detect(image, 12)
    assert reading is not None and reading.control_cue is not None
    assert reading.control_cue.cue_type == cue_type and reading.marker is None


def test_recorded_hammerhead_cue_sequence() -> None:
    """Real development message crops drive the expected control/loss sequence."""
    detector = TowerControlMessageDetector(CONTROL_DIR)
    fuser = TowerControlMessageFuser()
    examples = [
        (58.5, "ally_took_dev.png", "ally_control"),
        (67.5, "ally_lost_dev.png", "returning"),
        (76.5, "sequence_76_5.png", "ally_control"),
        (103.5, "sequence_103_5.png", "opponent_control"),
    ]
    for time, name, expected in examples:
        crop = cv2.imread(str(DEVELOPMENT_CONTROL / name))
        assert crop is not None
        image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        image[250:380, 300:1600] = crop
        cue = detector.detect(image)
        assert cue is not None
        state, evidence = fuser.step(frame(time, cue), "tower_control", "in_match")
        assert state == expected and evidence is not None
        assert evidence.video_time == time


@pytest.mark.parametrize(
    "name", ["at_checkpoint_development.png", "passed_development.png"]
)
def test_checkpoint_messages_do_not_become_control_messages(name: str) -> None:
    """Related objective text must not be mistaken for acquisition or loss."""
    crop = cv2.imread(str(DEVELOPMENT / "en" / name))
    assert crop is not None
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    image[250:380, 300:1600] = crop
    assert TowerControlMessageDetector(CONTROL_DIR).detect(image) is None


def test_invalid_or_ambiguous_cues_never_choose_a_team() -> None:
    """Conflicting sources or a team/cue mismatch leave state unknown."""
    fuser = TowerControlMessageFuser()
    mismatch = TowerControlCue(cue_type="ally_took", team="opponent", template_score=1)
    assert fuser.step(frame(1, mismatch), "tower_control", "in_match") == (
        "unknown",
        None,
    )
    ally = TowerControlCue(cue_type="ally_took", team="ally", template_score=1)
    opponent = TowerControlCue(
        cue_type="opponent_took", team="opponent", template_score=1
    )
    ambiguous = frame(2, ally)
    ambiguous.detections.extend(frame(2, opponent).detections)
    assert fuser.step(ambiguous, "tower_control", "in_match") == ("unknown", None)


def test_raw_opponent_loss_requires_grouped_observation() -> None:
    """A raw cue alone cannot bypass the grouped observation contract."""
    fuser = TowerControlMessageFuser()
    took = TowerControlCue(cue_type="opponent_took", team="opponent", template_score=1)
    ignored = TowerControlCue(cue_type="opponent_lost", team="opponent", template_score=1)
    fuser.step(frame(1, took), "tower_control", "in_match")
    state, evidence = fuser.step(frame(2, ignored), "tower_control", "in_match")
    assert (
        state == "opponent_control" and evidence is not None and evidence.video_time == 1
    )
    crop = cv2.imread(str(DEVELOPMENT_CONTROL / "opponent_lost_dev.png"))
    assert crop is not None
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    image[250:380, 300:1600] = crop
    cue = TowerControlMessageDetector(CONTROL_DIR, language="en").detect(image)
    assert cue is not None and cue.cue_type == "opponent_lost" and cue.language == "en"
