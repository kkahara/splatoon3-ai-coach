"""Local-kill banner detector: white skull plus adjacent team-color splat."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from splatoon3_ai_coach.config.models import SplatDetectorConfig
from splatoon3_ai_coach.vision.models import SplatReading
from splatoon3_ai_coach.vision.roi import crop_roi

# Template scales relative to loaded skull glyphs (cropped near 32px tall).
_TEMPLATE_SCALES = (0.75, 1.0, 1.25, 1.5)


def _load_skull_templates(template_dir: Path | None) -> list[np.ndarray]:
    """Load grayscale skull templates from ``template_dir / skull``."""
    if template_dir is None:
        return []
    folder = template_dir / "skull"
    if not folder.exists():
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


def _white_mask(roi: np.ndarray, value_threshold: int) -> np.ndarray:
    """Binary mask of bright, low-saturation pixels in a BGR ROI."""
    if roi.size == 0:
        return np.zeros(roi.shape[:2], dtype=np.uint8)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (0, 0, value_threshold), (180, 70, 255))


def _candidate_windows(
    mask: np.ndarray,
    *,
    pad: int = 4,
) -> list[tuple[int, int, int, int]]:
    """Bounding boxes around compact white components (candidate skulls)."""
    if mask.size == 0:
        return []
    cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, _, stats, _ = cv2.connectedComponentsWithStats(cleaned)
    height, width = mask.shape[:2]
    windows: list[tuple[int, int, int, int]] = []
    for index in range(1, count):
        x, y, bw, bh, area = stats[index]
        aspect = bw / max(bh, 1)
        if not (8 <= bw <= 80 and 8 <= bh <= 80 and 40 <= area <= 2500):
            continue
        if not (0.55 <= aspect <= 1.6):
            continue
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(width, x + bw + pad)
        y1 = min(height, y + bh + pad)
        windows.append((x0, y0, x1, y1))
    return windows


def _match_skull(
    gray_roi: np.ndarray,
    templates: list[np.ndarray],
    window: tuple[int, int, int, int] | None = None,
) -> tuple[float, tuple[int, int, int, int] | None]:
    """Best normalized correlation; returns score and match box in ROI coords."""
    if not templates or gray_roi.size == 0:
        return 0.0, None
    if window is None:
        search = gray_roi
        ox = oy = 0
    else:
        x0, y0, x1, y1 = window
        search = gray_roi[y0:y1, x0:x1]
        ox, oy = x0, y0
    if search.size == 0 or min(search.shape[:2]) < 8:
        return 0.0, None

    best_score = 0.0
    best_box: tuple[int, int, int, int] | None = None
    for template in templates:
        for scale in _TEMPLATE_SCALES:
            tw = max(8, int(template.shape[1] * scale))
            th = max(8, int(template.shape[0] * scale))
            if th > search.shape[0] or tw > search.shape[1]:
                continue
            scaled = cv2.resize(template, (tw, th), interpolation=cv2.INTER_AREA)
            result = cv2.matchTemplate(search, scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            score = float(max_val)
            if score > best_score:
                mx, my = max_loc
                best_score = score
                best_box = (ox + mx, oy + my, ox + mx + tw, oy + my + th)
    return best_score, best_box


def _adjacent_color_score(
    roi: np.ndarray,
    skull_box: tuple[int, int, int, int],
    *,
    min_saturation: int,
) -> float:
    """Fraction of saturated non-black pixels immediately right of the skull."""
    if roi.size == 0:
        return 0.0
    x0, y0, x1, y1 = skull_box
    width = max(8, x1 - x0)
    height = max(8, y1 - y0)
    ax0 = x1 + 1
    ax1 = min(roi.shape[1], ax0 + width)
    ay0 = max(0, y0 - 2)
    ay1 = min(roi.shape[0], y1 + 2)
    if ax1 <= ax0 or ay1 <= ay0:
        return 0.0
    patch = roi[ay0:ay1, ax0:ax1]
    if patch.size == 0:
        return 0.0
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    color = (
        (hsv[:, :, 1] >= min_saturation)
        & (hsv[:, :, 2] >= 50)
        & (hsv[:, :, 2] <= 245)
    )
    return float(np.mean(color))


class SplatDetector:
    """Detect the local player's kill banner on a fixed bottom-center ROI.

    Primary cues (skull icon + adjacent team color) are language-neutral.
    ``VisionConfig.language`` is reserved for a later victim-name OCR pass;
    this detector does not read text.
    """

    name = "splat"
    run_on_evidence = True

    def __init__(
        self,
        config: SplatDetectorConfig,
        cadence_fps: float | None = None,
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps
        self._last_positive_at: float | None = None
        self._templates = _load_skull_templates(config.template_dir)
        if config.template_dir is not None and not self._templates:
            logger.warning(
                "Splat detector has no skull templates under {}/skull",
                config.template_dir,
            )

    def detect(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
    ) -> tuple[SplatReading | None, float]:
        """Return a splat-banner reading, suppressing positives during debounce."""
        reading, confidence = self._observe(image)
        if not reading.detected:
            return reading, confidence
        if self._in_debounce(timestamp):
            return None, 0.0
        if timestamp is not None:
            self._last_positive_at = timestamp
        return reading, confidence

    def _in_debounce(self, timestamp: float | None) -> bool:
        """Return whether a positive detection should be suppressed."""
        if timestamp is None or self._last_positive_at is None:
            return False
        return timestamp - self._last_positive_at < self.config.debounce_seconds

    def _observe(self, image: np.ndarray) -> tuple[SplatReading, float]:
        """Score skull + adjacent color without applying debounce."""
        roi = crop_roi(image, self.config.banner_roi)
        if roi.size == 0 or not self._templates:
            return SplatReading(), 0.0

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        mask = _white_mask(roi, self.config.white_value_threshold)
        windows = _candidate_windows(mask)

        best_score = 0.0
        best_box: tuple[int, int, int, int] | None = None
        if windows:
            for window in windows:
                score, box = _match_skull(gray, self._templates, window)
                if score > best_score:
                    best_score = score
                    best_box = box
        # Fallback: full-ROI template search when white components are weak.
        full_score, full_box = _match_skull(gray, self._templates, None)
        if full_score > best_score:
            best_score = full_score
            best_box = full_box

        color_score = 0.0
        if best_box is not None:
            color_score = _adjacent_color_score(
                roi,
                best_box,
                min_saturation=self.config.adjacent_min_saturation,
            )

        skull_ok = best_score >= self.config.skull_match_threshold
        color_ok = color_score >= self.config.adjacent_color_min_ratio
        detected = skull_ok and color_ok
        confidence = _confidence(detected, best_score, color_score)
        return (
            SplatReading(
                detected=detected,
                skull_score=float(np.clip(best_score, 0.0, 1.0)),
                adjacent_color_score=float(np.clip(color_score, 0.0, 1.0)),
                victim_name=None,
                victim_name_confidence=0.0,
            ),
            confidence,
        )


def _confidence(detected: bool, skull_score: float, color_score: float) -> float:
    """Combine skull and color strengths into a detector score in [0, 1]."""
    if detected:
        return float(np.clip(0.65 * skull_score + 0.35 * color_score, 0.0, 1.0))
    return float(np.clip(max(skull_score, color_score) * 0.45, 0.0, 1.0))
