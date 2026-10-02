"""Elapsed-clock label parsing and conservative image-label interface."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .clock import ClockSource, ReviewClock, parse_elapsed_clock


@dataclass(frozen=True)
class LabelRead:
    """Result of attempting to read a cursor label."""

    clock: ReviewClock | None
    confidence: float
    reason: str | None = None


def read_display_text(
    display: str | None, *, confidence: float = 1.0
) -> LabelRead:
    """Validate externally supplied or OCR-normalized display text."""
    if display is None:
        return LabelRead(None, 0.0, "no display text")
    elapsed = parse_elapsed_clock(display)
    if elapsed is None:
        return LabelRead(None, 0.0, f"ambiguous clock: {display!r}")
    return LabelRead(
        ReviewClock(
            display=display.strip(),
            elapsed_seconds=elapsed,
            source=ClockSource.image,
            confidence=confidence,
        ),
        confidence,
    )


def label_region_score(image: np.ndarray, x: int) -> float:
    """Score whether a bright elapsed-label glyph cluster is below a cursor."""
    if image.size == 0:
        return 0.0
    height, width = image.shape[:2]
    left = max(0, x - 65)
    right = min(width, x + 65)
    top = int(height * 0.92)
    region = image[top:, left:right]
    if region.size == 0:
        return 0.0
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    return float(np.mean(gray > 180))

