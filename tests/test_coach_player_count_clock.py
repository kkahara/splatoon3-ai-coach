"""Coach-layer PlayerCountClock: fused snapshot counts only."""

from __future__ import annotations

import pytest

from splatoon3_ai_coach.coach.player_count_clock import (
    PlayerCountClock,
    PlayerCountObservation,
    build_player_count_clock,
    numbers_differential,
    numbers_state,
)
from splatoon3_ai_coach.vision.models import GameStateSnapshot


def _snap(
    timestamp: float,
    ally: int | None,
    opponent: int | None,
    *,
    confidence: float | None = 0.95,
) -> GameStateSnapshot:
    return GameStateSnapshot(
        timestamp=timestamp,
        ally_alive_count=ally,
        opponent_alive_count=opponent,
        player_count_confidence=confidence if ally is not None else None,
        evidence_ids=[f"pc:{timestamp}"] if ally is not None else [],
    )


def _obs(t: float, ally: int, opponent: int) -> PlayerCountObservation:
    return PlayerCountObservation(
        video_time=t,
        ally_alive_count=ally,
        opponent_alive_count=opponent,
        confidence=1.0,
    )


def test_exact_lookup() -> None:
    clock = build_player_count_clock([_snap(116.0, 3, 4)])
    obs = clock.at(116.0, max_gap_seconds=1.0)
    assert obs is not None
    assert obs.ally_alive_count == 3
    assert obs.opponent_alive_count == 4
    assert obs.source == "player_count_fusion"
    assert obs.confidence == pytest.approx(0.95)


def test_nearby_lookup_within_gap() -> None:
    clock = build_player_count_clock([_snap(116.0, 3, 4)])
    obs = clock.at(116.4, max_gap_seconds=1.0)
    assert obs is not None
    assert obs.video_time == pytest.approx(116.0)


def test_outside_gap_and_empty_return_none() -> None:
    clock = build_player_count_clock([_snap(116.0, 3, 4)])
    assert clock.at(118.0, max_gap_seconds=1.0) is None
    assert PlayerCountClock().at(116.0, max_gap_seconds=1.0) is None


def test_unknown_snapshots_excluded() -> None:
    clock = build_player_count_clock(
        [_snap(1.0, None, None), _snap(2.0, 4, 4), _snap(3.0, 2, None)]
    )
    assert len(clock.observations) == 1
    assert clock.observations[0].video_time == pytest.approx(2.0)


def test_nearest_prefers_closer() -> None:
    clock = build_player_count_clock([_snap(10.0, 4, 4), _snap(12.0, 2, 3)])
    obs = clock.at(11.2, max_gap_seconds=2.0)
    assert obs is not None
    assert obs.ally_alive_count == 2


def test_no_extrapolation_across_gap() -> None:
    clock = build_player_count_clock([_snap(10.0, 4, 4), _snap(20.0, 1, 1)])
    assert clock.at(15.0, max_gap_seconds=1.0) is None


def test_ordering_preserved() -> None:
    clock = build_player_count_clock(
        [_snap(3.0, 1, 1), _snap(1.0, 4, 4), _snap(2.0, 3, 3)]
    )
    times = [obs.video_time for obs in clock.observations]
    assert times == [1.0, 2.0, 3.0]


def test_observation_model_has_no_quality() -> None:
    obs = PlayerCountObservation(
        video_time=1.0,
        ally_alive_count=4,
        opponent_alive_count=4,
        confidence=1.0,
    )
    assert "quality" not in PlayerCountObservation.model_fields


def test_numbers_semantics() -> None:
    assert numbers_differential(_obs(1.0, 4, 4)) == 0
    assert numbers_state(_obs(1.0, 4, 4)) == "even"
    assert numbers_differential(_obs(1.0, 4, 3)) == 1
    assert numbers_state(_obs(1.0, 4, 3)) == "advantage"
    assert numbers_differential(_obs(1.0, 3, 4)) == -1
    assert numbers_state(_obs(1.0, 3, 4)) == "disadvantage"
    assert numbers_differential(None) is None
    assert numbers_state(None) is None


def test_window_exact_gap_zero() -> None:
    clock = build_player_count_clock(
        [
            _snap(40.0, 4, 4),
            _snap(43.0, 3, 4),
            _snap(46.0, 3, 4),
            _snap(48.0, 2, 4),
            _snap(51.0, 2, 3),
            _snap(54.0, 3, 3),
        ]
    )
    points = clock.window(
        48.0,
        [-5, -2, 0, 3, 6],
        max_gap_seconds=1.0,
    )
    assert [p.video_time for p in points] == [43.0, 46.0, 48.0, 51.0, 54.0]
    assert [p.gap_seconds for p in points] == [0.0, 0.0, 0.0, 0.0, 0.0]
    assert points[0].observation is not None
    assert points[0].observation.ally_alive_count == 3
    assert points[2].numbers_state == "disadvantage"
    assert points[2].numbers_differential == -2


def test_window_gap_rejection_leaves_missing() -> None:
    clock = build_player_count_clock([_snap(48.0, 3, 4)])
    points = clock.window(48.0, [-5.0], max_gap_seconds=1.0)
    assert points[0].observation is None
    assert points[0].gap_seconds is None
    assert points[0].numbers_state is None


def test_observations_between_inclusive() -> None:
    clock = build_player_count_clock(
        [_snap(40.0, 4, 4), _snap(43.0, 3, 4), _snap(48.0, 3, 4), _snap(55.0, 2, 2)]
    )
    got = clock.observations_between(43.0, 48.0)
    assert [o.video_time for o in got] == [43.0, 48.0]
