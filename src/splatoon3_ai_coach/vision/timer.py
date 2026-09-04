"""Timer glyph segmentation and template matching."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from splatoon3_ai_coach.config.models import TimerDetectorConfig
from splatoon3_ai_coach.types import NormalizedBox
from splatoon3_ai_coach.vision.glyphs import normalize_glyph
from splatoon3_ai_coach.vision.models import TimerReading
from splatoon3_ai_coach.vision.roi import crop_roi
from splatoon3_ai_coach.vision.templates import load_templates


@dataclass(frozen=True)
class GlyphBox:
    """Axis-aligned glyph candidate in ROI coordinates."""

    x: int
    y: int
    w: int
    h: int
    label: str = "candidate"
    accepted: bool = True
    reason: str = ""

    @property
    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h

    @property
    def area(self) -> int:
        return self.w * self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0


@dataclass
class SegmentationResult:
    """Full segmentation trace for one timer ROI."""

    gray: np.ndarray
    binary: np.ndarray
    raw_boxes: list[GlyphBox]
    filtered_boxes: list[GlyphBox]
    rejected_boxes: list[GlyphBox]
    final_boxes: list[GlyphBox]
    glyphs: list[np.ndarray]


def segment_glyphs(roi: np.ndarray, min_area: int = 8) -> list[np.ndarray]:
    """Segment candidate glyph images from a timer ROI."""
    return segment_timer_roi(roi, min_area=min_area).glyphs


# Relative slots inside the timer ROI for the common Splatoon M:SS layout.
# Measured from successful debug frames (digit ~20x34, colon ~10x24 in ~153x76 ROI).
TIMER_SLOTS_M_SS: tuple[tuple[str, NormalizedBox], ...] = (
    ("minute", (0.10, 0.12, 0.36, 0.90)),
    ("colon", (0.34, 0.25, 0.48, 0.82)),
    ("tens", (0.48, 0.12, 0.70, 0.90)),
    ("ones", (0.70, 0.12, 0.92, 0.90)),
)


def segment_timer_slots(roi: np.ndarray) -> SegmentationResult:
    """Crop fixed M:SS glyph slots inside the timer ROI.

    Connected components are used only to tighten each slot crop, not to
    discover glyph count/order. That avoids merged glyphs like ``11``/``22``.
    """
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    height, width = gray.shape[:2]
    final_boxes: list[GlyphBox] = []
    glyphs: list[np.ndarray] = []
    for label, rel_box in TIMER_SLOTS_M_SS:
        box = _absolute_box(rel_box, width, height, label=label)
        refined = _refine_slot_box(binary, box, label=label)
        final_boxes.append(refined)
        glyphs.append(
            gray[
                refined.y : refined.y + refined.h,
                refined.x : refined.x + refined.w,
            ]
        )

    return SegmentationResult(
        gray=gray,
        binary=binary,
        raw_boxes=list(final_boxes),
        filtered_boxes=list(final_boxes),
        rejected_boxes=[],
        final_boxes=final_boxes,
        glyphs=glyphs,
    )


def _absolute_box(
    rel: NormalizedBox,
    width: int,
    height: int,
    *,
    label: str,
) -> GlyphBox:
    """Convert a normalized ROI-relative box into pixel coordinates."""
    x1, y1, x2, y2 = rel
    left = int(round(x1 * width))
    top = int(round(y1 * height))
    right = int(round(x2 * width))
    bottom = int(round(y2 * height))
    left = max(0, min(left, width - 1))
    top = max(0, min(top, height - 1))
    right = max(left + 1, min(right, width))
    bottom = max(top + 1, min(bottom, height))
    return GlyphBox(left, top, right - left, bottom - top, label=label)


def _refine_slot_box(
    binary: np.ndarray,
    slot: GlyphBox,
    *,
    label: str,
) -> GlyphBox:
    """Tighten a fixed slot around ink inside it.

    Digits use the largest component. Colon uses the union of components so
    both dots stay in one crop.
    """
    patch = binary[slot.y : slot.y + slot.h, slot.x : slot.x + slot.w]
    if patch.size == 0 or int(patch.max()) == 0:
        return GlyphBox(slot.x, slot.y, slot.w, slot.h, label=label)

    contours, _ = cv2.findContours(
        patch, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return GlyphBox(slot.x, slot.y, slot.w, slot.h, label=label)

    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h < 4:
            continue
        boxes.append((x, y, w, h))
    if not boxes:
        return GlyphBox(slot.x, slot.y, slot.w, slot.h, label=label)

    if label == "colon":
        boxes.sort(key=lambda item: item[2] * item[3], reverse=True)
        keep = boxes[:2]
    else:
        # Union all ink in the slot so fragmented digits (e.g. "3") stay intact,
        # while the fixed slot still prevents merging with neighbors ("11", "22").
        keep = boxes

    x1 = min(item[0] for item in keep)
    y1 = min(item[1] for item in keep)
    x2 = max(item[0] + item[2] for item in keep)
    y2 = max(item[1] + item[3] for item in keep)
    x, y, w, h = x1, y1, x2 - x1, y2 - y1

    pad = 1
    abs_x = max(slot.x, slot.x + x - pad)
    abs_y = max(slot.y, slot.y + y - pad)
    abs_r = min(slot.x + slot.w, slot.x + x + w + pad)
    abs_b = min(slot.y + slot.h, slot.y + y + h + pad)
    return GlyphBox(abs_x, abs_y, abs_r - abs_x, abs_b - abs_y, label=label)


def segment_timer_roi(roi: np.ndarray, min_area: int = 8) -> SegmentationResult:
    """Run the full timer segmentation pipeline with intermediate boxes."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    # Bright HUD text on a dark timer plate; Otsu alone is kept for now,
    # but diagnostics expose whether this binary step is the failure point.
    _, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    raw_boxes: list[GlyphBox] = []
    for contour in cv2.findContours(
        binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )[0]:
        x, y, w, h = cv2.boundingRect(contour)
        if w * h < min_area:
            continue
        raw_boxes.append(GlyphBox(x, y, w, h, label="raw"))

    filtered, rejected = _filter_raw_boxes(raw_boxes, gray.shape[1], gray.shape[0])
    final_boxes = _merge_colon_pairs(filtered, gray.shape[1], gray.shape[0])
    final_boxes = sorted(final_boxes, key=lambda box: box.x)
    glyphs = [gray[box.y : box.y + box.h, box.x : box.x + box.w] for box in final_boxes]
    return SegmentationResult(
        gray=gray,
        binary=binary,
        raw_boxes=raw_boxes,
        filtered_boxes=filtered,
        rejected_boxes=rejected,
        final_boxes=final_boxes,
        glyphs=glyphs,
    )


