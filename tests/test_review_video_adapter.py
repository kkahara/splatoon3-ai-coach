"""Contracts for the Phase 2 timeline-video adapter."""

from pathlib import Path

import numpy as np

from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.media.video import VideoFrame
from splatoon3_ai_coach.review.clock import ClockSource, ReviewClock
from splatoon3_ai_coach.review.video_adapter import adapt_frames


def test_source_time_sampling_uses_visible_clock_not_video_spacing(
    tmp_path: Path,
) -> None:
    frames = [
        _frame(10.0, 0),
        _frame(10.9, 1),
        _frame(12.4, 2),
        _frame(13.1, 3),
    ]
    clocks = iter((151, 151, 152, 153))

    def read_clock(_image: np.ndarray, _x: int, _config: object) -> ReviewClock:
        value = next(clocks)
        return ReviewClock(
            display=f"{value // 60}:{value % 60:02d}",
            elapsed_seconds=value,
            source=ClockSource.image,
            confidence=1.0,
        )

    samples = adapt_frames(
        frames,
        tmp_path,
        timeline_config=ReviewTimelineConfig(),
        clock_reader=read_clock,
    )
    emitted = [sample for sample in samples if sample.clock is not None]
    assert [sample.clock.elapsed_seconds for sample in emitted] == [151, 151, 152, 153]
    assert [sample.source_video_time for sample in emitted] == [
        10.0,
        10.9,
        12.4,
        13.1,
    ]
    assert [path.name for path in sorted(tmp_path.iterdir())] == [
        "sample_000001.png",
        "sample_000002.png",
        "sample_000003.png",
        "sample_000004.png",
    ]


def test_timeline_layout_without_clock_is_diagnostic_only(tmp_path: Path) -> None:
    samples = adapt_frames(
        [_frame(0.0, 0)],
        tmp_path,
        timeline_config=ReviewTimelineConfig(),
        clock_reader=lambda _image, _x, _config: None,
    )
    assert len(samples) == 1
    assert samples[0].status == "unclocked_timeline"
    assert list(tmp_path.iterdir()) == []


def test_signed_boundary_clocks_pass_through_adapter(tmp_path: Path) -> None:
    values = iter((-3, 0, 299, 300, 301))

    def read_clock(_image: np.ndarray, _x: int, _config: object) -> ReviewClock:
        value = next(values)
        return ReviewClock(
            display=("-" if value < 0 else "") + f"{abs(value) // 60}:"
            f"{abs(value) % 60:02d}",
            elapsed_seconds=value,
            source=ClockSource.image,
            confidence=1.0,
        )

    frames = [_frame(float(index), index) for index in range(5)]
    samples = adapt_frames(
        frames,
        tmp_path,
        timeline_config=ReviewTimelineConfig(),
        clock_reader=read_clock,
    )
    assert [
        sample.clock.elapsed_seconds
        for sample in samples
        if sample.clock is not None
    ] == [-3, 0, 299, 300, 301]


def test_non_timeline_frame_is_rejected(tmp_path: Path) -> None:
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    samples = adapt_frames(
        [VideoFrame(timestamp=0, frame_index=0, image=image)],
        tmp_path,
        timeline_config=ReviewTimelineConfig(),
        clock_reader=lambda _image, _x, _config: _fail(),
    )
    assert samples[0].status == "rejected"
    assert samples[0].reason == "no timeline layout"


def _frame(timestamp: float, index: int) -> VideoFrame:
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    x = 140 + index * 120
    image[300:665, x : x + 8] = 255
    image[760:948, x : x + 8] = 255
    image[960:1010, x : x + 8] = 255
    return VideoFrame(timestamp=timestamp, frame_index=index, image=image)


def _fail() -> None:
    return None

