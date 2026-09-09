"""2D match-map ink analysis (observations, not GameEvents).

``MAP_OVERLAY`` (existing) = map visibility interval.
``MapObservation`` (this module) = sparse sampled ink measurement while the
map is visible. Ink fractions are classified pixel ratios — not continuous
state, not interpolated, and not an ``INK_COVERAGE_CHANGED`` event.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from loguru import logger
from pydantic import BaseModel, Field

from splatoon3_ai_coach.config.models import MapInkAnalyzerConfig
from splatoon3_ai_coach.vision.roi import crop_roi
from splatoon3_ai_coach.vision.stage_maps import StageMapGeometry, StageMapRegion

MAP_OBSERVATIONS_FILENAME = "map_observations.json"


class MapRegionObservation(BaseModel):
    """Ink pixel counts for one sampling rectangle."""

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
    """

    video_time: float = Field(ge=0)
    stage_id: str
    battle_mode_id: str | None = None
    ally_classified_fraction: float | None = None
    opponent_classified_fraction: float | None = None
    classified_fraction: float | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    regions: list[MapRegionObservation] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    geometry_battle_mode_id: str | None = None


class MapInkClassifier:
    """Deterministic HSV pixel classes: ally / opponent / other."""

    def __init__(self, config: MapInkAnalyzerConfig) -> None:
        self.config = config

    def classify_bgr(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return boolean masks ``(ally, opponent, other)`` for a BGR crop."""
        if image.size == 0:
            empty = np.zeros((0, 0), dtype=bool)
            return empty, empty, empty
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        ally = _hsv_in_ranges(hsv, self.config.ally_hsv_ranges)
        opponent = _hsv_in_ranges(hsv, self.config.opponent_hsv_ranges)
        # Prefer ally when both match (rare overlap).
        opponent = opponent & ~ally
        other = ~(ally | opponent)
        return ally, opponent, other


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
    """Measure classified ink fractions over configured sampling regions."""
    region_obs: list[MapRegionObservation] = []
    ally_total = 0
    opponent_total = 0
    classified_total = 0
    pixel_total = 0
    for region in geometry.regions:
        obs = _analyze_region(image, region, classifier)
        region_obs.append(obs)
        ally_total += obs.ally_ink_pixels
        opponent_total += obs.opponent_ink_pixels
        classified_total += obs.classified_pixels
        pixel_total += obs.total_pixels
    ally_frac, opponent_frac = _fractions(ally_total, opponent_total, classified_total)
    classified_frac = (
        None if pixel_total <= 0 else classified_total / float(pixel_total)
    )
    confidence = _observation_confidence(classified_frac, region_obs)
    return MapObservation(
        video_time=float(video_time),
        stage_id=geometry.stage_id,
        battle_mode_id=battle_mode_id,
        ally_classified_fraction=ally_frac,
        opponent_classified_fraction=opponent_frac,
        classified_fraction=classified_frac,
        confidence=confidence,
        regions=region_obs,
        evidence_ids=list(evidence_ids or []),
        geometry_battle_mode_id=geometry.battle_mode_id,
    )


def _analyze_region(
    image: np.ndarray,
    region: StageMapRegion,
    classifier: MapInkClassifier,
) -> MapRegionObservation:
    """Classify one sampling rectangle."""
    crop = crop_roi(image, region.roi)
    total = int(crop.shape[0] * crop.shape[1]) if crop.size else 0
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
    ally_mask, opponent_mask, other_mask = classifier.classify_bgr(crop)
    ally_n = int(np.count_nonzero(ally_mask))
    opponent_n = int(np.count_nonzero(opponent_mask))
    other_n = int(np.count_nonzero(other_mask))
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


def _observation_confidence(
    classified_fraction: float | None,
    regions: list[MapRegionObservation],
) -> float:
    """Aggregate confidence from classified density across regions."""
    if not regions or classified_fraction is None:
        return 0.0
    return float(min(1.0, max(0.0, classified_fraction)))


def write_map_observations(path: Path, observations: list[MapObservation]) -> None:
    """Persist sparse map observations as JSON (sidecar, not GameEvents)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [item.model_dump(mode="json") for item in observations]
    import json

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
) -> None:
    """Save overlay visualization for one map observation."""
    output_dir.mkdir(parents=True, exist_ok=True)
    canvas = image.copy()
    height, width = canvas.shape[:2]
    for region in geometry.regions:
        x1, y1, x2, y2 = region.roi
        left, top = int(x1 * width), int(y1 * height)
        right, bottom = int(x2 * width), int(y2 * height)
        crop = canvas[top:bottom, left:right]
        if crop.size == 0:
            continue
        ally_mask, opponent_mask, _ = classifier.classify_bgr(crop)
        tint = crop.copy()
        tint[ally_mask] = (tint[ally_mask] * 0.4 + np.array([0, 180, 0]) * 0.6).astype(
            np.uint8
        )
        tint[opponent_mask] = (
            tint[opponent_mask] * 0.4 + np.array([0, 0, 180]) * 0.6
        ).astype(np.uint8)
        canvas[top:bottom, left:right] = tint
        cv2.rectangle(canvas, (left, top), (right, bottom), (255, 255, 255), 1)
        cv2.putText(
            canvas,
            region.id,
            (left + 4, max(top + 14, 14)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    stamp = f"{observation.video_time:010.3f}"
    out = output_dir / f"map_ink_{stamp}.jpg"
    cv2.imwrite(str(out), canvas)
