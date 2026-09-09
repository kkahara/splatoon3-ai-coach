"""Loading application configuration from YAML."""

from pathlib import Path
from typing import Any

import yaml

from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.exceptions import ConfigError


def load_config(path: Path) -> AppConfig:
    """Load and validate application configuration from a YAML file."""
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")

    config_dir = path.parent.resolve()
    with path.open("r", encoding="utf-8") as handle:
        raw: dict[str, Any] = yaml.safe_load(handle) or {}

    _resolve_relative_paths(raw, config_dir)
    try:
        return AppConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"Invalid configuration in {path}: {exc}") from exc


def _resolve_relative_paths(raw: dict[str, Any], config_dir: Path) -> None:
    """Resolve relative output paths against the config file directory."""
    paths = raw.get("paths")
    if not isinstance(paths, dict):
        return

    for key in ("frame_output", "manifest_output"):
        value = paths.get(key)
        if value is None:
            continue
        path = Path(value)
        if not path.is_absolute():
            paths[key] = str((config_dir / path).resolve())

    vision = raw.get("vision")
    if isinstance(vision, dict):
        for key in (
            "timer",
            "death",
            "splat",
            "respawn",
            "map_overlay",
            "active_gameplay",
            "player_count",
        ):
            section = vision.get(key)
            if not isinstance(section, dict):
                continue
            for field in ("template_dir", "ouch_template_dir"):
                template_dir = section.get(field)
                if template_dir is not None:
                    path = Path(template_dir)
                    if not path.is_absolute():
                        section[field] = str((config_dir / path).resolve())
