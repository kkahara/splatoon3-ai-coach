"""Pluggable vision interfaces: per-frame detectors and optional trackers.

Detectors observe a single frame and return a ``Reading`` (plus confidence).
They must not encode match phase, death/respawn policy, or emit ``GameEvent``s.

Trackers / associators are siblings to splat-style fusers: they link readings
across time. They live under ``vision/`` but are **not** constructed via
``enabled_detectors`` and must not invent Scenario types or GameEvents.
"""

from typing import Any, Protocol, runtime_checkable

import numpy as np

from splatoon3_ai_coach.vision.models import Reading


@runtime_checkable
class BaseDetector(Protocol):
    """Structural interface for per-frame vision detectors."""

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


@runtime_checkable
class BaseTracker(Protocol):
    """Optional temporal associator for readings across frames.

    Implementations associate detections over time (e.g. multi-object tracks).
    Output is track/association state for fusion — never GameEvents or Scenario
    membership. Constructed separately from ``enabled_detectors`` (same pattern
    as splat fuser / review tracker).
    """

    name: str

    def update(
        self,
        readings: list[Reading],
        timestamp: float | None = None,
    ) -> Any:
        """Associate current-frame readings with prior track state.

        Return type is intentionally open until concrete YOLO/MOT land.
        """
