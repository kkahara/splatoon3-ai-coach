"""Shared sparse roster series (analysis; coach wraps via PlayerCountClock)."""

from __future__ import annotations

from splatoon3_ai_coach.analysis.player_count_series import (
    compress_player_count_observations,
    nearest_player_count_observation,
    observations_from_snapshots,
)
from splatoon3_ai_coach.coach.player_count_clock import build_player_count_clock
from splatoon3_ai_coach.vision.models import GameStateSnapshot


def _snap(t: float, ally: int | None, opp: int | None) -> GameStateSnapshot:
    return GameStateSnapshot(
        timestamp=t,
        match_phase="in_match",
        player_lifecycle="alive",
        ally_alive_count=ally,
        opponent_alive_count=opp,
        player_count_confidence=1.0,
    )


def test_observations_skip_unknown_counts() -> None:
    obs = observations_from_snapshots(
        [_snap(1.0, 4, 4), _snap(2.0, None, 3), _snap(3.0, 2, None)]
    )
    assert len(obs) == 1
    assert obs[0].video_time == 1.0


def test_compress_keeps_first_of_equal_run() -> None:
    obs = observations_from_snapshots(
        [_snap(1.0, 4, 4), _snap(2.0, 4, 4), _snap(3.0, 3, 4), _snap(4.0, 3, 4)]
    )
    out = compress_player_count_observations(obs)
    assert [(o.video_time, o.ally_alive_count, o.opponent_alive_count) for o in out] == [
        (1.0, 4, 4),
        (3.0, 3, 4),
    ]


def test_nearest_respects_max_gap_no_extrapolation() -> None:
    obs = observations_from_snapshots([_snap(10.0, 4, 4)])
    assert nearest_player_count_observation(obs, 12.0, max_gap_seconds=1.0) is None
    hit = nearest_player_count_observation(obs, 10.5, max_gap_seconds=1.0)
    assert hit is not None and hit.video_time == 10.0


def test_coach_clock_uses_shared_observations() -> None:
    snaps = [_snap(5.0, 3, 4), _snap(6.0, 3, 4), _snap(7.0, 2, 4)]
    clock = build_player_count_clock(snaps)
    shared = observations_from_snapshots(snaps)
    assert len(clock.observations) == len(shared)
    assert clock.observations[0].video_time == shared[0].video_time
    assert clock.at(5.0, max_gap_seconds=0.0) is not None


def test_numbers_state_from_counts() -> None:
    from splatoon3_ai_coach.analysis.player_count_series import (
        numbers_differential,
        numbers_state,
    )

    assert numbers_state(4, 4) == "even"
    assert numbers_state(4, 3) == "advantage"
    assert numbers_state(3, 4) == "disadvantage"
    assert numbers_state(None, 4) is None
    assert numbers_differential(4, 3) == 1
    assert numbers_differential(None, 3) is None
