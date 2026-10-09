"""Cursor Remaining labels and the penalty pill under each label."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.score import (
    _white_text_mask,
    read_penalty_side,
    read_score_side,
)
from splatoon3_ai_coach.vision.team_color_calibration import circular_hue_distance
from splatoon3_ai_coach.vision.templates import load_templates
from splatoon3_ai_coach.vision.timer import match_glyph

from .models import CursorScoreObservation

_MATCH_THRESHOLD = 0.55
_LABEL_UNKNOWN = "label unknown"
_UNREADABLE = "unreadable"


@dataclass
class _Label:
    """A Remaining label found near the cursor, before team assignment."""

    x: int
    y: int
    width: int
    height: int
    hue: float
    team_row: str | None = None


class CursorScoreReader:
    """Read both teams' remaining values and penalty pills at one cursor."""

    def __init__(self, template_dir: object | None = None) -> None:
        directory = template_dir or PROJECT_ROOT / "calibration" / "templates"
        self._templates = load_templates(directory)  # type: ignore[arg-type]

    def read(
        self,
        image: np.ndarray,
        cursor_x: int,
        *,
        top_hue: float | None,
        bottom_hue: float | None,
        config: ReviewTimelineConfig | None = None,
    ) -> CursorScoreObservation:
        """Assign each label to a team row by color, then read that label."""
        config = config or ReviewTimelineConfig()
        labels = _find_labels(image, cursor_x, config)
        _assign_teams(labels, top_hue, bottom_hue, config.team_color_min_margin)
        observation = _unknown_observation()
        for label in labels:
            if label.team_row not in {"top", "bottom"}:
                continue
            _apply_label(observation, image, label, labels, self._templates)
        return observation


def _unknown_observation() -> CursorScoreObservation:
    """Start from unknown. Zero is filled in only by a confident empty pill."""
    return CursorScoreObservation(
        top_remaining_reason=_LABEL_UNKNOWN,
        bottom_remaining_reason=_LABEL_UNKNOWN,
        top_penalty_reason=_LABEL_UNKNOWN,
        bottom_penalty_reason=_LABEL_UNKNOWN,
    )


def _find_labels(
    image: np.ndarray, cursor_x: int, config: ReviewTimelineConfig
) -> list[_Label]:
    """Find Remaining-label bodies to the left of the cursor."""
    height, width = image.shape[:2]
    y1 = int(height * config.graph_roi[1])
    y2 = int(height * config.graph_roi[3])
    x1 = max(0, cursor_x - int(width * 0.10))
    x2 = min(width, cursor_x + int(width * 0.015))
    if x2 <= x1 or y2 <= y1:
        return []
    window = image[y1:y2, x1:x2]
    boxes = _label_boxes(window)
    max_width = int(width * 0.11)
    labels: list[_Label] = []
    for x, y, box_w, box_h, hue in boxes:
        full_x = x1 + x
        right = full_x + box_w
        if box_w > max_width:
            full_x = right - max_width
            box_w = max_width
        labels.append(_Label(full_x, y1 + y, box_w, box_h, hue))
    return labels


