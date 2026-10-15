"""Tests for the development-only dynamic roster ROI prototype."""

from __future__ import annotations

import numpy as np
import roster_dynamic_roi as dynamic
import roster_geometry_survey as geometry


def _boxes() -> list[geometry.Box]:
    """Return two four-slot synthetic side layouts."""
    return [
        geometry.Box(20 + index * 30, 10, 45 + index * 30, 50) for index in range(4)
    ] + [geometry.Box(220 + index * 30, 10, 245 + index * 30, 50) for index in range(4)]


def test_layout_fit_stays_constrained_for_shifted_row() -> None:
    image = np.zeros((100, 400, 3), dtype=np.uint8)
    for box in _boxes():
        image[box.y1 : box.y2, box.x1 : box.x2] = (120, 80, 220)
    base = _boxes()
    fitted, model, _score = geometry.fit_layout(image, base, 0)
    assert model in {"M1", "M2"}
    assert (
        max(abs(a.center[0] - b.center[0]) for a, b in zip(fitted, base[:4], strict=True))
        <= 16
    )


def test_layout_fit_can_represent_a_scaled_row() -> None:
    image = np.zeros((100, 400, 3), dtype=np.uint8)
    for index in range(4):
        left = 25 + index * 34
        image[8:54, left : left + 38] = (80, 160, 220)
    fitted, _model, _score = geometry.fit_layout(image, _boxes(), 0)
    assert all(box.width >= 20 for box in fitted)


def test_normalized_crop_has_canonical_size() -> None:
    image = np.full((80, 80, 3), 100, dtype=np.uint8)
    crop = dynamic._normalized_crop(image, geometry.Box(10, 10, 50, 40))
    assert crop.shape == (96, 96, 3)


def test_missing_row_has_no_invalid_box() -> None:
    image = np.zeros((100, 400, 3), dtype=np.uint8)
    fitted, _model, _score = geometry.fit_layout(image, _boxes(), 0)
    assert len(fitted) == 4
    assert all(box.x2 > box.x1 and box.y2 > box.y1 for box in fitted)