def _filter_raw_boxes(
    boxes: list[GlyphBox],
    roi_width: int,
    roi_height: int,
) -> tuple[list[GlyphBox], list[GlyphBox]]:
    """Keep digit/dot-like components; drop bars, noise, and oversized blobs."""
    accepted: list[GlyphBox] = []
    rejected: list[GlyphBox] = []
    min_digit_h = max(int(roi_height * 0.40), 10)
    max_digit_w = max(int(roi_width * 0.35), 12)
    min_dot_h = max(int(roi_height * 0.05), 2)
    max_dot_h = max(int(roi_height * 0.35), 8)
    max_dot_w = max(int(roi_width * 0.12), 8)

    for box in boxes:
        # Drop wide background bars (the classic 150x40 timer plate contour).
        if box.w > max_digit_w and box.h < min_digit_h * 1.2:
            rejected.append(
                GlyphBox(
                    *box.as_tuple,
                    label="rejected",
                    accepted=False,
                    reason="wide_bar",
                )
            )
            continue

        # Digit-like: tall enough and not too wide.
        if box.h >= min_digit_h and box.w <= max_digit_w:
            # Edge-clipped fragments at the ROI border are usually HUD noise.
            touches_edge = box.x <= 1 or box.x + box.w >= roi_width - 1
            if touches_edge and box.w < max_digit_w * 0.7:
                rejected.append(
                    GlyphBox(
                        *box.as_tuple,
                        label="rejected",
                        accepted=False,
                        reason="edge_fragment",
                    )
                )
                continue
            accepted.append(GlyphBox(*box.as_tuple, label="digit_like"))
            continue

        # Colon-dot-like: small, roughly compact, not a random speck.
        aspect = box.w / max(box.h, 1)
        if (
            min_dot_h <= box.h <= max_dot_h
            and box.w <= max_dot_w
            and 0.4 <= aspect <= 2.5
            and box.area >= 8
        ):
            accepted.append(GlyphBox(*box.as_tuple, label="dot_like"))
            continue

        rejected.append(
            GlyphBox(
                *box.as_tuple,
                label="rejected",
                accepted=False,
                reason="size_or_aspect",
            )
        )
    return accepted, rejected


