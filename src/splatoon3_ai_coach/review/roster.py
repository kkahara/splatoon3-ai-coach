"""Cursor-roster slot classification for Review timeline screenshots."""

from __future__ import annotations

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ReviewTimelineConfig

from .models import CursorSlotObservation, SlotState


def classify_slot(
    roi: np.ndarray,
    *,
    slot_id: str,
    sample_id: str,
    elapsed_seconds: int | None,
    team_row: str,
) -> CursorSlotObservation:
    """Classify a slot only from explicit visual alive/dead cues."""
    if roi.size == 0:
        return _unknown(slot_id, sample_id, elapsed_seconds, team_row)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    edges = float(np.mean(cv2.Canny(gray, 80, 160) > 0))
    saturation = float(np.mean(hsv[..., 1] > 70))
    x_score = _x_marker_score(gray, hsv[..., 1])
    if x_score >= 0.16:
        state, confidence = SlotState.dead, min(1.0, x_score * 3.5)
    elif edges >= 0.025 and saturation >= 0.04:
        state, confidence = SlotState.alive, min(1.0, edges * 3 + saturation)
    else:
        return _unknown(slot_id, sample_id, elapsed_seconds, team_row)
    return CursorSlotObservation(
        slot_id=slot_id,
        team_row=team_row,  # type: ignore[arg-type]
        state=state,
        confidence=confidence,
        sample_id=sample_id,
        elapsed_seconds=elapsed_seconds,
    )


def extract_cursor_slots(
    image: np.ndarray,
    cursor_x: int | None,
    *,
    config: ReviewTimelineConfig,
    sample_id: str,
    elapsed_seconds: int | None,
) -> list[CursorSlotObservation]:
    """Extract eight cursor-relative roster slots, or eight unknowns."""
    if cursor_x is None:
        return [
            _unknown(f"{row}_{index}", sample_id, elapsed_seconds, row)
            for row in ("top", "bottom")
            for index in range(1, 5)
        ]
    height, width = image.shape[:2]
    slot_w = width * config.roster_row_width_fraction / 4
    left = cursor_x - 2 * slot_w
    observations: list[CursorSlotObservation] = []
    bands = (("top", config.roster_top_band), ("bottom", config.roster_bottom_band))
    for row, (top_fraction, bottom_fraction) in bands:
        y1, y2 = int(height * top_fraction), int(height * bottom_fraction)
        for index in range(4):
            x1 = int(round(left + index * slot_w))
            x2 = int(round(left + (index + 1) * slot_w))
            roi = image[y1:y2, max(0, x1) : min(width, x2)]
            observations.append(
                classify_slot(
                    roi,
                    slot_id=f"{row}_{index + 1}",
                    sample_id=sample_id,
                    elapsed_seconds=elapsed_seconds,
                    team_row=row,
                )
            )
    return observations


def _x_marker_score(gray: np.ndarray, saturation: np.ndarray) -> float:
    """Return the fraction of neutral mid-gray pixels on either diagonal arm.

    Saturated team-colored icon pixels share the same gray luminance range, so
    only low-saturation pixels count as the roster X marker.
    """
    height, width = gray.shape[:2]
    yy, xx = np.mgrid[0:height, 0:width]
    dx = (xx - (width - 1) / 2) / max(width, 1)
    dy = (yy - (height - 1) / 2) / max(height, 1)
    diagonal = (np.abs(dx - dy) < 0.13) | (np.abs(dx + dy) < 0.13)
    mid_gray = (gray >= 65) & (gray <= 155) & (saturation < 60)
    return float(np.mean(diagonal & mid_gray))


def _unknown(
    slot_id: str, sample_id: str, elapsed_seconds: int | None, team_row: str
) -> CursorSlotObservation:
    """Build an explicit unknown slot observation."""
    return CursorSlotObservation(
        slot_id=slot_id,
        team_row=team_row,  # type: ignore[arg-type]
        sample_id=sample_id,
        elapsed_seconds=elapsed_seconds,
    )

