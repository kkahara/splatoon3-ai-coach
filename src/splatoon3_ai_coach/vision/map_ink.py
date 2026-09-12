"""2D match-map ink analysis (observations, not GameEvents).

``MAP_OVERLAY`` (existing) = map visibility interval.
``MapObservation`` (this module) = sparse sampled ink measurement while the
map is visible. Ink fractions are classified pixel ratios — not continuous
state, not interpolated, and not an ``INK_COVERAGE_CHANGED`` event.

Stage geometry rectangles are **overlapping sampling regions**. Aggregate
counts use the **union** of those rectangles so overlapping pixels are counted
once. The classifier still decides which sampled pixels are ally/opponent/other.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
from loguru import logger
from pydantic import BaseModel, Field

from splatoon3_ai_coach.config.models import MapInkAnalyzerConfig
from splatoon3_ai_coach.types import NormalizedBox
from splatoon3_ai_coach.vision.stage_maps import StageMapGeometry, StageMapRegion
from splatoon3_ai_coach.vision.team_color_calibration import (
    ColorProfile,
    TeamColorCalibrationResult,
)

MAP_OBSERVATIONS_FILENAME = "map_observations.json"


class MapRegionObservation(BaseModel):
    """Diagnostic counts for one sampling rectangle (may overlap others).

    Observation-level fractions are derived from the **union** of all regions,
    not by averaging or summing these per-region counts.
    """

    region_id: str
    total_pixels: int = Field(ge=0)
    classified_pixels: int = Field(ge=0)
    ally_ink_pixels: int = Field(ge=0)
    opponent_ink_pixels: int = Field(ge=0)
    unclassified_pixels: int = Field(ge=0)
    ally_classified_fraction: float | None = None
    opponent_classified_fraction: float | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)


class MapObservation(BaseModel):
    """Sampled 2D map ink state at one video time.

    Not a ``GameEvent``. Does not imply ink state at other times.
    Aggregate fractions use the deduplicated union of sampling regions.
    """

    video_time: float = Field(ge=0)
    stage_id: str
    battle_mode_id: str | None = None
    ally_classified_fraction: float | None = None
    opponent_classified_fraction: float | None = None
    classified_fraction: float | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    # Union pixel totals (overlaps counted once).
    total_sample_pixels: int = Field(default=0, ge=0)
    classified_pixels: int = Field(default=0, ge=0)
    ally_ink_pixels: int = Field(default=0, ge=0)
    opponent_ink_pixels: int = Field(default=0, ge=0)
    unclassified_pixels: int = Field(default=0, ge=0)
    regions: list[MapRegionObservation] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    geometry_battle_mode_id: str | None = None


class MapInkClassifier:
    """Deterministic HSV pixel classes: ally / opponent / other.

    Starts in YAML range mode. After ``apply_calibration``, classifies with
    match-specific circular hue profiles (same instance; no mid-run swap).
    """

    def __init__(self, config: MapInkAnalyzerConfig) -> None:
        self.config = config
        self._calibration: TeamColorCalibrationResult | None = None
        self._ally_profile: ColorProfile | None = None
        self._opponent_profile: ColorProfile | None = None

    @property
    def calibration(self) -> TeamColorCalibrationResult | None:
        """Latched calibration applied to this classifier, if any."""
        return self._calibration

    def apply_calibration(self, result: TeamColorCalibrationResult) -> None:
        """Switch to circular profiles from a latched calibration (once)."""
        if self._calibration is not None:
            return
        tol = float(self.config.h_tolerance)
        s_min = int(self.config.s_min)
        v_min = int(self.config.v_min)
        self._ally_profile = ColorProfile(
            h_center=float(result.ally_h),
            h_tolerance=tol,
            s_min=s_min,
            v_min=v_min,
        )
        self._opponent_profile = ColorProfile(
            h_center=float(result.opponent_h),
            h_tolerance=tol,
            s_min=s_min,
            v_min=v_min,
        )
        self._calibration = result

    def classify_bgr(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return boolean masks ``(ally, opponent, other)`` for a BGR crop."""
        if image.size == 0:
            empty = np.zeros((0, 0), dtype=bool)
            return empty, empty, empty
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        if self._ally_profile is not None and self._opponent_profile is not None:
            return _classify_calibrated(hsv, self._ally_profile, self._opponent_profile)
        ally = _hsv_in_ranges(hsv, self.config.ally_hsv_ranges)
        opponent = _hsv_in_ranges(hsv, self.config.opponent_hsv_ranges)
        # Prefer ally when both match (rare overlap).
        opponent = opponent & ~ally
        other = ~(ally | opponent)
        return ally, opponent, other


