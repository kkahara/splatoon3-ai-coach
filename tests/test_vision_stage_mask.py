"""Stage playable-mask load / rasterize (no ink classification)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml
from pydantic import ValidationError

from splatoon3_ai_coach.vision.overlay_map_polygon import (
    OverlayMapPolygonConfig,
    load_overlay_map_polygon,
    overlay_polygon_sha256,
    overlay_polygon_to_bool,
    resolve_overlay_map_polygon,
    write_overlay_map_polygon,
)


def test_stage_mask_rasterize_square() -> None:
    config = OverlayMapPolygonConfig(
        stage_id="scorch_gorge",
        polygon=[(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)],
        erosion_pixels=0,
    )
    mask = overlay_polygon_to_bool(config, height=100, width=100)
    assert mask.shape == (100, 100)
    assert bool(mask[50, 50])
    assert not bool(mask[5, 5])
    assert int(np.count_nonzero(mask)) > 0


def test_erosion_zero_matches_fill() -> None:
    config = OverlayMapPolygonConfig(
        stage_id="scorch_gorge",
        polygon=[(0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.1, 0.9)],
        erosion_pixels=0,
    )
    eroded = OverlayMapPolygonConfig(
        stage_id="scorch_gorge",
        polygon=config.polygon,
        erosion_pixels=2,
    )
    base = overlay_polygon_to_bool(config, 80, 80)
    shrunk = overlay_polygon_to_bool(eroded, 80, 80)
    assert int(np.count_nonzero(shrunk)) < int(np.count_nonzero(base))
    assert np.all(shrunk <= base)


def test_resolve_overlay_map_polygon_missing(tmp_path: Path) -> None:
    stage_dir = tmp_path / "mahi_mahi_resort"
    stage_dir.mkdir()
    assert resolve_overlay_map_polygon(tmp_path, stage_id="mahi_mahi_resort") is None


def test_resolve_and_roundtrip_yaml(tmp_path: Path) -> None:
    stage_id = "mahi_mahi_resort"
    stage_dir = tmp_path / stage_id
    config = OverlayMapPolygonConfig(
        stage_id=stage_id,
        polygon=[(0.3, 0.2), (0.7, 0.25), (0.65, 0.8), (0.25, 0.75)],
        erosion_pixels=0,
    )
    path = stage_dir / "stage_mask.yaml"
    write_overlay_map_polygon(path, config)
    loaded = load_overlay_map_polygon(path)
    assert loaded.stage_id == stage_id
    assert len(loaded.polygon) == 4
    resolved = resolve_overlay_map_polygon(tmp_path, stage_id=stage_id)
    assert resolved is not None
    assert resolved.polygon == loaded.polygon


def test_invalid_vertex_rejected() -> None:
    with pytest.raises(ValidationError, match="must be in"):
        OverlayMapPolygonConfig(
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
    assert resolve_overlay_map_polygon(tmp_path, stage_id="scorch_gorge") is None


def test_registered_polygons_use_the_25_current_production_outlines() -> None:
    """Production uses the 25 drawings plus Museum's accepted redraw."""
    root = Path(__file__).resolve().parents[1] / "configs" / "stage_maps"
    drawings = sorted(root.glob("*/gameplay/*/stage_mask.yaml"))
    assert len(drawings) == 25
    canonical_files = sorted(root.glob("*/stage_mask.yaml"))
    assert len(canonical_files) == 25

    museum_polygon = OverlayMapPolygonConfig(
        stage_id="museum_dalfonsino",
        polygon=[
            (0.192708, 0.789815),
            (0.250521, 0.827778),
            (0.267708, 0.786111),
            (0.369792, 0.817593),
            (0.411458, 0.670370),
            (0.546875, 0.826852),
            (0.604688, 0.677778),
            (0.683333, 0.722222),
            (0.720833, 0.548148),
            (0.711458, 0.528704),
            (0.736979, 0.392593),
            (0.770312, 0.418519),
            (0.785937, 0.275000),
            (0.786979, 0.169444),
            (0.751563, 0.144444),
            (0.729167, 0.237037),
            (0.630729, 0.162037),
            (0.588021, 0.346296),
            (0.455208, 0.260185),
            (0.438021, 0.325000),
            (0.346354, 0.293519),
            (0.330208, 0.326852),
            (0.317708, 0.321296),
            (0.282813, 0.460185),
            (0.294792, 0.475926),
            (0.266667, 0.580556),
            (0.229167, 0.564815),
            (0.193229, 0.788889),
        ],
        erosion_pixels=0,
    )
    museum_registered = resolve_overlay_map_polygon(
        root, stage_id="museum_dalfonsino"
    )
    assert museum_registered == museum_polygon
    assert overlay_polygon_sha256(museum_registered) == (
        "4cb3b5a592f7af2c3f06e134cece66514b67c2e26f922df1172b427580d2f4a2"
    )

    for drawing in drawings:
        expected = load_overlay_map_polygon(drawing)
        registered = resolve_overlay_map_polygon(root, stage_id=expected.stage_id)
        assert registered is not None
        canonical = root / expected.stage_id / "stage_mask.yaml"
        assert canonical.is_file()
        if expected.stage_id == "museum_dalfonsino":
            assert registered == museum_polygon
        else:
            assert registered == expected
            assert canonical.read_bytes() == drawing.read_bytes()
            assert overlay_polygon_sha256(registered) == overlay_polygon_sha256(expected)


def test_wrong_stage_polygon_is_rejected(tmp_path: Path) -> None:
    """An incorrect identity must not authorize observations for another stage."""
    write_overlay_map_polygon(
        tmp_path / "scorch_gorge" / "stage_mask.yaml",
        OverlayMapPolygonConfig(
            stage_id="makomart", polygon=[(0.1, 0.1), (0.9, 0.1), (0.5, 0.9)]
        ),
    )
    assert resolve_overlay_map_polygon(tmp_path, stage_id="scorch_gorge") is None
