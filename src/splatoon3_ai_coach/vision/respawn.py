"""Respawn/waiting-UI detector (tight bottom-right ROI).

Primary cue: "Respawn in XX" banner templates. OCR and plate brightness
heuristics are supporting evidence only. OCR is skipped when template
``primary_hit`` already decides the frame. Never emits lifecycle events.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from splatoon3_ai_coach.config.models import RespawnDetectorConfig, VisionLanguage
from splatoon3_ai_coach.vision.language import resolve_language_template_dir
from splatoon3_ai_coach.vision.models import (
    RespawnCountdownValue,
    RespawnEvidenceType,
    RespawnReading,
)
from splatoon3_ai_coach.vision.roi import crop_roi

_RESPAWN_KEYWORDS: dict[str, tuple[str, ...]] = {
    VisionLanguage.EN.value: ("respawn",),
    VisionLanguage.JA.value: ("リスポーン", "復活", "respawn"),
}
_DIGIT_RE = re.compile(r"(?:respawn\s*in\s*)?0?([1-4])\b", re.IGNORECASE)
_SCALES = (0.7, 0.8, 0.9, 1.0, 1.1, 1.2)
# Strong template alone may trip without plate structure; mid scores need it.
_STRONG_TEMPLATE_FLOOR = 0.75


def _to_gray(roi: np.ndarray) -> np.ndarray:
    """Convert a BGR ROI to a contrast-normalized grayscale image."""
    if roi.size == 0:
        return roi
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    return clahe.apply(gray)


def _white_mask_bgr(roi: np.ndarray) -> np.ndarray:
    """Bright low-saturation mask approximating white banner glyphs."""
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (0, 0, 170), (180, 90, 255))


def _load_flat_templates(template_dir: Path | None) -> list[np.ndarray]:
    """Load grayscale templates directly from ``template_dir``."""
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


def _glyph_mask(template: np.ndarray) -> np.ndarray:
    """Bright glyphs from a grayscale template crop (banner text / yellow tab)."""
    return cv2.threshold(template, 180, 255, cv2.THRESH_BINARY)[1]


def _fit_template_to_size(ih: int, iw: int, template: np.ndarray) -> np.ndarray | None:
    """Shrink a template so it fits inside an ``ih``×``iw`` search image."""
    th, tw = template.shape[:2]
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


def _fit_template(image: np.ndarray, template: np.ndarray) -> np.ndarray | None:
    """Shrink a template so it fits inside ``image``."""
    return _fit_template_to_size(image.shape[0], image.shape[1], template)


def _scale_pyramid(ih: int, iw: int, template: np.ndarray) -> tuple[np.ndarray, ...]:
    """Fitted template variants at each ``_SCALES`` entry for a fixed search size."""
    fitted = _fit_template_to_size(ih, iw, template)
    if fitted is None:
        return ()
    variants: list[np.ndarray] = []
    for scale in _SCALES:
        if scale == 1.0:
            scaled = fitted
        else:
            new_w = max(4, int(fitted.shape[1] * scale))
            new_h = max(4, int(fitted.shape[0] * scale))
            if new_h > ih or new_w > iw:
                continue
            scaled = cv2.resize(
                fitted,
                (new_w, new_h),
                interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR,
            )
        variants.append(scaled)
    return tuple(variants)


@dataclass(frozen=True)
class PreparedRespawnTemplates:
    """Per-ROI-shape cache of scaled gray and white-glyph templates."""

    shape_hw: tuple[int, int]
    gray: tuple[tuple[np.ndarray, ...], ...]
    white: tuple[tuple[np.ndarray, ...], ...]


def prepare_respawn_templates(
    templates: list[np.ndarray], shape_hw: tuple[int, int]
) -> PreparedRespawnTemplates:
    """Precompute scale pyramids once for an invariant ROI size."""
    ih, iw = shape_hw
    gray = tuple(_scale_pyramid(ih, iw, template) for template in templates)
    white = tuple(
        _scale_pyramid(ih, iw, _glyph_mask(template)) for template in templates
    )
    return PreparedRespawnTemplates(shape_hw=shape_hw, gray=gray, white=white)


def _peak_corr(image: np.ndarray, template: np.ndarray) -> float:
    """Multi-scale peak ``TM_CCOEFF_NORMED`` of ``template`` inside ``image``."""
    best = 0.0
    for scaled in _scale_pyramid(image.shape[0], image.shape[1], template):
        result = cv2.matchTemplate(image, scaled, cv2.TM_CCOEFF_NORMED)
        best = max(best, float(result.max()))
    return best


def _peak_corr_prepared(image: np.ndarray, variants: tuple[np.ndarray, ...]) -> float:
    """Peak correlation using a precomputed scale pyramid."""
    best = 0.0
    for scaled in variants:
        result = cv2.matchTemplate(image, scaled, cv2.TM_CCOEFF_NORMED)
        best = max(best, float(result.max()))
    return best


def _best_template_score(
    roi: np.ndarray,
    templates: list[np.ndarray],
    *,
    prepared: PreparedRespawnTemplates | None = None,
    early_exit_score: float | None = _STRONG_TEMPLATE_FLOOR,
) -> float:
    """Peak of grayscale CLAHE and white-glyph mask correlations.

    ``prepared`` avoids per-frame glyph thresholding and scale resizing when the
    ROI shape is unchanged. When ``early_exit_score`` is set, matching stops once
    the running best reaches that value: for the default
    ``_STRONG_TEMPLATE_FLOOR``, that is already enough to make ``primary_hit``
    true regardless of plate structure, so remaining matches cannot change the
    boolean decision (returned score may be a lower bound of the true max).
    """
    if not templates or roi.size == 0:
        return 0.0
    gray = _to_gray(roi)
    white = _white_mask_bgr(roi)
    shape_hw = (gray.shape[0], gray.shape[1])
    if prepared is None or prepared.shape_hw != shape_hw:
        prepared = prepare_respawn_templates(templates, shape_hw)

    best = 0.0
    for gray_vars, white_vars in zip(prepared.gray, prepared.white, strict=True):
        best = max(best, _peak_corr_prepared(gray, gray_vars))
        if early_exit_score is not None and best >= early_exit_score:
            return best
        best = max(best, _peak_corr_prepared(white, white_vars))
        if early_exit_score is not None and best >= early_exit_score:
            return best
    return best


def _ocr_text(roi: np.ndarray, lang: str) -> str:
    """Optional OCR. Returns empty string when pytesseract is unavailable."""
    try:
        import pytesseract
    except ImportError:
        return ""
    if roi.size == 0:
        return ""
    gray = _to_gray(roi)
    binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    text = pytesseract.image_to_string(binary, lang=lang, config="--psm 7")
    return text.strip()


def _parse_countdown_value(text: str) -> RespawnCountdownValue | None:
    """Extract a respawn digit 1–4 from OCR text when present."""
    if not text:
        return None
    match = _DIGIT_RE.search(text)
    if match is None:
        return None
    value = int(match.group(1))
    if value in {1, 2, 3, 4}:
        return value  # type: ignore[return-value]
    return None


def _keyword_hit(text: str, keywords: tuple[str, ...]) -> bool:
    """Return whether any keyword appears in OCR text (case-insensitive)."""
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


class RespawnDetector:
    """Detect the post-death respawn/waiting banner in the BR ROI.

    Always returns a reading so lifecycle can count consecutive observations.
    Template match is primary; OCR and plate heuristics are supporting only.
    """

    name = "respawn"
    run_on_evidence = True

    def __init__(
        self,
        config: RespawnDetectorConfig,
        cadence_fps: float | None = None,
        *,
        language: VisionLanguage | str = VisionLanguage.EN,
        ocr_lang: str = "eng",
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps
        self.language = (
            language.value if isinstance(language, VisionLanguage) else str(language)
        )
        self.ocr_lang = ocr_lang
        self._template_dir = (
            resolve_language_template_dir(config.template_dir, self.language)
            if config.template_dir is not None
            else None
        )
        self._templates = _load_flat_templates(self._template_dir)
        self._prepared: PreparedRespawnTemplates | None = None
        self._last_template_at: float | None = None
        if self._template_dir is not None and not self._templates:
            logger.debug(
                "Respawn detector has no templates under {}; using OCR/heuristics",
                self._template_dir,
            )

    def _prepared_templates(self, shape_hw: tuple[int, int]) -> PreparedRespawnTemplates:
        """Return cached scale pyramids for this ROI size, building on first use."""
        if self._prepared is None or self._prepared.shape_hw != shape_hw:
            self._prepared = prepare_respawn_templates(self._templates, shape_hw)
        return self._prepared

    def detect(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
    ) -> tuple[RespawnReading | None, float]:
        """Return a respawn/waiting-UI reading for one frame."""
        reading, confidence = self._observe(image)
        if reading.detected and reading.evidence_type == "template":
            if timestamp is not None:
                self._last_template_at = timestamp
            return reading, confidence
        held = self._held_reading(reading, timestamp)
        if held is not None:
            return held, held.confidence
        return reading, confidence

    def _held_reading(
        self,
        reading: RespawnReading,
        timestamp: float | None,
    ) -> RespawnReading | None:
        """Keep a brief positive after the countdown plate disappears."""
        if timestamp is None or self._last_template_at is None:
            return None
        if timestamp - self._last_template_at > self.config.hold_seconds:
            return None
        return reading.model_copy(
            update={
                "detected": True,
                "evidence_type": "hold",
                "confidence": max(0.55, reading.confidence),
            }
        )

    def _observe(self, image: np.ndarray) -> tuple[RespawnReading, float]:
        """Score template, OCR, and plate cues; never emit events."""
        roi = crop_roi(image, self.config.roi)
        if roi.size == 0:
            empty = RespawnReading()
            return empty, 0.0

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        flat = gray.reshape(-1).astype(np.float32)
        dark_frac = float((flat <= 40).mean())
        bright_frac = float((flat >= 200).mean())
        p95 = float(np.percentile(flat, 95) / 255.0)
        yellow = cv2.inRange(hsv, (15, 60, 120), (40, 255, 255))
        yellow_frac = float((yellow > 0).mean())
        mean_sat = float(hsv[:, :, 1].mean() / 255.0)
        mean_val = float(hsv[:, :, 2].mean() / 255.0)
        presence_score = float(
            np.clip(0.4 * dark_frac + 0.3 * bright_frac + 0.3 * p95, 0.0, 1.0)
        )
        structure_ok = (
            dark_frac >= self.config.dark_frac_min
            and bright_frac >= self.config.bright_frac_min
            and p95 >= self.config.p95_min
        )

        template_score = _best_template_score(
            roi,
            self._templates,
            prepared=self._prepared_templates((roi.shape[0], roi.shape[1])),
        )
        primary_hit = False
        support_hit = False
        if self._templates:
            primary_hit = template_score >= self.config.match_threshold and (
                structure_ok or template_score >= _STRONG_TEMPLATE_FLOOR
            )
            support_hit = (
                structure_ok
                and template_score >= self.config.support_match_threshold
            )

        # OCR cannot change the decision once template primary_hit wins, and
        # lifecycle/events only consume detected + evidence_type (not ocr fields).
        ocr_raw = ""
        if not primary_hit:
            ocr_raw = _ocr_text(roi, self.ocr_lang)
        ocr_text = ocr_raw or None
        keywords = _RESPAWN_KEYWORDS.get(
            self.language,
            _RESPAWN_KEYWORDS[VisionLanguage.EN.value],
        )
        ocr_hit = _keyword_hit(ocr_raw, keywords)
        countdown_value = _parse_countdown_value(ocr_raw)

        detected = False
        evidence_type: RespawnEvidenceType | None = None
        confidence: float

        if self._templates:
            if primary_hit:
                detected = True
                evidence_type = "template"
                confidence = float(np.clip(template_score, 0.0, 1.0))
            elif ocr_hit and countdown_value is not None:
                detected = True
                evidence_type = "ocr"
                confidence = 0.58
            elif support_hit:
                # Plate structure + soft template: supporting evidence path.
                detected = True
                evidence_type = "heuristic"
                confidence = float(max(0.55, template_score))
            else:
                confidence = max(0.55, 1.0 - presence_score)
        elif structure_ok:
            detected = True
            evidence_type = "heuristic"
            confidence = max(0.55, presence_score)
        elif ocr_hit and countdown_value is not None:
            detected = True
            evidence_type = "ocr"
            confidence = 0.58
        else:
            confidence = max(0.55, 1.0 - presence_score)

        reading = RespawnReading(
            detected=detected,
            confidence=confidence,
            countdown_value=countdown_value if detected else None,
            template_score=template_score,
            ocr_text=ocr_text,
            evidence_type=evidence_type,
            presence_score=presence_score,
            dark_frac=dark_frac,
            bright_frac=bright_frac,
            p95=p95,
            yellow_frac=yellow_frac,
            mean_sat=mean_sat,
            mean_val=mean_val,
        )
        return reading, confidence
