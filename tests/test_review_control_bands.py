"""Static Review control-band contracts."""

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.review.control_bands import extract_control_segments
from splatoon3_ai_coach.review.models import (
    AxisCalibrationEvidence,
    CursorObservation,
    ReviewTimelineSample,
    TeamIdentity,
)


def test_static_control_bands_preserve_top_neutral_bottom() -> None:
    config = ReviewTimelineConfig(
        band_score_floor=0.1,
        band_score_margin=0.02,
    )
    samples = [_sample("a", 60), _sample("b", 140), _sample("c", 180)]
    images = {
        sample.sample_id: _band_image(int(sample.cursor.x or 0), config)
        for sample in samples
    }
    segments = extract_control_segments(
        images,
        samples,
        AxisCalibrationEvidence(
            slope=100,
            intercept=0,
            point_count=3,
            mean_absolute_error_seconds=0,
            max_absolute_error_seconds=0,
        ),
        TeamIdentity(top_color=20, bottom_color=100),
        config,
    )
    compressed = [
        segment.controller
        for segment in segments
        if segment.controller != "unknown"
    ]
    assert compressed == ["top", "neutral", "bottom"]


def _sample(sample_id: str, cursor_x: int) -> ReviewTimelineSample:
    return ReviewTimelineSample(
        sample_id=sample_id,
        capture_index=0,
        image_path="unused",
        image_sha256="hash",
        elapsed_seconds=0,
        cursor=CursorObservation(x=cursor_x, x_norm=cursor_x / 200, confidence=1),
    )


def _band_image(cursor_x: int, config: ReviewTimelineConfig) -> np.ndarray:
    hsv = np.zeros((100, 200, 3), dtype=np.uint8)
    x1, x2 = int(200 * config.graph_roi[0]), int(200 * config.graph_roi[2])
    y1, y2 = int(100 * config.graph_roi[1]), int(100 * config.graph_roi[3])
    third = (x2 - x1) // 3
    hsv[y1:y2, x1 : x1 + third] = (20, 220, 180)
    hsv[y1:y2, x1 + third : x1 + 2 * third] = (0, 10, 75)
    hsv[y1:y2, x1 + 2 * third : x2] = (100, 220, 180)
    image = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    image[y1:y2, cursor_x - 1 : cursor_x + 2] = 255
    return image
