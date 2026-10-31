"""Experimental observation of the Clam Blitz center basket presentation."""

from pathlib import Path

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ClamBasketStateDetectorConfig
from splatoon3_ai_coach.vision.models import ClamBasketStateReading
from splatoon3_ai_coach.vision.roi import crop_roi

# Categorical acceptance signal for fusion, not a calibrated probability.
_RECOGNIZED_STATE_CONFIDENCE = 0.7


class ClamBasketStateDetector:
    """Observe colored sectors and a dark border; never infer duration."""

    name = "clam_basket_state"
    run_on_evidence = True

    def __init__(
        self, config: ClamBasketStateDetectorConfig, cadence_fps: float | None = None
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps
        self._white_template = _load_template(
            config.template_dir / "closed_white_anchor.png"
        )
        self._dark_template = _load_template(
            config.template_dir / "open_dark_circle_anchor.png"
        )

    def detect(
        self, image: np.ndarray, timestamp: float | None = None
    ) -> tuple[ClamBasketStateReading, float]:
        """Measure color independently and report only supported visual state."""
        crop = crop_roi(image, self.config.roi)
        if crop.size == 0:
            return ClamBasketStateReading(timestamp=timestamp), 0.0
        crop = cv2.resize(crop, (97, 93), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        h, w = hsv.shape[:2]
        yy, xx = np.ogrid[:h, :w]
        dx, dy = (xx - (w - 1) / 2) / (w / 2), (yy - (h - 1) / 2) / (h / 2)
        radius = np.sqrt(dx * dx + dy * dy)
        ring = (radius >= 0.35) & (radius <= 0.75)
        colored = ring & (hsv[:, :, 1] >= 50) & (hsv[:, :, 2] >= 85)
        hue, concentration = _color_evidence(hsv[:, :, 0][colored])
        border = (radius >= 0.8) & (radius <= 0.95)
        border_dark = float((hsv[:, :, 2][border] < 80).mean()) if border.any() else 0.0
        angle = (np.arctan2(dy, dx) + np.pi) / (2 * np.pi)
        count = _colored_sectors(ring, colored, angle, self.config.sector_count)
        white = cv2.inRange(hsv, (0, 0, 180), (179, 65, 255))[20:73, 22:75]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)[20:73, 22:75]
        white_score = _template_score(white, self._white_template)
        dark_score = _template_score(gray, self._dark_template)
        if white_score >= 0.6:
            state = "closed"
        elif border_dark >= 0.35 and (
            count >= self.config.min_colored_sectors or dark_score >= 0.75
        ):
            state = "open"
        else:
            state = "unknown"
        return ClamBasketStateReading(
            visual_state=state,
            hue=hue,
            hue_concentration=concentration,
            timestamp=timestamp,
        ), _RECOGNIZED_STATE_CONFIDENCE if state != "unknown" else 0.0


def _load_template(path: Path) -> np.ndarray:
    """Load a configured basket HUD template."""
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"Basket template unavailable: {path}")
    return image


def _template_score(image: np.ndarray, template: np.ndarray) -> float:
    """Compare normalized central HUD appearance with a template."""
    return float(cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED).max())


def _color_evidence(hues: np.ndarray) -> tuple[float | None, float]:
    """Return circular mean and concentration, independent of state evidence."""
    if hues.size < 8:
        return None, 0.0
    vector = np.exp(1j * hues.astype(float) * (2 * np.pi / 180)).mean()
    hue = float((np.angle(vector) % (2 * np.pi)) * 180 / (2 * np.pi))
    return hue, float(abs(vector))


def _colored_sectors(
    ring: np.ndarray, colored: np.ndarray, angle: np.ndarray, sector_count: int
) -> int:
    """Count angular sectors with enough colored pixels as a binary cue."""
    count = 0
    for index in range(sector_count):
        sector = (
            ring & (angle >= index / sector_count) & (angle < (index + 1) / sector_count)
        )
        if sector.any() and colored[sector].mean() >= 0.55:
            count += 1
    return count
