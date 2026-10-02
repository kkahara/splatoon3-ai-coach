"""Confidence-based cursor detection for Review timeline screenshots."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.vision.roi import crop_roi


@dataclass(frozen=True)
class CursorCandidate:
    """One scored vertical cursor candidate."""

    x: int
    score: float
    graph_support: float
    lane_support: float
    scrub_support: float


@dataclass(frozen=True)
class CursorDetection:
    """Best cursor candidate and ambiguity diagnostics."""

    x: int | None
    confidence: float
    runner_up_margin: float
    candidates: tuple[CursorCandidate, ...]


def detect_cursor(
    image: np.ndarray, config: ReviewTimelineConfig
) -> CursorDetection:
    """Find a cursor using graph, lane, and scrub-bar evidence together."""
    graph = crop_roi(image, config.graph_roi)
    lane = crop_roi(image, config.event_lane_roi)
    scrub = crop_roi(image, config.scrub_bar_roi)
    if graph.size == 0 or lane.size == 0 or scrub.size == 0:
        return CursorDetection(None, 0.0, 0.0, ())
    graph_white = _white_fraction_by_column(graph)
    lane_white = _white_fraction_by_column(lane)
    scrub_white = _white_fraction_by_column(scrub)
    width = min(len(graph_white), len(lane_white), len(scrub_white))
    graph_values = graph_white[:width]
    lane_values = lane_white[:width]
    scrub_values = scrub_white[:width]
    scores = (
        0.60 * graph_values
        + 0.30 * lane_values
        + 0.10 * scrub_values
    )
    candidates = _runs_to_candidates(scores, width, image.shape[1], config)
    ordered = tuple(sorted(candidates, key=lambda item: item.score, reverse=True))
    if not ordered:
        return CursorDetection(None, 0.0, 0.0, ())
    best = ordered[0]
    second = ordered[1].score if len(ordered) > 1 else 0.0
    margin = max(0.0, best.score - second)
    if best.score < config.cursor_confidence_floor:
        return CursorDetection(None, best.score, margin, ordered)
    return CursorDetection(best.x, best.score, margin, ordered)


def _white_fraction_by_column(region: np.ndarray) -> np.ndarray:
    """Measure bright, low-saturation pixels in each column."""
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    mask = (hsv[..., 1] < 55) & (hsv[..., 2] > 185)
    return mask.mean(axis=0)


def _runs_to_candidates(
    scores: np.ndarray,
    width: int,
    image_width: int,
    config: ReviewTimelineConfig,
) -> list[CursorCandidate]:
    """Convert strong adjacent columns into one candidate per line."""
    threshold = max(0.35, config.cursor_confidence_floor * 0.75)
    strong = scores >= threshold
    candidates: list[CursorCandidate] = []
    start: int | None = None
    for index, value in enumerate(np.r_[strong, False]):
        if value and start is None:
            start = index
        elif not value and start is not None:
            end = index
            center = (start + end - 1) // 2
            support = float(scores[start:end].max())
            x = int(round(
                (center / max(width - 1, 1))
                * (config.graph_roi[2] - config.graph_roi[0])
                * image_width
                + config.graph_roi[0] * image_width
            ))
            candidates.append(
                CursorCandidate(
                    x=x,
                    score=support,
                    graph_support=float(scores[center]),
                    lane_support=float(scores[center]),
                    scrub_support=float(scores[center]),
                )
            )
            start = None
    return candidates

