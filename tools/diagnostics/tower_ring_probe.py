"""Compare simple radial and quadrant measurements on a labeled HUD crop.

This development-only probe uses an operator-specified center and ring hue.
Its fractions are diagnostic measurements, not checkpoint progress facts.
"""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def measure(path: Path, center: tuple[float, float], hue: float) -> dict:
    """Measure colored pixels in an annulus around a manually located marker."""
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(f"Cannot read image: {path}")
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    y, x = np.indices(image.shape[:2])
    dx, dy = x - center[0], y - center[1]
    radius = np.hypot(dx, dy)
    annulus = (radius >= 22) & (radius <= 28)
    hue_difference = np.abs(hsv[:, :, 0].astype(float) - hue)
    hue_difference = np.minimum(hue_difference, 180 - hue_difference)
    colored = (
        annulus & (hue_difference <= 12) & (hsv[:, :, 1] >= 70) & (hsv[:, :, 2] >= 90)
    )
    angle = (np.arctan2(dy, dx) + 2 * np.pi) % (2 * np.pi)
    sector = np.floor(angle * 72 / (2 * np.pi)).astype(int) % 72
    radial = []
    for index in range(72):
        pixels = annulus & (sector == index)
        radial.append(float(colored[pixels].mean()) if pixels.any() else 0.0)
    quadrants = []
    for index in range(4):
        pixels = annulus & (sector // 18 == index)
        quadrants.append(round(float(colored[pixels].mean()), 3))
    return {
        "path": str(path),
        "radial_sector_fraction": round(sum(value >= 0.25 for value in radial) / 72, 3),
        "quadrant_pixel_fractions": quadrants,
        "quadrants_above_threshold": sum(value >= 0.25 for value in quadrants),
    }


def main() -> None:
    """Print measurements for crops sharing manual center and ring hue."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", type=Path, nargs="+")
    parser.add_argument(
        "--center", type=float, nargs=2, required=True, metavar=("X", "Y")
    )
    parser.add_argument("--hue", type=float, required=True)
    args = parser.parse_args()
    for path in args.images:
        print(json.dumps(measure(path, tuple(args.center), args.hue)))


if __name__ == "__main__":
    main()
