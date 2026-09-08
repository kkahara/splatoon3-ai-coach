"""Unit tests for MapOverlayDetector close-button templates."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import MapOverlayDetectorConfig
from splatoon3_ai_coach.vision.map_overlay import MapOverlayDetector

_TEMPLATE_DIR = Path("calibration/templates/map_overlay")
_MAP_ROI = (0.0138889, 0.0171875, 0.1777778, 0.16875)


def _blank_frame() -> np.ndarray:
    return np.full((1080, 1920, 3), 40, dtype=np.uint8)


def _roi_slices() -> tuple[slice, slice]:
    x1, y1, x2, y2 = _MAP_ROI
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


def test_template_match_detects_close_x() -> None:
    templates = sorted(_TEMPLATE_DIR.glob("*.png"))
    assert templates, "expected map overlay close-button templates"
    crop = cv2.imread(str(templates[0]))
    assert crop is not None

    image = _blank_frame()
    ys, xs = _roi_slices()
    _paste_fit(image, crop, ys, xs)

    detector = MapOverlayDetector(
        MapOverlayDetectorConfig(
            map_roi=_MAP_ROI,
            template_dir=_TEMPLATE_DIR,
            match_threshold=0.70,
        )
    )
    reading, confidence = detector.detect(image, timestamp=1.0)
    assert reading is not None
    assert reading.present is True
    assert reading.template_score >= 0.70
    assert confidence >= 0.70


def test_blank_roi_is_not_present() -> None:
    detector = MapOverlayDetector(
        MapOverlayDetectorConfig(
            map_roi=_MAP_ROI,
            template_dir=_TEMPLATE_DIR,
            match_threshold=0.70,
        )
    )
    reading, confidence = detector.detect(_blank_frame(), timestamp=1.0)
    assert reading is not None
    assert reading.present is False
    assert confidence >= 0.50
