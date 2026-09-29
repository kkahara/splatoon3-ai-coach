"""Per-detector sampling below the shared HUD cadence."""

from __future__ import annotations

import numpy as np

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.media.video import VideoFrame
from splatoon3_ai_coach.vision.detector_schedule import (
    DetectorSchedule,
    build_detector_schedule,
)
from splatoon3_ai_coach.vision.models import PlayerCountReading, TimerReading
from splatoon3_ai_coach.vision.pipeline import observe_cadence_stream


class _Detector:
    def __init__(self, name: str, reading: object) -> None:
        self.name = name
        self.reading = reading

    def detect(self, image: np.ndarray, timestamp: float | None = None):
        _ = image, timestamp
        return self.reading, 0.9


def _names(schedule: DetectorSchedule, detectors, t: float) -> list[str]:
    return [d.name for d in schedule.filter(detectors, t)]


def test_one_hz_detector_runs_on_every_other_two_hz_frame() -> None:
    detectors = [_Detector("timer", None), _Detector("player_count", None)]
    schedule = DetectorSchedule(intervals={"player_count": 1.0}, tolerance=0.25)
    runs = [
        t
        for t in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0)
        if "player_count" in _names(schedule, detectors, t)
    ]
    assert runs == [0.0, 1.0, 2.0, 3.0]


def test_jittered_timestamps_do_not_drift_or_skip() -> None:
    detectors = [_Detector("player_count", None)]
    schedule = DetectorSchedule(intervals={"player_count": 1.0}, tolerance=0.25)
    times = (0.0, 0.52, 0.99, 1.51, 2.01, 2.49, 2.98)
    runs = [t for t in times if _names(schedule, detectors, t)]
    assert runs == [0.0, 0.99, 2.01, 2.98]


def test_default_config_samples_player_count_every_frame() -> None:
    vision = load_config(default_config_path()).vision
    assert build_detector_schedule(vision).intervals == {}


def test_sample_fps_builds_an_interval() -> None:
    vision = load_config(default_config_path()).vision
    vision.player_count.sample_fps = 1.0
    vision.player_count.hold_seconds = 1.5
    schedule = build_detector_schedule(vision)
    assert schedule.intervals == {"player_count": 1.0}
    assert schedule.tolerance == 0.5 / vision.hud_cadence_fps


def test_cadence_stream_skips_player_count_on_unscheduled_frames() -> None:
    image = np.zeros((90, 160, 3), dtype=np.uint8)
    detectors = [
        _Detector("timer", TimerReading(display="3:00", seconds_remaining=180.0)),
        _Detector("player_count", PlayerCountReading()),
    ]
    frames = [VideoFrame(timestamp=i * 0.5, frame_index=i, image=image) for i in range(4)]
    results, stats = observe_cadence_stream(
        iter(frames),
        detectors,
        cadence_fps=2.0,
        analysis_id="test",
        detector_versions={"timer": "timer@test", "player_count": "player_count@test"},
        schedule=DetectorSchedule(intervals={"player_count": 1.0}, tolerance=0.25),
    )
    per_frame = [{d.detector_name for d in r.detections} for r in results]
    assert per_frame == [
        {"timer", "player_count"},
        {"timer"},
        {"timer", "player_count"},
        {"timer"},
    ]
    assert stats.detector_invocations == {"timer": 4, "player_count": 2}
