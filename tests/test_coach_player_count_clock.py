"""Coach-layer PlayerCountClock: fused snapshot counts only."""

from __future__ import annotations

import pytest

from splatoon3_ai_coach.coach.player_count_clock import (
    PlayerCountClock,
    PlayerCountObservation,
    build_player_count_clock,
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
