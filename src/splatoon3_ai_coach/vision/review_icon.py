"""Early Review-chrome detection (acquisition metadata only).

Detects the pink TV / replay icon to help resolve ``VideoSource.REVIEW``.
Does not emit GameEvents and does not change evidence semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from splatoon3_ai_coach.config.models import ReviewIconConfig
from splatoon3_ai_coach.vision.roi import crop_roi


def _to_gray(roi: np.ndarray) -> np.ndarray:
    """BGR ROI → contrast-normalized grayscale."""
    if roi.size == 0:
        return roi
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    return clahe.apply(gray)


def _load_templates(template_dir: Path | None) -> list[np.ndarray]:
    """Load Review-icon templates (color-aware BGR preferred for pink chrome)."""
    if template_dir is None or not template_dir.exists():
        return []
    templates: list[np.ndarray] = []
    for path in sorted(template_dir.glob("*")):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            continue
        templates.append(image)
    return templates


def _fit_template(image: np.ndarray, template: np.ndarray) -> np.ndarray | None:
    """Shrink template to fit inside ``image``."""
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


def _best_score(roi_bgr: np.ndarray, templates: list[np.ndarray]) -> float:
    """Peak normalized correlation (grayscale) of templates against ROI."""
    if not templates or roi_bgr.size == 0:
        return 0.0
    gray = _to_gray(roi_bgr)
    best = 0.0
    for template in templates:
        tmpl_gray = _to_gray(template) if template.ndim == 3 else template
        scaled = _fit_template(gray, tmpl_gray)
        if scaled is None:
            continue
        result = cv2.matchTemplate(gray, scaled, cv2.TM_CCOEFF_NORMED)
        best = max(best, float(result.max()))
    return best


@dataclass
class ReviewIconHit:
    """One confident Review-icon observation."""

    video_time: float
    score: float


@dataclass
class ReviewIconTracker:
    """Latch Review-icon evidence within an early deadline."""

    config: ReviewIconConfig
    hit: ReviewIconHit | None = None
    _templates: list[np.ndarray] | None = None

    def _ensure_templates(self) -> list[np.ndarray]:
        if self._templates is None:
            self._templates = _load_templates(self.config.template_dir)
            if self.config.template_dir is not None and not self._templates:
                logger.warning(
                    "ReviewIconTracker: no templates under {}",
                    self.config.template_dir,
                )
        return self._templates

    def should_run(self, video_time: float) -> bool:
        """Whether the early pass should still examine this frame."""
        if self.hit is not None:
            return False
        return float(video_time) <= float(self.config.deadline_seconds)

    def observe(self, image: np.ndarray, *, video_time: float) -> ReviewIconHit | None:
        """Template-match Review icon; latch first confident hit."""
        if not self.should_run(video_time):
            return self.hit
        templates = self._ensure_templates()
        if not templates:
            return None
        roi = crop_roi(image, self.config.roi)
        score = _best_score(roi, templates)
        if score >= float(self.config.match_threshold):
            self.hit = ReviewIconHit(video_time=float(video_time), score=score)
            logger.info(
                "Review icon latched at {:.1f}s (score={:.3f})",
                video_time,
                score,
            )
        return self.hit
