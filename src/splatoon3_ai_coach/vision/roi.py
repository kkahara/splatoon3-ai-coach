"""Shared ROI helpers for vision detectors."""

from __future__ import annotations

import numpy as np

from splatoon3_ai_coach.types import NormalizedBox


def crop_roi(image: np.ndarray, box: NormalizedBox) -> np.ndarray:
    """Crop a normalized region from a BGR frame."""
    height, width = image.shape[:2]
    left, top, right, bottom = pixel_box_from_normalized(box, width, height)
    return image[top:bottom, left:right]


def pixel_box_from_normalized(
    box: NormalizedBox,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    """Convert ``[x1,y1,x2,y2]`` normalized coords to pixel bounds.

    Uses the same ``int()`` floors as historical ``crop_roi`` behavior.
    Returns ``(left, top, right, bottom)``.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"frame size must be positive, got {width}x{height}")
    x1, y1, x2, y2 = box
    left, top = int(x1 * width), int(y1 * height)
    right, bottom = int(x2 * width), int(y2 * height)
    return left, top, right, bottom


def normalized_box_from_pixels(
    x1: int | float,
    y1: int | float,
    x2: int | float,
    y2: int | float,
    width: int,
    height: int,
    *,
    decimals: int = 6,
) -> NormalizedBox:
    """Convert a pixel ``[x1,y1,x2,y2]`` box to a normalized ``NormalizedBox``.

    Clamps coordinates into the frame. Requires a positive area after clamping.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"frame size must be positive, got {width}x{height}")
    if decimals < 0:
        raise ValueError(f"decimals must be >= 0, got {decimals}")

    left = max(0.0, min(float(width), float(x1)))
    top = max(0.0, min(float(height), float(y1)))
    right = max(0.0, min(float(width), float(x2)))
    bottom = max(0.0, min(float(height), float(y2)))
    if right <= left or bottom <= top:
        raise ValueError(
            f"ROI must have positive area after clamp, got "
            f"({left}, {top}, {right}, {bottom}) on {width}x{height}"
        )

    nx1 = round(left / width, decimals)
    ny1 = round(top / height, decimals)
    nx2 = round(right / width, decimals)
    ny2 = round(bottom / height, decimals)
    # Keep a positive area after rounding (full-frame edge cases).
    if nx2 <= nx1:
        nx2 = min(1.0, nx1 + 10 ** (-decimals))
    if ny2 <= ny1:
        ny2 = min(1.0, ny1 + 10 ** (-decimals))
    return (nx1, ny1, nx2, ny2)
