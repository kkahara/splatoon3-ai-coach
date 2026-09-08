"""Vision layer: pluggable detectors, state fusion, and events.

Import concrete modules (``vision.pipeline``, ``vision.models``, …) rather
than relying on this package root. Keeping ``__init__`` light avoids pulling
the full analysis pipeline on every vision import.
"""

from splatoon3_ai_coach.vision.base import BaseDetector
from splatoon3_ai_coach.vision.models import (
    DeathReading,
    GameEvent,
    GameEventReason,
    GameEventSource,
    GameEventType,
    GameStateSnapshot,
    SplatReading,
    TimerReading,
    VisionFrameResult,
    VisionManifest,
)

__all__ = [
    "BaseDetector",
    "DeathReading",
    "GameEvent",
    "GameEventReason",
    "GameEventSource",
    "GameEventType",
    "GameStateSnapshot",
    "SplatReading",
    "TimerReading",
    "VisionFrameResult",
    "VisionManifest",
]
