"""Unit tests for tools/roi_visualize parse + render (no GUI)."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import roi_visualize as viz  # noqa: E402


def test_parse_unnamed_and_named_roi() -> None:
    assert viz.parse_roi_arg("[0.44, 0.06, 0.73, 0.28]") == (
        None,
        (0.44, 0.06, 0.73, 0.28),
    )
    assert viz.parse_roi_arg("R01=[0.4427,0.0648,0.7292,0.2778]") == (
        "R01",
        (0.4427, 0.0648, 0.7292, 0.2778),
    )


def test_collect_cli_rois_auto_names_and_name_flag() -> None:
    named = viz.collect_cli_rois(
        ["R01=[0.1,0.1,0.2,0.2]", "[0.3,0.3,0.4,0.4]"],
    )
    assert named[0][0] == "R01"
    assert named[1][0] == "roi"
    single = viz.collect_cli_rois(["[0.1,0.1,0.5,0.5]"], name="R01")
    assert single == [("R01", (0.1, 0.1, 0.5, 0.5))]


def test_collect_rejects_name_with_multiple_or_named() -> None:
    with pytest.raises(ValueError, match="--name only applies"):
        viz.collect_cli_rois(
            ["[0.1,0.1,0.2,0.2]", "[0.3,0.3,0.4,0.4]"],
            name="R01",
        )
    with pytest.raises(ValueError, match="--name only applies"):
        viz.collect_cli_rois(["R01=[0.1,0.1,0.2,0.2]"], name="X")


def test_rejects_inverted_and_out_of_range() -> None:
    with pytest.raises(ValueError, match="positive area"):
        viz.parse_normalized_box("[0.5, 0.1, 0.2, 0.3]")
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        viz.parse_normalized_box("[-0.1, 0.0, 0.5, 0.5]")


def test_render_rois_writes_nonempty_file(tmp_path: Path) -> None:
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    regions = [
        ("R01", (0.1, 0.1, 0.3, 0.3)),
        ("R02", (0.25, 0.25, 0.5, 0.5)),
    ]
    canvas = viz.render_rois(image, regions)
    assert canvas.shape == image.shape
    out = tmp_path / "out.jpg"
    assert cv2.imwrite(str(out), canvas)
    assert out.is_file() and out.stat().st_size > 0


def test_main_with_synthetic_image(tmp_path: Path) -> None:
    frame = tmp_path / "frame.png"
    out = tmp_path / "rois.jpg"
    cv2.imwrite(str(frame), np.zeros((240, 320, 3), dtype=np.uint8))
    code = viz.main(
        [
            str(frame),
            "--roi",
            "R01=[0.1,0.1,0.4,0.4]",
            "--roi",
            "R02=[0.3,0.3,0.7,0.7]",
            "-o",
            str(out),
        ]
    )
    assert code == 0
    assert out.is_file() and out.stat().st_size > 0
