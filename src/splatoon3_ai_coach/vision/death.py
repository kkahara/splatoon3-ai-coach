"""Death-screen detector: Ouch glyph plus supporting banner cues."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from splatoon3_ai_coach.config.models import DeathDetectorConfig, VisionLanguage
from splatoon3_ai_coach.vision.language import resolve_language_template_dir
from splatoon3_ai_coach.vision.models import DeathReading
from splatoon3_ai_coach.vision.roi import crop_roi

_OUCH_KEYWORDS = ("ouch",)
_BANNER_KEYWORDS = ("splat", "splatted")


def _to_gray(roi: np.ndarray) -> np.ndarray:
    """Convert a BGR ROI to a contrast-normalized grayscale image."""
    if roi.size == 0:
        return roi
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    return clahe.apply(gray)


def _yellow_ratio(roi: np.ndarray) -> float:
    """Fraction of saturated yellow/lime pixels (ink noise, not Ouch text)."""
    if roi.size == 0:
        return 0.0
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (15, 70, 130), (45, 255, 255))
    return float(cv2.countNonZero(mask)) / float(mask.size)


def _white_ratio(roi: np.ndarray) -> float:
    """Fraction of bright, low-saturation pixels (white HUD glyphs)."""
    if roi.size == 0:
        return 0.0
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 0, 190), (180, 70, 255))
    return float(cv2.countNonZero(mask)) / float(mask.size)


def _saturation_ratio(roi: np.ndarray, min_saturation: int = 80) -> float:
    """Fraction of pixels with strong chroma (ink / colorful stage)."""
    if roi.size == 0:
        return 1.0
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    return float(np.mean(hsv[:, :, 1] >= min_saturation))


def _mean_luma(roi: np.ndarray) -> float:
    """Mean luminance of a BGR ROI in [0, 1]."""
    if roi.size == 0:
        return 1.0
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    return float(gray.mean()) / 255.0


def _ocr_text(roi: np.ndarray) -> str:
    """Optional OCR. Returns empty string when pytesseract is unavailable."""
    try:
        import pytesseract
    except ImportError:
        return ""
    if roi.size == 0:
        return ""
    gray = _to_gray(roi)
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    text = pytesseract.image_to_string(binary, config="--psm 7")
    return text.lower().strip()


def _keyword_hit(text: str, keywords: tuple[str, ...]) -> bool:
    """Return whether any keyword appears in OCR text."""
    return any(keyword in text for keyword in keywords)


def _load_language_cue_templates(
    template_dir: Path | None,
    cue: str,
    language: VisionLanguage | str,
) -> list[np.ndarray]:
    """Load grayscale templates from ``template_dir / cue / {language}``."""
    if template_dir is None:
        return []
    folder = resolve_language_template_dir(template_dir / cue, language)
    templates: list[np.ndarray] = []
    for path in sorted(folder.glob("*")):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            continue
        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is None or image.size == 0:
            continue
        templates.append(image)
    return templates


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


class DeathDetector:
    """Detect the local player's death HUD on 1080p-relative ROIs."""

    name = "death"
    run_on_evidence = True

    def __init__(
        self,
        config: DeathDetectorConfig,
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
        self._ouch_templates = _load_language_cue_templates(
            config.template_dir, "ouch", self.language
        )
        self._banner_templates = _load_language_cue_templates(
            config.template_dir,
            "splatted",
            self.language,
        )
        if config.template_dir is not None and not (
            self._ouch_templates or self._banner_templates
        ):
            logger.debug(
                "Death detector has no templates under {}/{{ouch,splatted}}/{}; "
                "using heuristics and OCR",
                config.template_dir,
                self.language,
            )
        if config.ouch_require_glyph and not self._ouch_templates:
            logger.warning(
                "DeathDetector ouch_require_glyph=True but no Ouch templates loaded; "
                "only OCR can assert strong Ouch"
            )

    def detect(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
    ) -> tuple[DeathReading | None, float]:
        """Return a death-UI reading, suppressing positives during debounce."""
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

    def _observe(self, image: np.ndarray) -> tuple[DeathReading, float]:
        """Score Ouch glyph + supporting banner cues without applying debounce."""
        ouch_roi = crop_roi(image, self.config.ouch_roi)
        banner_roi = crop_roi(image, self.config.banner_roi)

        ouch_white = _white_ratio(ouch_roi)
        ouch_yellow = _yellow_ratio(ouch_roi)
        ouch_sat = _saturation_ratio(ouch_roi)
        ouch_template = _best_template_score(ouch_roi, self._ouch_templates)
        ouch_ocr = _keyword_hit(_ocr_text(ouch_roi), _OUCH_KEYWORDS)
        ouch_heuristic = (
            ouch_white >= self.config.ouch_white_ratio
            and ouch_sat <= self.config.ouch_max_saturation
            and ouch_yellow <= self.config.ouch_max_yellow_ratio
        )
        ouch_glyph = (
            ouch_template >= self.config.ouch_match_threshold or ouch_ocr
        )
        if self.config.ouch_require_glyph:
            ouch_detected = ouch_glyph
        else:
            ouch_detected = ouch_glyph or ouch_heuristic

        banner_dark_score = float(np.clip(1.0 - _mean_luma(banner_roi), 0.0, 1.0))
        banner_template = _best_template_score(banner_roi, self._banner_templates)
        banner_ocr = _keyword_hit(_ocr_text(banner_roi), _BANNER_KEYWORDS)
        banner_detected = (
            banner_template >= self.config.banner_match_threshold or banner_ocr
        )
        banner_ok = (
            banner_detected or banner_dark_score >= self.config.banner_dark_threshold
        )
        # Ouch / やられた in the dedicated ROI is the primary death cue.
        # The bottom banner is often a kill feed or still-bright death-cam
        # chrome, so it must not veto a strong glyph.
        if self.config.ouch_require_glyph:
            detected = ouch_detected
        else:
            detected = ouch_detected and banner_ok

        ouch_score = max(ouch_template, 1.0 if ouch_ocr else 0.0, ouch_white * 0.5)
        confidence = _confidence(
            detected, ouch_score, banner_dark_score, banner_detected, ouch_template
        )
        return (
            DeathReading(
                detected=detected,
                ouch_detected=ouch_detected,
                ouch_template_score=float(np.clip(ouch_template, 0.0, 1.0)),
                ouch_white_score=float(np.clip(ouch_white, 0.0, 1.0)),
                ouch_heuristic=ouch_heuristic,
                banner_detected=banner_detected,
                banner_template_score=float(np.clip(banner_template, 0.0, 1.0)),
                banner_dark_score=banner_dark_score,
            ),
            confidence,
        )


def _confidence(
    detected: bool,
    ouch_score: float,
    banner_dark_score: float,
    banner_detected: bool,
    ouch_template: float,
) -> float:
    """Combine cue strengths into a detector score in [0, 1]."""
    banner_score = max(banner_dark_score, 1.0 if banner_detected else 0.0)
    # Weight glyph match more heavily than darkness when scoring positives.
    glyph = max(ouch_template, ouch_score)
    if detected:
        return float(np.clip(0.65 * glyph + 0.35 * banner_score, 0.0, 1.0))
    return float(np.clip(1.0 - max(glyph, banner_score * 0.5), 0.0, 1.0))
