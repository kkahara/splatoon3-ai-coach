"""Per-detector sampling rates below the shared HUD cadence."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from splatoon3_ai_coach.config.models import VisionConfig
from splatoon3_ai_coach.vision.base import BaseDetector


@dataclass
class DetectorSchedule:
    """Decide which detectors run on a cadence frame.

    Detectors without an interval run on every cadence frame. A detector with
    an interval runs on the first frame and then on the first frame at least
    ``interval - tolerance`` seconds after its previous run, so a 1 Hz
    detector on a 2 Hz cadence runs on every other frame without drift.
    """

    intervals: dict[str, float] = field(default_factory=dict)
    tolerance: float = 0.0
    last_run: dict[str, float] = field(default_factory=dict)

    def filter(
        self, detectors: Sequence[BaseDetector], video_time: float
    ) -> list[BaseDetector]:
        """Detectors due on this frame; records the run for scheduled ones."""
        due: list[BaseDetector] = []
        for detector in detectors:
            interval = self.intervals.get(detector.name)
            if interval is not None:
                last = self.last_run.get(detector.name)
                if last is not None and video_time - last < interval - self.tolerance:
                    continue
                self.last_run[detector.name] = video_time
            due.append(detector)
        return due


def build_detector_schedule(config: VisionConfig) -> DetectorSchedule:
    """Intervals for detectors configured to sample below ``hud_cadence_fps``."""
    intervals: dict[str, float] = {}
    sample_fps = config.player_count.sample_fps
    if sample_fps is not None and sample_fps < config.hud_cadence_fps:
        intervals["player_count"] = 1.0 / sample_fps
    return DetectorSchedule(intervals=intervals, tolerance=0.5 / config.hud_cadence_fps)
