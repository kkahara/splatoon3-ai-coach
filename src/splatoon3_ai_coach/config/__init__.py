"""Typed application configuration."""

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import (
    AppConfig,
    ExtractionConfig,
    HudRegions,
    PathsConfig,
    VideoConfig,
)

__all__ = [
    "AppConfig",
    "ExtractionConfig",
    "HudRegions",
    "PathsConfig",
    "VideoConfig",
    "load_config",
]
