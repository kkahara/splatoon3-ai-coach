"""Example tests for the video loader.

This file is intentionally readable for contributors: it shows how to open a
video, read metadata, and iterate decoded frames without touching the rest of
the pipeline.
"""

from pathlib import Path
from time import sleep

import pytest

from splatoon3_ai_coach.exceptions import VideoLoadError
from splatoon3_ai_coach.media.video import VideoLoader


def test_media_package_import_does_not_circular_fail() -> None:
    """Package root must stay importable without pulling extraction/pipeline."""
    import splatoon3_ai_coach.media as media

    assert media is not None
    # Submodule import used by callers after slim package __init__.
    from splatoon3_ai_coach.media.video import VideoLoader as Loader

    assert Loader is VideoLoader


def test_missing_video_raises() -> None:
    with pytest.raises(VideoLoadError, match="not found"):
        VideoLoader(Path("does-not-exist.mp4")).open()


def test_unsupported_format_raises(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("not a video", encoding="utf-8")

    with pytest.raises(VideoLoadError, match="Unsupported video format"):
        VideoLoader(path).open()


def test_metadata_describes_the_stream(sample_video: Path) -> None:
    with VideoLoader(sample_video) as loader:
        metadata = loader.open()

    assert metadata.width == 320
    assert metadata.height == 180
    assert metadata.fps == pytest.approx(30, rel=0.01)
    assert metadata.duration_seconds > 0


def test_frames_have_increasing_timestamps(sample_video: Path) -> None:
    with VideoLoader(sample_video) as loader:
        timestamps = [frame.timestamp for frame in loader.frames()]

    assert timestamps == sorted(timestamps)
    assert len(timestamps) > 1


def test_frames_are_downscaled_to_configured_bounds(sample_video: Path) -> None:
    with VideoLoader(sample_video, max_width=160, max_height=160) as loader:
        first = next(iter(loader.frames()))

    height, width = first.image.shape[:2]
    assert width <= 160
    assert height <= 160


def test_decode_seconds_exclude_time_after_yield(sample_video: Path) -> None:
    with VideoLoader(sample_video) as loader:
        iterator = loader.frames()
        next(iterator)
        after_first = loader.decode_seconds
        sleep(0.05)
        next(iterator)
        delta = loader.decode_seconds - after_first

    assert after_first > 0
    assert loader.decoded_frame_count == 2
    assert delta < 0.05
