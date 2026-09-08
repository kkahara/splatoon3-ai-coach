"""Local-kill banner detector: squid-skull icon and Splatted / をたおした text."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from splatoon3_ai_coach.config.models import SplatDetectorConfig, VisionLanguage
from splatoon3_ai_coach.vision.language import resolve_language_template_dir
from splatoon3_ai_coach.vision.models import SplatBannerInstance, SplatReading
from splatoon3_ai_coach.vision.roi import crop_roi

# Template scales relative to loaded crops (icons ~30px; text ~40px tall).
_TEMPLATE_SCALES = (0.75, 1.0, 1.25, 1.5)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
_TEXT_NAME_MARKERS = ("splatted", "taoshita")
# Optional nested packs inside a language directory (language-neutral icons).
_NESTED_TEMPLATE_DIRS = ("skull",)


def _is_text_template(path: Path) -> bool:
    """Return whether a filename is a localized kill-banner text crop."""
    name = path.stem.lower()
    return any(marker in name for marker in _TEXT_NAME_MARKERS)


def _iter_template_paths(template_dir: Path) -> list[Path]:
    """Image files in a language template directory plus optional ``skull/``."""
    folders = [template_dir]
    for name in _NESTED_TEMPLATE_DIRS:
        nested = template_dir / name
        if nested.is_dir():
            folders.append(nested)
    paths: list[Path] = []
    seen: set[Path] = set()
    for folder in folders:
        for path in sorted(folder.glob("*")):
            if not path.is_file() or path.suffix.lower() not in _IMAGE_SUFFIXES:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(path)
    return paths


def _read_gray(path: Path) -> np.ndarray | None:
    """Load one template as grayscale, or None if unreadable."""
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None or image.size == 0:
        return None
    return image


def _load_classified_templates(
    template_dir: Path | None,
) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """Load icon and text templates from a resolved language template root."""
    if template_dir is None or not template_dir.exists():
        return [], []
    icons: list[np.ndarray] = []
    texts: list[np.ndarray] = []
    for path in _iter_template_paths(template_dir):
        image = _read_gray(path)
        if image is None:
            continue
        if _is_text_template(path):
            texts.append(image)
        else:
            icons.append(image)
    return icons, texts


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


def _match_templates(
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


def _score_icon_rows(
    roi: np.ndarray,
    gray: np.ndarray,
    templates: list[np.ndarray],
    white_value_threshold: int,
    *,
    min_score: float,
    min_vertical_gap: int = 18,
) -> list[tuple[float, tuple[int, int, int, int]]]:
    """Skull matches kept as distinct stacked rows (best first, then by y)."""
    raw: list[tuple[float, tuple[int, int, int, int]]] = []
    for window in _candidate_windows(_white_mask(roi, white_value_threshold)):
        score, box = _match_templates(gray, templates, window)
        if box is not None and score >= min_score:
            raw.append((score, box))
    full_score, full_box = _match_templates(gray, templates, None)
    if full_box is not None and full_score >= min_score:
        raw.append((full_score, full_box))
    return _nms_rows(raw, min_vertical_gap=min_vertical_gap)


def _nms_rows(
    hits: list[tuple[float, tuple[int, int, int, int]]],
    *,
    min_vertical_gap: int,
) -> list[tuple[float, tuple[int, int, int, int]]]:
    """Keep vertically separated matches, stronger score first."""
    ordered = sorted(hits, key=lambda item: item[0], reverse=True)
    kept: list[tuple[float, tuple[int, int, int, int]]] = []
    for score, box in ordered:
        cy = (box[1] + box[3]) / 2.0
        overlap = any(
            abs(cy - (other[1][1] + other[1][3]) / 2.0) < min_vertical_gap
            for other in kept
        )
        if overlap:
            continue
        kept.append((score, box))
    kept.sort(key=lambda item: item[1][1])
    return kept


def _template_peaks(
    gray: np.ndarray,
    templates: list[np.ndarray],
    *,
    min_score: float,
) -> list[tuple[float, tuple[int, int, int, int]]]:
    """Every template match at or above ``min_score`` (not just the best)."""
    if not templates or gray.size == 0 or min(gray.shape[:2]) < 8:
        return []
    peaks: list[tuple[float, tuple[int, int, int, int]]] = []
    for template in templates:
        for scale in _TEMPLATE_SCALES:
            tw = max(8, int(template.shape[1] * scale))
            th = max(8, int(template.shape[0] * scale))
            if th > gray.shape[0] or tw > gray.shape[1]:
                continue
            scaled = cv2.resize(template, (tw, th), interpolation=cv2.INTER_AREA)
            result = cv2.matchTemplate(gray, scaled, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(result >= min_score)
            for y, x in zip(ys.tolist(), xs.tolist(), strict=True):
                peaks.append(
                    (float(result[y, x]), (int(x), int(y), int(x + tw), int(y + th)))
                )
    return peaks


def _banner_strip(
    gray: np.ndarray, skull_box: tuple[int, int, int, int]
) -> np.ndarray:
    """Full-width row strip so victim text is inside the fingerprint."""
    height, _width = gray.shape[:2]
    _x0, y0, _x1, y1 = skull_box
    pad = 4
    sy0 = max(0, y0 - pad)
    sy1 = min(height, max(y1 + pad, sy0 + 12))
    return gray[sy0:sy1, :]


def banner_fingerprint(gray_strip: np.ndarray) -> str:
    """64-bit average hash of a banner strip, as 16 hex chars."""
    if gray_strip.size == 0 or min(gray_strip.shape[:2]) < 2:
        return "0" * 16
    small = cv2.resize(gray_strip, (8, 8), interpolation=cv2.INTER_AREA)
    mean = float(small.mean())
    bits = (small.reshape(-1) > mean).astype(np.uint8)
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def fingerprint_distance(left: str, right: str) -> int:
    """Hamming distance between two hex fingerprints."""
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return 64


class SplatDetector:
    """Detect the local player's kill banner on a fixed bottom-center ROI.

    Primary cue is the language-neutral squid-skull (and skull+ink) icon.
    Localized ``Splatted`` / ``をたおした`` crops are a second independent
    trip. Kill-feed skulls in the same ROI score well below the icon
    threshold and do not contain the banner text.
    """

    name = "splat"
    run_on_evidence = True

    def __init__(
        self,
        config: SplatDetectorConfig,
        cadence_fps: float | None = None,
        *,
        language: VisionLanguage | str = VisionLanguage.EN,
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps
        self.language = (
            language.value if isinstance(language, VisionLanguage) else str(language)
        )
        self._last_positive_at: float | None = None
        self._template_dir = (
            resolve_language_template_dir(config.template_dir, self.language)
            if config.template_dir is not None
            else None
        )
        self._icon_templates, self._text_templates = _load_classified_templates(
            self._template_dir
        )
        self._templates = self._icon_templates + self._text_templates
        if self._template_dir is not None and not self._templates:
            logger.warning(
                "Splat detector has no templates under {}",
                self._template_dir,
            )

    def detect(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
    ) -> tuple[SplatReading | None, float]:
        """Return every banner instance on this frame.

        Debounce (default 0) is a last-resort anti-glitch, not incident policy.
        """
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

    def _banner_rows(
        self, roi: np.ndarray, gray: np.ndarray
    ) -> list[tuple[float, tuple[int, int, int, int]]]:
        """Skull and text matches, merged into vertically distinct rows."""
        skulls = _score_icon_rows(
            roi,
            gray,
            self._icon_templates,
            self.config.white_value_threshold,
            min_score=self.config.skull_match_threshold,
        )
        texts = _nms_rows(
            _template_peaks(
                gray,
                self._text_templates,
                min_score=self.config.text_match_threshold,
            ),
            min_vertical_gap=18,
        )
        return _nms_rows(skulls + texts, min_vertical_gap=18)

    def _observe(self, image: np.ndarray) -> tuple[SplatReading, float]:
        """Score every stacked banner row without applying debounce."""
        roi = crop_roi(image, self.config.banner_roi)
        if roi.size == 0 or not self._templates:
            return SplatReading(), 0.0
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        merged = self._banner_rows(roi, gray)
        instances = [
            inst
            for _score, box in merged
            if (inst := self._instance_from_row(gray, box)) is not None
        ]
        if not instances:
            fallback = self._fallback_text_instance(gray)
            if fallback is not None:
                instances.append(fallback)
        skull_score = max((item.skull_score for item in instances), default=0.0)
        text_score = max((item.text_score for item in instances), default=0.0)
        color_score = 0.0
        if merged:
            color_score = _adjacent_color_score(
                roi, merged[0][1], min_saturation=self.config.adjacent_min_saturation
            )
        detected = bool(instances)
        return (
            SplatReading(
                detected=detected,
                skull_score=float(np.clip(skull_score, 0.0, 1.0)),
                text_score=float(np.clip(text_score, 0.0, 1.0)),
                adjacent_color_score=float(np.clip(color_score, 0.0, 1.0)),
                instances=instances,
            ),
            _confidence(detected, skull_score, text_score),
        )

    def _instance_from_row(
        self,
        gray: np.ndarray,
        box: tuple[int, int, int, int],
        skull_score: float | None = None,
    ) -> SplatBannerInstance | None:
        """Build one banner instance from a skull or text row."""
        strip = _banner_strip(gray, box)
        text_score, _ = _match_templates(strip, self._text_templates, None)
        if skull_score is None:
            skull_score, _ = _match_templates(strip, self._icon_templates, None)
        if (
            skull_score < self.config.skull_match_threshold
            and text_score < self.config.text_match_threshold
        ):
            return None
        height = max(gray.shape[0], 1)
        slot_y = float(np.clip(((box[1] + box[3]) / 2.0) / height, 0.0, 1.0))
        return SplatBannerInstance(
            fingerprint=banner_fingerprint(strip),
            slot_y=slot_y,
            skull_score=float(np.clip(skull_score, 0.0, 1.0)),
            text_score=float(np.clip(text_score, 0.0, 1.0)),
        )

    def _fallback_text_instance(self, gray: np.ndarray) -> SplatBannerInstance | None:
        """Single instance when only the full-ROI text cue fires."""
        text_score, box = _match_templates(gray, self._text_templates, None)
        if text_score < self.config.text_match_threshold:
            return None
        strip = _banner_strip(gray, box) if box is not None else gray
        height = max(gray.shape[0], 1)
        slot_y = 0.5
        if box is not None:
            slot_y = float(np.clip(((box[1] + box[3]) / 2.0) / height, 0.0, 1.0))
        return SplatBannerInstance(
            fingerprint=banner_fingerprint(strip),
            slot_y=slot_y,
            skull_score=0.0,
            text_score=float(np.clip(text_score, 0.0, 1.0)),
        )


def _confidence(detected: bool, skull_score: float, text_score: float) -> float:
    """Map the stronger banner cue into a detector score in [0, 1]."""
    peak = max(skull_score, text_score)
    if detected:
        return float(np.clip(peak, 0.0, 1.0))
    return float(np.clip(peak * 0.45, 0.0, 1.0))
