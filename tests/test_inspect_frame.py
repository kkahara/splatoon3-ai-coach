"""Tests for the inspect_frame developer utility."""

from pathlib import Path

import pytest

from splatoon3_ai_coach.exceptions import ManifestError
from splatoon3_ai_coach.extraction.models import ManifestFrame, TriggerType
from splatoon3_ai_coach.inspect_frame import (
    closest_frame,
    neighboring_frames,
    resolve_extraction_dir,
)
from splatoon3_ai_coach.media.manifest import MANIFEST_FILENAME


def _frame(timestamp: float, index: int, name: str) -> ManifestFrame:
    return ManifestFrame(
        timestamp=timestamp,
        trigger_type=TriggerType.MOTION,
        confidence=0.9,
        source_frame_index=index,
        source_pts=index,
        source_time_base_num=1,
        source_time_base_den=60,
        path=Path(name),
    )


def test_closest_frame_picks_nearest_timestamp() -> None:
    frames = [
        _frame(10.0, 100, "a.jpg"),
        _frame(16.0, 160, "b.jpg"),
        _frame(16.5, 165, "c.jpg"),
        _frame(20.0, 200, "d.jpg"),
    ]
    matched = closest_frame(frames, 16.25)
    assert matched.timestamp == pytest.approx(16.0)
    assert matched.source_frame_index == 160
    assert matched.path == Path("b.jpg")


def test_closest_frame_exact_match() -> None:
    frames = [_frame(16.25, 975, "hit.jpg"), _frame(17.0, 1000, "other.jpg")]
    matched = closest_frame(frames, 16.25)
    assert matched.path == Path("hit.jpg")
    assert matched.source_frame_index == 975


def test_closest_frame_empty_raises() -> None:
    with pytest.raises(ManifestError, match="no frames"):
        closest_frame([], 1.0)


def test_neighboring_frames_context() -> None:
    frames = [_frame(float(i), i, f"{i}.jpg") for i in range(5)]
    matched = frames[2]
    before, after = neighboring_frames(frames, matched, context=2)
    assert [f.timestamp for f in before] == [0.0, 1.0]
    assert [f.timestamp for f in after] == [3.0, 4.0]


def test_resolve_extraction_dir_from_analysis_out(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    (frames_dir / MANIFEST_FILENAME).write_text("{}", encoding="utf-8")
    assert resolve_extraction_dir(tmp_path) == frames_dir.resolve()


def test_resolve_extraction_dir_from_frames_dir(tmp_path: Path) -> None:
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    (frames_dir / MANIFEST_FILENAME).write_text("{}", encoding="utf-8")
    assert resolve_extraction_dir(frames_dir) == frames_dir.resolve()
