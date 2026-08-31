"""Turning raw signals into thresholded, scored detections.

Each detector answers two separate questions: did the signal cross its
configured threshold, and how far past that threshold did it go. A barely
triggered signal scores near 0.5 and a saturating one scores 1.0, so the
strongest detection at a timestamp is a meaningful choice rather than a tie.

These detectors are change detectors, not semantic Splatoon detectors. A
killfeed detection means the killfeed region changed, not that a splat was
recognized.
"""

import numpy as np

from splatoon3_ai_coach.analysis import signals
from splatoon3_ai_coach.analysis.models import Detection, EventType
from splatoon3_ai_coach.config.models import ExtractionConfig

# Signal value at which a detection is considered maximally confident. Values
# above the threshold are scaled across the span up to this ceiling.
SCENE_SATURATION = 1.0
STRUCTURAL_SATURATION = 1.0
REGION_SATURATION = 0.5
MOTION_SATURATION_FACTOR = 4.0


def _score(value: float, threshold: float, saturation: float) -> float:
    """Map a triggered signal onto [0.5, 1.0] by how far it clears its threshold."""
    span = max(saturation - threshold, 1e-6)
    strength = min(max((value - threshold) / span, 0.0), 1.0)
    return 0.5 + 0.5 * strength


class FrameDetectors:
    """Run every detector against a pair of consecutive analysis frames."""

    def __init__(self, config: ExtractionConfig) -> None:
        self.config = config

    def detect(
        self,
        previous: np.ndarray,
        current: np.ndarray,
        previous_histogram: np.ndarray,
        current_histogram: np.ndarray,
    ) -> list[Detection]:
        """Return every detection that fired, strongest first."""
        detections = [
            *self._detect_scene_change(
                previous, current, previous_histogram, current_histogram
            ),
            *self._detect_motion(previous, current),
            *self._detect_hud_changes(previous, current),
        ]
        return sorted(detections, key=lambda d: d.confidence, reverse=True)

    def _detect_scene_change(
        self,
        previous: np.ndarray,
        current: np.ndarray,
        previous_histogram: np.ndarray,
        current_histogram: np.ndarray,
    ) -> list[Detection]:
        """Fire when either colour distribution or structure changes enough."""
        histogram = signals.histogram_difference(previous_histogram, current_histogram)
        # Structural dissimilarity: 0 for identical frames, 1 for unrelated ones.
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
        return [Detection(EventType.SCENE_CHANGE, max(scores))]

    def _detect_motion(
        self,
        previous: np.ndarray,
        current: np.ndarray,
    ) -> list[Detection]:
        magnitude = signals.optical_flow_magnitude(previous, current)
        if magnitude < self.config.motion_threshold:
            return []

        saturation = self.config.motion_threshold * MOTION_SATURATION_FACTOR
        return [
            Detection(
                EventType.MOTION,
                _score(magnitude, self.config.motion_threshold, saturation),
            )
        ]

    def _detect_hud_changes(
        self,
        previous: np.ndarray,
        current: np.ndarray,
    ) -> list[Detection]:
        hud = self.config.hud
        regions = (
            (EventType.KILLFEED_CHANGE, hud.killfeed),
            (EventType.SPECIAL_CHANGE, hud.special_gauge),
            (EventType.OBJECTIVE_CHANGE, hud.objective_timer),
            (EventType.DEATH, hud.death_text),
        )

        detections = []
        for event_type, box in regions:
            change = signals.region_change(
                signals.crop_region(previous, box),
                signals.crop_region(current, box),
            )
            if change >= self.config.hud_threshold:
                detections.append(
                    Detection(
                        event_type,
                        _score(change, self.config.hud_threshold, REGION_SATURATION),
                    )
                )
        return detections
