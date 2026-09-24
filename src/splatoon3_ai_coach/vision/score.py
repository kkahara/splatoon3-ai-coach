"""Splat Zones remaining-score detector (observe-only).

Screen geometry only: ``left`` / ``right`` counters. Ally/opponent mapping
belongs to fusion later — not this detector.

Does not emit GameEvents. Penalty ``+N`` is an optional secondary observation
and must not gate main-counter success.
"""

from __future__ import annotations

from pathlib import Path

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
    return segment_timer_roi(cv2.cvtColor(inverted, cv2.COLOR_GRAY2BGR), min_area=min_area)


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


def read_penalty_side(
    image: np.ndarray,
    roi_box: NormalizedBox | None,
    templates: dict[str, list[np.ndarray]],
    *,
    match_threshold: float,
) -> int | None:
    """Optional +N observation. Failure does not affect main-counter confidence."""
    if roi_box is None:
        return None
    side = read_score_side(
        image, roi_box, templates, match_threshold=match_threshold
    )
    if not side.visible or side.value is None:
        return None
    return side.value


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
) -> ScoreReading:
    """Produce a dual-counter score reading for one full frame."""
    left = read_score_side(
        image, left_roi, templates, match_threshold=match_threshold
    )
    right = read_score_side(
        image, right_roi, templates, match_threshold=match_threshold
    )
    left_pen = read_penalty_side(
        image, left_penalty_roi, templates, match_threshold=match_threshold
    )
    right_pen = read_penalty_side(
        image, right_penalty_roi, templates, match_threshold=match_threshold
    )
    scores: list[float] = []
    scores.extend(left.digit_scores)
    scores.extend(right.digit_scores)
    confidence = float(np.mean(scores)) if scores else 0.0
    return ScoreReading(
        battle_mode_id=battle_mode_id,
        left=left,
        right=right,
        left_penalty=left_pen,
        right_penalty=right_pen,
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
        )
        if not reading.left.visible and not reading.right.visible:
            return None, 0.0
        if reading.confidence < self.config.min_usable_confidence:
            return reading, reading.confidence
        return reading, reading.confidence