def _label_boxes(window: np.ndarray) -> list[tuple[int, int, int, int, float]]:
    """Cluster bright team-colored pieces into label-sized boxes."""
    hsv = cv2.cvtColor(window, cv2.COLOR_BGR2HSV)
    mask = ((hsv[..., 1] > 100) & (hsv[..., 2] > 150)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    height, width = window.shape[:2]
    minimum = max(12, int(width * height * 0.004))
    pieces = [
        stats[index]
        for index in range(1, count)
        if int(stats[index][4]) >= minimum
    ]
    merged = _merge_pieces(pieces, height)
    boxes: list[tuple[int, int, int, int, float]] = []
    for x, y, box_w, box_h in merged:
        if not _label_sized(box_w, box_h, width, height):
            continue
        hue = _box_hue(hsv, x, y, box_w, box_h)
        if hue is None:
            continue
        boxes.append((x, y, box_w, box_h, hue))
    return boxes


def _label_sized(box_w: int, box_h: int, width: int, height: int) -> bool:
    """Accept a box whose size matches a Remaining label in this window."""
    return (
        width * 0.18 <= box_w <= width * 0.95
        and height * 0.08 <= box_h <= height * 0.28
    )


def _merge_pieces(
    pieces: list[np.ndarray], window_height: int
) -> list[tuple[int, int, int, int]]:
    """Join glyph holes in one label without joining the two teams' labels."""
    pending = [tuple(int(value) for value in piece[:4]) for piece in pieces]
    groups: list[list[tuple[int, int, int, int]]] = []
    for piece in pending:
        placed = False
        for group in groups:
            if any(_same_label(piece, other, window_height) for other in group):
                group.append(piece)
                placed = True
                break
        if not placed:
            groups.append([piece])
    return [_union(group) for group in groups]


def _same_label(
    left: tuple[int, int, int, int],
    right: tuple[int, int, int, int],
    window_height: int,
) -> bool:
    """Two pieces belong to one label when their vertical centers are close."""
    gap = window_height * 0.04
    left_center = left[1] + left[3] / 2
    right_center = right[1] + right[3] / 2
    return abs(left_center - right_center) <= max(gap, min(left[3], right[3]))


def _union(group: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    """Bounding box of one label's pieces."""
    x1 = min(piece[0] for piece in group)
    y1 = min(piece[1] for piece in group)
    x2 = max(piece[0] + piece[2] for piece in group)
    y2 = max(piece[1] + piece[3] for piece in group)
    return x1, y1, x2 - x1, y2 - y1


def _box_hue(
    hsv: np.ndarray, x: int, y: int, width: int, height: int
) -> float | None:
    """Median hue of the saturated pixels inside a label box."""
    crop = hsv[y : y + height, x : x + width]
    selected = (crop[..., 1] > 100) & (crop[..., 2] > 150)
    if int(np.count_nonzero(selected)) < 10:
        return None
    return float(np.median(crop[..., 0][selected].astype(np.float64)))


def _assign_teams(
    labels: list[_Label],
    top_hue: float | None,
    bottom_hue: float | None,
    minimum_margin: float,
) -> None:
    """Match label color to a team row. Vertical position is not used."""
    if top_hue is None or bottom_hue is None:
        return
    for label in labels:
        top_distance = circular_hue_distance(label.hue, top_hue)
        bottom_distance = circular_hue_distance(label.hue, bottom_hue)
        if abs(top_distance - bottom_distance) < minimum_margin:
            continue
        label.team_row = "top" if top_distance < bottom_distance else "bottom"
    _keep_closer_duplicate(labels, top_hue, "top")
    _keep_closer_duplicate(labels, bottom_hue, "bottom")


def _keep_closer_duplicate(
    labels: list[_Label], hue: float, team_row: str
) -> None:
    """Leave one label per team row when two boxes share a color match."""
    matched = [label for label in labels if label.team_row == team_row]
    if len(matched) < 2:
        return
    matched.sort(key=lambda label: circular_hue_distance(label.hue, hue))
    for label in matched[1:]:
        label.team_row = None


def _apply_label(
    observation: CursorScoreObservation,
    image: np.ndarray,
    label: _Label,
    labels: list[_Label],
    templates: dict[str, list[np.ndarray]],
) -> None:
    """Store one identified label's remaining value and its own penalty."""
    remaining, confidence = _read_remaining(image, label, templates)
    if remaining is None:
        penalty, penalty_confidence, visible, reason = None, 0.0, None, _LABEL_UNKNOWN
    else:
        penalty, penalty_confidence, visible, reason = _read_penalty(
            image, label, labels, templates
        )
    prefix = str(label.team_row)
    values: dict[str, object] = {
        f"{prefix}_remaining": remaining,
        f"{prefix}_remaining_confidence": confidence,
        f"{prefix}_remaining_reason": None if remaining is not None else _UNREADABLE,
        f"{prefix}_penalty": penalty,
        f"{prefix}_penalty_confidence": penalty_confidence,
        f"{prefix}_penalty_pill_visible": visible,
        f"{prefix}_penalty_reason": reason,
    }
    for name, value in values.items():
        setattr(observation, name, value)


def _read_remaining(
    image: np.ndarray, label: _Label, templates: dict[str, list[np.ndarray]]
) -> tuple[int | None, float]:
    """Read the large digits on one Remaining label."""
    box = (label.x, label.y, label.width, label.height)
    reading = read_score_side(
        image, _normalized_box(image, *box), templates, match_threshold=_MATCH_THRESHOLD
    )
    if reading.visible and reading.value is not None:
        scores = reading.digit_scores
        return int(reading.value), float(np.mean(scores)) if scores else 0.0
    lifted = _expanded_box(image, label)
    x, y, width, height = lifted
    return _white_digits(image[y : y + height, x : x + width], templates)


def _expanded_box(
    image: np.ndarray, label: _Label
) -> tuple[int, int, int, int]:
    """Include digits that sit slightly outside the saturated label body."""
    height, width = image.shape[:2]
    lift = int(label.height * 0.45)
    left = max(0, label.x - int(label.width * 0.2))
    top = max(0, label.y - lift)
    right = min(width, label.x + label.width)
    bottom = min(height, label.y + label.height)
    return left, top, right - left, bottom - top


def _white_digits(
    crop: np.ndarray, templates: dict[str, list[np.ndarray]]
) -> tuple[int | None, float]:
    """Match the tall white digit row and ignore the cursor line and the word."""
    if crop.size == 0:
        return None, 0.0
    mask = _white_text_mask(crop)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    pieces = []
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        too_thin = width < height * 0.25
        too_tall = height > crop.shape[0] * 0.75
        if area < 8 or height < 8 or too_thin or too_tall:
            continue
        pieces.append((x, y, width, height))
    if not pieces:
        return None, 0.0
    tallest = max(piece[3] for piece in pieces)
    digit_templates = {
        key: value for key, value in templates.items() if key in "0123456789"
    }
    pairs: list[tuple[int, str, float]] = []
    for x, y, width, height in sorted(pieces):
        if height < tallest * 0.65:
            continue
        glyph = mask[y : y + height, x : x + width]
        symbol, score = match_glyph(glyph, digit_templates, 0.5)
        if symbol is None:
            continue
        pairs.append((x, symbol, float(score)))
    if not pairs:
        return None, 0.0
    value = int("".join(symbol for _, symbol, _ in pairs[:3]))
    if value > 100:
        return None, 0.0
    return value, float(np.mean([score for _, _, score in pairs[:3]]))


def _read_penalty(
    image: np.ndarray,
    label: _Label,
    labels: list[_Label],
    templates: dict[str, list[np.ndarray]],
) -> tuple[int | None, float, bool | None, str | None]:
    """Read the pill directly under a confidently read label.

    The caller only reaches this after the label's remaining value was read.
    """
    window = _pill_window(label, labels, image.shape[0])
    if window is None:
        return None, 0.0, None, "penalty region unavailable"
    x, y, width, height = window
    reading = read_penalty_side(
        image,
        _normalized_box(image, x, y, width, height),
        templates,
        match_threshold=_MATCH_THRESHOLD,
    )
    scores = reading.digit_scores
    confidence = float(np.mean(scores)) if scores else 0.0
    pill_present = _dark_pill_box(image[y : y + height, x : x + width]) is not None
    return penalty_result(reading.value, confidence, pill_present)


def penalty_result(
    value: int | None, confidence: float, pill_present: bool
) -> tuple[int | None, float, bool | None, str | None]:
    """Penalty semantics for a label that was already confidently read.

    Readable digits give that number. A pill without readable digits is
    unknown, never zero. Zero needs the label's own region to be empty.
    """
    if value is not None:
        return int(value), confidence, True, None
    if pill_present:
        return None, 0.0, True, _UNREADABLE
    return 0, 1.0, False, None


def _pill_window(
    label: _Label, labels: list[_Label], image_height: int
) -> tuple[int, int, int, int] | None:
    """Short region under this label, stopping before the next label."""
    below = [
        other.y
        for other in labels
        if other is not label and other.y >= label.y + label.height
    ]
    next_top = min(below) if below else image_height
    overlap = int(label.height * 0.35)
    top = max(0, label.y + label.height - overlap)
    height = min(int(label.height * 0.95), next_top - top)
    if height < max(8, int(label.height * 0.25)):
        return None
    return label.x, top, label.width, height


def _dark_pill_box(crop: np.ndarray) -> tuple[int, int, int, int] | None:
    """Bounding box of this label's dark pill, or none when the region is empty."""
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    dark = ((hsv[..., 2] < 70) & (hsv[..., 1] < 100)).astype(np.uint8)
    dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((3, 7), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(dark)
    minimum = max(12, int(crop.shape[0] * crop.shape[1] * 0.04))
    candidates = [
        stats[index]
        for index in range(1, count)
        if int(stats[index][4]) >= minimum
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda item: int(item[4]))
    pad = 2
    x = max(0, int(best[0]) - pad)
    y = max(0, int(best[1]) - pad)
    return x, y, int(best[2]) + pad * 2, int(best[3]) + pad * 2


def _normalized_box(
    image: np.ndarray, x: int, y: int, width: int, height: int
) -> tuple[float, float, float, float]:
    """Convert a pixel box to the normalized box the score reader expects."""
    frame_h, frame_w = image.shape[:2]
    return (
        x / frame_w,
        y / frame_h,
        (x + width) / frame_w,
        (y + height) / frame_h,
    )
