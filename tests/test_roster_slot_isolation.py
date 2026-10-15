"""Tests for the development-only roster slot-isolation experiment."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import roster_slot_isolation as isolation


def _boxes() -> list[isolation.Box]:
    """Return production-like overlapping boxes."""
    return [
        isolation.Box(20 + index * 30, 10, 65 + index * 30, 60) for index in range(4)
    ] + [isolation.Box(220 + index * 30, 10, 265 + index * 30, 60) for index in range(4)]


def test_contamination_metrics_detect_neighbor_energy() -> None:
    profile = np.zeros(180, dtype=np.float32)
    profile[20:55] = 1.0
    profile[50:80] = 1.0
    target = (20.0, 70.0)
    extent, coverage, occupancy, contamination, offset, status = isolation._slot_metrics(
        profile,
        isolation.Box(0, 0, 100, 60),
        isolation.Box(20, 0, 90, 60),
        target,
        [(20.0, 70.0), (70.0, 120.0), (120.0, 170.0), (170.0, 180.0)],
        0,
        None,
    )
    assert extent > 0
    assert coverage > 0.85
    assert occupancy > 0
    assert contamination > 0
    assert offset != 0
    assert status == "target_covered_neighbor_contaminated"


def test_divider_estimate_stays_within_calibrated_bounds() -> None:
    image = np.zeros((100, 400, 3), dtype=np.uint8)
    image[:, 20:55] = (80, 120, 220)
    image[:, 80:115] = (80, 120, 220)
    image[:, 140:175] = (80, 120, 220)
    image[:, 200:235] = (80, 120, 220)
    boxes = _boxes()
    dividers, _confidence, _ok = isolation.estimate_dividers(image, boxes, 0)
    prior = isolation.canonical_dividers(boxes, 0)
    assert all(
        abs(actual - expected) <= 18
        for actual, expected in zip(dividers, prior, strict=True)
    )


def test_variant_b_preserves_native_canvas_dimensions() -> None:
    image = np.full((80, 120, 3), 40, dtype=np.uint8)
    image[10:60, 20:70] = (80, 120, 220)
    box = isolation.Box(10, 10, 90, 70)
    crop = isolation._variant_b(image, box, (30.0, 70.0))
    assert crop.shape == (box.height, box.width, 3)


def test_variant_a_keeps_original_pixels() -> None:
    image = np.full((30, 40, 3), 80, dtype=np.uint8)
    original = image.copy()
    template = np.full((8, 8), 80, dtype=np.uint8)
    mask = np.full((8, 8), 255, dtype=np.uint8)
    isolation._matches(
        image,
        [SimpleNamespace(image=template, mask=mask, name="synthetic")],
        allowed_center_x=(10.0, 30.0),
    )
    assert np.array_equal(image, original)


def test_territory_labels_neighbor_and_target_separately() -> None:
    target = (50.0, 100.0)
    match = isolation.Match(0.01, 0, 0, 75.0, 20.0, "x")
    assert isolation._territory(match, target) == "target territory"
    neighbor = isolation.Match(0.01, 0, 0, 120.0, 20.0, "x")
    assert isolation._territory(neighbor, target) == "neighbor territory"
