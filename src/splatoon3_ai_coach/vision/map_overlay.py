"""Map-viewing detector (independent gameplay evidence)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from splatoon3_ai_coach.config.models import MapOverlayDetectorConfig
from splatoon3_ai_coach.vision.models import MapOverlayReading
from splatoon3_ai_coach.vision.roi import crop_roi


def _to_gray(roi: np.ndarray) -> np.ndarray:
    """Convert a BGR ROI to a contrast-normalized grayscale image."""
    if roi.size == 0:
        return roi
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    return clahe.apply(gray)


def _load_flat_templates(template_dir: Path | None) -> list[np.ndarray]:
    """Load grayscale close-button templates from ``template_dir``."""
    if template_dir is None or not template_dir.exists():
        return []
    templates: list[np.ndarray] = []
    for path in sorted(template_dir.glob("*")):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            continue
        templates.append(image)
    return templates


def _fit_template(image: np.ndarray, template: np.ndarray) -> np.ndarray | None:
    """Shrink a template so it fits inside ``image``."""
    th, tw = template.shape[:2]
    ih, iw = image.shape[:2]
    if th < 4 or tw < 4 or ih < 4 or iw < 4:
        return None
    scale = min(1.0, ih / th, iw / tw)
    if scale < 1.0:
        new_w = max(4, int(tw * scale))
        new_h = max(4, int(th * scale))
        template = cv2.resize(template, (new_w, new_h), interpolation=cv2.INTER_AREA)
    if template.shape[0] > ih or template.shape[1] > iw:
        return None
    return template


def _best_template_score(roi: np.ndarray, templates: list[np.ndarray]) -> float:
    """Peak normalized correlation of templates against a grayscale ROI."""
    if not templates or roi.size == 0:
        return 0.0
    gray = _to_gray(roi)
    best = 0.0
    for template in templates:
        scaled = _fit_template(gray, template)
        if scaled is None:
            continue
        result = cv2.matchTemplate(gray, scaled, cv2.TM_CCOEFF_NORMED)
        best = max(best, float(result.max()))
    return best


class MapOverlayDetector:
    """Detect when the player is looking at the in-game map.

    Primary cue: the top-left close “X” in a circle. Output is coaching
    evidence (open frequency, timing, duration). It must not drive
    death-recovery lifecycle transitions. Always returns a reading so
    absences are observable.
    """

    name = "map_overlay"
    run_on_evidence = True

    def __init__(
        self,
        config: MapOverlayDetectorConfig,
        cadence_fps: float | None = None,
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps
        self._templates = _load_flat_templates(config.template_dir)
        if config.template_dir is not None and not self._templates:
            logger.warning(
                "MapOverlayDetector has no templates under {}; present stays false",
                config.template_dir,
            )

    def detect(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
    ) -> tuple[MapOverlayReading | None, float]:
        """Return a map-viewing presence reading for one frame."""
        _ = timestamp
        return self._observe(image)

    def _observe(self, image: np.ndarray) -> tuple[MapOverlayReading, float]:
        """Match the close-button template in ``map_roi``."""
        map_roi = crop_roi(image, self.config.map_roi)
        if map_roi.size == 0:
            return MapOverlayReading(), 0.0

        template_score = _best_template_score(map_roi, self._templates)
        present = bool(self._templates) and template_score >= self.config.match_threshold
        map_edge = _edge_frac(map_roi)
        tank_roi = crop_roi(image, self.config.tank_roi)
        peri_roi = crop_roi(image, self.config.periphery_roi)
        tank_edge = _edge_frac(tank_roi) if tank_roi.size else 0.0
        peri_blur = _blur_score(peri_roi) if peri_roi.size else 0.0
        confidence = (
            template_score
            if present
            else max(self.config.min_usable_confidence, 1.0 - template_score)
        )
        return (
            MapOverlayReading(
                present=present,
                template_score=float(np.clip(template_score, 0.0, 1.0)),
                map_edge_frac=map_edge,
                periphery_blur=peri_blur,
                center_tank_edge_frac=tank_edge,
            ),
            float(np.clip(confidence, 0.0, 1.0)),
        )


def _edge_frac(roi: np.ndarray) -> float:
    """Canny edge density for a BGR ROI."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 140)
    return float((edges > 0).mean())


def _blur_score(roi: np.ndarray) -> float:
    """Return a [0, 1] score that rises as the ROI becomes blurrier."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return float(np.clip(1.0 - lap_var / 200.0, 0.0, 1.0))
