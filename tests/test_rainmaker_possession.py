"""Development checks for explicit Rainmaker possession messages and state."""


import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import RainmakerPossessionConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    MapOverlayReading,
    MatchIntroReading,
    RainmakerPossessionCue,
    RainmakerPossessionReading,
    RainmakerScoreReading,
    TimerReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.rainmaker_possession import RainmakerPossessionDetector
from splatoon3_ai_coach.vision.rainmaker_possession_observations import (
    collect_rainmaker_possession_observations,
)
from splatoon3_ai_coach.vision.rainmaker_possession_state import (
    RainmakerPossessionFuser,
)
from splatoon3_ai_coach.vision.state import fuse_game_state

_CUES = (
    "ally_acquired_rainmaker",
    "opponent_acquired_rainmaker",
    "ally_lost_rainmaker",
    "opponent_lost_rainmaker",
    "rainmaker_reset",
)
DEVELOPMENT = PROJECT_ROOT / "analysis/development/rainmaker"


@pytest.mark.parametrize("language", ["en", "ja"])
@pytest.mark.parametrize("cue", _CUES)
def test_survey_templates_detect_labeled_cues(language: str, cue: str) -> None:
    """Each language template reads as the same language-neutral cue."""
    image = _source_frame(language, cue)
    detector = RainmakerPossessionDetector(
        RainmakerPossessionConfig(), language=language
    )
    reading, confidence = detector.detect(image, timestamp=10)
    assert reading is not None
    assert reading.cue.cue == cue
    assert reading.cue.language == language
    assert reading.cue.template_name.startswith(cue)
    assert confidence >= detector.threshold


def test_japanese_144_is_negative_and_does_not_read_gold_lane_icon() -> None:
    """The operator-labeled 2:24 crop has no state message or cue evidence."""
    image = _survey_frame("ja_144.png")
    reading, _ = RainmakerPossessionDetector(
        RainmakerPossessionConfig(), language="ja"
    ).detect(image, timestamp=144)
    assert reading is None


def test_repeated_frames_group_once_and_keep_all_direct_provenance() -> None:
    """One displayed message groups samples and retains its first time."""
    frames = [
        _frame("a1", 5, "ally_acquired_rainmaker"),
        _frame("a2", 5.5, "ally_acquired_rainmaker"),
        _frame("a3", 6, "ally_acquired_rainmaker"),
    ]
    observations = collect_rainmaker_possession_observations(frames)
    assert len(observations) == 1
    assert observations[0].first_video_time == 5
    assert [sample.evidence_id for sample in observations[0].samples] == [
        "e-a1", "e-a2", "e-a3"
    ]
    assert [sample.cue.language for sample in observations[0].samples] == [
        "en", "en", "en"
    ]


@pytest.mark.parametrize(
    ("cue", "expected"),
    [
        ("ally_acquired_rainmaker", "ally_possession"),
        ("opponent_acquired_rainmaker", "opponent_possession"),
        ("ally_lost_rainmaker", "neutral"),
        ("opponent_lost_rainmaker", "neutral"),
        ("rainmaker_reset", "neutral"),
    ],
)
def test_direct_cues_establish_state_without_prior_sequence(
    cue: str, expected: str
) -> None:
    """Acquisition and loss cues directly correct unknown state."""
    frame = _frame("direct", 9, cue)
    observation = collect_rainmaker_possession_observations([frame])[0]
    fuser = RainmakerPossessionFuser()
    state, evidence = fuser.step(
        frame, "rainmaker", "in_match", observation
    )
    assert state == expected
    assert evidence is not None
    assert evidence.video_time == 9
    assert evidence.detector_evidence_ids == ["e-direct"]
    assert evidence.source_frame.frame_id == "direct"


def test_acquisition_replaces_opposite_state_and_missing_frames_hold() -> None:
    """A later explicit acquisition corrects stale state; silence holds it."""
    fuser = RainmakerPossessionFuser()
    _apply(fuser, _frame("opp", 1, "opponent_acquired_rainmaker"))
    assert fuser.state == "opponent_possession"
    _apply(fuser, _frame("ally", 2, "ally_acquired_rainmaker"))
    assert fuser.state == "ally_possession"
    held_evidence = fuser.evidence
    map_overlay = DetectorResult(
        id="map-open",
        detector_name="map_overlay",
        detector_version="test",
        confidence=1.0,
        reading=MapOverlayReading(present=True),
    )
    state, evidence = fuser.step(
        VisionFrameResult(
            frame_id="map", timestamp=3, source="cadence", detections=[map_overlay]
        ),
        "rainmaker",
        "in_match",
    )
    assert state == "ally_possession"
    assert evidence == held_evidence


