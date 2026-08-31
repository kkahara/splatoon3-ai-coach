"""Tests for video ingestion."""

from pathlib import Path

import pytest

from splatoon3_ai_coach.io.video import VideoLoader


def test_missing_video_raises() -> None:
    with pytest.raises(FileNotFoundError):
        VideoLoader(Path("does-not-exist.mp4")).open()


def test_unsupported_format_raises(tmp_path: Path) -> None:
    path = tmp_path / "sample.txt"
    path.write_text("not a video", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported video format"):
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
