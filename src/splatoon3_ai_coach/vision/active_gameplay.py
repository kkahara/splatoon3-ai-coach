"""Positive normal-gameplay detector (third-person weapon + HUD chrome)."""

from __future__ import annotations

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ActiveGameplayDetectorConfig
from splatoon3_ai_coach.vision.models import ActiveGameplayReading
from splatoon3_ai_coach.vision.roi import crop_roi


class ActiveGameplayDetector:
    """Positive evidence of controllable third-person gameplay.

    Uses weapon-band structure plus bottom-left HUD chrome. Absence of
    countdown/death is **not** used here; fusion applies conflict filters.
    """

    name = "active_gameplay"
    run_on_evidence = True

    def __init__(
        self,
        config: ActiveGameplayDetectorConfig,
        cadence_fps: float | None = None,
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps

    def detect(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
    ) -> tuple[ActiveGameplayReading | None, float]:
        """Return an active-gameplay reading for one frame."""
        _ = timestamp
        return self._observe(image)

    def _observe(self, image: np.ndarray) -> tuple[ActiveGameplayReading, float]:
        """Score weapon + HUD structure as positive gameplay evidence."""
        weapon = crop_roi(image, self.config.weapon_roi)
        hud = crop_roi(image, self.config.hud_roi)
        if weapon.size == 0 or hud.size == 0:
            return ActiveGameplayReading(), 0.0

        weapon_edge, weapon_std = _roi_structure(weapon)
        hud_edge, _ = _roi_structure(hud)
        weapon_ok = (
            weapon_edge >= self.config.weapon_edge_min
            and weapon_std >= self.config.weapon_luma_std_min
        )
        hud_ok = hud_edge >= self.config.hud_edge_min
        detected = weapon_ok and hud_ok
        score = float(
            np.clip(
                0.55 * min(weapon_edge / max(self.config.weapon_edge_min, 1e-6), 1.5)
                + 0.25
                * min(weapon_std / max(self.config.weapon_luma_std_min, 1e-6), 1.5)
                + 0.20 * min(hud_edge / max(self.config.hud_edge_min, 1e-6), 1.5),
                0.0,
                1.0,
            )
        )
        if not detected:
            score = float(np.clip(score * 0.4, 0.0, 1.0))
        return (
            ActiveGameplayReading(
                detected=detected,
                score=score,
                weapon_edge_frac=weapon_edge,
                weapon_luma_std=weapon_std,
                hud_edge_frac=hud_edge,
            ),
            score,
        )


def _roi_structure(roi: np.ndarray) -> tuple[float, float]:
    """Return (edge_fraction, luma_std_normalized) for a BGR ROI."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 140)
    edge_frac = float((edges > 0).mean())
    luma_std = float(gray.astype(np.float32).std() / 255.0)
    return edge_frac, luma_std
