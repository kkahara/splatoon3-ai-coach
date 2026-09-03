"""Tests for the low-level visual measurements."""

import numpy as np

from splatoon3_ai_coach.extraction.signals import (
    crop_region,
    grayscale_histogram,
    histogram_difference,
    optical_flow_magnitude,
    region_change,
    ssim_score,
)


def blank_frame(value: int = 0) -> np.ndarray:
    return np.full((180, 320, 3), value, dtype=np.uint8)


def test_identical_frames_have_high_ssim() -> None:
    frame = blank_frame()
    assert ssim_score(frame, frame) > 0.99


def test_different_frames_have_low_ssim() -> None:
    assert ssim_score(blank_frame(0), blank_frame(255)) < 0.5


def test_identical_histograms_have_zero_difference() -> None:
    histogram = grayscale_histogram(blank_frame())
    assert histogram_difference(histogram, histogram) < 0.001


def test_changed_region_is_detected() -> None:
    first = np.zeros((100, 100, 3), dtype=np.uint8)
    second = first.copy()
    second[20:80, 20:80] = 255
    assert region_change(first, second) > 0.20


def test_unchanged_region_reports_no_change() -> None:
    frame = blank_frame()
    assert region_change(frame, frame) == 0.0


def test_crop_region_uses_normalized_coordinates() -> None:
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    cropped = crop_region(frame, (0.5, 0.0, 1.0, 0.5))
    assert cropped.shape[:2] == (50, 100)


def test_static_frames_have_low_motion() -> None:
    frame = blank_frame()
    assert optical_flow_magnitude(frame, frame) < 0.01
