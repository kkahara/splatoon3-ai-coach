"""Direct opponent-lost cue observations and sparse grouping."""

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import TowerControlDetectorConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.models import (
    AnalysisIdentity,
    DetectorResult,
    MatchIntroReading,
    TimerReading,
    TowerControlCue,
    TowerControlReading,
    TowerMarkerReading,
    TowerScorePodReading,
    TowerTeamObservation,
    VisionFrameResult,
    VisionManifest,
)
from splatoon3_ai_coach.vision.pipeline import refuse_vision_manifest
from splatoon3_ai_coach.vision.state import fuse_game_state
from splatoon3_ai_coach.vision.tower_control import TowerControlDetector
from splatoon3_ai_coach.vision.tower_control_state import TowerControlMessageFuser
from splatoon3_ai_coach.vision.tower_opponent_lost import (
    collect_opponent_lost_observations,
)

ROOT = PROJECT_ROOT / "calibration/tower_control"
DEVELOPMENT = PROJECT_ROOT / "analysis/development/tower_control"


@pytest.mark.parametrize(
    ("language", "filename"),
    [
        ("en", "opponent_lost_dev.png"),
        ("en", "opponent_lost_urchin_dev.png"),
        ("ja", "opponent_lost_dev.png"),
        ("ja", "opponent_lost_mincemeat_dev.png"),
    ],
)
def test_localized_development_examples(language: str, filename: str) -> None:
    """Cue survives the actual Tower detector with its configured language."""
    crop = cv2.imread(str(DEVELOPMENT / language / "control" / filename))
    assert crop is not None
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    image[250:380, 300:1600] = crop
    detector = TowerControlDetector(
        TowerControlDetectorConfig(
            template_dir=PROJECT_ROOT / "calibration/templates",
            checkpoint_cue_template_dir=ROOT,
        ),
        language=language,
    )
    reading, _ = detector.detect(image, 186.5)
    assert reading is not None and reading.control_cue is not None
    assert reading.control_cue.cue_type == "opponent_lost"
    assert reading.control_cue.language == language
    assert reading.timestamp == 186.5
    source = VisionFrameResult(
        frame_id=f"{language}-frame",
        timestamp=186.5,
        source="cadence",
        detections=[
            DetectorResult(
                id=f"{language}-cue",
                detector_name="tower_control",
                detector_version="test",
                confidence=reading.control_cue.template_score,
                reading=reading,
            )
        ],
    )
    observations = collect_opponent_lost_observations([source])
    assert len(observations) == 1
    assert observations[0].cue == "opponent_lost_tower"
    assert observations[0].first_video_time == 186.5
    assert observations[0].samples[0].evidence_id == f"{language}-cue"
    assert observations[0].samples[0].language == language


@pytest.mark.parametrize(
    "filename", ["other_tower_message_dev.png", "near_miss_negative_dev.png"]
)
def test_japanese_other_messages_are_not_opponent_loss(filename: str) -> None:
    """Reject other messages, including ガチヤグラを もどされた！."""
    crop = cv2.imread(str(DEVELOPMENT / "ja/control" / filename))
    assert crop is not None
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    image[250:380, 300:1600] = crop
    detector = TowerControlDetector(
        TowerControlDetectorConfig(
            template_dir=PROJECT_ROOT / "calibration/templates",
            checkpoint_cue_template_dir=ROOT,
        ),
        language="ja",
    )
    reading, _ = detector.detect(image, 1)
    assert (
        reading is None
        or reading.control_cue is None
        or (reading.control_cue.cue_type != "opponent_lost")
    )


def _frame(time: float, *, cue: str = "opponent_lost") -> VisionFrameResult:
    """Create one direct cue result with source provenance."""
    observation = TowerControlCue.model_validate(
        {"cue_type": cue, "team": "opponent", "template_score": 0.99, "language": "ja"}
    )
    return VisionFrameResult(
        frame_id=f"frame-{time}",
        timestamp=time,
        source="cadence",
        source_frame_index=10,
        detections=[
            DetectorResult(
                id=f"cue-{time}",
                detector_name="tower_control",
                detector_version="test",
                confidence=0.99,
                reading=TowerControlReading(timestamp=time, control_cue=observation),
            )
        ],
    )


def test_adjacent_positive_frames_are_one_observation_with_all_provenance() -> None:
    """Repeated frames group once while separate messages retain separate records."""
    frames = [_frame(1), _frame(1.5), _frame(2.5), _frame(5)]
    observations = collect_opponent_lost_observations(frames)
    assert len(observations) == 2
    first = observations[0]
    assert first.cue == "opponent_lost_tower"
    assert (first.first_video_time, first.last_video_time) == (1, 2.5)
    assert [sample.evidence_id for sample in first.samples] == [
        "cue-1",
        "cue-1.5",
        "cue-2.5",
    ]
    assert first.samples[0].source_frame.frame_id == "frame-1"
    assert first.samples[0].source_frame.source_frame_index == 10
    assert first.samples[0].language == "ja"
    assert first.max_gap_seconds == 1.0


def test_observations_survive_manifest_serialization() -> None:
    """The persisted observation retains source identities and language."""
    frames = [_frame(1), _frame(1.5)]
    manifest = VisionManifest(
        analysis=AnalysisIdentity(
            analysis_id="test",
            package_version="test",
            vision_config_sha256="v",
            video_identity="video",
            language="en",
        ),
        video_identity="video",
        frame_results=frames,
        tower_opponent_lost_observations=collect_opponent_lost_observations(frames),
    )
    restored = VisionManifest.model_validate_json(manifest.model_dump_json())
    samples = restored.tower_opponent_lost_observations[0].samples
    assert [sample.evidence_id for sample in samples] == ["cue-1", "cue-1.5"]
    assert [sample.language for sample in samples] == ["ja", "ja"]
    config = load_config(PROJECT_ROOT / "configs/default.yaml")
    refused = refuse_vision_manifest(restored, config)
    assert len(refused.tower_opponent_lost_observations) == 1
    assert refused.tower_opponent_lost_observations[0].first_video_time == 1


