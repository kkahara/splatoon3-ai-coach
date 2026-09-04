"""Typed application configuration."""

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import (
    AppConfig,
    CoachConfig,
    DeathDetectorConfig,
    ExtractionConfig,
    HudRegions,
    PathsConfig,
    SplatDetectorConfig,
    TimerDetectorConfig,
    VideoConfig,
    VisionConfig,
    VisionLanguage,
    VisionOcrConfig,
)
from splatoon3_ai_coach.config.paths import default_config_path, resolve_config_path
from splatoon3_ai_coach.config.settings import CoachSettings

__all__ = [
    "AppConfig",
    "CoachConfig",
    "CoachSettings",
    "DeathDetectorConfig",
    "ExtractionConfig",
    "HudRegions",
    "PathsConfig",
    "SplatDetectorConfig",
    "TimerDetectorConfig",
    "VideoConfig",
    "VisionConfig",
    "VisionLanguage",
    "VisionOcrConfig",
    "default_config_path",
    "load_config",
    "resolve_config_path",
]
