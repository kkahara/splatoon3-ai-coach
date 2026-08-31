"""Tests for meaningful-frame selection."""

from dataclasses import dataclass

import numpy as np

from splatoon3_ai_coach.analysis.extractor import MeaningfulFrameExtractor
from splatoon3_ai_coach.analysis.models import EventType
from splatoon3_ai_coach.config.models import ExtractionConfig
from splatoon3_ai_coach.io.video import VideoFrame


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

    assert result.frames[0].event_type == EventType.KEYFRAME
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

    event_types = {event.event_type for event in result.events}
    assert EventType.SCENE_CHANGE in event_types


def test_events_record_their_context_window(
    extraction_config: ExtractionConfig,
) -> None:
    images = [solid(0)] * 60 + [solid(255)] * 60
    extractor = MeaningfulFrameExtractor(extraction_config)
    result = extractor.extract(FakeLoader(build_frames(images)))

    event = result.events[-1]
    assert event.context_start == max(
        event.timestamp - extraction_config.context_before_seconds, 0.0
    )
    assert event.context_end == (
        event.timestamp + extraction_config.context_after_seconds
    )


def test_analysis_rate_limits_how_often_detectors_run(
    extraction_config: ExtractionConfig,
) -> None:
    # Alternating frames would trigger on every source frame at 30 FPS, but
    # analysis_fps=6 plus min_event_gap_seconds caps how many can be kept.
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
