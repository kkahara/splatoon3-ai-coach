"""HUD / weapon / center structure evidence. Not a match-phase or event."""

from __future__ import annotations

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ActiveGameplayDetectorConfig
from splatoon3_ai_coach.vision.models import ActiveGameplayReading
from splatoon3_ai_coach.vision.roi import crop_roi


def _roi_structure(roi: np.ndarray) -> tuple[float, float]:
    """Return (edge_fraction, luma_std_normalized) for a BGR ROI."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 140)
    edge_frac = float((edges > 0).mean())
    luma_std = float(gray.astype(np.float32).std() / 255.0)
    return edge_frac, luma_std


class ActiveGameplayDetector:
    """Per-frame HUD/weapon/center evidence of controllable camera chrome.

    Does not interpret match phase, death, or respawn. Lifecycle decides
    whether HUD chrome means ACTIVE play. Weapon-band and center ink-tank
    feed ``return_control`` for the post-respawn latch only.
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
        """Return HUD/weapon/center evidence for one frame."""
        _ = timestamp
        return self._observe(image)

    def _observe(self, image: np.ndarray) -> tuple[ActiveGameplayReading, float]:
        """Score HUD chrome; weapon+center set return_control."""
        weapon = crop_roi(image, self.config.weapon_roi)
        hud = crop_roi(image, self.config.hud_roi)
        center = crop_roi(image, self.config.center_roi)
        if weapon.size == 0 or hud.size == 0 or center.size == 0:
            return ActiveGameplayReading(), 0.0

        weapon_edge, weapon_std = _roi_structure(weapon)
        hud_edge, _ = _roi_structure(hud)
        center_edge, _ = _roi_structure(center)
        weapon_ok = (
            weapon_edge >= self.config.weapon_edge_min
            and weapon_std >= self.config.weapon_luma_std_min
        )
        hud_ok = hud_edge >= self.config.hud_edge_min
        center_ok = center_edge >= self.config.center_edge_min
        detected = hud_ok
        return_control = weapon_ok and center_ok
        score = _structure_score(
            self.config, weapon_edge, weapon_std, hud_edge, center_edge
        )
        confidence = max(score, 0.55) if detected or return_control else max(0.55, 1.0 - score)
        return (
            ActiveGameplayReading(
                detected=detected,
                score=score,
                weapon_edge_frac=weapon_edge,
                weapon_luma_std=weapon_std,
                hud_edge_frac=hud_edge,
                center_edge_frac=center_edge,
                center_control=center_ok,
                return_control=return_control,
            ),
            confidence,
        )


def _structure_score(
    config: ActiveGameplayDetectorConfig,
    weapon_edge: float,
    weapon_std: float,
    hud_edge: float,
    center_edge: float,
) -> float:
    """Weighted [0, 1] mix of the three structure cues."""
    return float(
        np.clip(
            0.40 * min(weapon_edge / max(config.weapon_edge_min, 1e-6), 1.5)
            + 0.20 * min(weapon_std / max(config.weapon_luma_std_min, 1e-6), 1.5)
            + 0.15 * min(hud_edge / max(config.hud_edge_min, 1e-6), 1.5)
            + 0.25 * min(center_edge / max(config.center_edge_min, 1e-6), 1.5),
            0.0,
            1.0,
        )
    )