def _looks_like_colon_pair(a: GlyphBox, b: GlyphBox, roi_height: int) -> bool:
    """Return whether two boxes look like vertically stacked colon dots."""
    upper, lower = (a, b) if a.y <= b.y else (b, a)
    # Must be stacked, not side-by-side.
    if lower.y < upper.y + upper.h * 0.5:
        return False
    x_center_delta = abs(upper.cx - lower.cx)
    if x_center_delta > max(6.0, 0.08 * roi_height):
        return False
    size_ratio = max(upper.area, lower.area) / max(min(upper.area, lower.area), 1)
    if size_ratio > 3.0:
        return False
    gap = lower.y - (upper.y + upper.h)
    # Dots should be separated, but not farther apart than roughly a digit.
    if gap < 0 or gap > roi_height * 0.45:
        return False
    combined_h = (lower.y + lower.h) - upper.y
    # Colon span is typically ~30-50% of the timer ROI height.
    if combined_h < roi_height * 0.25 or combined_h > roi_height * 0.85:
        return False
    return True


def _merge_colon_pairs(
    boxes: list[GlyphBox],
    roi_width: int,
    roi_height: int,
) -> list[GlyphBox]:
    """Merge two vertically aligned dots into one colon box."""
    del roi_width  # Reserved for future spatial priors.
    dots = [box for box in boxes if box.label == "dot_like"]
    others = [box for box in boxes if box.label != "dot_like"]
    used: set[int] = set()
    merged: list[GlyphBox] = list(others)

    for i, top in enumerate(dots):
        if i in used:
            continue
        best_j: int | None = None
        best_score = float("inf")
        for j, bottom in enumerate(dots):
            if j == i or j in used:
                continue
            if not _looks_like_colon_pair(top, bottom, roi_height):
                continue
            gap = abs(bottom.cy - top.cy)
            if gap < best_score:
                best_score = gap
                best_j = j
        if best_j is None:
            continue
        partner = dots[best_j]
        used.add(i)
        used.add(best_j)
        upper, lower = (top, partner) if top.y <= partner.y else (partner, top)
        x = min(upper.x, lower.x)
        y = min(upper.y, lower.y)
        w = max(upper.x + upper.w, lower.x + lower.w) - x
        h = max(upper.y + upper.h, lower.y + lower.h) - y
        merged.append(GlyphBox(x, y, w, h, label="colon"))

    return merged


