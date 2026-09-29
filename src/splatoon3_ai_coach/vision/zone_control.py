"""Observe the highlighted scoreboard pod used by Splat Zones control."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ZoneControlDetectorConfig
from splatoon3_ai_coach.types import NormalizedBox
from splatoon3_ai_coach.vision.models import ZoneControlReading, ZoneControlState
from splatoon3_ai_coach.vision.roi import crop_roi

PodClass = Literal["lit", "dim", "ambiguous"]
# Threshold distance that maps to full confidence.
_MARGIN_SCALE = 0.15


@dataclass(frozen=True)
class _PodObservation:
    lit: float
    dark: float
    pod: PodClass
    margin: float


@dataclass(frozen=True)
class _PodPixels:
    lit: float
    dark: float
    edges: float


def _pod_fractions(
    image: np.ndarray,
    roi: NormalizedBox,
    config: ZoneControlDetectorConfig,
) -> _PodPixels:
    """Return lit, dark, and edge pixel fractions inside one pod ROI."""
    crop = crop_roi(image, roi)
    if crop.size == 0:
        return _PodPixels(0.0, 0.0, 0.0)
    value = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)[:, :, 2]
    lit = float((value >= config.lit_value_min).mean())
    dark = float((value < config.dark_value_max).mean())
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    edges = float((cv2.Canny(gray, 60, 160) > 0).mean())
    return _PodPixels(lit, dark, edges)


def _classify_pod(
    pixels: _PodPixels,
    config: ZoneControlDetectorConfig,
) -> _PodObservation:
    """Classify one pod as lit, dim, or ambiguous with a threshold margin.

    A pod without digit edges (blurred or HUD-less frame) is ambiguous.
    """
    lit, dark = pixels.lit, pixels.dark
    if pixels.edges < config.min_edge_fraction:
        return _PodObservation(lit, dark, "ambiguous", 0.0)
    lit_margin = min(
        lit - config.lit_min_fraction,
        config.lit_max_dark_fraction - dark,
    )
    dim_margin = min(
        dark - config.dim_min_dark_fraction,
        config.dim_max_lit_fraction - lit,
        lit - config.dim_min_lit_fraction,
    )
    if lit_margin >= 0:
        return _PodObservation(lit, dark, "lit", lit_margin)
    if dim_margin >= 0:
        return _PodObservation(lit, dark, "dim", dim_margin)
    return _PodObservation(lit, dark, "ambiguous", 0.0)


def _decide(left: _PodObservation, right: _PodObservation) -> ZoneControlState:
    """Map the two pod classes to an observed control state."""
    pods = (left.pod, right.pod)
    if pods == ("dim", "dim"):
        return "neutral"
    if pods == ("lit", "dim"):
        return "ally_control"
    if pods == ("dim", "lit"):
        return "opponent_control"
    return "unknown"


def read_zone_control(
    image: np.ndarray,
    config: ZoneControlDetectorConfig,
) -> ZoneControlReading:
    """Read control from which scoreboard pod is lit.

    Exactly one lit pod beside a dim pod is ownership; two dim pods are
    neutral. Two lit pods, any ambiguous pod, or a missing HUD are unknown —
    the detector prefers ``unknown`` over asserting ownership.
    """
    left = _classify_pod(_pod_fractions(image, config.left_pod_roi, config), config)
    right = _classify_pod(_pod_fractions(image, config.right_pod_roi, config), config)
    state = _decide(left, right)
    confidence = 0.0
    if state != "unknown":
        margin = min(left.margin, right.margin)
        confidence = 0.6 + 0.4 * min(1.0, margin / _MARGIN_SCALE)
    return ZoneControlReading(
        observed_state=state,
        left_signal=left.lit,
        right_signal=right.lit,
        left_dark=left.dark,
        right_dark=right.dark,
        left_pod=left.pod,
        right_pod=right.pod,
        confidence=confidence,
    )


class ZoneControlDetector:
    """Observe-only detector for the highlighted Splat Zones scoreboard pod."""

    name = "zone_control"
    run_on_evidence = True

    def __init__(
        self,
        config: ZoneControlDetectorConfig,
        cadence_fps: float | None = None,
    ) -> None:
        self.config = config
        self.cadence_fps = cadence_fps

    def detect(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
    ) -> tuple[ZoneControlReading | None, float]:
        """Return the visual ownership cue without temporal interpretation."""
        _ = timestamp
        reading = read_zone_control(image, self.config)
        return reading, reading.confidence
