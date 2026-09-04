"""Shared ROI helpers for vision detectors."""

import numpy as np

from splatoon3_ai_coach.types import NormalizedBox


def crop_roi(image: np.ndarray, box: NormalizedBox) -> np.ndarray:
    """Crop a normalized region from a BGR frame."""
    height, width = image.shape[:2]
    x1, y1, x2, y2 = box
    left, top = int(x1 * width), int(y1 * height)
    right, bottom = int(x2 * width), int(y2 * height)
    return image[top:bottom, left:right]