def test_observed_opening_to_live_match_initializes_neutral_only_once() -> None:
    """A positively observed normal opening anchors neutral; a late start does not."""
    fuser = RainmakerPossessionFuser()
    _apply(fuser, _plain_frame("opening", 1), phase="opening_countdown")
    state, evidence = _apply(
        fuser, _plain_frame("go", 2), phase="in_match"
    )
    assert state == "neutral"
    assert evidence is not None
    assert evidence.initialization == "observed_match_start"
    late = RainmakerPossessionFuser()
    state, evidence = _apply(late, _plain_frame("late", 25), phase="in_match")
    assert state == "unknown"
    assert evidence is None


def test_a_new_transition_separates_observation_groups_and_refusion_provenance() -> None:
    """Cue switches split groups and persisted state evidence survives roundtrip."""
    frames = [
        _frame("one", 1, "ally_acquired_rainmaker"),
        _frame("two", 1.5, "ally_acquired_rainmaker"),
        _frame("three", 2, "opponent_lost_rainmaker"),
    ]
    observations = collect_rainmaker_possession_observations(frames)
    assert [item.cue for item in observations] == [
        "ally_acquired_rainmaker", "opponent_lost_rainmaker"
    ]
    fuser = RainmakerPossessionFuser()
    for frame in frames:
        prefix = next(
            observation.model_copy(update={
                "samples": observation.samples[:index + 1],
                "last_video_time": sample.video_time,
            })
            for observation in observations
            for index, sample in enumerate(observation.samples)
            if sample.source_frame.frame_id == frame.frame_id
        )
        fuser.step(frame, "rainmaker", "in_match", prefix)
    assert fuser.state == "neutral"
    assert fuser.evidence is not None
    restored = type(fuser.evidence).model_validate_json(fuser.evidence.model_dump_json())
    assert restored == fuser.evidence
    assert restored.cue_observation.cue == "opponent_lost_rainmaker"


def test_count_observations_do_not_initialize_or_change_possession() -> None:
    """Rainmaker count evidence is independent of the possession fuser."""
    score = DetectorResult(
        id="count-change",
        detector_name="rainmaker_score",
        detector_version="test",
        confidence=0.99,
        reading=RainmakerScoreReading(timestamp=3, pods=[]),
    )
    frame = VisionFrameResult(
        frame_id="count-only", timestamp=3, source="cadence", detections=[score]
    )
    fuser = RainmakerPossessionFuser()
    state, evidence = fuser.step(frame, "rainmaker", "in_match")
    assert state == "unknown"
    assert evidence is None


def test_state_fusion_and_refusion_retain_cue_provenance() -> None:
    """Stored raw cue frames reproduce the same factual state and evidence."""
    config = load_config(PROJECT_ROOT / "configs/default.yaml").vision
    frames = [
        _timer_frame("opening", 0, "5:00", 300, intro=True),
        _timer_frame("taken", 1, "4:59", 299, intro=False, cue="ally_acquired_rainmaker"),
        _timer_frame("held", 1.5, "4:58", 298, intro=False),
    ]
    first = fuse_game_state(frames, config.timer, config.state_fusion)
    second = fuse_game_state(frames, config.timer, config.state_fusion)
    assert [item.rainmaker_possession_state for item in first] == [
        "unknown", "ally_possession", "ally_possession"
    ]
    assert first[1].rainmaker_possession_evidence is not None
    assert first[1].rainmaker_possession_evidence.source_frame.frame_id == "taken"
    assert (
        first[1].rainmaker_possession_evidence
        == second[1].rainmaker_possession_evidence
    )


