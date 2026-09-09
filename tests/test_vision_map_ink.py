"""Match intro identity + map ink observations (not GameEvents)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.models import (
    MapInkAnalyzerConfig,
    MatchIntroDetectorConfig,
    VisionLanguage,
)
from splatoon3_ai_coach.vision.map_ink import (
    MapInkClassifier,
    MapObservation,
    analyze_map_ink,
)
from splatoon3_ai_coach.vision.match_intro import (
    MatchIdentityTracker,
    MatchIntroDetector,
    MatchIntroReading,
)
from splatoon3_ai_coach.vision.models import GameEventType
from splatoon3_ai_coach.vision.stage_maps import (
    StageMapGeometry,
    StageMapRegion,
    resolve_stage_map_geometry,
)


def _write_gray_png(path: Path, value: int = 200, *, pattern: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img = np.full((48, 96), value, dtype=np.uint8)
    if pattern:
        img[::4, :] = min(255, value + 40)
        img[:, ::6] = max(0, value - 50)
        cv2.putText(img, "X", (20, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.8, 255, 2)
    cv2.imwrite(str(path), img)


@pytest.fixture()
def intro_templates(tmp_path: Path) -> Path:
    root = tmp_path / "match_intro"
    for lang in ("en", "ja"):
        _write_gray_png(root / lang / "battle_modes" / "turf_war.png", 210)
        _write_gray_png(root / lang / "stages" / "scorch_gorge.png", 180)
    return root


def test_match_intro_resolves_ids_from_templates(intro_templates: Path) -> None:
    cfg = MatchIntroDetectorConfig(
        template_dir=intro_templates,
        match_threshold=0.5,
        battle_mode_roi=(0.2, 0.3, 0.8, 0.6),
        stage_roi=(0.5, 0.7, 0.95, 0.95),
    )
    detector = MatchIntroDetector(cfg, language=VisionLanguage.EN)
    frame = np.full((1080, 1920, 3), 30, dtype=np.uint8)
    h, w = frame.shape[:2]

    def _paste_native(box: tuple[float, float, float, float], gray: np.ndarray) -> None:
        x1, y1, x2, y2 = box
        left, top = int(x1 * w), int(y1 * h)
        right, bottom = int(x2 * w), int(y2 * h)
        th, tw = gray.shape[:2]
        # Place native-size template inside the ROI (detector scales templates down).
        end_y = min(bottom, top + th)
        end_x = min(right, left + tw)
        bgr = cv2.cvtColor(gray[: end_y - top, : end_x - left], cv2.COLOR_GRAY2BGR)
        frame[top:end_y, left:end_x] = bgr

    mode_t = cv2.imread(str(intro_templates / "en" / "battle_modes" / "turf_war.png"), 0)
    stage_t = cv2.imread(str(intro_templates / "en" / "stages" / "scorch_gorge.png"), 0)
    assert mode_t is not None and stage_t is not None
    _paste_native(cfg.battle_mode_roi, mode_t)
    _paste_native(cfg.stage_roi, stage_t)
    reading, conf = detector.detect(frame)
    assert isinstance(reading, MatchIntroReading)
    assert reading.battle_mode_id == "turf_war"
    assert reading.stage_id == "scorch_gorge"
    assert conf >= 0.5


def test_identity_early_stop_and_fail_closed() -> None:
    tracker = MatchIdentityTracker(intro_deadline_seconds=10.0)
    tracker.update(
        MatchIntroReading(
            stage_id="scorch_gorge",
            battle_mode_id="turf_war",
            stage_template_score=0.9,
            battle_mode_template_score=0.9,
        ),
        video_time=5.0,
    )
    assert tracker.identity.resolved
    assert tracker.identity.map_ink_enabled
    assert tracker.should_run_intro_detector(6.0) is False

    late = MatchIdentityTracker(intro_deadline_seconds=10.0)
    late.update(MatchIntroReading(stage_id="scorch_gorge"), video_time=11.0)
    assert late.identity.resolved is False
    assert late.identity.map_ink_enabled is False
    assert late.identity.intro_closed


def test_geometry_mode_override_then_default(tmp_path: Path) -> None:
    stage = tmp_path / "scorch_gorge"
    stage.mkdir()
    (stage / "default.yaml").write_text(
        "stage_id: scorch_gorge\nregions:\n  - id: R01\n    roi: [0.1,0.1,0.4,0.4]\n",
        encoding="utf-8",
    )
    (stage / "rainmaker.yaml").write_text(
        "stage_id: scorch_gorge\nbattle_mode_id: rainmaker\n"
        "regions:\n  - id: RM1\n    roi: [0.2,0.2,0.5,0.5]\n",
        encoding="utf-8",
    )
    mode = resolve_stage_map_geometry(
        tmp_path, stage_id="scorch_gorge", battle_mode_id="rainmaker"
    )
    assert mode is not None
    assert mode.regions[0].id == "RM1"
    default = resolve_stage_map_geometry(
        tmp_path, stage_id="scorch_gorge", battle_mode_id="turf_war"
    )
    assert default is not None
    assert default.regions[0].id == "R01"


def test_classifier_ally_opponent_neutral() -> None:
    classifier = MapInkClassifier(MapInkAnalyzerConfig())
    image = np.zeros((40, 40, 3), dtype=np.uint8)
    # Green-ish BGR for ally HSV default ~H35-95
    image[0:20, 0:20] = (40, 200, 40)
    # Red-ish for opponent
    image[0:20, 20:40] = (40, 40, 200)
    # Gray neutral
    image[20:40, :] = (80, 80, 80)
    ally, opponent, other = classifier.classify_bgr(image)
    assert int(np.count_nonzero(ally)) > 0
    assert int(np.count_nonzero(opponent)) > 0
    assert int(np.count_nonzero(other)) > 0


def test_analyze_map_ink_uses_classified_fraction_not_total() -> None:
    geometry = StageMapGeometry(
        stage_id="scorch_gorge",
        regions=[StageMapRegion(id="R01", roi=(0.0, 0.0, 1.0, 1.0))],
    )
    classifier = MapInkClassifier(MapInkAnalyzerConfig())
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    image[:, :] = (80, 80, 80)  # unclassified
    image[10:40, 10:40] = (40, 200, 40)  # ally
    obs = analyze_map_ink(
        image,
        geometry,
        classifier,
        video_time=42.0,
        battle_mode_id="turf_war",
        evidence_ids=["map_overlay:1"],
    )
    assert obs.ally_classified_fraction is not None
    assert obs.ally_classified_fraction == pytest.approx(1.0)
    assert obs.opponent_classified_fraction == pytest.approx(0.0)
    assert obs.classified_fraction is not None
    assert obs.classified_fraction < 1.0
    assert "death_location" not in MapObservation.model_fields


def test_zero_classified_yields_none_fractions() -> None:
    geometry = StageMapGeometry(
        stage_id="scorch_gorge",
        regions=[StageMapRegion(id="R01", roi=(0.0, 0.0, 1.0, 1.0))],
    )
    classifier = MapInkClassifier(MapInkAnalyzerConfig())
    image = np.full((50, 50, 3), 90, dtype=np.uint8)
    obs = analyze_map_ink(
        image, geometry, classifier, video_time=1.0, battle_mode_id=None
    )
    assert obs.ally_classified_fraction is None
    assert obs.opponent_classified_fraction is None
    assert obs.confidence == 0.0


def test_no_ink_coverage_game_event() -> None:
    assert not hasattr(GameEventType, "INK_COVERAGE_CHANGED")
    names = {item.value for item in GameEventType}
    assert "ink_coverage" not in names
    assert "map_observation" not in names
    assert GameEventType.MAP_OVERLAY.value == "map_overlay"


def test_repo_stage_default_pack_loads() -> None:
    root = Path(__file__).resolve().parents[1] / "configs" / "stage_maps"
    geom = resolve_stage_map_geometry(
        root, stage_id="scorch_gorge", battle_mode_id="turf_war"
    )
    assert geom is not None
    assert len(geom.regions) >= 2
    for region in geom.regions:
        x1, y1, x2, y2 = region.roi
        assert 0.0 <= x1 < x2 <= 1.0
        assert 0.0 <= y1 < y2 <= 1.0
