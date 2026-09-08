"""Tests for configuration loading and validation."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import ExtractionConfig, VisionConfig
from splatoon3_ai_coach.exceptions import ConfigError


def test_default_config_is_valid() -> None:
    config = load_config(default_config_path())
    assert isinstance(config.extraction, ExtractionConfig)
    assert isinstance(config.vision, VisionConfig)
    assert config.extraction.analysis_fps > 0
    assert config.vision.splat.skull_match_threshold == pytest.approx(0.80)
    assert config.vision.splat.text_match_threshold == pytest.approx(0.70)
    assert config.scenarios.post_death_follow_seconds == pytest.approx(8.0)
    assert config.scenarios.post_death_max_seconds == pytest.approx(30.0)
    assert config.scenarios.engagement_gap_seconds == pytest.approx(3.0)
    assert config.scenarios.engagement_include_following_death_seconds == pytest.approx(
        2.0
    )


def test_missing_config_raises() -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(Path("does-not-exist.yaml"))


def test_analysis_step_is_the_inverse_of_analysis_fps() -> None:
    config = load_config(default_config_path())
    assert config.extraction.analysis_step_seconds == pytest.approx(
        1.0 / config.extraction.analysis_fps
    )


def test_relative_paths_resolve_against_config_directory(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir()
    config_path = config_dir / "local.yaml"
    default_raw = yaml.safe_load(default_config_path().read_text(encoding="utf-8"))
    config_path.write_text(
        yaml.safe_dump(
            {
                "video": {"max_width": 1920, "max_height": 1080},
                "paths": {
                    "frame_output": "./out/frames",
                    "manifest_output": "./out/manifests",
                },
                "extraction": default_raw["extraction"],
                "vision": default_raw["vision"],
            }
        ),
        encoding="utf-8",
    )

    config = load_config(config_path)
    assert config.paths.frame_output == (config_dir / "out/frames").resolve()


def test_out_of_range_hud_region_is_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load(default_config_path().read_text(encoding="utf-8"))
    raw["extraction"]["hud"]["killfeed"] = [0.5, 0.0, 1.5, 0.3]

    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config(path)


def test_inverted_hud_region_is_rejected(tmp_path: Path) -> None:
    raw = yaml.safe_load(default_config_path().read_text(encoding="utf-8"))
    raw["extraction"]["hud"]["killfeed"] = [0.9, 0.3, 0.5, 0.1]

    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises((ConfigError, ValidationError)):
        load_config(path)
