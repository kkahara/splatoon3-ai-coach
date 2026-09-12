"""Unit tests for tools/map_ink_color_audit (synthetic; no GUI)."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import map_ink_color_audit as audit  # noqa: E402
from splatoon3_ai_coach.config.models import MapInkAnalyzerConfig
from splatoon3_ai_coach.vision.map_ink import MapInkClassifier
from splatoon3_ai_coach.vision.stage_maps import StageMapGeometry, StageMapRegion


def _full_frame_geometry() -> StageMapGeometry:
    return StageMapGeometry(
        stage_id="test_stage",
        regions=[StageMapRegion(id="R01", roi=(0.0, 0.0, 1.0, 1.0))],
    )


def _bgr_from_hsv(h: int, s: int, v: int, shape: tuple[int, int] = (100, 100)) -> np.ndarray:
    """Solid BGR image with the given OpenCV HSV triple."""
    hsv = np.zeros((shape[0], shape[1], 3), dtype=np.uint8)
    hsv[:, :] = (h, s, v)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def test_two_hue_blobs_recover_bins_and_share_of_union() -> None:
    """Two saturated blobs → top clusters; share_of_union uses full union."""
    # 100x100 frame; left half H=125 (blue-ish), right half H=12 (orange)
    left = _bgr_from_hsv(125, 200, 200, (100, 50))
    right = _bgr_from_hsv(12, 200, 200, (100, 50))
    image = np.concatenate([left, right], axis=1)
    assert image.shape == (100, 100, 3)

    # Low-sat gray strip would be ignored if present — paint a corner low-S
    # via HSV: S=10 should not enter candidates.
    gray = _bgr_from_hsv(90, 10, 180, (20, 20))
    image[0:20, 0:20] = gray

    clf = MapInkClassifier(MapInkAnalyzerConfig())
    result = audit.audit_map_union_colors(
        image,
        _full_frame_geometry(),
        clf,
        s_min=70,
        v_min=40,
        bin_width=10,
        top_n=5,
        label="synthetic",
    )

    assert result.union_pixels == 100 * 100
    # Candidates exclude the 20x20 low-S patch
    assert result.low_sat_pixels >= 20 * 20
    assert result.candidate_pixels < result.union_pixels
    assert result.candidate_fraction == pytest.approx(
        result.candidate_pixels / result.union_pixels
    )

    assert len(result.clusters) >= 2
    tops = {(c.h_lo, c.h_hi): c for c in result.clusters[:2]}
    # H=125 → bin [120,130); H=12 → bin [10,20)
    assert (120, 130) in tops or any(c.h_lo == 120 for c in result.clusters)
    assert (10, 20) in tops or any(c.h_lo == 10 for c in result.clusters)

    blue = next(c for c in result.clusters if c.h_lo == 120)
    orange = next(c for c in result.clusters if c.h_lo == 10)
    # share_of_union denominator is full union, not candidates
    assert blue.share_of_union == pytest.approx(blue.count / result.union_pixels)
    assert orange.share_of_union == pytest.approx(orange.count / result.union_pixels)
    assert blue.share_of_union < 0.55  # not relative to candidates-only
    assert blue.count + orange.count <= result.candidate_pixels


def test_low_sat_background_does_not_dominate_clusters() -> None:
    image = _bgr_from_hsv(40, 5, 200, (80, 80))  # low S everywhere
    # Small saturated blob
    blob = _bgr_from_hsv(130, 220, 220, (20, 20))
    image[30:50, 30:50] = blob
    clf = MapInkClassifier(MapInkAnalyzerConfig())
    result = audit.audit_map_union_colors(
        image, _full_frame_geometry(), clf, s_min=70, top_n=3
    )
    assert result.candidate_pixels == 20 * 20
    assert result.clusters[0].h_lo == 130
    assert result.clusters[0].count == 20 * 20
    assert result.clusters[0].share_of_union == pytest.approx(400 / 6400)


def test_format_report_includes_candidate_fraction() -> None:
    clf = MapInkClassifier(MapInkAnalyzerConfig())
    image = _bgr_from_hsv(125, 200, 200, (40, 40))
    result = audit.audit_map_union_colors(
        image, _full_frame_geometry(), clf, label="t"
    )
    text = audit.format_audit_report(result)
    assert "candidate_fraction" in text
    assert "share_of_union" in text
    assert "test_stage" in text


def test_main_writes_overlay(tmp_path: Path) -> None:
    frame = tmp_path / "frame.png"
    out = tmp_path / "audit.jpg"
    geom = tmp_path / "geom.yaml"
    geom.write_text(
        "stage_id: synth\nregions:\n  - id: R01\n    roi: [0.0, 0.0, 1.0, 1.0]\n",
        encoding="utf-8",
    )
    cv2.imwrite(str(frame), _bgr_from_hsv(12, 200, 200, (60, 60)))
    code = audit.main(
        [str(frame), "--geometry", str(geom), "-o", str(out), "--label", "x"]
    )
    assert code == 0
    assert out.is_file() and out.stat().st_size > 0
