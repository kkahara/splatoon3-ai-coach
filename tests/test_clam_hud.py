"""Clam score and experimental basket gauge observation coverage."""

import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.analysis.pipeline import write_scenarios
from splatoon3_ai_coach.analysis.scenario_models import ScenarioType
from splatoon3_ai_coach.analysis.scenarios import build_scenarios
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.coaching_candidates import rank_candidates
from splatoon3_ai_coach.coach.death_importance import score_death_candidate
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.coach.load_analysis import load_coach_analysis_bundle
from splatoon3_ai_coach.config.loader import _resolve_relative_paths, load_config
from splatoon3_ai_coach.config.models import (
    ClamBasketStateDetectorConfig,
    ScoreDetectorConfig,
)
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.media.video import VideoFrame
from splatoon3_ai_coach.media.vision_manifest import (
    load_vision_manifest,
    save_vision_manifest,
)
from splatoon3_ai_coach.vision.basket_team import resolve_basket_team
from splatoon3_ai_coach.vision.clam_basket_fusion import fuse_basket_state
from splatoon3_ai_coach.vision.clam_basket_state import ClamBasketStateDetector
from splatoon3_ai_coach.vision.game_language_result import with_detected_language
from splatoon3_ai_coach.vision.models import (
    BasketTeamObservation,
    ClamBasketStateReading,
    DetectorResult,
    ScoreReading,
    ScoreSideReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.pipeline import (
    _interpret_observations,
    observe_cadence_stream,
    refuse_vision_manifest,
)
from splatoon3_ai_coach.vision.registry import build_detectors
from splatoon3_ai_coach.vision.score import ClamScoreDetector
from splatoon3_ai_coach.vision.team_color_calibration import TeamColorCalibrationResult


def test_clam_scores_match_labeled_study_frames() -> None:
    """The production primitive reproduces the labeled EN/JA study values."""
    root = PROJECT_ROOT / "analysis" / "clam_blitz_score_survey"
    if not (root / "gt/gt.json").exists() or not (root / "samples").exists():
        pytest.skip("Clam Blitz study samples unavailable")
    truth = json.loads((root / "gt/gt.json").read_text())["frames"]
    detector = ClamScoreDetector(
        ScoreDetectorConfig(
            template_dir=PROJECT_ROOT / "calibration/templates",
            battle_mode_id="clam_blitz",
        )
    )
    for path, expected in truth.items():
        image = cv2.imread(str(root / "samples" / path))
        assert image is not None
        reading, _ = detector.detect(image)
        assert reading is not None
        assert (reading.left.value, reading.right.value) == (
            expected["left"]["value"],
            expected["right"]["value"],
        ), path


def test_basket_state_color_and_unknown() -> None:
    """Color remains observable even when presentation evidence is inadequate."""
    config = ClamBasketStateDetectorConfig(roi=(0, 0, 1, 1))
    detector = ClamBasketStateDetector(config)
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    cv2.ellipse(image, (50, 50), (38, 38), 0, 0, 180, (0, 128, 255), -1)
    reading, _ = detector.detect(image)
    assert reading.visual_state == "open"
    assert reading.hue == pytest.approx(15, abs=2)
    blank, _ = detector.detect(np.zeros_like(image))
    assert blank.visual_state == "unknown" and blank.hue is None
    image[:] = (0, 128, 255)
    colored, _ = detector.detect(image)
    assert colored.visual_state == "unknown"
    assert colored.hue == pytest.approx(15, abs=2)


def test_nested_basket_template_path_uses_config_directory(tmp_path: Path) -> None:
    """Basket template paths follow the same YAML-relative rule as other detectors."""
    raw = {
        "paths": {},
        "vision": {"clam_blitz": {"basket_state": {"template_dir": "assets"}}},
    }
    _resolve_relative_paths(raw, tmp_path)
    assert raw["vision"]["clam_blitz"]["basket_state"]["template_dir"] == str(
        tmp_path / "assets"
    )


def test_basket_team_requires_mode_and_separated_colors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A calibrated color is assigned only with an unambiguous matching mode."""
    reading = ClamBasketStateReading(visual_state="open", hue=179, hue_concentration=0.95)
    detection = DetectorResult(
        id="gauge",
        detector_name="clam_basket_state",
        detector_version="test",
        confidence=0.95,
        reading=reading,
    )
    colors = TeamColorCalibrationResult(
        ally_h=1, opponent_h=90, separation_degrees=89, calibrated_at=5
    )
    assert resolve_basket_team(detection, colors, "clam_blitz").scoring_team == "unknown"
    # Exercise the prerequisite logic without asserting that validation is complete.
    monkeypatch.setattr(
        "splatoon3_ai_coach.vision.basket_team.OPEN_STATE_VALIDATED", True
    )
    assert resolve_basket_team(detection, colors, "clam_blitz").scoring_team == "ally"
    assert resolve_basket_team(detection, colors, None).scoring_team == "unknown"
    assert resolve_basket_team(detection, None, "clam_blitz").scoring_team == "unknown"
    assert resolve_basket_team(detection, colors, "splat_zones").scoring_team == "unknown"
    detection.reading = reading.model_copy(update={"visual_state": "unknown"})
    assert resolve_basket_team(detection, colors, "clam_blitz").scoring_team == "unknown"
    detection.reading = reading.model_copy(update={"hue": 45.0})
    assert resolve_basket_team(detection, colors, "clam_blitz").scoring_team == "unknown"


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        (75, "closed"),
        (80, "open"),
        (81, "unknown"),
        (85, "open"),
        (94, "open"),
        (95, "open"),
        (96, "open"),
        (97, "closed"),
        (208, "closed"),
        (216, "open"),
        (220, "open"),
        (221, "open"),
        (222, "open"),
        (295, "open"),
        (300, "open"),
        (302, "open"),
        (303, "open"),
        (304, "unknown"),
        (305, "unknown"),
    ],
)
def test_real_basket_transition_crops(timestamp: int, expected: str) -> None:
    """Exercise manually reviewed transition crops, not full accuracy validation."""
    path = PROJECT_ROOT / "tests/fixtures/clam_basket_state" / f"{timestamp}.png"
    image = cv2.imread(str(path))
    assert image is not None
    detector = ClamBasketStateDetector(ClamBasketStateDetectorConfig(roi=(0, 0, 1, 1)))
    reading, confidence = detector.detect(image, float(timestamp))
    assert reading.visual_state == expected
    assert reading.timestamp == timestamp
    assert confidence == (0.0 if expected == "unknown" else 0.7)


def test_basket_fusion_mode_gate_and_unreadable() -> None:
    """Only usable in-match Clam readings become snapshot state."""
    reading = ClamBasketStateReading(visual_state="closed")
    result = DetectorResult(
        id="anchor",
        detector_name="clam_basket_state",
        detector_version="test",
        confidence=0.7,
        reading=reading,
    )
    frame = VisionFrameResult(
        frame_id="f", timestamp=10, source="cadence", detections=[result]
    )
    assert fuse_basket_state(frame, "clam_blitz", "in_match") == ("closed", ["anchor"])
    assert fuse_basket_state(frame, "splat_zones", "in_match") == ("unknown", [])
    assert fuse_basket_state(frame, None, "in_match") == ("unknown", [])
    assert fuse_basket_state(frame, "clam_blitz", "post_match") == ("unknown", [])
    assert fuse_basket_state(
        frame, "clam_blitz", "in_match", map_overlay_present=True
    ) == ("unknown", [])
    result.confidence = 0.49
    assert fuse_basket_state(frame, "clam_blitz", "in_match") == ("unknown", [])
    result.confidence = 0.7
    result.reading = ClamBasketStateReading(visual_state="unknown")
    assert fuse_basket_state(frame, "clam_blitz", "in_match") == ("unknown", [])


def test_clam_readings_do_not_change_fused_trace_or_scenarios() -> None:
    """Tracked fixture protects isolation even without local study artifacts."""
    path = PROJECT_ROOT / "tests/fixtures/vision_frames/2026-09-07_14-02-14_190-227.json"
    frames = [
        VisionFrameResult.model_validate(x)
        for x in json.loads(path.read_text())["frame_results"]
    ]
    config = load_config(PROJECT_ROOT / "configs/default.yaml")
    augmented = [frame.model_copy(deep=True) for frame in frames]
    readings = [
        DetectorResult(
            id="clam",
            detector_name="clam_score",
            detector_version="test",
            confidence=1,
            reading=ScoreReading(
                battle_mode_id="clam_blitz", left=ScoreSideReading(value=3, visible=True)
            ),
        ),
        DetectorResult(
            id="gauge",
            detector_name="clam_basket_state",
            detector_version="test",
            confidence=1,
            reading=ClamBasketStateReading(
                visual_state="open", hue=3, hue_concentration=1
            ),
        ),
    ]
    for frame in augmented:
        frame.detections.extend(readings)
        frame.basket_team = BasketTeamObservation(
            state_result_id="gauge", scoring_team="ally"
        )
    original = _interpret_observations(frames, config)
    with_clam = _interpret_observations(augmented, config)
    assert original == with_clam
    scenarios = build_scenarios(original[1], config.scenarios)
    assert scenarios
    assert scenarios == build_scenarios(with_clam[1], config.scenarios)


def test_experimental_observations_do_not_change_replay(tmp_path: Path) -> None:
    """Real observations remain isolated through fusion, scenarios and coaching."""
    root = (
        PROJECT_ROOT
        / "analysis/map_ink_validation/polygon_registration_2026-10-30/fresh/brinewater"
    )
    image_path = (
        PROJECT_ROOT / "analysis/clam_blitz_score_survey/samples/en_humpback/t00169.0.png"
    )
    if not (root / "vision_manifest.json").is_file() or not image_path.is_file():
        pytest.skip("Local replay or Clam study sample unavailable")
    config = load_config(PROJECT_ROOT / "configs/default.yaml")
    names = {"clam_score", "clam_basket_state"}
    assert names.isdisjoint(config.vision.enabled_detectors)
    experimental = config.model_copy(deep=True)
    experimental.vision.enabled_detectors = sorted(names)
    detectors = build_detectors(with_detected_language(experimental, "en").vision)
    image = cv2.imread(str(image_path))
    assert image is not None
    results, _ = observe_cadence_stream(
        iter([VideoFrame(timestamp=0, frame_index=0, image=image)]),
        detectors,
        cadence_fps=2,
        analysis_id="test",
        detector_versions={n: n for n in names},
    )
    readings = results[0].detections
    assert {item.detector_name for item in readings} == names
    assert (
        next(
            d for d in readings if d.detector_name == "clam_basket_state"
        ).reading.visual_state
        == "open"
    )

    original = load_vision_manifest(root)
    augmented = original.model_copy(deep=True)
    assert augmented.frame_results
    for frame in augmented.frame_results:
        frame.detections.extend(readings)
        frame.basket_team = BasketTeamObservation(
            state_result_id="gauge", scoring_team="ally"
        )

    products = []
    for label, manifest in (("base", original), ("clam", augmented)):
        directory = tmp_path / label
        directory.mkdir()
        for filename in ("map_observations.json", "match_identity.json"):
            if (root / filename).is_file():
                shutil.copy(root / filename, directory / filename)
        refused = refuse_vision_manifest(manifest, config)
        save_vision_manifest(refused, directory)
        write_scenarios(refused.game_events, config, directory, manifest=refused)
        bundle = load_coach_analysis_bundle(directory, min_usable_confidence=0.5)
        contexts = {context.scenario_id: context for context in bundle.contexts}
        inputs = [
            CoachInput(
                primary_scenario=scenario, primary_context=contexts[scenario.scenario_id]
            )
            for scenario in bundle.scenarios
            if scenario.scenario_type is ScenarioType.DEATH_EPISODE
        ]
        units = [score_death_candidate(item, roster_scoring=False) for item in inputs]
        ranking = rank_candidates(
            [unit.to_candidate() for unit in units], max_llm_units=3
        )
        products.append(
            (
                refused.state_snapshots,
                refused.game_events,
                bundle.scenarios,
                bundle.contexts,
                bundle.game_clock,
                bundle.player_count_clock,
                bundle.battle_mode_id,
                [unit.model_dump(mode="json") for unit in units],
                ranking,
                [
                    build_coach_llm_view(item, unit).model_dump(mode="json")
                    for item, unit in zip(inputs, units, strict=True)
                ],
            )
        )
    assert products[0][2] and products[0][7]
    assert products[0] == products[1]
