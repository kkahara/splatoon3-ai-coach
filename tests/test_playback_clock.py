"""Playback clock: video seconds vs observed game-clock seconds."""

from __future__ import annotations

import pytest
from public_site.results import _recovery

from splatoon3_ai_coach.analysis.scenario_context import build_scenario_contexts
from splatoon3_ai_coach.analysis.scenarios import build_scenarios
from splatoon3_ai_coach.coach.coach_input import build_coach_input_for_scenario
from splatoon3_ai_coach.coach.death_importance import score_death_candidate
from splatoon3_ai_coach.coach.game_clock import GameClock, GameClockObservation
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.coach.playback_clock import (
    PlaybackClockInfo,
    PlaybackClockStatus,
    clocked_duration,
    estimate_playback_clock,
    to_game_seconds,
    to_video_seconds,
)
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.vision.models import GameEvent, GameEventReason, GameEventType


def _obs(video_time: float, seconds: int) -> GameClockObservation:
    return GameClockObservation(
        video_time=video_time,
        seconds_remaining=seconds,
        confidence=0.9,
        display=f"{seconds // 60}:{seconds % 60:02d}",
    )


def _clock(rate: float, *, start: float = 0.0, end: float = 120.0, top: int = 290):
    """Countdown sampled at 2 fps; ``rate`` game seconds per video second."""
    obs = []
    t = start
    while t <= end:
        obs.append(_obs(t, int(top - (t - start) * rate)))
        t += 0.5
    return GameClock(observations=tuple(obs))


def test_normal_speed_match_is_normal() -> None:
    info = estimate_playback_clock(_clock(1.0))
    assert info.status is PlaybackClockStatus.NORMAL
    assert info.game_seconds_per_video_second == pytest.approx(1.0, abs=0.03)


def test_replay_speed_is_rate_mismatch_with_both_directions() -> None:
    info = estimate_playback_clock(_clock(1.5))
    assert info.status is PlaybackClockStatus.RATE_MISMATCH
    assert info.game_seconds_per_video_second == pytest.approx(1.5, abs=0.03)
    assert info.video_seconds_per_game_second == pytest.approx(1 / 1.5, abs=0.02)
    assert to_game_seconds(9.0, info) == pytest.approx(13.5, abs=0.3)
    assert to_video_seconds(14.0, info) == pytest.approx(9.33, abs=0.2)


def test_insufficient_clock_observations() -> None:
    info = estimate_playback_clock(_clock(1.0, end=3.0))
    assert info.status is PlaybackClockStatus.INSUFFICIENT_CLOCK_EVIDENCE
    assert info.game_seconds_per_video_second is None
    empty = estimate_playback_clock(GameClock())
    assert empty.status is PlaybackClockStatus.INSUFFICIENT_CLOCK_EVIDENCE


def test_frozen_runs_are_excluded() -> None:
    frozen = [_obs(t * 0.5, 300) for t in range(20)]  # opening 5:00 for 10s
    flowing = list(_clock(1.0, start=10.0, end=100.0, top=299).observations)
    info = estimate_playback_clock(GameClock(observations=tuple(frozen + flowing)))
    assert info.excluded_frozen == 20
    assert info.status is PlaybackClockStatus.NORMAL


def test_malformed_non_monotonic_reads_are_excluded() -> None:
    base = list(_clock(1.0).observations)
    base[40] = _obs(base[40].video_time, 299)  # countdown jumps up
    info = estimate_playback_clock(GameClock(observations=tuple(base)))
    assert info.excluded_non_monotonic >= 1
    assert info.status is PlaybackClockStatus.NORMAL


def test_single_bad_read_cannot_decide_the_rate() -> None:
    base = list(_clock(1.0).observations)
    base[100] = _obs(base[100].video_time, 5)  # one wild low misread
    info = estimate_playback_clock(GameClock(observations=tuple(base)))
    assert info.status is PlaybackClockStatus.NORMAL
    assert info.game_seconds_per_video_second == pytest.approx(1.0, abs=0.05)


def _mismatch() -> PlaybackClockInfo:
    return estimate_playback_clock(_clock(1.5))


def test_game_clock_duration_from_samples() -> None:
    clock = GameClock(observations=(_obs(37.5, 274), _obs(46.5, 260)))
    value = clocked_duration(
        37.5, 46.5, info=_mismatch(), game_clock=clock, max_gap_seconds=1.0
    )
    assert value is not None
    assert value.video_seconds == pytest.approx(9.0)
    assert (value.value, value.clock, value.method) == (14.0, "game", "clock_samples")


