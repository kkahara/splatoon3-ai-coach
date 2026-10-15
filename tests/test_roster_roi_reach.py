"""Tests for the development-only roster ROI reach experiment."""

from __future__ import annotations

import cv2
import numpy as np
import roster_roi_reach as reach

from splatoon3_ai_coach.vision.player_count import (
    MaskedXTemplate,
    _best_masked_sqdiff,
    derive_x_mask,
)


def _x_patch(size: int = 40, thickness: int = 5) -> np.ndarray:
    patch = np.full((size, size), 30, dtype=np.uint8)
    cv2.line(patch, (2, 2), (size - 3, size - 3), 100, thickness)
    cv2.line(patch, (size - 3, 2), (2, size - 3), 100, thickness)
    return patch


def _templates() -> list[MaskedXTemplate]:
    image = _x_patch()
    return [MaskedXTemplate("synthetic", image, derive_x_mask(image))]


def _frame(x: int) -> np.ndarray:
    gray = np.full((80, 260), 200, dtype=np.uint8)
    gray[20:60, x : x + 40] = _x_patch()
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


BOX = (60, 10, 140, 70)


def test_zero_extension_equals_production_matcher() -> None:
    frame = _frame(80)
    maps = reach.slot_maps(frame, BOX, _templates())
    roi = frame[BOX[1] : BOX[3], BOX[0] : BOX[2]]
    production, _ = _best_masked_sqdiff(roi, _templates())
    assert abs(reach.best_placement(maps, BOX, 0, 0).score - production) < 1e-6


def test_right_extension_recovers_clipped_x_only_once_reachable() -> None:
    frame = _frame(110)
    maps = reach.slot_maps(frame, BOX, _templates())
    assert reach.label_of(reach.best_placement(maps, BOX, 0, 0).score) == "alive"
    assert reach.label_of(reach.best_placement(maps, BOX, 0, 9).score) == "alive"
    hit = reach.best_placement(maps, BOX, 0, 10)
    assert reach.label_of(hit.score) == "x"
    assert hit.left == 110


def test_left_extension_does_not_enlarge_right_side() -> None:
    frame = _frame(110)
    maps = reach.slot_maps(frame, BOX, _templates())
    assert reach.label_of(reach.best_placement(maps, BOX, 24, 0).score) == "alive"


def test_reach_interval_and_bins() -> None:
    frame = _frame(80)
    maps = reach.slot_maps(frame, BOX, _templates())
    lo, hi = reach.reach_interval(maps, BOX, "synthetic")
    assert hi - lo == (BOX[2] - BOX[0]) - 40
    assert reach.distance_bin(0.4) == "inside 0"
    assert reach.distance_bin(3.0) == "inside 1-4"
    assert reach.distance_bin(-12.0) == "beyond 10-14"
    assert reach.distance_bin(20.0) == "inside 15+"


def test_clusters_split_on_gap() -> None:
    groups = reach.clusters([591.1, 607.1, 592.1, 608.1, 543.0])
    assert [len(group) for group in groups] == [1, 2, 2]


def test_count_metrics_side_and_avb() -> None:
    names = reach.SLOTS
    labels = {"S": {name: "alive" for name in names} | {"a1": "x"}}
    fixed = {("S", name): "alive" for name in names}
    predicted = dict(fixed) | {("S", "a1"): "x"}
    result = reach.count_metrics(labels, fixed, predicted)
    assert result["side_count_exact"] == 2
    assert result["raw_avb_exact"] == 1
    assert result["avb_transitions"] == {"wrong->correct": 1}


def test_count_metrics_skip_ambiguous_side() -> None:
    names = reach.SLOTS
    labels = {"S": {name: "alive" for name in names} | {"o2": "?"}}
    fixed = {("S", name): "alive" for name in names if name != "o2"}
    result = reach.count_metrics(labels, fixed, dict(fixed))
    assert result["side_count_comparable"] == 1
    assert result["raw_avb_comparable"] == 0
