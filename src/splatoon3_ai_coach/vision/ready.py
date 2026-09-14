"""Pre-match ``Ready?`` plate detector (template match).

Evidence only — not a GameEvent. The pipeline runs this detector only after
stage identity is known and before the match clock first ticks away from the
frozen opening values (5:00 / 3:00).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from splatoon3_ai_coach.config.models import ReadyDetectorConfig, VisionLanguage
from splatoon3_ai_coach.vision.language import resolve_language_template_dir
from splatoon3_ai_coach.vision.models import ReadyReading
from splatoon3_ai_coach.vision.roi import crop_roi


def _to_gray(roi: np.ndarray) -> np.ndarray:
    """Convert a BGR ROI to a contrast-normalized grayscale image."""
    if roi.size == 0:
        return roi
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    return clahe.apply(gray)


def _load_templates(template_dir: Path | None, language: VisionLanguage | str) -> list[np.ndarray]:
    """Load grayscale Ready? templates from ``template_dir / {language}``."""
    if template_dir is None:
        return []
    try:
        folder = resolve_language_template_dir(template_dir, language)
    except Exception as exc:  # noqa: BLE001 — missing lang pack is soft-fail
        logger.warning("ReadyDetector: {}", exc)
        return []
    templates: list[np.ndarray] = []
    for path in sorted(folder.glob("*")):
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


class ReadyDetector:
    """Detect the centered pre-match ``Ready?`` plate.

    Always returns a reading so absences are observable when the detector runs.
    Temporal gating (stage known → first clock tick) lives in the pipeline.
    """

    name = "ready"
    run_on_evidence = True

    def __init__(
        self,
        config: ReadyDetectorConfig,
        *,
        language: VisionLanguage | str,
        cadence_fps: float | None = None,
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps
        self._templates = _load_templates(config.template_dir, language)
        if config.template_dir is not None and not self._templates:
            logger.warning(
                "ReadyDetector has no templates under {}; present stays false",
                config.template_dir,
            )

    def detect(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
    ) -> tuple[ReadyReading, float]:
        """Return a Ready? presence reading for one frame."""
        _ = timestamp
        roi = crop_roi(image, self.config.roi)
        if roi.size == 0:
            return ReadyReading(), 0.0
        score = _best_template_score(roi, self._templates)
        present = bool(self._templates) and score >= self.config.match_threshold
        confidence = (
            score
            if present
            else max(self.config.min_usable_confidence, 1.0 - score)
        )
        return (
            ReadyReading(
                present=present,
                template_score=float(np.clip(score, 0.0, 1.0)),
            ),
            float(np.clip(confidence, 0.0, 1.0)),
        )
