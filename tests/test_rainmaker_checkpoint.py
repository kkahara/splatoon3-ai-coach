"""Development milestones, conservative grouping, and persistent cleared evidence."""

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import RainmakerCheckpointConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT, default_config_path
from splatoon3_ai_coach.vision.game_language_result import with_detected_language
from splatoon3_ai_coach.vision.models import (
    AnalysisIdentity,
    DetectorResult,
    MatchIntroReading,
    RainmakerCheckpointReading,
    RainmakerPossessionCue,
    RainmakerPossessionReading,
    TimerReading,
    VisionFrameResult,
    VisionManifest,
)
from splatoon3_ai_coach.vision.pipeline import refuse_vision_manifest
from splatoon3_ai_coach.vision.rainmaker_checkpoint import RainmakerCheckpointDetector
from splatoon3_ai_coach.vision.rainmaker_checkpoint_observations import (
    checkpoint_observations_by_frame,
    collect_rainmaker_checkpoint_observations,
)
from splatoon3_ai_coach.vision.rainmaker_checkpoint_state import RainmakerCheckpointFuser
from splatoon3_ai_coach.vision.registry import build_detectors

ROOT = PROJECT_ROOT / "calibration/rainmaker"
DEVELOPMENT = PROJECT_ROOT / "analysis/development/rainmaker"


@pytest.mark.parametrize(
    "name,language,team",
    [
        ("en23_105", "en", "ally"),
        ("en20_74", "en", "opponent"),
        ("ja_ally_165.5", "ja", "ally"),
        ("ja_ally_166", "ja", "ally"),
        ("ja_opponent_190", "ja", "opponent"),
        ("ja_opponent_190.5", "ja", "opponent"),
    ],
)
def test_labeled_messages(name: str, language: str, team: str) -> None:
    """English/Japanese explicit messages produce the same team milestone."""
    image = np.zeros((1080, 1920, 3), np.uint8)
    image[250:380, 200:1720] = cv2.imread(
        str(DEVELOPMENT / "checkpoint_development" / f"{name}.png")
    )
    reading, _ = RainmakerCheckpointDetector(
        RainmakerCheckpointConfig(), language=language
    ).detect(image, 42)
    assert reading is not None and reading.team == team
    assert reading.timestamp == 42 and reading.language == language
    assert reading.template_name.startswith(team)


def test_other_messages_and_missing_message_are_negative() -> None:
    """Possession, lead-change, and the early 189 s crop cannot establish a clear."""
    detector = RainmakerCheckpointDetector(RainmakerCheckpointConfig(), language="ja")
    names = [
        "ally_took",
        "opponent_took",
        "ally_lost",
        "rainmaker_reset",
        "rainmaker_defended",
        "count_lead_changed",
        "count_lead_changed_ally",
        "opponent_checkpoint_broke",
    ]
    for name in names:
        image = np.zeros((1080, 1920, 3), np.uint8)
        image[250:380, 300:1600] = cv2.imread(
            str(DEVELOPMENT / "control_messages_ja" / f"{name}_dev.png")
        )
        assert detector.detect(image)[0] is None, name
    image = np.zeros((1080, 1920, 3), np.uint8)
    image[100:400, 260:1660] = cv2.imread(
        str(DEVELOPMENT / "possession_survey/ja_144.png")
    )
    assert detector.detect(image)[0] is None


def test_grouping_and_intervening_message_boundaries() -> None:
    """Repeated positives group once; another state message bounds a display."""
    frames = [_frame(1, "ally"), _frame(1.5, "ally"), _frame(2, None)]
    frames[2].detections.append(
        DetectorResult(
            id="possession",
            detector_name="rainmaker_possession",
            detector_version="test",
            confidence=1,
            reading=RainmakerPossessionReading(
                timestamp=2,
                cue=RainmakerPossessionCue(
                    cue="ally_acquired_rainmaker",
                    language="en",
                    template_score=1,
                    template_name="ally.png",
                ),
            ),
        )
    )
    frames.extend([_frame(2.5, "ally"), _frame(3, "opponent")])
    observations = collect_rainmaker_checkpoint_observations(frames)
    assert len(observations) == 3
    assert observations[0].first_video_time == 1
    assert len(observations[0].samples) == 2
    assert observations[0].samples[0].evidence_id == "cue-1"
    assert observations[0].samples[0].source_frame.source_pts == 60


