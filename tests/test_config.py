"""Tests for configuration loading and validation."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import AppConfig, ExtractionConfig

DEFAULT_CONFIG = Path("configs/default.yaml")


def test_default_config_is_valid() -> None:
    config = load_config(DEFAULT_CONFIG)
    assert isinstance(config.extraction, ExtractionConfig)
    assert config.extraction.analysis_fps > 0


def test_missing_config_raises() -> None:
    with pytest.raises(FileNotFoundError):
        load_config(Path("does-not-exist.yaml"))


def test_analysis_step_is_the_inverse_of_analysis_fps() -> None:
    config = load_config(DEFAULT_CONFIG)
    assert config.extraction.analysis_step_seconds == pytest.approx(
        1.0 / config.extraction.analysis_fps
    )


def test_out_of_range_hud_region_is_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    raw["extraction"]["hud"]["killfeed"] = [0.5, 0.0, 1.5, 0.3]

    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValidationError, match=r"\[0, 1\]"):
        load_config(path)


def test_inverted_hud_region_is_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    raw["extraction"]["hud"]["killfeed"] = [0.9, 0.3, 0.5, 0.1]

    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ValidationError, match="positive area"):
        load_config(path)


def test_unknown_top_level_section_is_ignored() -> None:
    config = AppConfig.model_validate(
        {
            **yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8")),
            "future_section": {"unused": True},
        }
    )
    assert isinstance(config, AppConfig)
