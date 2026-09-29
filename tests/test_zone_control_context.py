"""Tests for sparse ScenarioContext zone-control evidence."""

from __future__ import annotations

from splatoon3_ai_coach.analysis.zone_control_context import (
    build_zone_control_evidence,
)
from splatoon3_ai_coach.vision.models import GameStateSnapshot


def _snapshot(
    t: float,
    state: str,
    quality: str = "observed",
) -> GameStateSnapshot:
    return GameStateSnapshot(
        timestamp=t,
        zone_control_state=state,
        zone_control_quality=quality,
        evidence_ids=[f"analysis:result:{t}:zone_control:read"],
    )


def test_sparse_samples_and_transitions_preserve_provenance() -> None:
    evidence = build_zone_control_evidence(
        [
            _snapshot(8.0, "opponent_control"),
            _snapshot(9.0, "neutral"),
            _snapshot(10.0, "opponent_control"),
        ],
        window_start=7.0,
        window_end=11.0,
        anchor=10.0,
        death_time=10.0,
        pre_death_offset_seconds=0.5,
        lookback_seconds=1.0,
        max_gap_seconds=0.2,
    )
    assert evidence is not None
    assert evidence.at_anchor is not None
    assert evidence.at_anchor.state == "opponent_control"
    assert len(evidence.transitions) == 2
    assert evidence.transitions[0].to_state == "neutral"
    assert evidence.transitions[1].to_state == "opponent_control"
    assert evidence.at_anchor.evidence_ids == ["analysis:result:10.0:zone_control:read"]


def test_held_keeps_prior_state_for_a_later_confirmed_transition() -> None:
    evidence = build_zone_control_evidence(
        [
            _snapshot(1.0, "ally_control"),
            _snapshot(1.5, "ally_control", "held"),
            _snapshot(2.0, "opponent_control"),
        ],
        window_start=0.0,
        window_end=3.0,
        anchor=1.5,
        death_time=None,
        pre_death_offset_seconds=0.5,
        lookback_seconds=1.0,
        max_gap_seconds=0.2,
    )
    assert evidence is not None
    assert evidence.at_anchor is not None
    assert evidence.at_anchor.quality == "held"
    assert [(t.from_state, t.to_state, t.video_time) for t in evidence.transitions] == [
        ("ally_control", "opponent_control", 2.0)
    ]
    assert evidence.transitions[0].evidence_ids == [
        "analysis:result:2.0:zone_control:read"
    ]


def test_state_established_before_window_supports_transition_inside() -> None:
    evidence = build_zone_control_evidence(
        [
            _snapshot(1.0, "neutral"),
            _snapshot(5.0, "neutral", "held"),
            _snapshot(6.0, "ally_control"),
        ],
        window_start=4.0,
        window_end=7.0,
        anchor=6.0,
        death_time=None,
        pre_death_offset_seconds=0.5,
        lookback_seconds=1.0,
        max_gap_seconds=0.2,
    )
    assert evidence is not None
    assert [(t.from_state, t.to_state) for t in evidence.transitions] == [
        ("neutral", "ally_control")
    ]


def test_held_only_window_yields_no_transitions() -> None:
    evidence = build_zone_control_evidence(
        [
            _snapshot(1.0, "ally_control", "held"),
            _snapshot(1.5, "opponent_control", "held"),
        ],
        window_start=0.0,
        window_end=3.0,
        anchor=1.5,
        death_time=None,
        pre_death_offset_seconds=0.5,
        lookback_seconds=1.0,
        max_gap_seconds=0.2,
    )
    assert evidence is not None
    assert evidence.transitions == []


def test_unknown_gap_does_not_bridge_states() -> None:
    evidence = build_zone_control_evidence(
        [
            _snapshot(1.0, "ally_control"),
            _snapshot(1.5, "unknown", "unknown"),
            _snapshot(2.0, "opponent_control"),
        ],
        window_start=0.0,
        window_end=3.0,
        anchor=2.0,
        death_time=None,
        pre_death_offset_seconds=0.5,
        lookback_seconds=1.0,
        max_gap_seconds=0.2,
    )
    assert evidence is not None
    assert evidence.transitions == []
