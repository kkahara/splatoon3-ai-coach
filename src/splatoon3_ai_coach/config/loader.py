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
        timer = vision.get("timer")
        if isinstance(timer, dict):
            template_dir = timer.get("template_dir")
            if template_dir is not None:
                path = Path(template_dir)
                if not path.is_absolute():
                    timer["template_dir"] = str((config_dir / path).resolve())
        death = vision.get("death")
        if isinstance(death, dict):
            template_dir = death.get("template_dir")
            if template_dir is not None:
                path = Path(template_dir)
                if not path.is_absolute():
                    death["template_dir"] = str((config_dir / path).resolve())
        splat = vision.get("splat")
        if isinstance(splat, dict):
            template_dir = splat.get("template_dir")
            if template_dir is not None:
                path = Path(template_dir)
                if not path.is_absolute():
                    splat["template_dir"] = str((config_dir / path).resolve())