def test_endpoint_without_exact_timer_sample_uses_nearest_read() -> None:
    clock = GameClock(
        observations=(_obs(37.0, 275), _obs(38.0, 273), _obs(46.0, 261), _obs(47.0, 259))
    )
    value = clocked_duration(
        37.5, 46.5, info=_mismatch(), game_clock=clock, max_gap_seconds=1.0
    )
    assert value is not None and value.method == "clock_samples"
    assert value.start_gap_seconds == pytest.approx(0.5)
    assert value.uncertainty_seconds is not None and value.uncertainty_seconds > 1.0


def test_missing_endpoint_falls_back_to_rate_conversion() -> None:
    value = clocked_duration(
        37.5, 46.5, info=_mismatch(), game_clock=GameClock(), max_gap_seconds=1.0
    )
    assert value is not None
    assert value.method == "rate_converted"
    assert value.value == pytest.approx(13.5, abs=0.3)


def test_no_conversion_when_clock_evidence_is_inadequate() -> None:
    info = estimate_playback_clock(GameClock())
    value = clocked_duration(37.5, 46.5, info=info, game_clock=None, max_gap_seconds=1.0)
    assert value is not None
    assert value.video_seconds == pytest.approx(9.0)
    assert value.value is None and value.clock is None


def _trace_c_input(info: PlaybackClockInfo | None, clock: GameClock):
    config = load_config(default_config_path())
    events = [
        GameEvent(
            start_time=37.5,
            event_type=GameEventType.DEATH,
            reason=GameEventReason.ALIVE_TO_DEAD,
            confidence=1.0,
        ),
        GameEvent(
            start_time=43.0,
            event_type=GameEventType.RESPAWN,
            reason=GameEventReason.COUNTDOWN_PLATE_ENDED,
            confidence=1.0,
        ),
        GameEvent(
            start_time=46.5,
            event_type=GameEventType.ACTIVE_AGAIN,
            reason=GameEventReason.AWAITING_CONTROL_TO_ALIVE,
            confidence=1.0,
        ),
    ]
    scenarios = build_scenarios(events, config.scenarios)
    contexts = build_scenario_contexts(events, scenarios, config.scenarios)
    death_id = next(s.scenario_id for s in scenarios if s.scenario_id.startswith("death"))
    return build_coach_input_for_scenario(
        death_id,
        scenarios,
        contexts,
        clock,
        max_gap_seconds=1.0,
        playback_clock=info,
    )


def test_out_of_play_uses_game_clock_when_rate_mismatch() -> None:
    clock = GameClock(
        observations=tuple(_clock(1.5, start=0.0, end=60.0, top=300).observations)
    )
    info = estimate_playback_clock(clock)
    assert info.status is PlaybackClockStatus.RATE_MISMATCH
    coach_input = _trace_c_input(info, clock)
    durations = coach_input.durations
    assert durations is not None and durations.death_to_active_again is not None
    assert durations.death_to_active_again.video_seconds == pytest.approx(9.0)
    assert durations.death_to_active_again.clock == "game"
    assert durations.death_to_active_again.value == pytest.approx(14.0, abs=1.0)
    line = _recovery(coach_input.model_dump(mode="json"))
    assert line is not None and "(game clock)" in line
    assert "9.0s out of play" not in line
    codes = {limit.code for limit in coach_input.evidence_limits}
    assert "playback_rate_mismatch" in codes
    view = build_coach_llm_view(coach_input, score_death_candidate(coach_input))
    assert view.death is not None
    assert view.death["death_to_active_again"]["clock"] == "game"
    assert view.death["death_to_active_again"]["video_seconds"] == pytest.approx(9.0)
    assert view.playback is not None and view.playback["status"] == "rate_mismatch"


def test_out_of_play_video_seconds_at_normal_speed() -> None:
    clock = _clock(1.0, end=60.0, top=300)
    info = estimate_playback_clock(clock)
    line = _recovery(_trace_c_input(info, clock).model_dump(mode="json"))
    assert line == "9.0s out of play"


def test_out_of_play_omitted_when_playback_unverified() -> None:
    coach_input = _trace_c_input(None, GameClock())
    assert _recovery(coach_input.model_dump(mode="json")) is None
    codes = {limit.code for limit in coach_input.evidence_limits}
    assert "playback_clock_insufficient" in codes
