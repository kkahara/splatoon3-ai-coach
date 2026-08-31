"""Low-level visual measurements between two frames.

These functions are pure and stateless: they return raw numbers and make no
decision about whether a number is interesting. Thresholding lives in
`detectors.py`.
"""

import cv2
import numpy as np

# Comparisons run on a fixed small size so cost does not scale with source
# resolution, and so thresholds mean the same thing for 1080p and 4K input.
COMPARISON_SIZE = (320, 180)
ROI_COMPARISON_SIZE = (256, 128)


def _prepare_gray(frame: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, size).astype(np.float32)


def grayscale_histogram(frame: np.ndarray, bins: int = 32) -> np.ndarray:
    """Return a normalized grayscale histogram."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    histogram = cv2.calcHist([gray], [0], None, [bins], [0, 256])
    return cv2.normalize(histogram, histogram).flatten()


def histogram_difference(previous: np.ndarray, current: np.ndarray) -> float:
    """Return histogram correlation distance, 0 for identical histograms."""
    correlation = cv2.compareHist(
        previous.astype(np.float32),
        current.astype(np.float32),
        cv2.HISTCMP_CORREL,
    )
    return float(1.0 - correlation)


def ssim_score(previous: np.ndarray, current: np.ndarray) -> float:
    """Return a lightweight SSIM-like similarity score, 1.0 for identical frames.

    This avoids a scikit-image dependency and is meant as a fast screening
    signal rather than a scientific image-quality metric.
    """
    a = _prepare_gray(previous, COMPARISON_SIZE)
    b = _prepare_gray(current, COMPARISON_SIZE)

    mean_a, mean_b = a.mean(), b.mean()
    var_a, var_b = a.var(), b.var()
    covariance = float(((a - mean_a) * (b - mean_b)).mean())

    # Stabilizing constants from the standard SSIM formulation for 8-bit input.
    c1, c2 = 6.5025, 58.5225
    numerator = (2 * mean_a * mean_b + c1) * (2 * covariance + c2)
    denominator = (mean_a**2 + mean_b**2 + c1) * (var_a + var_b + c2)
    return float(numerator / denominator)


def optical_flow_magnitude(previous: np.ndarray, current: np.ndarray) -> float:
    """Return median optical-flow magnitude in pixels between two frames."""
    a = _prepare_gray(previous, COMPARISON_SIZE).astype(np.uint8)
    b = _prepare_gray(current, COMPARISON_SIZE).astype(np.uint8)
    flow = cv2.calcOpticalFlowFarneback(
        a,
        b,
        None,
        pyr_scale=0.5,
        levels=2,
        winsize=15,
        iterations=2,
        poly_n=5,
        poly_sigma=1.2,
        flags=0,
    )
    magnitude, _ = cv2.cartToPolar(flow[..., 0], flow[..., 1])
    return float(np.median(magnitude))


def crop_region(
    frame: np.ndarray,
    box: tuple[float, float, float, float],
) -> np.ndarray:
    """Crop a normalized x1, y1, x2, y2 region from a frame."""
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = box
    left, top = int(x1 * width), int(y1 * height)
    right, bottom = int(x2 * width), int(y2 * height)
    return frame[top:bottom, left:right]


def region_change(previous: np.ndarray, current: np.ndarray) -> float:
    """Return mean absolute pixel change between two regions, scaled to [0, 1]."""
    a = cv2.resize(previous, ROI_COMPARISON_SIZE)
    b = cv2.resize(current, ROI_COMPARISON_SIZE)
    return float(cv2.absdiff(a, b).mean() / 255.0)
