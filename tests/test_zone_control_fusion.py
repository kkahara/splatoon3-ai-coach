"""Tests for zone-control state fusion and transition events."""

from __future__ import annotations

from splatoon3_ai_coach.analysis.zone_control_context import build_zone_control_evidence
from splatoon3_ai_coach.config.models import EventFusionConfig, ZoneControlDetectorConfig
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    GameEvent,
    GameEventReason,
    GameEventType,
    GameStateSnapshot,
    VisionFrameResult,
    ZoneControlReading,
)
from splatoon3_ai_coach.vision.zone_control_fusion import ZoneControlFuser

CONFIG = ZoneControlDetectorConfig(confirm_readings=2, hold_seconds=1.0)


def _frame(t: float, state: str, confidence: float = 0.9) -> VisionFrameResult:
    return VisionFrameResult(
        frame_id=f"frame:{t}",
        timestamp=t,
        source="cadence",
        detections=[
            DetectorResult(
                id=f"zone:{t}",
                detector_name="zone_control",
                detector_version="zone_control@test",
                confidence=confidence,
                reading=ZoneControlReading(
                    observed_state=state,
                    confidence=confidence,
                ),
            )
        ],
    )


def test_mode_gate_and_confirmation() -> None:
    disabled = ZoneControlFuser(CONFIG, "turf_war")
    assert disabled.step(_frame(0, "ally_control"), "in_match").state == "unknown"

    fuser = ZoneControlFuser(CONFIG, "splat_zones")
    assert fuser.step(_frame(0, "ally_control"), "in_match").state == "unknown"
    confirmed = fuser.step(_frame(0.5, "ally_control"), "in_match")
    assert (confirmed.state, confirmed.quality) == ("ally_control", "observed")


def test_one_frame_flip_is_held_and_does_not_transition() -> None:
    fuser = ZoneControlFuser(CONFIG, "splat_zones")
    fuser.step(_frame(0, "ally_control"), "in_match")
    fuser.step(_frame(0.5, "ally_control"), "in_match")
    held = fuser.step(_frame(1.0, "opponent_control"), "in_match")
    assert (held.state, held.quality) == ("ally_control", "held")
    assert fuser.step(_frame(1.5, "ally_control"), "in_match").state == "ally_control"


def test_hold_expires_and_unknown_breaks_confirmation() -> None:
    fuser = ZoneControlFuser(CONFIG, "splat_zones")
    fuser.step(_frame(0, "ally_control"), "in_match")
    fuser.step(_frame(0.5, "ally_control"), "in_match")
    assert fuser.step(_frame(2.0, "unknown"), "in_match").state == "unknown"
    assert fuser.step(_frame(2.5, "opponent_control"), "in_match").state == "unknown"


def test_hold_expiry_forgets_state_so_same_state_needs_reconfirmation() -> None:
    fuser = ZoneControlFuser(CONFIG, "splat_zones")
    fuser.step(_frame(0, "ally_control"), "in_match")
    fuser.step(_frame(0.5, "ally_control"), "in_match")
    assert fuser.step(_frame(2.0, "unknown"), "in_match").quality == "unknown"
    first = fuser.step(_frame(2.5, "ally_control"), "in_match")
    assert (first.state, first.quality) == ("unknown", "unknown")
    second = fuser.step(_frame(3.0, "ally_control"), "in_match")
    assert (second.state, second.quality) == ("ally_control", "observed")


def test_low_confidence_gap_clears_pending_confirmation() -> None:
    fuser = ZoneControlFuser(CONFIG, "splat_zones")
    fuser.step(_frame(0, "ally_control"), "in_match")
    fuser.step(_frame(0.5, "ally_control"), "in_match")
    assert fuser.step(_frame(1.0, "opponent_control"), "in_match").quality == "held"
    gap = fuser.step(_frame(1.2, "opponent_control", confidence=0.1), "in_match")
    assert (gap.state, gap.quality) == ("ally_control", "held")
    assert fuser.step(_frame(1.4, "opponent_control"), "in_match").quality == "held"
    confirmed = fuser.step(_frame(1.45, "opponent_control"), "in_match")
    assert (confirmed.state, confirmed.quality) == ("opponent_control", "observed")


# End-to-end: fusion → snapshots → GameEvents and ScenarioContext.

Step = tuple[float, str | None]


def _fuse(steps: list[Step]) -> list[GameStateSnapshot]:
    """Fuse (time, state) steps; ``None`` means no usable reading."""
    fuser = ZoneControlFuser(CONFIG, "splat_zones")
    out: list[GameStateSnapshot] = []
    for t, state in steps:
        frame = (
            _frame(t, "ally_control", confidence=0.1)
            if state is None
            else _frame(t, state)
        )
        fused = fuser.step(frame, "in_match")
        out.append(
            GameStateSnapshot(
                timestamp=t,
                zone_control_state=fused.state,
                zone_control_quality=fused.quality,
                evidence_ids=[f"{item}:zone_control:read" for item in fused.evidence_ids],
            )
        )
    return out


def _zone_events(snapshots: list[GameStateSnapshot]) -> list[GameEvent]:
    return [
        event
        for event in infer_events(snapshots, EventFusionConfig())
        if event.reason is GameEventReason.ZONE_CONTROL_TRANSITION
    ]


def _context_transitions(snapshots: list[GameStateSnapshot]) -> list[tuple]:
    evidence = build_zone_control_evidence(
        snapshots,
        window_start=0.0,
        window_end=snapshots[-1].timestamp,
        anchor=snapshots[-1].timestamp,
        death_time=None,
        pre_death_offset_seconds=0.5,
        lookback_seconds=1.0,
        max_gap_seconds=0.2,
    )
    transitions = evidence.transitions if evidence is not None else []
    return [(t.video_time, t.from_state, t.to_state, t.evidence_ids) for t in transitions]