def _apply(
    fuser: RainmakerPossessionFuser,
    frame: VisionFrameResult,
    phase: str = "in_match",
) -> tuple[str, object]:
    """Apply a one-frame grouped cue to the mode-specific state fuser."""
    grouped = collect_rainmaker_possession_observations([frame])
    return fuser.step(frame, "rainmaker", phase, grouped[0] if grouped else None)


def _frame(frame_id: str, timestamp: float, cue: str) -> VisionFrameResult:
    """Construct a direct semantic cue sample for fusion tests."""
    reading = RainmakerPossessionReading(
        cue=RainmakerPossessionCue(
            cue=cue,
            template_score=0.99,
            language="en",
            template_name=f"{cue}__en_dev.png",
        ),
        timestamp=timestamp,
    )
    result = DetectorResult(
        id=f"e-{frame_id}", detector_name="rainmaker_possession",
        detector_version="test", confidence=0.99, reading=reading,
    )
    return VisionFrameResult(
        frame_id=frame_id, timestamp=timestamp, source="cadence", detections=[result]
    )


def _plain_frame(frame_id: str, timestamp: float) -> VisionFrameResult:
    """Construct a cue-free frame for match-boundary tests."""
    return VisionFrameResult(frame_id=frame_id, timestamp=timestamp, source="cadence")


def _timer_frame(
    frame_id: str,
    timestamp: float,
    display: str,
    seconds: int,
    *,
    intro: bool,
    cue: str | None = None,
) -> VisionFrameResult:
    """Construct timer/mode evidence plus an optional direct possession cue."""
    readings = [
        DetectorResult(
            id=f"timer-{frame_id}", detector_name="timer", detector_version="test",
            confidence=0.99,
            reading=TimerReading(display=display, seconds_remaining=seconds),
        )
    ]
    if intro:
        readings.append(DetectorResult(
            id=f"mode-{frame_id}", detector_name="match_intro", detector_version="test",
            confidence=0.99,
            reading=MatchIntroReading(
                battle_mode_id="rainmaker", battle_mode_template_score=0.99
            ),
        ))
    frame = VisionFrameResult(
        frame_id=frame_id, timestamp=timestamp, source="cadence", detections=readings
    )
    if cue is not None:
        frame.detections.append(_frame(frame_id, timestamp, cue).detections[0])
    return frame


def _source_frame(language: str, cue: str) -> np.ndarray:
    """Place one surveyed native crop in its original HUD coordinates."""
    source = {
        "ja": {
            "ally_acquired_rainmaker": ("control_messages_ja/ally_took_dev.png", True),
            "opponent_acquired_rainmaker": (
                "control_messages_ja/opponent_took_dev.png",
                True,
            ),
            "ally_lost_rainmaker": ("control_messages_ja/ally_lost_dev.png", True),
            "opponent_lost_rainmaker": (
                "control_messages_ja/rainmaker_defended_dev.png",
                True,
            ),
            "rainmaker_reset": ("control_messages_ja/rainmaker_reset_dev.png", True),
        },
        "en": {
            "ally_acquired_rainmaker": ("possession_survey/en23_115.png", False),
            "opponent_acquired_rainmaker": ("possession_survey/en20_238.png", False),
            "ally_lost_rainmaker": ("possession_survey/en23_125.png", False),
            "opponent_lost_rainmaker": ("possession_survey/en23_205.png", False),
            "rainmaker_reset": ("possession_survey/en23_225.png", False),
        },
    }
    relative_path, message_roi = source[language][cue]
    return _load_crop(relative_path, message_roi)


def _load_crop(relative_path: str, message_roi: bool) -> np.ndarray:
    """Embed a development crop into a synthetic 1080p source frame."""
    path = DEVELOPMENT / relative_path
    crop = cv2.imread(str(path))
    assert crop is not None, path
    image = np.zeros((1080, 1920, 3), np.uint8)
    if message_roi:
        image[250:380, 300:1600] = crop
    else:
        image[100:400, 260:1660] = crop
    return image


def _survey_frame(name: str) -> np.ndarray:
    """Embed the 1400×300 operator-review crop at its source position."""
    path = DEVELOPMENT / "possession_survey" / name
    crop = cv2.imread(str(path))
    assert crop is not None
    image = np.zeros((1080, 1920, 3), np.uint8)
    image[100:400, 260:1660] = crop
    return image
