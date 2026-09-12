"""Unit tests for vision ROI pixel ↔ normalized conversion."""

from __future__ import annotations

import numpy as np
import pytest

from splatoon3_ai_coach.vision.roi import (
    crop_roi,
    normalized_box_from_pixels,
    pixel_box_from_normalized,
)


def test_full_frame_pixels_normalize_to_unit_box() -> None:
    box = normalized_box_from_pixels(0, 0, 1920, 1080, 1920, 1080, decimals=6)
    assert box == (0.0, 0.0, 1.0, 1.0)


def test_known_mid_box_normalization() -> None:
    # 1651/1920, 11/1080, 1910/1920, 194/1080
    box = normalized_box_from_pixels(1651, 11, 1910, 194, 1920, 1080, decimals=6)
    assert box == (
        round(1651 / 1920, 6),
        round(11 / 1080, 6),
        round(1910 / 1920, 6),
        round(194 / 1080, 6),
    )


def test_pixel_box_from_normalized_matches_crop_roi_floors() -> None:
    width, height = 1920, 1080
    norm = (0.859896, 0.010185, 0.994792, 0.179630)
    left, top, right, bottom = pixel_box_from_normalized(norm, width, height)
    assert (left, top, right, bottom) == (
        int(0.859896 * width),
        int(0.010185 * height),
        int(0.994792 * width),
        int(0.179630 * height),
    )
    image = np.zeros((height, width, 3), dtype=np.uint8)
    cropped = crop_roi(image, norm)
    assert cropped.shape[0] == bottom - top
    assert cropped.shape[1] == right - left


def test_rejects_zero_area_and_inverted_boxes() -> None:
    with pytest.raises(ValueError, match="positive area"):
        normalized_box_from_pixels(100, 100, 100, 200, 1920, 1080)
    with pytest.raises(ValueError, match="positive area"):
        normalized_box_from_pixels(200, 100, 100, 200, 1920, 1080)


def test_clamps_out_of_frame_pixels() -> None:
    box = normalized_box_from_pixels(-10, -5, 2000, 1200, 1920, 1080, decimals=6)
    assert box == (0.0, 0.0, 1.0, 1.0)


def test_rejects_non_positive_frame_size() -> None:
    with pytest.raises(ValueError, match="positive"):
        pixel_box_from_normalized((0.0, 0.0, 1.0, 1.0), 0, 1080)
    with pytest.raises(ValueError, match="positive"):
        normalized_box_from_pixels(0, 0, 10, 10, 1920, 0)
