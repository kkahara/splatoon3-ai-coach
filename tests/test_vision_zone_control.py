"""Tests for observe-only Splat Zones control-highlight detection."""

from __future__ import annotations

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ZoneControlDetectorConfig
from splatoon3_ai_coach.vision.zone_control import read_zone_control

CONFIG = ZoneControlDetectorConfig(
    left_pod_roi=(0.1, 0.1, 0.4, 0.9),
    right_pod_roi=(0.6, 0.1, 0.9, 0.9),
)
_DARK = (15, 15, 15)
_WHITE = (255, 255, 255)


def _strokes(pod: np.ndarray, color: tuple[int, int, int]) -> None:
    """Draw digit-like vertical strokes covering ~25% of the pod."""
    height, width = pod.shape[:2]
    for x in range(3, width - 3, 8):
        pod[height // 5 : 4 * height // 5, x : x + 3] = color


def _lit_pod(color: tuple[int, int, int]) -> np.ndarray:
    pod = np.zeros((80, 30, 3), dtype=np.uint8)
    pod[:] = color
    _strokes(pod, _WHITE)
    return pod


def _dim_pod(color: tuple[int, int, int]) -> np.ndarray:
    pod = np.zeros((80, 30, 3), dtype=np.uint8)
    pod[:] = _DARK
    _strokes(pod, color)
    return pod


def _image(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    image[10:90, 10:40] = left
    image[10:90, 60:90] = right
    return image


def test_highlighted_left_pod_is_ally_control() -> None:
    image = _image(_lit_pod((0, 220, 0)), _dim_pod((220, 0, 220)))
    reading = read_zone_control(image, CONFIG)
    assert reading.observed_state == "ally_control"
    assert (reading.left_pod, reading.right_pod) == ("lit", "dim")
    assert reading.left_signal > reading.right_signal


def test_highlighted_right_pod_is_opponent_control() -> None:
    image = _image(_dim_pod((0, 220, 0)), _lit_pod((220, 0, 220)))
    reading = read_zone_control(image, CONFIG)
    assert reading.observed_state == "opponent_control"


def test_two_dim_pods_with_coloured_digits_are_neutral() -> None:
    image = _image(_dim_pod((0, 220, 0)), _dim_pod((220, 0, 220)))
    reading = read_zone_control(image, CONFIG)
    assert reading.observed_state == "neutral"
    assert reading.confidence >= 0.6


def test_two_lit_pods_are_unknown() -> None:
    image = _image(_lit_pod((0, 220, 0)), _lit_pod((220, 0, 220)))
    reading = read_zone_control(image, CONFIG)
    assert reading.observed_state == "unknown"
    assert reading.confidence == 0.0


def test_flat_dark_frame_without_digits_is_unknown() -> None:
    image = _image(np.full((80, 30, 3), 15, np.uint8), np.full((80, 30, 3), 15, np.uint8))
    reading = read_zone_control(image, CONFIG)
    assert reading.observed_state == "unknown"


def test_blurred_band_without_digit_edges_is_unknown_not_neutral() -> None:
    """A dark, defocused frame with a bright band must not read as neutral."""
    image = np.full((100, 100, 3), 15, dtype=np.uint8)
    image[40:62, :] = (40, 140, 230)
    image = cv2.GaussianBlur(image, (0, 0), 6)
    reading = read_zone_control(image, CONFIG)
    assert reading.observed_state == "unknown"
