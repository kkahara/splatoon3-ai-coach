"""Display rendering for annotation (blind; no colour alteration).

Crops are padded with neutral grey at image borders and enlarged by integer
nearest-neighbour replication only. The marker never covers the centre pixel.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from map_ink_reference.constants import (
    DEFAULT_CROP_SIZE,
    DEFAULT_ZOOM,
    MAX_CROP_SIZE,
    MIN_CROP_SIZE,
    PAD_GREY_BGR,
    SWATCH_DISPLAY_SIZE,
)
from map_ink_reference.errors import MalformedInput


@dataclass(frozen=True)
class DisplaySettings:
    """Pilot-adjustable display parameters (within the frozen limits)."""

    crop_size: int = DEFAULT_CROP_SIZE
    zoom: int = DEFAULT_ZOOM
    marker_bgr: tuple[int, int, int] = (255, 0, 255)
    marker_thickness: int = 1

    def __post_init__(self) -> None:
        if not MIN_CROP_SIZE <= self.crop_size <= MAX_CROP_SIZE:
            raise MalformedInput(f"crop size must be {MIN_CROP_SIZE}-{MAX_CROP_SIZE}")
        if self.zoom < 1:
            raise MalformedInput("zoom must be a positive integer")


def padded_crop(image: np.ndarray, x: int, y: int, size: int) -> np.ndarray:
    """``size``×``size`` crop with the point at index ``size // 2``; grey padding."""
    height, width = image.shape[:2]
    if not (0 <= x < width and 0 <= y < height):
        raise MalformedInput(f"point ({x}, {y}) outside {width}x{height}")
    out = np.empty((size, size, 3), dtype=np.uint8)
    out[:, :] = PAD_GREY_BGR
    half = size // 2
    x0, y0 = x - half, y - half
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(width, x0 + size), min(height, y0 + size)
    out[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0] = image[sy0:sy1, sx0:sx1]
    return out


def zoom_nearest(image: np.ndarray, zoom: int) -> np.ndarray:
    """Integer nearest-neighbour enlargement by pixel replication."""
    return np.repeat(np.repeat(image, zoom, axis=0), zoom, axis=1)


def _draw_crosshair(canvas: np.ndarray, cx: int, cy: int, zoom: int, s: DisplaySettings):
    gap = zoom * 2
    length = zoom * 6
    left, right = cx - gap, cx + zoom + gap
    top, bottom = cy - gap, cy + zoom + gap
    color, thick = s.marker_bgr, s.marker_thickness
    mid_y, mid_x = cy + zoom // 2, cx + zoom // 2
    cv2.line(canvas, (left - length, mid_y), (left, mid_y), color, thick)
    cv2.line(canvas, (right, mid_y), (right + length, mid_y), color, thick)
    cv2.line(canvas, (mid_x, top - length), (mid_x, top), color, thick)
    cv2.line(canvas, (mid_x, bottom), (mid_x, bottom + length), color, thick)


def point_view(
    image: np.ndarray, x: int, y: int, settings: DisplaySettings
) -> np.ndarray:
    """Zoomed crop around a point with a crosshair leaving the centre uncovered."""
    crop = padded_crop(image, x, y, settings.crop_size)
    zoomed = zoom_nearest(crop, settings.zoom)
    centre = (settings.crop_size // 2) * settings.zoom
    _draw_crosshair(zoomed, centre, centre, settings.zoom, settings)
    return zoomed


def swatch_view(image: np.ndarray, x: int, y: int, zoom: int) -> np.ndarray:
    """9×9 neighbourhood of a swatch pixel at the given zoom (display only)."""
    return zoom_nearest(padded_crop(image, x, y, SWATCH_DISPLAY_SIZE), zoom)


def encode_png(image: np.ndarray) -> bytes:
    """Lossless PNG bytes for serving."""
    ok, buf = cv2.imencode(".png", image)
    if not ok:
        raise MalformedInput("PNG encoding failed")
    return buf.tobytes()