def _classify_calibrated(
    hsv: np.ndarray,
    ally: ColorProfile,
    opponent: ColorProfile,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Circular H profiles with nearest-center tie-break on overlap."""
    h = hsv[:, :, 0].astype(np.float32)
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]
    candidate = (s >= ally.s_min) & (v >= ally.v_min)
    dist_a = _circular_hue_distance_arr(h, ally.h_center)
    dist_o = _circular_hue_distance_arr(h, opponent.h_center)
    in_ally = candidate & (dist_a <= ally.h_tolerance)
    in_opp = candidate & (dist_o <= opponent.h_tolerance)
    exclusive_ally = in_ally & ~in_opp
    exclusive_opp = in_opp & ~in_ally
    both = in_ally & in_opp
    # Exact distance ties prefer ally (matches YAML overlap preference).
    nearer_ally = both & (dist_a <= dist_o)
    nearer_opp = both & (dist_o < dist_a)
    ally_mask = exclusive_ally | nearer_ally
    opp_mask = exclusive_opp | nearer_opp
    other = ~(ally_mask | opp_mask)
    return ally_mask, opp_mask, other


def _circular_hue_distance_arr(h: np.ndarray, center: float) -> np.ndarray:
    """Vectorized OpenCV H-circle distance to a center."""
    d = np.abs(h - float(center)) % 180.0
    return np.minimum(d, 180.0 - d)


def _hsv_in_ranges(hsv: np.ndarray, ranges: list[list[int]]) -> np.ndarray:
    """OR of inclusive HSV range masks; each range is [h1,s1,v1,h2,s2,v2]."""
    mask = np.zeros(hsv.shape[:2], dtype=bool)
    for item in ranges:
        if len(item) != 6:
            continue
        h1, s1, v1, h2, s2, v2 = (int(v) for v in item)
        lower = np.array([h1, s1, v1], dtype=np.uint8)
        upper = np.array([h2, s2, v2], dtype=np.uint8)
        part = cv2.inRange(hsv, lower, upper) > 0
        mask |= part
    return mask


def analyze_map_ink(
    image: np.ndarray,
    geometry: StageMapGeometry,
    classifier: MapInkClassifier,
    *,
    video_time: float,
    battle_mode_id: str | None,
    evidence_ids: list[str] | None = None,
) -> MapObservation:
    """Measure classified ink fractions over the union of sampling regions.

    Rectangles may overlap non-map pixels and each other on purpose. Overlaps
    are deduplicated in the aggregate; the classifier decides usable ink pixels.
    """
    height, width = image.shape[:2]
    union = _union_region_mask(height, width, geometry.regions)
    ally_full, opponent_full, other_full = classifier.classify_bgr(image)
    ally_u = ally_full & union
    opponent_u = opponent_full & union
    other_u = other_full & union
    ally_n = int(np.count_nonzero(ally_u))
    opponent_n = int(np.count_nonzero(opponent_u))
    other_n = int(np.count_nonzero(other_u))
    classified = ally_n + opponent_n
    total = int(np.count_nonzero(union))
    ally_frac, opponent_frac = _fractions(ally_n, opponent_n, classified)
    classified_frac = None if total <= 0 else classified / float(total)
    region_obs = [
        _analyze_region_slice(ally_full, opponent_full, other_full, height, width, region)
        for region in geometry.regions
    ]
    confidence = 0.0 if classified_frac is None else float(min(1.0, max(0.0, classified_frac)))
    return MapObservation(
        video_time=float(video_time),
        stage_id=geometry.stage_id,
        battle_mode_id=battle_mode_id,
        ally_classified_fraction=ally_frac,
        opponent_classified_fraction=opponent_frac,
        classified_fraction=classified_frac,
        confidence=confidence,
        total_sample_pixels=total,
        classified_pixels=classified,
        ally_ink_pixels=ally_n,
        opponent_ink_pixels=opponent_n,
        unclassified_pixels=other_n,
        regions=region_obs,
        evidence_ids=list(evidence_ids or []),
        geometry_battle_mode_id=geometry.battle_mode_id,
    )


def _union_region_mask(
    height: int,
    width: int,
    regions: list[StageMapRegion],
) -> np.ndarray:
    """Boolean mask of the union of normalized sampling rectangles."""
    mask = np.zeros((height, width), dtype=bool)
    for region in regions:
        left, top, right, bottom = _pixel_box(region.roi, width, height)
        if right > left and bottom > top:
            mask[top:bottom, left:right] = True
    return mask


def _pixel_box(roi: NormalizedBox, width: int, height: int) -> tuple[int, int, int, int]:
    """Convert a normalized ROI to inclusive-exclusive pixel bounds."""
    x1, y1, x2, y2 = roi
    left = int(x1 * width)
    top = int(y1 * height)
    right = int(x2 * width)
    bottom = int(y2 * height)
    left = max(0, min(width, left))
    right = max(0, min(width, right))
    top = max(0, min(height, top))
    bottom = max(0, min(height, bottom))
    return left, top, right, bottom


def _analyze_region_slice(
    ally_full: np.ndarray,
    opponent_full: np.ndarray,
    other_full: np.ndarray,
    height: int,
    width: int,
    region: StageMapRegion,
) -> MapRegionObservation:
    """Per-rectangle diagnostic slice (overlaps allowed; not used for aggregates)."""
    left, top, right, bottom = _pixel_box(region.roi, width, height)
    total = max(0, (right - left) * (bottom - top))
    if total == 0:
        return MapRegionObservation(
            region_id=region.id,
            total_pixels=0,
            classified_pixels=0,
            ally_ink_pixels=0,
            opponent_ink_pixels=0,
            unclassified_pixels=0,
            confidence=0.0,
        )
    ally_n = int(np.count_nonzero(ally_full[top:bottom, left:right]))
    opponent_n = int(np.count_nonzero(opponent_full[top:bottom, left:right]))
    other_n = int(np.count_nonzero(other_full[top:bottom, left:right]))
    classified = ally_n + opponent_n
    ally_frac, opponent_frac = _fractions(ally_n, opponent_n, classified)
    conf = 0.0 if classified == 0 else min(1.0, classified / float(total))
    return MapRegionObservation(
        region_id=region.id,
        total_pixels=total,
        classified_pixels=classified,
        ally_ink_pixels=ally_n,
        opponent_ink_pixels=opponent_n,
        unclassified_pixels=other_n,
        ally_classified_fraction=ally_frac,
        opponent_classified_fraction=opponent_frac,
        confidence=conf,
    )


def _fractions(
    ally: int, opponent: int, classified: int
) -> tuple[float | None, float | None]:
    """Ally/opponent shares of classified pixels; None when nothing classified."""
    if classified <= 0:
        return None, None
    return ally / float(classified), opponent / float(classified)


def write_map_observations(path: Path, observations: list[MapObservation]) -> None:
    """Persist sparse map observations as JSON (sidecar, not GameEvents)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [item.model_dump(mode="json") for item in observations]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote {} map observations to {}", len(observations), path)


def write_map_ink_diagnostic(
    output_dir: Path,
    *,
    image: np.ndarray,
    observation: MapObservation,
    geometry: StageMapGeometry,
    classifier: MapInkClassifier,
    stem: str | None = None,
) -> Path:
    """Save geometry + classification overlay for one map observation.

    Distinguishes rectangle boundaries, union sample area, ally / opponent /
    unclassified pixels inside the union. Writes under ``debug_map_ink/``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    height, width = image.shape[:2]
    union = _union_region_mask(height, width, geometry.regions)
    ally_full, opponent_full, other_full = classifier.classify_bgr(image)
    ally_u = ally_full & union
    opponent_u = opponent_full & union
    other_u = other_full & union

    canvas = image.copy()
    # Unclassified sample area first (dim), then ink classes on top.
    canvas[other_u] = (
        canvas[other_u].astype(np.float32) * 0.45 + np.array([40, 40, 40], dtype=np.float32) * 0.55
    ).astype(np.uint8)
    canvas[ally_u] = (
        canvas[ally_u].astype(np.float32) * 0.35 + np.array([40, 220, 40], dtype=np.float32) * 0.65
    ).astype(np.uint8)
    canvas[opponent_u] = (
        canvas[opponent_u].astype(np.float32) * 0.35 + np.array([40, 40, 220], dtype=np.float32) * 0.65
    ).astype(np.uint8)

    # Union boundary (thick cyan) — separate from per-rectangle boxes.
    union_u8 = (union.astype(np.uint8) * 255)
    contours, _ = cv2.findContours(union_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(canvas, contours, -1, (255, 220, 0), 2)

    # Individual sampling rectangles (thin white) + region ids.
    for region in geometry.regions:
        left, top, right, bottom = _pixel_box(region.roi, width, height)
        cv2.rectangle(canvas, (left, top), (right, bottom), (255, 255, 255), 1)
        cv2.putText(
            canvas,
            region.id,
            (left + 4, max(top + 14, 14)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

    _draw_map_ink_legend(canvas, observation)
    name = stem or f"map_ink_{observation.video_time:010.3f}"
    out = output_dir / f"{name}.jpg"
    cv2.imwrite(str(out), canvas)
    return out


def _draw_map_ink_legend(canvas: np.ndarray, observation: MapObservation) -> None:
    """HUD legend for diagnostic colors and key fractions."""
    lines = [
        "white box = region ROI",
        "cyan outline = union sample",
        "green = ally class",
        "red = opponent class",
        "dim = unclassified in union",
        (
            f"classified_frac="
            f"{observation.classified_fraction:.3f}"
            if observation.classified_fraction is not None
            else "classified_frac=n/a"
        ),
        f"union={observation.total_sample_pixels} cls={observation.classified_pixels}",
    ]
    x, y0 = 12, 28
    for index, line in enumerate(lines):
        y = y0 + index * 22
        cv2.putText(
            canvas,
            line,
            (x, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 0, 0),
            3,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            line,
            (x, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )


def union_bbox_normalized(
    geometry: StageMapGeometry,
) -> tuple[float, float, float, float] | None:
    """Axis-aligned bbox of all region ROIs in normalized coordinates."""
    if not geometry.regions:
        return None
    xs1 = [r.roi[0] for r in geometry.regions]
    ys1 = [r.roi[1] for r in geometry.regions]
    xs2 = [r.roi[2] for r in geometry.regions]
    ys2 = [r.roi[3] for r in geometry.regions]
    return (min(xs1), min(ys1), max(xs2), max(ys2))
