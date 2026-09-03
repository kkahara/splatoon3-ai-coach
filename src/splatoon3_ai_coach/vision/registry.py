"""Registry of available vision detectors."""

from splatoon3_ai_coach.config.models import VisionConfig
from splatoon3_ai_coach.vision.base import BaseDetector
from splatoon3_ai_coach.vision.timer import TimerDetector


def build_detectors(config: VisionConfig) -> list[BaseDetector]:
    """Construct configured detectors for an analysis run."""
    detectors: list[BaseDetector] = []
    if "timer" in config.enabled_detectors:
        timer = TimerDetector(
            config.timer,
            cadence_fps=config.hud_cadence_fps,
        )
        detectors.append(timer)
    return detectors
