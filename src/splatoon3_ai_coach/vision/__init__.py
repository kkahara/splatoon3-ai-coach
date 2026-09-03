"""Vision layer: pluggable detectors, state fusion, and events."""

from splatoon3_ai_coach.vision.base import BaseDetector
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.models import (
    GameEvent,
    GameEventType,
    GameStateSnapshot,
    TimerReading,
    VisionFrameResult,
    VisionManifest,
)
from splatoon3_ai_coach.vision.pipeline import run_vision
from splatoon3_ai_coach.vision.registry import build_detectors
from splatoon3_ai_coach.vision.state import fuse_timer_state
from splatoon3_ai_coach.vision.timer import TimerDetector

__all__ = [
    "BaseDetector",
    "GameEvent",
    "GameEventType",
    "GameStateSnapshot",
    "TimerDetector",
    "TimerReading",
    "VisionFrameResult",
    "VisionManifest",
    "build_detectors",
    "fuse_timer_state",
    "infer_events",
    "run_vision",
]
