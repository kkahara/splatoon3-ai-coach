"""Tests for the read-only roster X appearance study."""

from __future__ import annotations

import cv2
import numpy as np
import roster_x_appearance as xa

from splatoon3_ai_coach.vision.player_count import MaskedXTemplate, derive_x_mask


def _x_patch(size: int = 40, thickness: int = 5) -> np.ndarray:
    """Mid-gray diagonal X on a dark background."""
    patch = np.full((size, size), 30, dtype=np.uint8)
    cv2.line(patch, (2, 2), (size - 3, size - 3), 100, thickness)
    cv2.line(patch, (size - 3, 2), (2, size - 3), 100, thickness)
    return patch


def _template() -> MaskedXTemplate:
    image = _x_patch()
    return MaskedXTemplate(name="synthetic", image=image, mask=derive_x_mask(image))


def _frame_with_x(x: int, y: int) -> np.ndarray:
    """BGR frame with a light background and an X pasted at (x, y)."""
    gray = np.full((120, 200), 200, dtype=np.uint8)
    gray[y : y + 40, x : x + 40] = _x_patch()
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def test_match_templates_finds_and_sorts() -> None:
    frame = _frame_with_x(50, 30)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    other = MaskedXTemplate("other", np.full((40, 40), 160, np.uint8), _template().mask)
    hits = xa.match_templates(gray, [other, _template()])
    assert hits[0].name == "synthetic"
    assert hits[0].score < 1e-3
    assert (hits[0].x, hits[0].y) == (50, 30)
    assert hits[0].score <= hits[1].score


def test_masked_support_is_clean_at_true_placement() -> None:
    frame = _frame_with_x(50, 30)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hit = xa.match_templates(gray, [_template()])[0]
    support = xa.masked_support(gray, hsv, hit)
    assert support["mask_in_gate_frac"] > 0.95
    assert support["mask_bright_frac"] < 0.05
    assert abs(support["mask_mean_diff"]) < 2


def test_context_probe_recovers_x_clipped_by_roi_edge() -> None:
    frame = _frame_with_x(130, 40)
    box = (60, 30, 160, 100)
    roi_gray = cv2.cvtColor(frame[30:100, 60:160], cv2.COLOR_BGR2GRAY)
    in_roi = xa.match_templates(roi_gray, [_template()])[0]
    probe = xa.context_probe(frame, box, [_template()])
    assert in_roi.score > 0.03
    assert probe["context_best_score"] < 1e-3
    assert probe["context_overhang_px"]["right"] == 10
    assert probe["context_center_x"] > probe["reachable_center_x"][1]


def test_x_geometry_reports_extent_touch_and_diagonals() -> None:
    frame = _frame_with_x(130, 40)
    roi = frame[30:100, 60:160]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    geometry = xa.x_geometry(gray, hsv)
    assert geometry["x_found"]
    assert "right" in geometry["x_touches"]
    assert abs(abs(geometry["x_angle_down_deg"]) - 45) < 8
    assert abs(abs(geometry["x_angle_up_deg"]) - 45) < 8


def test_x_geometry_absent_returns_empty() -> None:
    roi = np.full((60, 60, 3), 200, dtype=np.uint8)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    geometry = xa.x_geometry(gray, hsv)
    assert geometry["x_found"] is False
    assert geometry["x_width"] is None


def test_matched_control_prefers_same_frame_same_side() -> None:
    def row(sample: str, slot: str, group: str, match: str = "m") -> object:
        return type(
            "Row",
            (),
            {
                "sample_id": sample,
                "slot": slot,
                "group": group,
                "match": match,
                "side": "ally" if slot.startswith("a") else "opponent",
            },
        )()

    fn = row("R1", "a1", "fn")
    rows = [fn, row("R0", "a1", "tp"), row("R1", "o2", "tp"), row("R1", "a3", "tp")]
    assert xa.matched_control(fn, rows).slot == "a3"
    rows = [fn, row("R0", "a1", "tp"), row("R1", "o2", "tp")]
    assert xa.matched_control(fn, rows).slot == "o2"
    rows = [fn, row("R0", "a1", "tp", "other"), row("R2", "a1", "tp")]
    assert xa.matched_control(fn, rows).sample_id == "R2"
