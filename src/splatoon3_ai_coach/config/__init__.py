"""Typed application configuration."""

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import (
    AppConfig,
    CoachConfig,
    ExtractionConfig,
    HudRegions,
    PathsConfig,
    TimerDetectorConfig,
    VideoConfig,
    VisionConfig,
)
from splatoon3_ai_coach.config.paths import default_config_path, resolve_config_path
from splatoon3_ai_coach.config.settings import CoachSettings

__all__ = [
    "AppConfig",
    "CoachConfig",
    "CoachSettings",
    "ExtractionConfig",
    "HudRegions",
    "PathsConfig",
    "TimerDetectorConfig",
    "VideoConfig",
    "VisionConfig",
    "default_config_path",
    "load_config",
    "resolve_config_path",
]
