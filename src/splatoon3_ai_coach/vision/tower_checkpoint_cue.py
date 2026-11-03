"""Development-only English checkpoint-passed text observation.

The caller supplies the language-specific template. This is an unregistered
prototype: the match threshold and frame geometry have not been validated.
"""

from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, Field


class CheckpointPassedReading(BaseModel):
    """Positive text-cue observation without team or checkpoint attribution."""

    timestamp: float | None = Field(default=None, ge=0)
    template_score: float = Field(ge=0, le=1)


class CheckpointPassedDetector:
    """Match a supplied English phrase template in a normalized message region."""

    def __init__(self, template_path: Path, threshold: float = 0.93) -> None:
        self.template = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
        if self.template is None or self.template.size == 0:
            raise ValueError(f"Checkpoint cue template unavailable: {template_path}")
        if not 0 < threshold <= 1:
            raise ValueError("threshold must be in (0, 1]")
        self.threshold = threshold

    def detect(
        self, image: np.ndarray, timestamp: float | None = None
    ) -> tuple[CheckpointPassedReading | None, float]:
        """Return a positive cue candidate; no match is not proof of absence."""
        if image.ndim != 3 or image.shape[2] != 3 or image.size == 0:
            return None, 0.0
        frame = cv2.resize(image, (1920, 1080), interpolation=cv2.INTER_AREA)
        region = frame[250:380, 300:1600]
        if any(a < b for a, b in zip(region.shape[:2], self.template.shape, strict=True)):
            return None, 0.0
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 210, 255, cv2.THRESH_BINARY)
        score = float(
            cv2.matchTemplate(binary, self.template, cv2.TM_CCOEFF_NORMED).max()
        )
        if not np.isfinite(score) or score < self.threshold:
            return None, 0.0
        score = min(1.0, max(0.0, score))
        return CheckpointPassedReading(timestamp=timestamp, template_score=score), score
