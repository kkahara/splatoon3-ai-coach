"""Development measurement of a supplied checkpoint-ring candidate.

This does not locate or establish the presence of a ring; callers must supply
an independently identified marker center and ring hue. It is not registered
in the vision pipeline and its development thresholds are not validated.
"""

from typing import Literal

import cv2
import numpy as np
from pydantic import BaseModel, Field

RingProgress = Literal[
    "full_or_nearly_full",
    "three_quarters_remaining",
    "half_remaining",
    "one_quarter_remaining",
    "unknown",
]


class CheckpointProgressReading(BaseModel):
    """Coarse appearance of one supplied, already identified ring candidate."""

    timestamp: float | None = Field(default=None, ge=0)
    progress: RingProgress = "unknown"


def measure_checkpoint_ring_candidate(
    image: np.ndarray,
    center: tuple[float, float],
    ring_hue: float,
    *,
    timestamp: float | None = None,
) -> CheckpointProgressReading:
    """Measure quadrant occupancy using normalized frame center and OpenCV hue.

    This bounded experiment assumes 1920×1080 HUD geometry after resizing.
    Zero occupied quadrants is unknown: disappearance does not establish clear.
    A result does not itself establish that a checkpoint ring is present.
    """
    if (
        image.ndim != 3
        or image.shape[2] != 3
        or image.size == 0
        or not all(0 <= value <= 1 for value in center)
        or not 0 <= ring_hue < 180
    ):
        return CheckpointProgressReading(timestamp=timestamp)
    frame = cv2.resize(image, (1920, 1080), interpolation=cv2.INTER_AREA)
    x, y = round(center[0] * 1920), round(center[1] * 1080)
    if x < 29 or y < 29 or x + 29 >= 1920 or y + 29 >= 1080:
        return CheckpointProgressReading(timestamp=timestamp)
    hsv = cv2.cvtColor(frame[y - 29 : y + 30, x - 29 : x + 30], cv2.COLOR_BGR2HSV)
    yy, xx = np.indices(hsv.shape[:2])
    dx, dy = xx - 29, yy - 29
    radius = np.hypot(dx, dy)
    annulus = (radius >= 22) & (radius <= 28)
    difference = np.abs(hsv[:, :, 0].astype(float) - ring_hue)
    difference = np.minimum(difference, 180 - difference)
    colored = annulus & (difference <= 12) & (hsv[:, :, 1] >= 70) & (hsv[:, :, 2] >= 90)
    quadrants = [(dx >= 0) & (dy >= 0), (dx < 0) & (dy >= 0)]
    quadrants += [(dx < 0) & (dy < 0), (dx >= 0) & (dy < 0)]
    count = sum(
        float(colored[annulus & quadrant].mean()) >= 0.25 for quadrant in quadrants
    )
    category: RingProgress = {
        4: "full_or_nearly_full",
        3: "three_quarters_remaining",
        2: "half_remaining",
        1: "one_quarter_remaining",
    }.get(count, "unknown")
    return CheckpointProgressReading(timestamp=timestamp, progress=category)
