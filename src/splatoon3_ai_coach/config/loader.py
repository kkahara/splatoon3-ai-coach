"""Loading application configuration from YAML."""

from pathlib import Path
from typing import Any

import yaml

from splatoon3_ai_coach.config.models import AppConfig


def load_config(path: Path) -> AppConfig:
    """Load and validate application configuration from a YAML file."""
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle) or {}
    return AppConfig.model_validate(raw)
