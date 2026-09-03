"""Tests for change-trigger thresholding and confidence scoring."""

import numpy as np

from splatoon3_ai_coach.config.models import ExtractionConfig
from splatoon3_ai_coach.extraction.models import TriggerType
from splatoon3_ai_coach.extraction.signals import grayscale_histogram
from splatoon3_ai_coach.extraction.triggers import ChangeTriggerPipeline


def frame(value: int = 0) -> np.ndarray:
    return np.full((180, 320, 3), value, dtype=np.uint8)


def evaluate(config: ExtractionConfig, previous: np.ndarray, current: np.ndarray):
    return ChangeTriggerPipeline(config).evaluate(
        previous,
        current,
        grayscale_histogram(previous),
        grayscale_histogram(current),
    )


def test_identical_frames_produce_no_triggers(
    extraction_config: ExtractionConfig,
) -> None:
    still = frame()
    assert evaluate(extraction_config, still, still) == []


def test_full_scene_cut_is_detected(extraction_config: ExtractionConfig) -> None:
    triggers = evaluate(extraction_config, frame(0), frame(255))
    assert TriggerType.SCENE_CHANGE in {t.trigger_type for t in triggers}


def test_triggers_are_sorted_by_confidence(extraction_config: ExtractionConfig) -> None:
    triggers = evaluate(extraction_config, frame(0), frame(255))
    confidences = [t.confidence for t in triggers]
    assert confidences == sorted(confidences, reverse=True)


def test_confidence_stays_within_bounds(extraction_config: ExtractionConfig) -> None:
    triggers = evaluate(extraction_config, frame(0), frame(255))
    assert all(0.5 <= t.confidence <= 1.0 for t in triggers)


def test_hud_region_change_is_attributed_to_that_region(
    extraction_config: ExtractionConfig,
) -> None:
    previous = frame()
    current = previous.copy()
    current[4:54, 218:316] = 255

    detected = {t.trigger_type for t in evaluate(extraction_config, previous, current)}
    assert TriggerType.KILLFEED_CHANGE in detected
    assert TriggerType.SPECIAL_CHANGE not in detected
