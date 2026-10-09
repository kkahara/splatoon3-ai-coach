"""Signed Review clock labels: pre-GO countdown (``-M:SS``) and overtime (``+M:SS``)."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.models import ReviewTimelineVideoConfig
from splatoon3_ai_coach.review.clock import (
    format_elapsed_clock,
    is_countdown_display,
    is_overtime_display,
    parse_elapsed_clock,
)
from splatoon3_ai_coach.review.models import (
    CursorObservation,
    ReviewTimelineSample,
    TimelineEventMarker,
)
from splatoon3_ai_coach.review.second_states import build_second_states
from splatoon3_ai_coach.review.video_adapter import _read_clock_from_image

LABELS = Path(__file__).parent / "fixtures" / "review" / "clock_labels"
# (fixture, cursor x in the 1920x1080 source frame, expected value, display)
REAL_LABELS = [
    ("overtime_+0-28.png", 1735, -28, "+0:28"),
    ("countdown_-0-02.png", 144, -2, "-0:02"),
    ("regulation_0-03.png", 1586, 3, "0:03"),
    ("regulation_4-44.png", 230, 284, "4:44"),
]


def test_overtime_display_parses_past_zero() -> None:
    assert parse_elapsed_clock("+0:28") == -28
    assert parse_elapsed_clock("+1:05") == -65
    assert is_overtime_display("+0:28")
    assert not is_overtime_display("-0:03")
    assert is_countdown_display("-0:03")
    assert not is_countdown_display("0:03")


def test_format_keeps_overtime_and_countdown_distinct() -> None:
    assert format_elapsed_clock(-28, overtime=True) == "+0:28"
    assert format_elapsed_clock(-3) == "-0:03"
    assert format_elapsed_clock(284) == "4:44"
    assert format_elapsed_clock(0, overtime=True) == "0:00"


@pytest.mark.parametrize(("name", "cursor_x", "value", "display"), REAL_LABELS)
def test_real_label_sign(name: str, cursor_x: int, value: int, display: str) -> None:
    config = ReviewTimelineVideoConfig()
    crop = cv2.imread(str(LABELS / name))
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    top = int(1080 * config.clock_roi_top_fraction)
    left = max(0, cursor_x - int(1920 * config.clock_roi_width_fraction / 2))
    frame[top : top + crop.shape[0], left : left + crop.shape[1]] = crop
    clock = _read_clock_from_image(frame, cursor_x, config)
    assert clock is not None
    assert clock.elapsed_seconds == value
    assert clock.display == display


def _sample(index: int, value: int, display: str) -> ReviewTimelineSample:
    return ReviewTimelineSample(
        sample_id=f"sample-{index:06d}",
        capture_index=index,
        image_path=f"{index}.jpg",
        image_sha256="hash",
        elapsed_seconds=value,
        displayed_clock=display,
        clock_confidence=1,
        cursor=CursorObservation(x=100, confidence=1),
    )


def test_overtime_samples_share_seconds_with_overtime_events() -> None:
    samples = [
        _sample(0, -2, "-0:02"),
        _sample(1, 1, "0:01"),
        _sample(2, 0, "0:00"),
        _sample(3, -16, "+0:16"),
        _sample(4, -2, "+0:02"),
    ]
    death = TimelineEventMarker(
        marker_id="death-ot",
        team_row="top",
        kind="death",
        x_norm=0.9,
        elapsed_estimate=-16.4,
        player_slot_id="top_1",
    )
    states = {s.elapsed_second: s for s in build_second_states(samples, [death], [], [])}
    assert min(states) == -16 and max(states) == 1
    assert states[-16].death_marker_ids == ["death-ot"]
    assert [p.sample_id for p in states[-16].provenance] == ["sample-000003"]
    assert [p.sample_id for p in states[-2].provenance] == ["sample-000004"]
    assert [p.sample_id for p in states[1].provenance] == ["sample-000001"]
