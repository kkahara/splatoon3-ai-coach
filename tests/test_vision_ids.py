"""Tests for Phase 3 vision models and IDs."""

from splatoon3_ai_coach.vision.ids import (
    compute_analysis_id,
    make_frame_id,
    make_result_id,
    reading_hash,
)
from splatoon3_ai_coach.vision.models import TimerReading


def test_reading_hash_is_deterministic() -> None:
    reading = TimerReading(display="2:07", seconds_remaining=127.0)
    assert reading_hash(reading) == reading_hash(reading)


def test_frame_id_uses_source_frame_index() -> None:
    analysis_id = "abc123"
    frame_id = make_frame_id(analysis_id, 42, 1.5)
    assert frame_id == "abc123:frame:42"


def test_result_id_includes_reading_hash() -> None:
    reading = TimerReading(display="1:30", seconds_remaining=90.0)
    result_id = make_result_id("analysis", 10, 5.0, "timer", "timer@0.2.0", reading)
    assert ":timer:timer@0.2.0:" in result_id


def test_analysis_id_is_deterministic() -> None:
    first = compute_analysis_id("video", "extract", "vision")
    second = compute_analysis_id("video", "extract", "vision")
    assert first == second


def test_cadence_only_analysis_id_uses_empty_extraction_hash() -> None:
    first = compute_analysis_id("video", "", "vision")
    second = compute_analysis_id("video", "", "vision")
    assert first == second
    assert first != compute_analysis_id("video", "extract", "vision")
