"""Stage playable-mask load / rasterize (no ink classification)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from splatoon3_ai_coach.vision.stage_mask import (
    StageMaskConfig,
    load_stage_mask,
    resolve_stage_mask,
    stage_mask_to_bool,
    write_stage_mask,
)


def test_stage_mask_rasterize_square() -> None:
    config = StageMaskConfig(
        stage_id="scorch_gorge",
        polygon=[(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)],
        erosion_pixels=0,
    )
    mask = stage_mask_to_bool(config, height=100, width=100)
    assert mask.shape == (100, 100)
    assert bool(mask[50, 50])
    assert not bool(mask[5, 5])
    assert int(np.count_nonzero(mask)) > 0


def test_erosion_zero_matches_fill() -> None:
    config = StageMaskConfig(
        stage_id="scorch_gorge",
        polygon=[(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)],
        erosion_pixels=0,
    )
    eroded = StageMaskConfig(
        stage_id="scorch_gorge",
        polygon=config.polygon,
        erosion_pixels=2,
    )
    base = stage_mask_to_bool(config, 80, 80)
    shrunk = stage_mask_to_bool(eroded, 80, 80)
    assert int(np.count_nonzero(shrunk)) < int(np.count_nonzero(base))
    assert np.all(shrunk <= base)


def test_resolve_stage_mask_missing(tmp_path: Path) -> None:
    stage_dir = tmp_path / "mahi_mahi_resort"
    stage_dir.mkdir()
    assert resolve_stage_mask(tmp_path, stage_id="mahi_mahi_resort") is None


def test_resolve_and_roundtrip_yaml(tmp_path: Path) -> None:
    stage_id = "mahi_mahi_resort"
    stage_dir = tmp_path / stage_id
    config = StageMaskConfig(
        stage_id=stage_id,
        polygon=[(0.3, 0.2), (0.7, 0.25), (0.65, 0.8), (0.25, 0.75)],
        erosion_pixels=0,
    )
    path = stage_dir / "stage_mask.yaml"
    write_stage_mask(path, config)
    loaded = load_stage_mask(path)
    assert loaded.stage_id == stage_id
    assert len(loaded.polygon) == 4
    resolved = resolve_stage_mask(tmp_path, stage_id=stage_id)
    assert resolved is not None
    assert resolved.polygon == loaded.polygon


def test_invalid_vertex_rejected() -> None:
    with pytest.raises(Exception):
        StageMaskConfig(
            stage_id="x",
            polygon=[(0.0, 0.0), (1.5, 0.0), (0.5, 1.0)],
        )


def test_invalid_file_falls_back_to_none(tmp_path: Path) -> None:
    stage_dir = tmp_path / "scorch_gorge"
    stage_dir.mkdir()
    (stage_dir / "stage_mask.yaml").write_text(
        yaml.safe_dump({"stage_id": "scorch_gorge", "polygon": [[0.1, 0.1]]}),
        encoding="utf-8",
    )
    assert resolve_stage_mask(tmp_path, stage_id="scorch_gorge") is None
