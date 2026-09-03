"""Turning raw signals into thresholded, scored change triggers.

Each trigger answers two separate questions: did the signal cross its configured
threshold, and how far past that threshold did it go. These are change triggers,
not semantic Splatoon detectors. Semantic interpretation lives in `vision/`.
"""

import numpy as np

from splatoon3_ai_coach.config.models import ExtractionConfig
from splatoon3_ai_coach.extraction import signals
from splatoon3_ai_coach.extraction.models import ChangeTrigger, TriggerType

SCENE_SATURATION = 1.0
STRUCTURAL_SATURATION = 1.0
REGION_SATURATION = 0.5
MOTION_SATURATION_FACTOR = 4.0


def _score(value: float, threshold: float, saturation: float) -> float:
    """Map a triggered signal onto [0.5, 1.0] by how far it clears its threshold."""
    span = max(saturation - threshold, 1e-6)
    strength = min(max((value - threshold) / span, 0.0), 1.0)
    return 0.5 + 0.5 * strength


class ChangeTriggerPipeline:
    """Run every change trigger against a pair of consecutive analysis frames."""

    def __init__(self, config: ExtractionConfig) -> None:
        self.config = config

    def evaluate(
        self,
        previous: np.ndarray,
        current: np.ndarray,
        previous_histogram: np.ndarray,
        current_histogram: np.ndarray,
    ) -> list[ChangeTrigger]:
        """Return every trigger that fired, strongest first."""
        triggers = [
            *self._scene_change(previous, current, previous_histogram, current_histogram),
            *self._motion(previous, current),
            *self._hud_changes(previous, current),
        ]
        return sorted(triggers, key=lambda trigger: trigger.confidence, reverse=True)

    def _scene_change(
        self,
        previous: np.ndarray,
        current: np.ndarray,
        previous_histogram: np.ndarray,
        current_histogram: np.ndarray,
    ) -> list[ChangeTrigger]:
        histogram = signals.histogram_difference(previous_histogram, current_histogram)
        structural = 1.0 - signals.ssim_score(previous, current)
        structural_threshold = 1.0 - self.config.ssim_threshold

        scores = []
        if histogram >= self.config.scene_threshold:
            scores.append(
                _score(histogram, self.config.scene_threshold, SCENE_SATURATION)
            )
        if structural >= structural_threshold:
            scores.append(_score(structural, structural_threshold, STRUCTURAL_SATURATION))

        if not scores:
            return []
        return [ChangeTrigger(TriggerType.SCENE_CHANGE, max(scores))]

    def _motion(self, previous: np.ndarray, current: np.ndarray) -> list[ChangeTrigger]:
        magnitude = signals.optical_flow_magnitude(previous, current)
        if magnitude < self.config.motion_threshold:
            return []

        saturation = self.config.motion_threshold * MOTION_SATURATION_FACTOR
        return [
            ChangeTrigger(
                TriggerType.MOTION,
                _score(magnitude, self.config.motion_threshold, saturation),
            )
        ]

    def _hud_changes(
        self,
        previous: np.ndarray,
        current: np.ndarray,
    ) -> list[ChangeTrigger]:
        hud = self.config.hud
        regions = (
            (TriggerType.KILLFEED_CHANGE, hud.killfeed),
            (TriggerType.SPECIAL_CHANGE, hud.special_gauge),
            (TriggerType.OBJECTIVE_CHANGE, hud.objective_timer),
            (TriggerType.DEATH_UI_CHANGE, hud.death_text),
        )

        triggers = []
        for trigger_type, box in regions:
            change = signals.region_change(
                signals.crop_region(previous, box),
                signals.crop_region(current, box),
            )
            if change >= self.config.hud_threshold:
                triggers.append(
                    ChangeTrigger(
                        trigger_type,
                        _score(change, self.config.hud_threshold, REGION_SATURATION),
                    )
                )
        return triggers
