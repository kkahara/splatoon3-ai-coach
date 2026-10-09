"""Contracts for the Phase 2 timeline-video adapter."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.media.video import VideoFrame
from splatoon3_ai_coach.review.clock import ClockSource, ReviewClock
from splatoon3_ai_coach.review.cursor import detect_cursor
from splatoon3_ai_coach.review.models import ScrollDirection
from splatoon3_ai_coach.review.timeline_pipeline import import_timeline
from splatoon3_ai_coach.review.video_adapter import (
    VideoSample,
    _layout_confidence,
    _ranked,
    _write_adapter_manifest,
    adapt_frames,
    timeline_structure_score,
)
from splatoon3_ai_coach.vision.timer import TimerDetector

REAL_PLAYER_VIEW = (
    Path(__file__).resolve().parents[1]
    / "analysis"
    / "review_validation"
    / "fixtures"
    / "player_view_frame_3960.png"
)
REAL_REVIEW_FIXTURES = Path(__file__).parent / "fixtures" / "review"


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
    retained = [sample for sample in emitted if sample.retained]
    assert [sample.clock.elapsed_seconds for sample in retained] == [151, 152, 153]
    assert [path.suffix for path in tmp_path.iterdir()] == [".jpg", ".jpg", ".jpg"]


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


def test_player_view_with_bright_vertical_edge_is_rejected(tmp_path: Path) -> None:
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    image[:] = (200, 60, 20)
    image[:, 1100:1920] = (20, 160, 230)
    image[300:1010, 420:428] = 255
    config = ReviewTimelineConfig()
    cursor = detect_cursor(image, config)
    assert _layout_confidence(image, cursor) >= config.video.layout_confidence_floor
    samples = adapt_frames(
        [VideoFrame(timestamp=66.0, frame_index=3960, image=image)],
        tmp_path,
        timeline_config=config,
        clock_reader=lambda _image, _x, _config: _clock(134),
    )
    assert samples[0].status == "rejected"
    assert samples[0].reason == "no timeline structure"
    assert list(tmp_path.iterdir()) == []


def test_timeline_frame_passes_structure_check() -> None:
    config = ReviewTimelineConfig()
    assert timeline_structure_score(_frame(0.0, 0).image, config) >= (
        config.video.structure_confidence_floor
    )


@pytest.mark.parametrize(
    "name",
    ("timeline_21-49-20.jpg", "timeline_21-56-52.jpg"),
)
def test_colored_real_timeline_frames_pass_structure_check(name: str) -> None:
    config = ReviewTimelineConfig()
    image = cv2.imread(str(REAL_REVIEW_FIXTURES / name))
    assert image is not None
    assert timeline_structure_score(image, config) >= (
        config.video.structure_confidence_floor
    )


def test_real_non_timeline_frame_fails_structure_check() -> None:
    config = ReviewTimelineConfig()
    image = cv2.imread(
        str(REAL_REVIEW_FIXTURES / "player_view_21-56-52.jpg")
    )
    assert image is not None
    assert timeline_structure_score(image, config) < (
        config.video.structure_confidence_floor
    )


@pytest.mark.skipif(not REAL_PLAYER_VIEW.exists(), reason="real adapter output absent")
def test_real_player_view_frame_is_rejected(tmp_path: Path) -> None:
    image = cv2.imread(str(REAL_PLAYER_VIEW))
    samples = adapt_frames(
        [VideoFrame(timestamp=66.0, frame_index=3960, image=image)],
        tmp_path,
        timeline_config=ReviewTimelineConfig(),
        clock_reader=lambda _image, _x, _config: _clock(134),
    )
    assert samples[0].status == "rejected"


def test_dense_interval_processes_every_frame_and_buckets_by_clock(
    tmp_path: Path,
) -> None:
    clocks = iter((10, 10, 9, 8))

    def read_clock(_image: np.ndarray, _x: int, _config: object) -> ReviewClock:
        return _clock(next(clocks))

    frames = [_frame(index * 0.01, index) for index in range(4)]
    config = ReviewTimelineConfig()
    assert config.video.sample_interval_seconds == 0
    samples = adapt_frames(
        frames, tmp_path, timeline_config=config, clock_reader=read_clock
    )
    assert len(samples) == 4
    assert [sample.scroll_direction for sample in samples] == [
        ScrollDirection.unknown,
        ScrollDirection.unknown,
        ScrollDirection.backward,
        ScrollDirection.backward,
    ]
    assert [sample.source_video_time for sample in samples] == [0.0, 0.01, 0.02, 0.03]
    retained = [sample for sample in samples if sample.retained]
    retained_keys = [
        (sample.clock.elapsed_seconds, sample.scroll_direction) for sample in retained
    ]
    assert retained_keys == [
        (10, ScrollDirection.unknown),
        (9, ScrollDirection.backward),
        (8, ScrollDirection.backward),
    ]
    assert all(path.suffix == ".jpg" for path in tmp_path.iterdir())


def test_bucket_limit_keeps_ranked_candidates(tmp_path: Path) -> None:
    def read_clock(_image: np.ndarray, _x: int, _config: object) -> ReviewClock:
        return _clock(40)

    config = ReviewTimelineConfig()
    config.video.frames_per_second_bucket = 2
    sharp = _frame(0.0, 0)
    soft_image = cv2.GaussianBlur(_frame(0.01, 1).image, (5, 5), 0)
    soft = VideoFrame(timestamp=0.01, frame_index=1, image=soft_image)
    samples = adapt_frames(
        [sharp, soft],
        tmp_path,
        timeline_config=config,
        clock_reader=read_clock,
    )
    retained = [sample for sample in samples if sample.retained]
    assert [sample.frame_index for sample in retained] == [0, 1]
    assert samples[0].rank_key > samples[1].rank_key
    assert len(list(tmp_path.iterdir())) == 2


def test_rank_key_order_is_deterministic() -> None:
    weaker = _video_sample([1, 0.2, 0.1, 0.5, 0.5, 10, -2])
    stronger = _video_sample([1, 0.9, 0.4, 0.8, 0.8, 30, -5])
    tied_later = _video_sample([1, 0.9, 0.4, 0.8, 0.8, 30, -9])
    assert [sample.frame_index for sample in _ranked([weaker, tied_later, stronger])] == [
        5,
        9,
        2,
    ]


def test_timer_detector_is_built_once_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"count": 0}
    original = TimerDetector.__init__

    def counted(self: TimerDetector, *args: object, **kwargs: object) -> None:
        calls["count"] += 1
        original(self, *args, **kwargs)

    monkeypatch.setattr(TimerDetector, "__init__", counted)
    adapt_frames([_frame(0.0, 0), _frame(0.02, 1)], tmp_path)
    assert calls["count"] == 1


def test_manifest_carries_direction_and_video_provenance(tmp_path: Path) -> None:
    clocks = iter((10, 11, 10))

    def read_clock(_image: np.ndarray, _x: int, _config: object) -> ReviewClock:
        return _clock(next(clocks))

    frames = [_frame(index * 0.5, index) for index in range(3)]
    samples = adapt_frames(
        frames,
        tmp_path / "timeline_samples",
        timeline_config=ReviewTimelineConfig(),
        clock_reader=read_clock,
    )
    _write_adapter_manifest(tmp_path, "video", {}, samples)
    dataset = import_timeline(
        tmp_path / "timeline_samples",
        config=ReviewTimelineConfig(),
        manifest_path=tmp_path / "timeline_samples_manifest.yaml",
    )
    assert [
        (sample.elapsed_seconds, sample.scroll_direction, sample.source_video_time)
        for sample in dataset.samples
    ] == [
        (10, ScrollDirection.unknown, 0.0),
        (11, ScrollDirection.forward, 0.5),
        (10, ScrollDirection.backward, 1.0),
    ]
    [state_10, _state_11] = dataset.second_states
    assert {item.scroll_direction for item in state_10.provenance} == {
        ScrollDirection.unknown,
        ScrollDirection.backward,
    }


def _video_sample(rank_key: list[float]) -> VideoSample:
    return VideoSample(
        source_video_time=0,
        frame_index=int(-rank_key[-1]),
        timeline_confidence=1,
        cursor_confidence=1,
        status="clocked_timeline",
        rank_key=rank_key,
    )


def _clock(value: int) -> ReviewClock:
    return ReviewClock(
        display=f"{value // 60}:{value % 60:02d}",
        elapsed_seconds=value,
        source=ClockSource.image,
        confidence=1.0,
    )


def _frame(timestamp: float, index: int) -> VideoFrame:
    image = np.zeros((1080, 1920, 3), dtype=np.uint8)
    image[756:950, 134:1747] = 75
    image[984:992, 140:1745] = 133
    x = 140 + index * 120
    image[300:665, x : x + 8] = 255
    image[760:948, x : x + 8] = 255
    image[960:1010, x : x + 8] = 255
    return VideoFrame(timestamp=timestamp, frame_index=index, image=image)


def _fail() -> None:
    return None

