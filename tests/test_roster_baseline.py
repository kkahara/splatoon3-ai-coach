"""Offline gameplay-HUD roster consistency diagnostic."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "diagnostics"))

import roster_baseline as rb  # noqa: E402

from splatoon3_ai_coach.vision.models import (  # noqa: E402
    DetectorResult,
    PlayerCountReading,
    TimerReading,
    VisionFrameResult,
)


def _point(t: float, ally: int | None, opponent: int | None) -> rb.RawRosterPoint:
    return rb.RawRosterPoint(
        video_time=t,
        ally=ally,
        opponent=opponent,
        scores=(),
        visible=ally is not None,
        confidence=0.9 if ally is not None else 0.0,
    )


def _frame(t: float, *, player_count: bool = True) -> VisionFrameResult:
    detections = []
    if player_count:
        detections.append(
            DetectorResult(
                id=f"pc:{t}",
                detector_name="player_count",
                detector_version="player_count@test",
                confidence=0.9,
                reading=PlayerCountReading(),
            )
        )
    detections.append(
        DetectorResult(
            id=f"timer:{t}",
            detector_name="timer",
            detector_version="timer@test",
            confidence=0.0,
            reading=TimerReading(display="5:00", seconds_remaining=300),
        )
    )
    return VisionFrameResult(
        frame_id=f"f:{t}", timestamp=t, source="cadence", detections=detections
    )


def test_change_stats_use_consistency_language_and_reset_across_unknown() -> None:
    points = [
        _point(0.0, 4, 4),
        _point(0.5, 3, 4),
        _point(1.0, 4, 4),
        _point(1.5, None, None),
        _point(2.0, 2, 3),
    ]
    assert rb._change_stats(points) == {
        "pairs": 2,
        "changed": 2,
        "magnitude_1": 2,
        "magnitude_2plus": 0,
        "reversals": 1,
    }


def test_phase_frames_keep_alternating_roster_results_only() -> None:
    frames = [_frame(0.0), _frame(0.5, player_count=False), _frame(1.0)]
    even = rb.phase_frames(frames, 0)
    odd = rb.phase_frames(frames, 1)
    assert [rb._player_result(frame) is not None for frame in even] == [
        True,
        False,
        False,
    ]
    assert [rb._player_result(frame) is not None for frame in odd] == [
        False,
        False,
        True,
    ]
    assert any(item.detector_name == "timer" for item in even[0].detections)


def test_logic_status_checks_persisted_slots_against_current_threshold() -> None:
    current = rb.RawRosterPoint(
        video_time=0,
        ally=3,
        opponent=4,
        scores=(0.01, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2),
        visible=True,
        confidence=0.99,
    )
    stale = rb.RawRosterPoint(
        video_time=1,
        ally=2,
        opponent=4,
        scores=current.scores,
        visible=True,
        confidence=0.99,
    )
    assert rb._logic_status([current], 0.03) == (rb.CURRENT_LOGIC, 0)
    assert rb._logic_status([current, stale], 0.03) == (rb.PRE_CURRENT_LOGIC, 1)


def test_summary_keeps_source_classes_separate() -> None:
    live = rb.MatchBaseline(
        match="live",
        video_identity="a",
        source_class="live_gameplay",
        language="en",
        detector_logic=rb.CURRENT_LOGIC,
        scheduled_readings=10,
        visible_readings=8,
    )
    replay = live.model_copy(
        update={
            "match": "replay",
            "video_identity": "b",
            "source_class": "replay_derived",
            "scheduled_readings": 5,
            "visible_readings": 1,
        }
    )
    result = rb.summarize([live, replay])
    by_source = result["by_source"]
    assert by_source["live_gameplay"]["matches"] == 1
    assert by_source["live_gameplay"]["visibility_rate"] == 0.8
    assert by_source["replay_derived"]["matches"] == 1
    assert by_source["replay_derived"]["visibility_rate"] == 0.2
