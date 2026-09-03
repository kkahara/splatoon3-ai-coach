"""Tests for timer parsing and state fusion."""

import pytest

from splatoon3_ai_coach.config.models import StateFusionConfig, TimerDetectorConfig
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    TimerReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.state import fuse_timer_state
from splatoon3_ai_coach.vision.timer import parse_timer_display


def timer_config() -> TimerDetectorConfig:
    return TimerDetectorConfig(
        roi=(0.0, 0.0, 1.0, 1.0),
        template_dir=".",
        min_usable_confidence=0.5,
    )


def frame_result(
    timestamp: float,
    display: str,
    seconds: float,
    confidence: float,
    index: int,
) -> VisionFrameResult:
    reading = TimerReading(display=display, seconds_remaining=seconds)
    return VisionFrameResult(
        frame_id=f"frame:{index}",
        timestamp=timestamp,
        source="cadence",
        source_frame_index=index,
        detections=[
            DetectorResult(
                id=f"result:{index}",
                detector_name="timer",
                detector_version="timer@0.2.0",
                confidence=confidence,
                reading=reading,
            )
        ],
    )


def test_parse_timer_display() -> None:
    assert parse_timer_display("2:07") == 127.0
    assert parse_timer_display("bad") is None


def test_low_confidence_observation_is_retained_but_not_accepted() -> None:
    results = [frame_result(0.0, "2:07", 127.0, 0.2, 0)]
    snapshots = fuse_timer_state(results, timer_config(), StateFusionConfig())
    assert results[0].detections
    assert snapshots[0].quality == "unknown"


def test_monotonic_timer_rejects_upward_jump() -> None:
    results = [
        frame_result(0.0, "2:10", 130.0, 0.9, 0),
        frame_result(1.0, "2:47", 167.0, 0.9, 1),
        frame_result(2.0, "2:08", 128.0, 0.9, 2),
    ]
    snapshots = fuse_timer_state(results, timer_config(), StateFusionConfig())
    assert snapshots[1].match_time_remaining == pytest.approx(130.0)
    assert snapshots[1].quality == "held"
    assert snapshots[2].quality in {"observed", "smoothed", "held"}
