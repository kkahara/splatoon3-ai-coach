"""Implausible Review clock readings never become per-second or axis evidence.

Reader garbage such as ``95:00`` or ``-9:18`` keeps its frame and displayed
text for provenance, but must not create a state, move the axis, shift
neighbouring states, change event timestamps, or set a neighbour's direction.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.review.clock import (
    ClockPhase,
    ClockSource,
    ReviewClock,
    clock_phase,
)
from splatoon3_ai_coach.review.event_lane import calibrate_axis, extract_lane_events
from splatoon3_ai_coach.review.models import (
    AxisCalibrationEvidence,
    CursorObservation,
    ReviewTimelineSample,
    ScrollDirection,
)
from splatoon3_ai_coach.review.second_states import build_second_states
from splatoon3_ai_coach.review.timeline_pipeline import _read_sample
from splatoon3_ai_coach.review.video_adapter import adapt_frames
from tests.test_review_video_adapter import _frame

CORPUS = Path(__file__).parent / "fixtures" / "review" / "lane_corpus" / "21-56-52"
GARBAGE = ["95:00", "90:03", "9:01", "-9:18", "-95:08", "+40:03"]


@pytest.mark.parametrize(
    ("display", "value", "phase"),
    [
        ("5:00", 300, ClockPhase.regulation),
        ("4:44", 284, ClockPhase.regulation),
        ("0:00", 0, ClockPhase.regulation),
        ("+0:28", -28, ClockPhase.overtime),
        ("-0:03", -3, ClockPhase.countdown),
        ("95:00", 5700, None),
        ("9:01", 541, None),
        ("-9:18", -558, None),
        ("+40:03", -2403, None),
    ],
)
def test_clock_phase(display: str, value: int, phase: ClockPhase | None) -> None:
    config = ReviewTimelineConfig()
    assert (
        clock_phase(
            display,
            value,
            regulation_seconds=config.regulation_seconds,
            max_overtime_seconds=config.max_overtime_seconds,
            max_countdown_seconds=config.max_countdown_seconds,
        )
        == phase
    )


def _imported(index: int, display: str, x: float | None = None) -> ReviewTimelineSample:
    """A sample through the real import path with a manifest clock label."""
    path = CORPUS / "sample-000236.jpg"
    image = cv2.imread(str(path))
    entries = [{"capture_index": index, "elapsed_clock": display}]
    sample = _read_sample(
        image, path, f"sample-{index:06d}", index, entries, ReviewTimelineConfig()
    )
    if x is not None:
        sample.cursor = CursorObservation(x=x * 1920, x_norm=x, confidence=1.0)
    return sample


def test_implausible_clock_keeps_provenance_but_no_value() -> None:
    sample = _imported(7, "95:00")
    assert sample.elapsed_seconds is None
    assert sample.displayed_clock == "95:00"
    assert sample.clock_status == "implausible"
    assert "implausible clock 95:00" in sample.warnings


def _valid_and_garbage() -> tuple[list[ReviewTimelineSample], list[ReviewTimelineSample]]:
    axis = yaml.safe_load((CORPUS / "corpus.yaml").read_text("utf-8"))["axis"]
    valid = []
    for index, clock in enumerate(range(290, 200, -9)):
        x = (clock - axis["intercept"]) / axis["slope"]
        valid.append(_imported(index, f"{clock // 60}:{clock % 60:02d}", x))
    garbage = [
        _imported(100 + index, display, x=0.3 + 0.05 * index)
        for index, display in enumerate(GARBAGE)
    ]
    return valid, garbage


def test_implausible_clock_creates_no_state_and_keeps_neighbours() -> None:
    valid, garbage = _valid_and_garbage()
    clean = build_second_states(valid, [], [], [])
    mixed = build_second_states(valid + garbage, [], [], [])
    assert [s.elapsed_second for s in mixed] == [s.elapsed_second for s in clean]
    assert mixed == clean


def test_implausible_clock_does_not_move_axis() -> None:
    valid, garbage = _valid_and_garbage()
    clean = calibrate_axis(valid)
    mixed = calibrate_axis(valid + garbage)
    assert clean is not None and mixed is not None
    assert (mixed.slope, mixed.intercept) == (clean.slope, clean.intercept)
    assert mixed.point_count == clean.point_count
    assert mixed.rejected_point_count == clean.rejected_point_count


def test_overtime_enters_axis_fit_only_when_enabled() -> None:
    def x_of(clock: int) -> float:
        return 0.08 + (300 - clock) / 300 * 0.8

    valid = [
        _imported(i, f"{c // 60}:{c % 60:02d}", x_of(c))
        for i, c in enumerate(range(290, 10, -20))
    ]
    overtime = [
        _imported(200 + i, f"+0:{s:02d}", x_of(-s)) for i, s in enumerate((5, 12, 20))
    ]
    base = calibrate_axis(valid + overtime)
    included = calibrate_axis(
        valid + overtime, ReviewTimelineConfig(axis_include_overtime=True)
    )
    assert base is not None and included is not None
    assert base.point_count == len(valid)
    assert included.point_count == len(valid) + len(overtime)


def test_implausible_clock_does_not_change_event_timestamps() -> None:
    corpus = yaml.safe_load((CORPUS / "corpus.yaml").read_text("utf-8"))
    config = load_config(default_config_path()).review
    images, frames = {}, []
    for frame in corpus["frames"]:
        image = cv2.imread(str(CORPUS / frame["file"]))
        images[frame["sample_id"]] = image
        frames.append(
            ReviewTimelineSample(
                sample_id=frame["sample_id"],
                capture_index=frame["capture_index"],
                image_path=frame["file"],
                image_sha256="0" * 64,
                cursor=CursorObservation(
                    x=frame["cursor_x"], x_norm=frame["cursor_x"] / 1920, confidence=1.0
                ),
            )
        )
    valid, garbage = _valid_and_garbage()
    events = []
    for axis_samples in (valid, valid + garbage):
        axis = calibrate_axis(axis_samples, config)
        assert isinstance(axis, AxisCalibrationEvidence)
        markers, specials = extract_lane_events(images, frames, axis, config)
        events.append(
            [(e.slot_id, e.special_id, e.elapsed_estimate) for e in specials]
            + [(m.player_slot_id, m.kind, m.elapsed_estimate) for m in markers]
        )
    assert events[0] and events[0] == events[1]


def test_implausible_clock_does_not_set_neighbour_direction(tmp_path: Path) -> None:
    labels = iter([("1:40", 100), ("-9:18", -558), ("1:39", 99)])

    def read_clock(_image: np.ndarray, _x: int, _config: object) -> ReviewClock:
        display, value = next(labels)
        return ReviewClock(
            display=display, elapsed_seconds=value, source=ClockSource.image, confidence=1
        )

    samples = adapt_frames(
        [_frame(float(i), i) for i in range(3)],
        tmp_path,
        timeline_config=ReviewTimelineConfig(),
        clock_reader=read_clock,
    )
    directions = [s.scroll_direction for s in samples]
    assert directions[1] == ScrollDirection.unknown
    assert directions[2] == ScrollDirection.backward