def test_missing_or_inconsistent_cues_are_not_fabricated() -> None:
    """Other messages, mismatched timestamps, and duplicate candidates are skipped."""
    other = _frame(1, cue="opponent_took")
    stale = _frame(2)
    stale.detections[0].reading.timestamp = 3
    ambiguous = _frame(4)
    ambiguous.detections.extend(_frame(4).detections)
    assert collect_opponent_lost_observations([other, stale, ambiguous]) == []
    with pytest.raises(ValueError):
        collect_opponent_lost_observations([], max_gap_seconds=0)


def test_other_control_message_bounds_two_displays() -> None:
    """An intervening direct message prevents two lost cues from merging."""
    frames = [_frame(1), _frame(1.5, cue="opponent_took"), _frame(2)]
    observations = collect_opponent_lost_observations(frames)
    assert [item.first_video_time for item in observations] == [1, 2]


def test_score_and_old_marker_are_not_opponent_loss_evidence() -> None:
    """Neither score nor legacy marker/team data can create a cue observation."""
    item = _frame(1)
    item.tower_team = TowerTeamObservation(
        result_id="cue-1", control_presentation="opponent"
    )
    reading = item.detections[0].reading
    assert isinstance(reading, TowerControlReading)
    reading.control_cue = None
    reading.marker = TowerMarkerReading(
        center_x=0.5, lane_position=0.5, bbox=(0.4, 0.1, 0.6, 0.2), usable=True
    )
    reading.score_pods = [
        TowerScorePodReading(
            value=68, bbox=(0.4, 0.1, 0.5, 0.2), center_x=0.45, usable=True
        )
    ]
    assert collect_opponent_lost_observations([item]) == []
    assert TowerControlMessageFuser().step(item, "tower_control", "in_match") == (
        "unknown",
        None,
    )


def test_raw_opponent_loss_alone_does_not_change_state() -> None:
    """The fuser requires the grouped observation, not an ungrouped reading."""
    fuser = TowerControlMessageFuser()
    assert fuser.step(_frame(1), "tower_control", "in_match") == ("unknown", None)


def test_grouped_cue_establishes_returning_once_with_incremental_provenance() -> None:
    """The first observed frame sets state; later positives only add support."""
    frames = [_frame(1), _frame(1.5)]
    observation = collect_opponent_lost_observations(frames)[0]
    first = observation.model_copy(
        update={"samples": observation.samples[:1], "last_video_time": 1}
    )
    fuser = TowerControlMessageFuser()
    state, evidence = fuser.step(
        frames[0], "tower_control", "in_match", opponent_lost_observation=first
    )
    assert state == "returning" and evidence is not None
    assert evidence.video_time == 1 and evidence.detector_evidence_id == "cue-1"
    assert evidence.opponent_lost_observation is not None
    assert [s.evidence_id for s in evidence.opponent_lost_observation.samples] == [
        "cue-1"
    ]
    state, evidence = fuser.step(
        frames[1], "tower_control", "in_match", opponent_lost_observation=observation
    )
    assert state == "returning" and evidence is not None and evidence.video_time == 1
    assert evidence.opponent_lost_observation is not None
    assert [s.evidence_id for s in evidence.opponent_lost_observation.samples] == [
        "cue-1",
        "cue-1.5",
    ]


def test_snapshot_fusion_uses_only_observed_cue_and_prior_support() -> None:
    """Snapshots change once and never contain future frame provenance."""
    intro = VisionFrameResult(
        frame_id="intro",
        timestamp=0,
        source="cadence",
        detections=[
            DetectorResult(
                id="intro-result",
                detector_name="match_intro",
                detector_version="test",
                confidence=1,
                reading=MatchIntroReading(
                    stage_id="makomart",
                    battle_mode_id="tower_control",
                    stage_template_score=0.99,
                    battle_mode_template_score=0.99,
                ),
            )
        ],
    )
    before = _frame(0.5, cue="opponent_took")
    first, repeated = _frame(1), _frame(1.5)
    after = _frame(2)
    after.detections.clear()
    frames = [intro, before, first, repeated, after]
    for index, frame in enumerate(frames):
        frame.detections.append(
            DetectorResult(
                id=f"timer-{index}",
                detector_name="timer",
                detector_version="test",
                confidence=0.99,
                reading=TimerReading(display="2:05", seconds_remaining=125),
            )
        )
    config = load_config(PROJECT_ROOT / "configs/default.yaml")
    snapshots = fuse_game_state(frames, config.vision.timer, config.vision.state_fusion)
    assert [s.tower_control_state for s in snapshots[1:]] == [
        "opponent_control",
        "returning",
        "returning",
        "returning",
    ]
    first_evidence, repeated_evidence = (
        snapshots[2].tower_control_evidence,
        snapshots[3].tower_control_evidence,
    )
    assert first_evidence is not None and repeated_evidence is not None
    assert first_evidence.video_time == repeated_evidence.video_time == 1
    assert first_evidence.opponent_lost_observation is not None
    assert repeated_evidence.opponent_lost_observation is not None
    assert [s.evidence_id for s in first_evidence.opponent_lost_observation.samples] == [
        "cue-1"
    ]
    assert [
        s.evidence_id for s in repeated_evidence.opponent_lost_observation.samples
    ] == ["cue-1", "cue-1.5"]
