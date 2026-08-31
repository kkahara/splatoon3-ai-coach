"""Tests for thresholding and confidence scoring."""

import numpy as np

from splatoon3_ai_coach.analysis.detectors import FrameDetectors
from splatoon3_ai_coach.analysis.models import EventType
from splatoon3_ai_coach.analysis.signals import grayscale_histogram
from splatoon3_ai_coach.config.models import ExtractionConfig


def frame(value: int = 0) -> np.ndarray:
    return np.full((180, 320, 3), value, dtype=np.uint8)


def detect(config: ExtractionConfig, previous: np.ndarray, current: np.ndarray):
    return FrameDetectors(config).detect(
        previous,
        current,
        grayscale_histogram(previous),
        grayscale_histogram(current),
    )


def test_identical_frames_produce_no_detections(
    extraction_config: ExtractionConfig,
) -> None:
    still = frame()
    assert detect(extraction_config, still, still) == []


def test_full_scene_cut_is_detected(extraction_config: ExtractionConfig) -> None:
    detections = detect(extraction_config, frame(0), frame(255))
    assert EventType.SCENE_CHANGE in {d.event_type for d in detections}


def test_detections_are_sorted_by_confidence(
    extraction_config: ExtractionConfig,
) -> None:
    detections = detect(extraction_config, frame(0), frame(255))
    confidences = [d.confidence for d in detections]
    assert confidences == sorted(confidences, reverse=True)


def test_confidence_stays_within_bounds(extraction_config: ExtractionConfig) -> None:
    detections = detect(extraction_config, frame(0), frame(255))
    assert all(0.5 <= d.confidence <= 1.0 for d in detections)


def test_hud_region_change_is_attributed_to_that_region(
    extraction_config: ExtractionConfig,
) -> None:
    previous = frame()
    current = previous.copy()
    # Fill the killfeed box (x 0.68-0.99, y 0.02-0.30) only.
    current[4:54, 218:316] = 255

    detected = {d.event_type for d in detect(extraction_config, previous, current)}
    assert EventType.KILLFEED_CHANGE in detected
    assert EventType.SPECIAL_CHANGE not in detected
