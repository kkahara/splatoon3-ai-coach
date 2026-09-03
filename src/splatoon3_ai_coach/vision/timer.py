"""Timer glyph segmentation and template matching."""

import re

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import TimerDetectorConfig
from splatoon3_ai_coach.types import NormalizedBox
from splatoon3_ai_coach.vision.glyphs import normalize_glyph
from splatoon3_ai_coach.vision.models import TimerReading
from splatoon3_ai_coach.vision.templates import load_templates


def crop_roi(image: np.ndarray, box: NormalizedBox) -> np.ndarray:
    """Crop a normalized region from a BGR frame."""
    height, width = image.shape[:2]
    x1, y1, x2, y2 = box
    left, top = int(x1 * width), int(y1 * height)
    right, bottom = int(x2 * width), int(y2 * height)
    return image[top:bottom, left:right]


def segment_glyphs(roi: np.ndarray, min_area: int = 20) -> list[np.ndarray]:
    """Segment candidate glyph images from a timer ROI."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h < min_area:
            continue
        boxes.append((x, gray[y : y + h, x : x + w]))

    boxes.sort(key=lambda item: item[0])
    return [glyph for _, glyph in boxes]


def match_glyph(
    glyph: np.ndarray,
    templates: dict[str, list[np.ndarray]],
    match_threshold: float,
) -> tuple[str | None, float]:
    """Match one glyph against templates, returning symbol and score."""
    normalized = normalize_glyph(glyph)
    best_symbol = None
    best_score = 0.0

    for symbol, examples in templates.items():
        for template in examples:
            if template.shape != normalized.shape:
                continue
            result = cv2.matchTemplate(normalized, template, cv2.TM_CCOEFF_NORMED)
            score = float(result.max())
            if score > best_score:
                best_score = score
                best_symbol = symbol

    if best_symbol is None or best_score < match_threshold:
        return None, best_score
    return best_symbol, best_score


def parse_timer_display(display: str) -> float | None:
    """Parse M:SS or MM:SS into seconds remaining."""
    match = re.fullmatch(r"(\d+):(\d{2})", display.strip())
    if not match:
        return None
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    if seconds >= 60:
        return None
    return float(minutes * 60 + seconds)


class TimerDetector:
    """Detect match timer from calibrated templates."""

    name = "timer"
    run_on_evidence = True

    def __init__(
        self,
        config: TimerDetectorConfig,
        cadence_fps: float | None = None,
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps
        self._templates = load_templates(config.template_dir)

    def detect(self, image: np.ndarray) -> tuple[TimerReading | None, float]:
        """Return a timer reading and detector score for one frame image."""
        roi = crop_roi(image, self.config.roi)
        glyphs = segment_glyphs(roi)
        if not glyphs:
            return None, 0.0

        symbols: list[str] = []
        scores: list[float] = []
        for glyph in glyphs:
            symbol, score = match_glyph(
                glyph, self._templates, self.config.match_threshold
            )
            if symbol is None:
                return None, float(np.mean(scores)) if scores else 0.0
            symbols.append(":" if symbol == "colon" else symbol)
            scores.append(score)

        display = _symbols_to_display(symbols)
        seconds = parse_timer_display(display)
        if seconds is None:
            return None, float(np.mean(scores)) if scores else 0.0

        confidence = float(np.clip(np.mean(scores), 0.0, 1.0))
        return TimerReading(display=display, seconds_remaining=seconds), confidence


def _symbols_to_display(symbols: list[str]) -> str:
    """Join matched symbols into a timer display string."""
    text = "".join(symbols)
    if ":" not in text and len(text) >= 3:
        return f"{text[:-2]}:{text[-2:]}"
    return text
