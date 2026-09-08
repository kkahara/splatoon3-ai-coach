"""Tests for meaningful-frame selection."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from splatoon3_ai_coach.config.models import ExtractionConfig
from splatoon3_ai_coach.extraction.extractor import MeaningfulFrameExtractor
from splatoon3_ai_coach.extraction.models import TriggerType
from splatoon3_ai_coach.extraction.sampler import FrameSampler
from splatoon3_ai_coach.media.video import VideoFrame, VideoLoader


@dataclass
class FakeLoader:
    """Stands in for VideoLoader so tests do not need a decoded file."""

    video_frames: list[VideoFrame]

    def frames(self):
        return iter(self.video_frames)


def build_frames(images: list[np.ndarray], fps: float = 30.0) -> list[VideoFrame]:
    return [
        VideoFrame(timestamp=index / fps, frame_index=index, image=image)
        for index, image in enumerate(images)
    ]


def solid(value: int) -> np.ndarray:
    return np.full((180, 320, 3), value, dtype=np.uint8)


def test_first_frame_is_a_keyframe(extraction_config: ExtractionConfig) -> None:
    extractor = MeaningfulFrameExtractor(extraction_config)
    result = extractor.extract(FakeLoader(build_frames([solid(0)] * 30)))

    assert result.frames[0].trigger_type == TriggerType.KEYFRAME
    assert result.frames[0].confidence == 1.0


def test_static_video_keeps_only_the_keyframe(
    extraction_config: ExtractionConfig,
) -> None:
    extractor = MeaningfulFrameExtractor(extraction_config)
    result = extractor.extract(FakeLoader(build_frames([solid(0)] * 60)))

    assert len(result.frames) == 1


def test_scene_cut_is_selected(extraction_config: ExtractionConfig) -> None:
    images = [solid(0)] * 30 + [solid(255)] * 30
    extractor = MeaningfulFrameExtractor(extraction_config)
    result = extractor.extract(FakeLoader(build_frames(images)))

    trigger_types = {event.trigger_type for event in result.trigger_events}
    assert TriggerType.SCENE_CHANGE in trigger_types


def test_trigger_events_record_their_context_window(
    extraction_config: ExtractionConfig,
) -> None:
    images = [solid(0)] * 60 + [solid(255)] * 60
    extractor = MeaningfulFrameExtractor(extraction_config)
    result = extractor.extract(FakeLoader(build_frames(images)))

    event = result.trigger_events[-1]
    assert event.context_start == max(
        event.timestamp - extraction_config.context_before_seconds, 0.0
    )
    assert event.context_end == (
        event.timestamp + extraction_config.context_after_seconds
    )


def test_analysis_rate_limits_how_often_triggers_run(
    extraction_config: ExtractionConfig,
) -> None:
    images = [solid(0) if index % 2 else solid(255) for index in range(300)]
    extractor = MeaningfulFrameExtractor(extraction_config)
    result = extractor.extract(FakeLoader(build_frames(images)))

    assert len(result.frames) <= extraction_config.analysis_fps * 10


def test_rate_limit_caps_frames_per_minute(
    extraction_config: ExtractionConfig,
) -> None:
    capped = extraction_config.model_copy(update={"max_frames_per_minute": 3})
    images = [solid(0) if index % 2 else solid(255) for index in range(300)]
    extractor = MeaningfulFrameExtractor(capped)
    result = extractor.extract(FakeLoader(build_frames(images)))

    assert len(result.frames) <= 3


def test_frame_sampler_decimates_by_timestamp() -> None:
    frames = build_frames([solid(0)] * 60, fps=30.0)
    sampled = list(FrameSampler(1.0 / 6).sample(iter(frames)))
    assert len(sampled) < len(frames)
    assert sampled[0].timestamp == 0.0
    assert sampled[0] is frames[0]
    assert {id(frame) for frame in sampled} <= {id(frame) for frame in frames}
    min_gap = 1.0 / 6
    gaps = [
        later.timestamp - earlier.timestamp
        for earlier, later in zip(sampled, sampled[1:], strict=False)
    ]
    assert gaps
    assert all(gap + 1e-9 >= min_gap for gap in gaps)


class _InstrumentedFrameStream:
    """Already-decoded iterator that forbids seek/reopen."""

    def __init__(self, frames: list[VideoFrame]) -> None:
        self._iter = iter(frames)
        self.next_calls = 0

    def __iter__(self) -> "_InstrumentedFrameStream":
        return self

    def __next__(self) -> VideoFrame:
        frame = next(self._iter)
        self.next_calls += 1
        return frame

    def seek(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("FrameSampler must not seek")

    def open(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("FrameSampler must not reopen the video")

    def frames(self) -> None:
        raise AssertionError("FrameSampler must not start an independent decode")


def test_frame_sampler_is_pure_cadence_filter() -> None:
    """Sampler only next()s an existing stream; it never seeks or re-decodes."""
    frames = build_frames([solid(0)] * 30, fps=30.0)
    stream = _InstrumentedFrameStream(frames)
    sampled = list(FrameSampler(0.5).sample(stream))

    assert stream.next_calls == len(frames)
    assert sampled
    assert len(sampled) < len(frames)
    assert all(item is frames[item.frame_index] for item in sampled)
    last_kept = -float("inf")
    expected_indices: list[int] = []
    for frame in frames:
        if frame.timestamp - last_kept >= 0.5:
            expected_indices.append(frame.frame_index)
            last_kept = frame.timestamp
    assert [frame.frame_index for frame in sampled] == expected_indices


def test_frame_sampler_filters_videoloader_frames_sequentially(
    sample_video: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cadence sampling walks one VideoLoader.frames() pass; no second decode."""
    constructed: list[VideoLoader] = []
    original_init = VideoLoader.__init__

    def tracking_init(
        self: VideoLoader, *args: object, **kwargs: object
    ) -> None:
        constructed.append(self)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(VideoLoader, "__init__", tracking_init)

    with VideoLoader(sample_video) as loader:
        decoded: list[VideoFrame] = []

        def tap(frames):
            for frame in frames:
                decoded.append(frame)
                yield frame

        sampled = list(FrameSampler(0.5).sample(tap(loader.frames())))

    assert len(constructed) == 1
    assert decoded
    assert [frame.frame_index for frame in decoded] == list(range(len(decoded)))
    assert loader.decoded_frame_count == len(decoded)
    assert {id(frame) for frame in sampled} <= {id(frame) for frame in decoded}
    assert len(sampled) < len(decoded)
