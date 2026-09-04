"""Base protocol for pluggable vision detectors."""

from typing import Protocol, runtime_checkable

import numpy as np

from splatoon3_ai_coach.vision.models import Reading


@runtime_checkable
class BaseDetector(Protocol):
    """Structural interface for vision detectors."""

    name: str
    run_on_evidence: bool
    cadence_fps: float | None

    def detect(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
    ) -> tuple[Reading | None, float]:
        """Run detection on one frame image.

        ``timestamp`` is optional so detectors can debounce across frames.
        """
