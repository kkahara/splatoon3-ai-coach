"""Unit tests for MapOverlayDetector close-button + layout gates."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import MapOverlayDetectorConfig
from splatoon3_ai_coach.vision.map_overlay import MapOverlayDetector

_TEMPLATE_DIR = Path("calibration/templates/map_overlay")
_MAP_ROI = (0.0138889, 0.0171875, 0.1777778, 0.16875)
_TANK_ROI = (0.46, 0.40, 0.54, 0.62)


def _blank_frame() -> np.ndarray:
    return np.full((1080, 1920, 3), 40, dtype=np.uint8)


def _roi_slices(box: tuple[float, float, float, float]) -> tuple[slice, slice]:
    x1, y1, x2, y2 = box
    return (
        slice(int(y1 * 1080), int(y2 * 1080)),
        slice(int(x1 * 1920), int(x2 * 1920)),
    )


def _paste_fit(image: np.ndarray, crop: np.ndarray, ys: slice, xs: slice) -> None:
    """Paste ``crop`` into ``image[ys, xs]`` without stretching aspect ratio."""
    roi_h, roi_w = ys.stop - ys.start, xs.stop - xs.start
    th, tw = crop.shape[:2]
    scale = min(roi_h / th, roi_w / tw)
    new_w = max(4, int(tw * scale))
    new_h = max(4, int(th * scale))
    fitted = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_AREA)
    image[ys.start : ys.start + new_h, xs.start : xs.start + new_w] = fitted


def _paint_center_edges(image: np.ndarray) -> None:
    """Add high-frequency structure in tank_roi (open-map center cue)."""
    ys, xs = _roi_slices(_TANK_ROI)
    h = ys.stop - ys.start
    w = xs.stop - xs.start
    noise = np.zeros((h, w, 3), dtype=np.uint8)
    for i in range(0, h, 3):
        noise[i, :, :] = 220
    for j in range(0, w, 3):
        noise[:, j, :] = 180
    image[ys, xs] = noise


def _detector(**overrides: object) -> MapOverlayDetector:
    cfg = MapOverlayDetectorConfig(
        map_roi=_MAP_ROI,
        tank_roi=_TANK_ROI,
        template_dir=_TEMPLATE_DIR,
        match_threshold=0.70,
        map_edge_max=0.04,
        tank_edge_min=0.10,
        support_template_min=0.35,
        **overrides,  # type: ignore[arg-type]
    )
    return MapOverlayDetector(cfg)


def test_layout_and_template_detect_map_open() -> None:
    templates = sorted(_TEMPLATE_DIR.glob("*.png"))
    assert templates, "expected map overlay close-button templates"
    crop = cv2.imread(str(templates[0]))
    assert crop is not None

    image = _blank_frame()
    ys, xs = _roi_slices(_MAP_ROI)
    # Soft flat map-roi background + small close-X (keeps map_edge low).
    image[ys, xs] = 30
    _paste_fit(image, crop, ys, xs)
    _paint_center_edges(image)

    reading, confidence = _detector().detect(image, timestamp=1.0)
    assert reading is not None
    assert reading.present is True
    assert reading.template_score >= 0.35
    assert reading.center_tank_edge_frac >= 0.10
    assert confidence >= 0.35


def test_strong_template_without_layout_is_not_present() -> None:
    """HUD X-like matches without map layout must not fire (GT FP @15s)."""
    templates = sorted(_TEMPLATE_DIR.glob("*.png"))
    crop = cv2.imread(str(templates[0]))
    assert crop is not None
    image = _blank_frame()
    ys, xs = _roi_slices(_MAP_ROI)
    _paste_fit(image, crop, ys, xs)
    # Center stays flat → tank_edge low.
    reading, _confidence = _detector().detect(image, timestamp=1.0)
    assert reading is not None
    assert reading.present is False


def test_blank_roi_is_not_present() -> None:
    reading, confidence = _detector().detect(_blank_frame(), timestamp=1.0)
    assert reading is not None
    assert reading.present is False
    assert confidence >= 0.50
