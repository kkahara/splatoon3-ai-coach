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
        return _unknown_rows(sample_id, elapsed_seconds)
    height, width = image.shape[:2]
    slot_w = width * config.roster_row_width_fraction / 4
    half_row = 2 * slot_w
    nominal_center = float(np.clip(cursor_x, half_row, width - half_row))
    observations: list[CursorSlotObservation] = []
    bands = (("top", config.roster_top_band), ("bottom", config.roster_bottom_band))
    for row, (top_fraction, bottom_fraction) in bands:
        y1, y2 = int(height * top_fraction), int(height * bottom_fraction)
        if not _row_is_reliable(image[y1:y2], nominal_center, slot_w, config):
            observations.extend(
                _unknown(f"{row}_{index}", sample_id, elapsed_seconds, row)
                for index in range(1, 5)
            )
            continue
        left = nominal_center - half_row
        for index in range(4):
            x1 = int(round(left + index * slot_w))
            x2 = int(round(left + (index + 1) * slot_w))
            roi = image[y1:y2, x1:x2]
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


def _row_is_reliable(
    row_image: np.ndarray,
    nominal_center: float,
    slot_w: float,
    config: ReviewTimelineConfig,
) -> bool:
    """Return whether the row shows clear repeated slot-edge structure.

    The search over nearby offsets only gates visibility. Slots stay on the
    cursor-centered grid: on real sweeps the best offset beat the nominal one
    by under 0.01 edge density and was always misaligned, sometimes by a
    full slot (reading one player's icon as another's).
    """
    radius = slot_w * config.roster_refine_slot_radius
    offsets = np.linspace(-radius, radius, config.roster_refine_steps)
    scores = sorted(
        (
            _row_edge_score(row_image, nominal_center + float(offset), slot_w)
            for offset in offsets
        ),
        reverse=True,
    )
    return (
        scores[0] >= config.roster_refine_min_edge_energy
        and scores[0] - scores[1] >= config.roster_refine_min_margin
    )


def _row_edge_score(row_image: np.ndarray, center: float, slot_w: float) -> float:
    """Mean edge density across four complete candidate slots."""
    width = row_image.shape[1]
    left = center - 2 * slot_w
    right = center + 2 * slot_w
    if left < 0 or right > width:
        return 0.0
    gray = cv2.cvtColor(row_image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 80, 160) > 0
    densities = []
    for index in range(4):
        x1 = int(round(left + index * slot_w))
        x2 = int(round(left + (index + 1) * slot_w))
        densities.append(float(np.mean(edges[:, x1:x2])))
    return float(np.mean(densities))


def _unknown_rows(
    sample_id: str, elapsed_seconds: int | None
) -> list[CursorSlotObservation]:
    """Build unknown observations for all eight slots."""
    return [
        _unknown(f"{row}_{index}", sample_id, elapsed_seconds, row)
        for row in ("top", "bottom")
        for index in range(1, 5)
    ]


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
