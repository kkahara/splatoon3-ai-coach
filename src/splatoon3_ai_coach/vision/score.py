"""Splat Zones remaining-score detector (observe-only).

Screen geometry only: ``left`` / ``right`` counters. Ally/opponent mapping
belongs to fusion later — not this detector.

Does not emit GameEvents. Penalty ``+N`` is an optional secondary observation
and must not gate main-counter success.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ScoreDetectorConfig
from splatoon3_ai_coach.types import NormalizedBox
from splatoon3_ai_coach.vision.models import ScoreReading, ScoreSideReading
from splatoon3_ai_coach.vision.roi import crop_roi
from splatoon3_ai_coach.vision.templates import load_templates
from splatoon3_ai_coach.vision.timer import (
    SegmentationResult,
    match_glyph,
    segment_timer_roi,
)

_WHITE_MIN = 185
_WHITE_MAX_SPREAD = 60


def _segment_score_roi(
    roi: np.ndarray,
    *,
    invert: bool,
    min_area: int = 25,
) -> SegmentationResult:
    """Segment digit glyphs; invert for dark digits on bright team-colored pods."""
    if not invert:
        return segment_timer_roi(roi, min_area=min_area)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
    inverted = cv2.bitwise_not(gray)
    return segment_timer_roi(
        cv2.cvtColor(inverted, cv2.COLOR_GRAY2BGR), min_area=min_area
    )


def _digit_pairs_from_seg(
    seg: SegmentationResult,
    digit_templates: dict[str, list[np.ndarray]],
    *,
    match_threshold: float,
) -> list[tuple[int, str, float]]:
    """Keep left-to-right digit-like glyphs; drop edge fragments and colon noise."""
    roi_w = int(seg.gray.shape[1])
    pairs: list[tuple[int, str, float]] = []
    for box, glyph in zip(seg.final_boxes, seg.glyphs, strict=True):
        if glyph.shape[0] < 5 or glyph.shape[1] < 2:
            continue
        if box.label in {"dot_like", "colon"}:
            continue
        if box.x <= 1 or box.x + box.w >= roi_w - 1:
            continue
        symbol, score = match_glyph(glyph, digit_templates, match_threshold)
        if symbol is None:
            continue
        pairs.append((box.x, symbol, float(score)))
    pairs.sort(key=lambda item: item[0])
    return pairs[:3]


def _candidate_rank(side: ScoreSideReading) -> tuple[int, float]:
    """Prefer more digits, then higher mean score."""
    mean = float(np.mean(side.digit_scores)) if side.digit_scores else 0.0
    return (len(side.digit_scores), mean)


def read_score_side(
    image: np.ndarray,
    roi_box: NormalizedBox,
    templates: dict[str, list[np.ndarray]],
    *,
    match_threshold: float,
) -> ScoreSideReading:
    """Read one remaining counter (normal then inverted polarity)."""
    crop = crop_roi(image, roi_box)
    digit_templates = {k: v for k, v in templates.items() if k in "0123456789"}
    best: ScoreSideReading | None = None
    for invert in (False, True):
        seg = _segment_score_roi(crop, invert=invert)
        pairs = _digit_pairs_from_seg(
            seg, digit_templates, match_threshold=match_threshold
        )
        if not pairs:
            continue
        symbols = [p[1] for p in pairs]
        scores = [p[2] for p in pairs]
        try:
            value = int("".join(symbols))
        except ValueError:
            continue
        if value > 100:
            continue
        candidate = ScoreSideReading(
            value=value,
            digit_scores=scores,
            visible=True,
        )
        if best is None or _candidate_rank(candidate) > _candidate_rank(best):
            best = candidate
    return best if best is not None else ScoreSideReading(visible=False)


@dataclass(frozen=True)
class PenaltySideRead:
    """One ``+N`` penalty observation (screen side, not team)."""

    value: int | None
    digit_scores: list[float]


_NO_PENALTY = PenaltySideRead(value=None, digit_scores=[])


def _white_text_mask(roi: np.ndarray) -> np.ndarray:
    """Near-white, low-saturation pixels (penalty text is white on a dark pill)."""
    if roi.ndim == 2:
        return np.where(roi >= _WHITE_MIN, 255, 0).astype(np.uint8)
    lo = roi.min(axis=2)
    hi = roi.max(axis=2)
    mask = (lo >= _WHITE_MIN) & ((hi.astype(np.int16) - lo) <= _WHITE_MAX_SPREAD)
    return np.where(mask, 255, 0).astype(np.uint8)


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Bounding boxes (x, y, w, h) of connected components, left to right."""
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    boxes = [
        (int(s[0]), int(s[1]), int(s[2]), int(s[3]))
        for s in stats[1:count]
        if int(s[4]) >= 6
    ]
    return sorted(boxes, key=lambda b: b[0])


def _is_plus(mask: np.ndarray, box: tuple[int, int, int, int]) -> bool:
    """Cross shape: filled centre row and column, empty corners."""
    x, y, w, h = box
    if h < 5 or w < 5 or not 0.6 <= w / h <= 1.6:
        return False
    cell = mask[y : y + h, x : x + w] > 0
    ys = np.linspace(0, h, 4).astype(int)
    xs = np.linspace(0, w, 4).astype(int)

    def fill(r: int, c: int) -> float:
        part = cell[ys[r] : ys[r + 1], xs[c] : xs[c + 1]]
        return float(part.mean()) if part.size else 0.0

    arms = [fill(1, 1), fill(0, 1), fill(2, 1), fill(1, 0), fill(1, 2)]
    corners = [fill(0, 0), fill(0, 2), fill(2, 0), fill(2, 2)]
    return min(arms) >= 0.45 and max(corners) <= 0.35