def _event_tuples(events: list[GameEvent]) -> list[tuple]:
    return [
        (
            e.start_time,
            e.from_zone_control,
            e.to_zone_control,
            [i for i in e.evidence_ids if ":zone_control:" in i],
        )
        for e in events
    ]


def test_event_time_is_confirming_snapshot_with_all_candidate_ids() -> None:
    snapshots = _fuse(
        [(0, "neutral"), (0.5, "neutral"), (1.0, "ally_control"), (1.5, "ally_control")]
    )
    events = _zone_events(snapshots)
    assert [e.event_type for e in events] == [GameEventType.ALLY_GAIN_CONTROL]
    assert events[0].start_time == 1.5
    assert _event_tuples(events)[0][3] == [
        "zone:1.0:zone_control:read",
        "zone:1.5:zone_control:read",
    ]
    assert _context_transitions(snapshots) == _event_tuples(events)


def test_observed_held_observed_same_state_emits_nothing() -> None:
    snapshots = _fuse(
        [(0, "ally_control"), (0.5, "ally_control"), (1.0, None), (1.5, "ally_control")]
    )
    assert [s.zone_control_quality for s in snapshots][2] == "held"
    assert _zone_events(snapshots) == []
    assert _context_transitions(snapshots) == []


def test_observed_held_then_confirmed_change_is_one_transition() -> None:
    snapshots = _fuse(
        [
            (0, "ally_control"),
            (0.5, "ally_control"),
            (0.7, None),
            (0.9, "opponent_control"),
            (1.1, "opponent_control"),
        ]
    )
    events = _zone_events(snapshots)
    assert [(e.event_type, e.start_time) for e in events] == [
        (GameEventType.ZONE_CONTROL_CHANGED, 1.1)
    ]
    assert (events[0].from_zone_control, events[0].to_zone_control) == (
        "ally_control",
        "opponent_control",
    )
    assert _context_transitions(snapshots) == _event_tuples(events)


def test_unknown_gap_recovery_initialises_without_transition() -> None:
    snapshots = _fuse(
        [
            (0, "ally_control"),
            (0.5, "ally_control"),
            (1.0, None),
            (2.0, None),
            (2.5, "opponent_control"),
            (3.0, "opponent_control"),
        ]
    )
    assert snapshots[3].zone_control_quality == "unknown"
    assert snapshots[5].zone_control_quality == "observed"
    assert _zone_events(snapshots) == []
    assert _context_transitions(snapshots) == []


def test_direct_ally_opponent_change_never_manufactures_neutral() -> None:
    snapshots = _fuse(
        [
            (0, "opponent_control"),
            (0.5, "opponent_control"),
            (1.0, "ally_control"),
            (1.5, "ally_control"),
        ]
    )
    events = _zone_events(snapshots)
    assert [e.event_type for e in events] == [GameEventType.ZONE_CONTROL_CHANGED]
    assert not any(s.zone_control_state == "neutral" for s in snapshots)
    assert _context_transitions(snapshots) == _event_tuples(events)


def test_events_and_context_agree_on_a_mixed_sequence() -> None:
    states: list[str | None] = [
        "neutral", "neutral", "ally_control", None, "ally_control", "ally_control",
        "opponent_control", "ally_control", "ally_control", None, None, None, None,
        "neutral", "neutral", "opponent_control", "opponent_control", None,
        "neutral", "neutral", "ally_control", "ally_control",
    ]
    snapshots = _fuse([(i * 0.5, state) for i, state in enumerate(states)])
    events = _zone_events(snapshots)
    assert events
    assert _context_transitions(snapshots) == _event_tuples(events)


def _snapshot(t: float, previous: str, current: str) -> list[GameStateSnapshot]:
    return [
        GameStateSnapshot(
            timestamp=t,
            zone_control_state=previous,
            zone_control_quality="observed",
        ),
        GameStateSnapshot(
            timestamp=t + 0.5,
            zone_control_state=current,
            zone_control_quality="observed",
        ),
    ]


def test_transition_table_and_confirmation_timestamp() -> None:
    cases = [
        ("neutral", "ally_control", GameEventType.ALLY_GAIN_CONTROL),
        ("neutral", "opponent_control", GameEventType.OPPONENT_GAIN_CONTROL),
        ("ally_control", "neutral", GameEventType.ALLY_LOSE_CONTROL),
        ("opponent_control", "neutral", GameEventType.OPPONENT_LOSE_CONTROL),
        ("ally_control", "opponent_control", GameEventType.ZONE_CONTROL_CHANGED),
    ]
    for previous, current, expected in cases:
        events = infer_events(_snapshot(10.0, previous, current), EventFusionConfig())
        assert len(events) == 1
        assert events[0].event_type is expected
        assert events[0].start_time == 10.5


def test_unknown_breaks_event_chain() -> None:
    snapshots = [
        GameStateSnapshot(
            timestamp=1.0,
            zone_control_state="ally_control",
            zone_control_quality="observed",
        ),
        GameStateSnapshot(timestamp=1.5),
        GameStateSnapshot(
            timestamp=2.0,
            zone_control_state="opponent_control",
            zone_control_quality="observed",
        ),
    ]
    assert not [
        event for event in infer_events(snapshots, EventFusionConfig())
        if event.event_type is GameEventType.ZONE_CONTROL_CHANGED
    ]
