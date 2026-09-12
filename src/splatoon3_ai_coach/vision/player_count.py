"""HUD roster-X marker detector for roster alive-count evidence.

Detects the dark-gray X over each of eight calibrated player-slot ROIs via
masked ``TM_SQDIFF_NORMED`` (X-only mask; raw grayscale ROI; no CLAHE).
This is **not** local-player ``DEATH`` — only teammate/opponent icon X marks.
Does not recognize icons, weapons, players, or OCR. Coaching vocabulary
(alive counts) is produced later by fusion, not here.

Per-slot scores in ``PlayerCountReading`` are **SQDIFF** distances
(lower = better X match). Detection is owned here as
``score <= sqdiff_match_threshold``.

``DetectorResult.confidence`` remains higher-is-better for fusion:
``1.0 - min(slot_sqdiff)`` across the eight slots (strongest X evidence).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from splatoon3_ai_coach.config.models import PlayerCountDetectorConfig
from splatoon3_ai_coach.types import NormalizedBox
from splatoon3_ai_coach.vision.models import PlayerCountReading
from splatoon3_ai_coach.vision.roi import crop_roi

_SLOT_COUNT = 4
_VALID_SLOT_INDEXES = frozenset({1, 2, 3, 4})

# Validated mask construction (see player_count_crop_diagnostic_masked_sqdiff).
_X_GRAY_LO = 70
_X_GRAY_HI = 140
_DIAG_THICKNESS = 0.11


@dataclass(frozen=True)
class MaskedXTemplate:
    """Grayscale X template plus load-time X-only mask."""

    name: str
    image: np.ndarray
    mask: np.ndarray


def _to_raw_gray(roi: np.ndarray) -> np.ndarray:
    """Convert a BGR ROI to raw grayscale (no CLAHE)."""
    if roi.size == 0:
        return roi
    return cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)


def _diagonal_band(h: int, w: int, thickness: float = _DIAG_THICKNESS) -> np.ndarray:
    """Boolean mask of the two diagonal X arms."""
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    dx = (xx - cx) / max(w, 1)
    dy = (yy - cy) / max(h, 1)
    return (np.abs(dx - dy) < thickness) | (np.abs(dx + dy) < thickness)


def derive_x_mask(gray: np.ndarray) -> np.ndarray:
    """Derive an X-only mask from a real-image template.

    Same construction as the masked-SQDIFF diagnostic:

    1. Geometric X diagonal-band prior
    2. Mid-gray intensity gate ``[_X_GRAY_LO, _X_GRAY_HI]``
    3. Morphological open + close
    4. Largest connected component
    """
    h, w = gray.shape[:2]
    diag = _diagonal_band(h, w)
    mid = (gray >= _X_GRAY_LO) & (gray <= _X_GRAY_HI)
    raw = (diag & mid).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    cleaned = cv2.morphologyEx(raw, cv2.MORPH_OPEN, kernel, iterations=1)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, kernel, iterations=1)

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(cleaned)
    if n_labels <= 1:
        return cleaned
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = 1 + int(np.argmax(areas))
    return np.where(labels == keep, 255, 0).astype(np.uint8)


def _load_masked_templates(template_dir: Path | None) -> list[MaskedXTemplate]:
    """Load grayscale templates and derive X masks once at load time."""
    if template_dir is None or not template_dir.exists():
        return []
    templates: list[MaskedXTemplate] = []
    for path in sorted(template_dir.glob("*")):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            continue
        mask = derive_x_mask(image)
        if not np.any(mask):
            logger.warning("Skipping {}; derived X mask is empty", path.name)
            continue
        templates.append(MaskedXTemplate(name=path.stem, image=image, mask=mask))
    return templates


def _fit_template_and_mask(
    image: np.ndarray,
    template: np.ndarray,
    mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Shrink template and mask together so they fit inside ``image``."""
    th, tw = template.shape[:2]
    ih, iw = image.shape[:2]
    if th < 4 or tw < 4 or ih < 4 or iw < 4:
        return None
    scale = min(1.0, ih / th, iw / tw)
    if scale < 1.0:
        new_w = max(4, int(tw * scale))
        new_h = max(4, int(th * scale))
        template = cv2.resize(template, (new_w, new_h), interpolation=cv2.INTER_AREA)
        mask = cv2.resize(mask, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
    if template.shape[0] > ih or template.shape[1] > iw:
        return None
    if not np.any(mask):
        return None
    return template, mask


def _best_masked_sqdiff(
    roi: np.ndarray,
    templates: list[MaskedXTemplate],
) -> tuple[float, str | None]:
    """Lowest masked SQDIFF across templates (lower = better X match).

    Uses raw grayscale only. Returns ``(1.0, None)`` when matching is impossible.
    """
    if not templates or roi.size == 0:
        return 1.0, None
    gray = _to_raw_gray(roi)
    best = 1.0
    best_name: str | None = None
    for item in templates:
        fitted = _fit_template_and_mask(gray, item.image, item.mask)
        if fitted is None:
            continue
        tmpl, mask = fitted
        result = cv2.matchTemplate(
            gray,
            tmpl,
            cv2.TM_SQDIFF_NORMED,
            mask=mask,
        )
        score = float(result.min())
        if score < best:
            best = score
            best_name = item.name
    return best, best_name


def _sanitize_dead_slots(indexes: tuple[int, ...]) -> tuple[int, ...]:
    """Keep unique 1-based slot indexes in ascending order."""
    return tuple(sorted({idx for idx in indexes if idx in _VALID_SLOT_INDEXES}))


def alive_counts_from_reading(
    reading: PlayerCountReading,
) -> tuple[int, int]:
    """Derive ``(ally_alive, opponent_alive)`` as ``4 - dead_count`` each side."""
    ally_dead = _sanitize_dead_slots(reading.ally_dead_slots)
    opp_dead = _sanitize_dead_slots(reading.opponent_dead_slots)
    return _SLOT_COUNT - len(ally_dead), _SLOT_COUNT - len(opp_dead)


def _confidence_from_sqdiff(scores: list[float]) -> float:
    """Map best (lowest) SQDIFF to higher-is-better detector confidence."""
    if not scores:
        return 0.0
    return float(max(0.0, min(1.0, 1.0 - min(scores))))


class PlayerCountDetector:
    """Match roster-X templates inside eight configured player-slot ROIs.

    Matching: masked ``TM_SQDIFF_NORMED`` on raw grayscale (no CLAHE).
    A slot is marked when its best SQDIFF ``<= sqdiff_match_threshold``.
    Confidence: ``1.0 - min(slot_sqdiff)`` across all eight slots.
    """

    name = "player_count"
    run_on_evidence = True

    def __init__(
        self,
        config: PlayerCountDetectorConfig,
        cadence_fps: float | None = None,
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps
        self._templates = _load_masked_templates(config.template_dir)
        if config.template_dir is not None and not self._templates:
            logger.warning(
                "PlayerCountDetector has no templates under {}; reading stays empty",
                config.template_dir,
            )

    def detect(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
    ) -> tuple[PlayerCountReading | None, float]:
        """Return X-marker slot observations for one frame."""
        _ = timestamp
        if not self._templates:
            return None, 0.0
        return self._observe(image)

    def _observe(self, image: np.ndarray) -> tuple[PlayerCountReading, float]:
        """Score each slot ROI; mark dead when best SQDIFF ≤ threshold."""
        ally_scores, ally_dead = self._score_side(image, self.config.ally_slots)
        opp_scores, opp_dead = self._score_side(image, self.config.opponent_slots)
        all_scores = ally_scores + opp_scores
        confidence = _confidence_from_sqdiff(all_scores)
        reading = PlayerCountReading(
            ally_dead_slots=_sanitize_dead_slots(tuple(ally_dead)),
            opponent_dead_slots=_sanitize_dead_slots(tuple(opp_dead)),
            ally_slot_scores=tuple(float(s) for s in ally_scores),
            opponent_slot_scores=tuple(float(s) for s in opp_scores),
        )
        return reading, confidence

    def _score_side(
        self,
        image: np.ndarray,
        slots: list[NormalizedBox],
    ) -> tuple[list[float], list[int]]:
        """Return per-slot SQDIFF scores and 1-based indexes that pass threshold."""
        scores: list[float] = []
        dead: list[int] = []
        threshold = self.config.sqdiff_match_threshold
        for index, box in enumerate(slots, start=1):
            roi = crop_roi(image, box)
            score, _name = (
                _best_masked_sqdiff(roi, self._templates) if roi.size else (1.0, None)
            )
            scores.append(score)
            if score <= threshold:
                dead.append(index)
        return scores, dead