def _digits_after_plus(
    boxes: list[tuple[int, int, int, int]],
    plus: tuple[int, int, int, int],
    roi_height: int,
) -> list[tuple[int, int, int, int]]:
    """Digit boxes on the same text line immediately right of the ``+``."""
    px, py, pw, ph = plus
    plus_cy = py + ph / 2.0
    digits: list[tuple[int, int, int, int]] = []
    right_edge = px + pw
    for box in boxes:
        x, y, w, h = box
        if x < right_edge:
            continue
        if not ph * 1.05 <= h <= min(ph * 2.6, roi_height):
            continue
        if abs((y + h / 2.0) - plus_cy) > h * 0.3 or w > h:
            continue
        ref_h = digits[-1][3] if digits else h
        if x - right_edge > ref_h * 0.6 or abs(h - ref_h) > ref_h * 0.2:
            break
        digits.append(box)
        right_edge = x + w
        if len(digits) == 3:
            break
    return digits


def read_penalty_side(
    image: np.ndarray,
    roi_box: NormalizedBox | None,
    templates: dict[str, list[np.ndarray]],
    *,
    match_threshold: float,
    max_value: int = 99,
) -> PenaltySideRead:
    """Optional ``+N`` observation; never affects main-counter confidence.

    A value is reported only when a ``+`` glyph is followed by digit glyphs on
    the same line. Anything else (empty pill, scene clutter) is no reading.
    """
    if roi_box is None:
        return _NO_PENALTY
    crop = crop_roi(image, roi_box)
    mask = _white_text_mask(crop)
    boxes = _components(mask)
    digit_templates = {k: v for k, v in templates.items() if k in "0123456789"}
    for plus in (b for b in boxes if _is_plus(mask, b)):
        digit_boxes = _digits_after_plus(boxes, plus, mask.shape[0])
        if not digit_boxes:
            continue
        symbols: list[str] = []
        scores: list[float] = []
        for x, y, w, h in digit_boxes:
            symbol, score = match_glyph(
                mask[y : y + h, x : x + w], digit_templates, match_threshold
            )
            if symbol is None:
                break
            symbols.append(symbol)
            scores.append(float(score))
        if len(symbols) != len(digit_boxes):
            continue
        value = int("".join(symbols))
        if 0 < value <= max_value:
            return PenaltySideRead(value=value, digit_scores=scores)
    return _NO_PENALTY


def read_score_frame(
    image: np.ndarray,
    *,
    left_roi: NormalizedBox,
    right_roi: NormalizedBox,
    templates: dict[str, list[np.ndarray]],
    match_threshold: float,
    left_penalty_roi: NormalizedBox | None = None,
    right_penalty_roi: NormalizedBox | None = None,
    battle_mode_id: str | None = "splat_zones",
    penalty_max_value: int = 99,
) -> ScoreReading:
    """Produce a dual-counter score reading for one full frame."""
    left = read_score_side(image, left_roi, templates, match_threshold=match_threshold)
    right = read_score_side(image, right_roi, templates, match_threshold=match_threshold)
    left_pen = read_penalty_side(
        image,
        left_penalty_roi,
        templates,
        match_threshold=match_threshold,
        max_value=penalty_max_value,
    )
    right_pen = read_penalty_side(
        image,
        right_penalty_roi,
        templates,
        match_threshold=match_threshold,
        max_value=penalty_max_value,
    )
    scores: list[float] = []
    scores.extend(left.digit_scores)
    scores.extend(right.digit_scores)
    confidence = float(np.mean(scores)) if scores else 0.0
    return ScoreReading(
        battle_mode_id=battle_mode_id,
        left=left,
        right=right,
        left_penalty=left_pen.value,
        right_penalty=right_pen.value,
        left_penalty_scores=left_pen.digit_scores,
        right_penalty_scores=right_pen.digit_scores,
        penalty_evaluated=(
            left_penalty_roi is not None and right_penalty_roi is not None
        ),
        confidence=confidence,
    )


class ScoreDetector:
    """Detect fixed left/right Splat Zones remaining counters.

    Observe-only. No ally/opponent labeling, no GameEvents, no fusion.
    """

    name = "score"
    run_on_evidence = True

    def __init__(
        self,
        config: ScoreDetectorConfig,
        cadence_fps: float | None = None,
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps
        self._templates = load_templates(config.template_dir)

    def detect(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
    ) -> tuple[ScoreReading | None, float]:
        """Return a score reading and detector confidence for one frame."""
        _ = timestamp
        reading = read_score_frame(
            image,
            left_roi=self.config.left_roi,
            right_roi=self.config.right_roi,
            templates=self._templates,
            match_threshold=self.config.match_threshold,
            left_penalty_roi=self.config.left_penalty_roi,
            right_penalty_roi=self.config.right_penalty_roi,
            battle_mode_id=self.config.battle_mode_id,
            penalty_max_value=self.config.penalty_max_value,
        )
        main_seen = reading.left.visible or reading.right.visible
        penalty_seen = (
            reading.left_penalty is not None or reading.right_penalty is not None
        )
        if not main_seen and not penalty_seen:
            return None, 0.0
        return reading, reading.confidence
