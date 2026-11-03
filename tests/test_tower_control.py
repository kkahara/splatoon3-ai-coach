"""Tower Control direct HUD observation and storage boundaries."""

import cv2
import numpy as np
import pytest
import yaml

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import TowerControlDetectorConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    SourceFrameReference,
    TowerControlReading,
    TowerMarkerReading,
    TowerScorePodReading,
    TowerTeamObservation,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.team_color_calibration import (
    TeamColorCalibrationResult,
)
from splatoon3_ai_coach.vision.tower_control import TowerControlDetector
from splatoon3_ai_coach.vision.tower_fusion import fuse_tower_observation
from splatoon3_ai_coach.vision.tower_team import resolve_tower_team


def _reading() -> TowerControlReading:
    """Build one native Tower HUD reading without team/movement semantics."""
    return TowerControlReading(
        timestamp=12.5,
        score_pods=[
            TowerScorePodReading(
                value=83,
                bbox=(0.50, 0.19, 0.56, 0.26),
                center_x=0.53,
                hue=130,
                hue_concentration=0.9,
                digit_scores=[0.8, 0.85],
                usable=True,
            )
        ],
        marker=TowerMarkerReading(
            center_x=0.61,
            lane_position=0.72,
            bbox=(0.59, 0.12, 0.63, 0.18),
            appearance="colored_presentation",
            hue=12,
            hue_concentration=0.92,
            usable=True,
        ),
    )


def _frame(*, team: str = "unknown") -> VisionFrameResult:
    """Wrap the reading with normal frame/result provenance."""
    result = DetectorResult(
        id="tower-result",
        detector_name="tower_control",
        detector_version="test",
        confidence=0.9,
        reading=_reading(),
    )
    return VisionFrameResult(
        frame_id="frame",
        timestamp=12.5,
        source="cadence",
        source_frame_index=750,
        detections=[result],
        tower_team=TowerTeamObservation(
            result_id=result.id,
            control_presentation=team,
        ),
    )


def test_tower_fusion_copies_only_mode_gated_direct_observations() -> None:
    """Fusion stores this frame and does not hold or reinterpret it."""
    pods, marker, presentation, ids = fuse_tower_observation(
        _frame(team="ally"), "tower_control", "in_match"
    )
    assert [pod.value for pod in pods] == [83]
    assert marker is None
    assert presentation == "unknown" and ids == ["tower-result"]
    assert fuse_tower_observation(_frame(), "splat_zones", "in_match") == (
        [],
        None,
        "unknown",
        [],
    )
    assert fuse_tower_observation(
        _frame(), "tower_control", "in_match", map_overlay_present=True
    ) == ([], None, "unknown", [])


def test_tower_team_mapping_requires_calibrated_marker_hue() -> None:
    """Screen position never assigns a team; established hue calibration may."""
    detection = _frame().detections[0]
    calibration = TeamColorCalibrationResult(
        ally_h=10,
        opponent_h=120,
        separation_degrees=70,
        calibrated_at=8.0,
    )
    resolved = resolve_tower_team(
        detection, calibration, "tower_control", TowerControlDetectorConfig()
    )
    assert resolved.control_presentation == "ally"
    assert (
        resolve_tower_team(
            detection, None, "tower_control", TowerControlDetectorConfig()
        ).control_presentation
        == "unknown"
    )
    reading = _reading()
    assert reading.marker is not None
    neutral = reading.model_copy(
        update={
            "marker": reading.marker.model_copy(update={"appearance": "neutral_like"})
        }
    )
    neutral_detection = detection.model_copy(update={"reading": neutral})
    assert (
        resolve_tower_team(
            neutral_detection,
            calibration,
            "tower_control",
            TowerControlDetectorConfig(),
        ).control_presentation
        == "unknown"
    )


def test_tower_detector_reports_no_evidence_on_blank_frame() -> None:
    """An absent HUD stays absent instead of becoming neutral/returning."""
    detector = TowerControlDetector(
        TowerControlDetectorConfig(template_dir=PROJECT_ROOT / "calibration/templates")
    )
    reading, confidence = detector.detect(np.zeros((1080, 1920, 3), dtype=np.uint8))
    assert reading is None and confidence == 0


@pytest.mark.parametrize(
    ("fixture", "expected_x"), [("museum_30.png", 0.5), ("makomart_110.png", 0.535)]
)
def test_marker_uses_tower_core_in_development_frames(
    fixture: str, expected_x: float
) -> None:
    """A checkpoint/background cue must not displace the visible marker."""
    crop = cv2.imread(str(PROJECT_ROOT / "tests/fixtures/tower_control" / fixture))
    assert crop is not None
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    image[105:205, 450:1470] = crop
    detector = TowerControlDetector(
        TowerControlDetectorConfig(template_dir=PROJECT_ROOT / "calibration/templates")
    )
    marker, _ = detector._marker(image)
    assert marker is not None
    assert marker.center_x == pytest.approx(expected_x, abs=0.006)


def test_tower_models_keep_canonical_frame_provenance() -> None:
    """Snapshot source references use the existing video timeline contract."""
    source = SourceFrameReference(
        frame_id="frame", source_frame_index=750, timestamp=12.5
    )
    assert source.timestamp == _reading().timestamp


def test_tower_template_path_resolves_from_config_directory(tmp_path) -> None:
    """Tower digit templates follow the standard config-relative path rule."""
    config_path = tmp_path / "config.yaml"
    base = yaml.safe_load((PROJECT_ROOT / "configs/default.yaml").read_text())
    base["vision"]["tower_control"]["template_dir"] = "tower-templates"
    config_path.write_text(yaml.safe_dump(base))
    config = load_config(config_path)
    assert config.vision.tower_control.template_dir == tmp_path / "tower-templates"