def write_segmentation_debug(
    roi_bgr: np.ndarray,
    result: SegmentationResult,
    output_path: Path,
) -> Path:
    """Write a labeled segmentation diagnostic image for one ROI."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if roi_bgr.ndim == 3:
        canvas = roi_bgr.copy()
    else:
        canvas = cv2.cvtColor(roi_bgr, cv2.COLOR_GRAY2BGR)
    # Upscale small ROIs so labels are readable.
    scale = max(1, 400 // max(canvas.shape[1], 1))
    canvas = cv2.resize(
        canvas,
        (canvas.shape[1] * scale, canvas.shape[0] * scale),
        interpolation=cv2.INTER_NEAREST,
    )

    def draw(box: GlyphBox, color: tuple[int, int, int], index: int) -> None:
        x1, y1 = box.x * scale, box.y * scale
        x2, y2 = (box.x + box.w) * scale, (box.y + box.h) * scale
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 1)
        label = f"{index}:{box.label}:{box.w}x{box.h}"
        if box.reason:
            label += f":{box.reason}"
        cv2.putText(
            canvas,
            label,
            (x1, max(12, y1 - 2)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.35,
            color,
            1,
            cv2.LINE_AA,
        )

    for index, box in enumerate(result.rejected_boxes):
        draw(box, (0, 0, 255), index)
    for index, box in enumerate(result.final_boxes):
        color = (0, 255, 255) if box.label == "colon" else (0, 255, 0)
        draw(box, color, index)

    # Stack ROI debug + binary side-by-side.
    binary_bgr = cv2.cvtColor(result.binary, cv2.COLOR_GRAY2BGR)
    binary_bgr = cv2.resize(
        binary_bgr,
        (canvas.shape[1], canvas.shape[0]),
        interpolation=cv2.INTER_NEAREST,
    )
    panel = np.concatenate([canvas, binary_bgr], axis=1)
    cv2.imwrite(str(output_path), panel)
    return output_path


@dataclass(frozen=True)
class MatchDiagnostics:
    """Detailed recognition diagnostics for one glyph."""

    bbox_size: tuple[int, int]
    normalized_shape: tuple[int, int]
    best_symbol: str | None
    best_score: float
    scores_by_symbol: dict[str, float]
    shape_mismatches: int
    templates_compared: int
    accepted: bool
    normalized: np.ndarray
    best_template: np.ndarray | None


def match_glyph(
    glyph: np.ndarray,
    templates: dict[str, list[np.ndarray]],
    match_threshold: float,
) -> tuple[str | None, float]:
    """Match one glyph against templates, returning symbol and score."""
    diagnostics = diagnose_glyph_match(glyph, templates, match_threshold)
    symbol = diagnostics.best_symbol if diagnostics.accepted else None
    return symbol, diagnostics.best_score


def diagnose_glyph_match(
    glyph: np.ndarray,
    templates: dict[str, list[np.ndarray]],
    match_threshold: float,
) -> MatchDiagnostics:
    """Match one glyph and return full per-symbol score diagnostics."""
    height, width = glyph.shape[:2]
    normalized = normalize_glyph(glyph)
    scores_by_symbol: dict[str, float] = {}
    best_symbol: str | None = None
    best_score = -1.0
    best_template: np.ndarray | None = None
    shape_mismatches = 0
    templates_compared = 0

    for symbol, examples in templates.items():
        symbol_best = -1.0
        symbol_best_template: np.ndarray | None = None
        for template in examples:
            if template.shape != normalized.shape:
                shape_mismatches += 1
                continue
            templates_compared += 1
            result = cv2.matchTemplate(normalized, template, cv2.TM_CCOEFF_NORMED)
            score = float(result.max())
            if score > symbol_best:
                symbol_best = score
                symbol_best_template = template
        if symbol_best >= 0.0:
            scores_by_symbol[symbol] = symbol_best
        if symbol_best > best_score:
            best_score = symbol_best
            best_symbol = symbol
            best_template = symbol_best_template

    if best_score < 0.0:
        best_score = 0.0

    accepted = best_symbol is not None and best_score >= match_threshold
    logger.debug(
        "match_glyph bbox={}x{} norm={} best={} score={:.3f} "
        "accepted={} compared={} shape_skip={} scores={}",
        width,
        height,
        normalized.shape,
        best_symbol,
        best_score,
        accepted,
        templates_compared,
        shape_mismatches,
        {
            symbol: round(score, 3)
            for symbol, score in sorted(
                scores_by_symbol.items(), key=lambda item: item[1], reverse=True
            )[:5]
        },
    )
    return MatchDiagnostics(
        bbox_size=(width, height),
        normalized_shape=(normalized.shape[1], normalized.shape[0]),
        best_symbol=best_symbol,
        best_score=best_score,
        scores_by_symbol=scores_by_symbol,
        shape_mismatches=shape_mismatches,
        templates_compared=templates_compared,
        accepted=accepted,
        normalized=normalized,
        best_template=best_template,
    )


def write_match_debug(
    diagnostics: MatchDiagnostics,
    output_path: Path,
    *,
    expected_label: str = "",
) -> Path:
    """Save normalized runtime glyph beside its best-matching template."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    left = cv2.cvtColor(diagnostics.normalized, cv2.COLOR_GRAY2BGR)
    if diagnostics.best_template is None:
        right = np.zeros_like(left)
    else:
        right = cv2.cvtColor(diagnostics.best_template, cv2.COLOR_GRAY2BGR)
    # Upscale for readability.
    scale = 4
    left = cv2.resize(
        left,
        (left.shape[1] * scale, left.shape[0] * scale),
        interpolation=cv2.INTER_NEAREST,
    )
    right = cv2.resize(
        right,
        (right.shape[1] * scale, right.shape[0] * scale),
        interpolation=cv2.INTER_NEAREST,
    )
    gap = np.full((left.shape[0], 8, 3), 40, dtype=np.uint8)
    panel = np.concatenate([left, gap, right], axis=1)
    header = (
        f"bbox={diagnostics.bbox_size[0]}x{diagnostics.bbox_size[1]} "
        f"norm={diagnostics.normalized_shape[0]}x{diagnostics.normalized_shape[1]} "
        f"best={diagnostics.best_symbol}:{diagnostics.best_score:.3f} "
        f"skip={diagnostics.shape_mismatches} cmp={diagnostics.templates_compared}"
    )
    if expected_label:
        header = f"expect={expected_label} " + header
    canvas = np.zeros((panel.shape[0] + 28, panel.shape[1], 3), dtype=np.uint8)
    canvas[28:, :] = panel
    cv2.putText(
        canvas,
        header[:90],
        (4, 18),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.4,
        (220, 220, 220),
        1,
        cv2.LINE_AA,
    )
    cv2.imwrite(str(output_path), canvas)
    return output_path


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
        *,
        debug_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps
        self.debug_dir = debug_dir
        self._templates = load_templates(config.template_dir)
        self._debug_count = 0

    def detect(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
    ) -> tuple[TimerReading | None, float]:
        """Return a timer reading and detector score for one frame image."""
        _ = timestamp
        roi = crop_roi(image, self.config.roi)

        # Primary: fixed M:SS slot geometry. Fallback: connected-component seg.
        slot_result = segment_timer_slots(roi)
        reading, confidence = self._read_from_segmentation(
            slot_result,
            fixed_colon=True,
        )
        if reading is not None:
            if self.debug_dir is not None and self._debug_count < 30:
                path = self.debug_dir / f"slots_{self._debug_count:04d}.png"
                write_segmentation_debug(roi, slot_result, path)
                self._debug_count += 1
            return reading, confidence

        fallback = segment_timer_roi(roi)
        reading, confidence = self._read_from_segmentation(
            fallback,
            fixed_colon=False,
        )
        if self.debug_dir is not None and self._debug_count < 30:
            path = self.debug_dir / f"fallback_{self._debug_count:04d}.png"
            write_segmentation_debug(roi, fallback, path)
            self._debug_count += 1
        if reading is None and fallback.glyphs:
            logger.debug(
                "Timer match failed: final_boxes={} labels={} raw conf={:.3f}",
                len(fallback.final_boxes),
                [box.label for box in fallback.final_boxes],
                confidence,
            )
        return reading, confidence

    def _read_from_segmentation(
        self,
        segmentation: SegmentationResult,
        *,
        fixed_colon: bool,
    ) -> tuple[TimerReading | None, float]:
        """Match glyphs from one segmentation result into a timer reading."""
        glyphs = segmentation.glyphs
        boxes = segmentation.final_boxes
        if not glyphs:
            return None, 0.0

        raw_symbols: list[str] = []
        raw_scores: list[float] = []
        for box, glyph in zip(boxes, glyphs, strict=True):
            if glyph.shape[1] < 2 or glyph.shape[0] < 2:
                continue

            # Fixed-geometry path: the colon slot is known a priori.
            if fixed_colon and box.label == "colon":
                raw_symbols.append(":")
                raw_scores.append(1.0)
                continue

            symbol, score = match_glyph(
                glyph, self._templates, self.config.match_threshold
            )
            raw_scores.append(score)
            if symbol is None:
                continue
            raw_symbols.append(":" if symbol == "colon" else symbol)

        display = extract_timer_display("".join(raw_symbols))
        seconds = parse_timer_display(display) if display else None
        if display and seconds is not None:
            confidence = float(np.clip(np.mean(raw_scores), 0.0, 1.0))
            return (
                TimerReading(display=display, seconds_remaining=seconds),
                confidence,
            )
        return None, float(np.mean(raw_scores)) if raw_scores else 0.0


def extract_timer_display(raw: str) -> str | None:
    """Extract the first valid M:SS or MM:SS substring from a noisy OCR string."""
    if not raw:
        return None
    match = re.search(r"(\d{1,2}):(\d{2})(?!\d)", raw)
    if not match:
        match = re.search(r"(\d{1,2}):(\d{2})", raw)
    if match:
        display = match.group(0)
        if parse_timer_display(display) is not None:
            return display
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) >= 3:
        for start in range(0, len(digits) - 2):
            candidate = f"{digits[start]}:{digits[start + 1 : start + 3]}"
            if parse_timer_display(candidate) is not None:
                return candidate
    return None
