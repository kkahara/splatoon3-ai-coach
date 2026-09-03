"""Glyph normalization for template matching."""

import cv2
import numpy as np

GLYPH_WIDTH = 32
GLYPH_HEIGHT = 56


def normalize_glyph(image: np.ndarray) -> np.ndarray:
    """Resize a grayscale glyph to a fixed canvas with preserved aspect ratio."""
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    height, width = gray.shape[:2]
    if height == 0 or width == 0:
        return np.zeros((GLYPH_HEIGHT, GLYPH_WIDTH), dtype=np.uint8)

    scale = min(GLYPH_WIDTH / width, GLYPH_HEIGHT / height)
    new_size = (max(int(width * scale), 1), max(int(height * scale), 1))
    resized = cv2.resize(gray, new_size, interpolation=cv2.INTER_AREA)

    canvas = np.zeros((GLYPH_HEIGHT, GLYPH_WIDTH), dtype=np.uint8)
    y_offset = (GLYPH_HEIGHT - resized.shape[0]) // 2
    x_offset = (GLYPH_WIDTH - resized.shape[1]) // 2
    canvas[
        y_offset : y_offset + resized.shape[0],
        x_offset : x_offset + resized.shape[1],
    ] = resized
    return canvas
