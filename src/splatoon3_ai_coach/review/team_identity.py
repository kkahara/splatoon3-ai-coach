"""Top/bottom Review-row colors and optional ally-row resolution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.vision.team_color_calibration import (
    TeamColorCalibrationResult,
    circular_hue_distance,
)

from .models import ReviewTimelineSample, TeamIdentity


def observe_row_hues(
    images: dict[str, np.ndarray],
    samples: list[ReviewTimelineSample],
    config: ReviewTimelineConfig,
) -> tuple[float | None, float | None]:
    """Return median saturated hue for each cursor-relative roster row."""
    row_values: dict[str, list[float]] = {"top": [], "bottom": []}
    for sample in samples:
        image = images.get(sample.sample_id)
        if image is None or sample.cursor.x is None:
            continue
        height, width = image.shape[:2]
        half_row = width * config.roster_row_width_fraction / 2
        center = float(np.clip(sample.cursor.x, half_row, width - half_row))
        x1, x2 = int(center - half_row), int(center + half_row)
        for row, band in (
            ("top", config.roster_top_band),
            ("bottom", config.roster_bottom_band),
        ):
            crop = image[int(height * band[0]) : int(height * band[1]), x1:x2]
            hue = _saturated_hue(crop)
            if hue is not None:
                row_values[row].append(hue)
    return _median_or_none(row_values["top"]), _median_or_none(row_values["bottom"])


def resolve_team_identity(
    top_h: float | None,
    bottom_h: float | None,
    *,
    config: ReviewTimelineConfig,
    gameplay_analysis_dir: Path | None = None,
    ally_row: Literal["top", "bottom"] | None = None,
) -> TeamIdentity:
    """Resolve ally row from roster-gated gameplay calibration, then CLI fallback."""
    calibration = _load_gameplay_calibration(gameplay_analysis_dir)
    if (
        calibration is not None
        and calibration.roster_visibility_basis == "player_count.roster_visible"
        and top_h is not None
        and bottom_h is not None
    ):
        top_distance = circular_hue_distance(calibration.ally_h, top_h)
        bottom_distance = circular_hue_distance(calibration.ally_h, bottom_h)
        margin = abs(top_distance - bottom_distance)
        if margin >= config.team_color_min_margin:
            resolved = "top" if top_distance < bottom_distance else "bottom"
            return TeamIdentity(
                top_color=top_h,
                bottom_color=bottom_h,
                ally_row=resolved,
                source="gameplay_color_roster_gated",
                side_provenance="roster_gated_calibration",
                side_evidence_ids=list(calibration.accepted_frame_ids),
                side_evidence_times=list(calibration.accepted_frame_times),
                side_provenance_reason=(
                    "ally row resolved from roster-visible gameplay calibration"
                ),
            )
    if ally_row is not None:
        return TeamIdentity(
            top_color=top_h,
            bottom_color=bottom_h,
            ally_row=ally_row,
            source="cli",
            side_provenance="operator_declared",
            side_provenance_reason="ally row supplied by the operator",
        )
    return TeamIdentity(
        top_color=top_h,
        bottom_color=bottom_h,
        side_provenance_reason=(
            "ally row unknown because roster-gated gameplay calibration is absent"
        ),
    )


def _load_gameplay_calibration(
    directory: Path | None,
) -> TeamColorCalibrationResult | None:
    if directory is None:
        return None
    path = directory / "match_identity.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    calibration = payload.get("team_color_calibration")
    if not isinstance(calibration, dict):
        return None
    try:
        return TeamColorCalibrationResult.from_dict(calibration)
    except (KeyError, TypeError, ValueError):
        return None


def _saturated_hue(image: np.ndarray) -> float | None:
    if image.size == 0:
        return None
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = (hsv[..., 1] >= 70) & (hsv[..., 2] >= 40)
    if int(np.count_nonzero(mask)) < 20:
        return None
    return float(np.median(hsv[..., 0][mask].astype(np.float64)))


def _median_or_none(values: list[float]) -> float | None:
    return float(np.median(np.asarray(values, dtype=np.float64))) if values else None