def test_latching_is_monotonic_and_resets_between_matches() -> None:
    """Only direct messages clear a team; silence holds the first establishing cue."""
    frames = [
        _frame(1, "ally"),
        _frame(1.5, "ally"),
        _frame(2, None),
        _frame(3, "opponent"),
        _frame(5, "ally"),
    ]
    groups = checkpoint_observations_by_frame(frames)
    fuser = RainmakerCheckpointFuser()
    states = [
        fuser.step(frame, "rainmaker", "in_match", groups.get(frame.frame_id))
        for frame in frames
    ]
    assert states[0].ally == "cleared" and states[0].opponent == "unknown"
    assert len(states[0].ally_evidence.samples) == 1  # No future support.
    assert len(states[1].ally_evidence.samples) == 2
    assert states[2] == states[1]
    assert states[-1].ally == states[-1].opponent == "cleared"
    assert states[-1].ally_evidence.first_video_time == 1
    assert fuser.step(_frame(6, None), "rainmaker", "post_match", None) is None
    fresh = fuser.step(_frame(7, None), "rainmaker", "in_match", None)
    assert fresh.ally == fresh.opponent == "unknown"
    assert fuser.step(_frame(8, "ally"), "tower_control", "in_match", None) is None


def test_manifest_refusion_preserves_milestone_provenance() -> None:
    """Serialized raw readings rebuild grouped observations and held cleared facts."""
    config = load_config(default_config_path())
    config.vision.enabled_detectors = ["rainmaker_checkpoint"]
    detectors = build_detectors(with_detected_language(config, "en").vision)
    assert isinstance(detectors[0], RainmakerCheckpointDetector)
    frames = [
        _frame(1, "ally"),
        _frame(1.5, "ally"),
        _frame(2, None),
        _frame(3, "opponent"),
    ]
    frames[0].detections.append(
        DetectorResult(
            id="mode",
            detector_name="match_intro",
            detector_version="test",
            confidence=1,
            reading=MatchIntroReading(
                battle_mode_id="rainmaker", battle_mode_template_score=1
            ),
        )
    )
    manifest = VisionManifest(
        video_identity="dev-video",
        frame_results=frames,
        analysis=AnalysisIdentity(
            analysis_id="test",
            package_version="test",
            vision_config_sha256="test",
            video_identity="dev-video",
            language="en",
        ),
    )
    first = refuse_vision_manifest(manifest, config)
    restored = VisionManifest.model_validate_json(first.model_dump_json())
    second = refuse_vision_manifest(restored, config)
    assert (
        first.rainmaker_checkpoint_observations
        == second.rainmaker_checkpoint_observations
    )
    assert len(first.rainmaker_checkpoint_observations) == 2
    state = second.state_snapshots[2].rainmaker_checkpoint_state
    assert state.ally == "cleared" and state.opponent == "unknown"
    assert state.ally_evidence.first_video_time == 1
    assert [sample.evidence_id for sample in state.ally_evidence.samples] == [
        "cue-1",
        "cue-1.5",
    ]
    assert state.ally_evidence.samples[0].reading.template_name == "ally__dev.png"
    assert second.state_snapshots[2].rainmaker_possession_state == "unknown"


def _frame(time: float, team: str | None) -> VisionFrameResult:
    """Create an in-match source with canonical metadata and an optional direct cue."""
    detections = [
        DetectorResult(
            id=f"timer-{time}",
            detector_name="timer",
            detector_version="test",
            confidence=1,
            reading=TimerReading(display="3:20", seconds_remaining=200),
        )
    ]
    if team is not None:
        detections.append(
            DetectorResult(
                id=f"cue-{time}",
                detector_name="rainmaker_checkpoint",
                detector_version="test",
                confidence=1,
                reading=RainmakerCheckpointReading(
                    team=team,
                    timestamp=time,
                    language="en",
                    template_name=f"{team}__dev.png",
                    template_score=1,
                ),
            )
        )
    return VisionFrameResult(
        frame_id=f"frame-{time}",
        timestamp=time,
        source="cadence",
        source_pts=int(time * 60),
        source_time_base_num=1,
        source_time_base_den=60,
        source_frame_index=int(time * 60),
        detections=detections,
    )
