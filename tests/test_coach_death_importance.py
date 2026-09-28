"""Pre-death roster sampling, roster factors, thresholds, config identity."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from splatoon3_ai_coach.analysis.scenario_context import (
    DeathEpisodeContext,
    ScenarioContext,
    ScenarioRelations,
    TimelineContext,
)
from splatoon3_ai_coach.analysis.scenario_evidence import (
    PlayerCountPoint,
    PlayersEvidence,
)
from splatoon3_ai_coach.analysis.scenario_models import (
    Scenario,
    ScenarioOutcome,
    ScenarioType,
)
from splatoon3_ai_coach.coach.claim_catalog import (
    FINAL_30S_REMAINING,
    FIRST_30S_ELAPSED,
    REDEATH_MAX_GAP_SECONDS,
    ROSTER_MIN_GAP,
    ROSTER_PRE_DEATH_OFFSET_SECONDS,
    DeathImportanceFactorId,
)
from splatoon3_ai_coach.coach.coach_input import CoachInput, GameClockSample
from splatoon3_ai_coach.coach.death_importance import (
    detect_death_importance_factors,
    pre_death_roster,
    score_death_candidate,
)
from splatoon3_ai_coach.coach.game_clock import GameClockObservation
from splatoon3_ai_coach.coach.importance_config import (
    importance_config_hash,
    importance_config_payload,
)
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.config.models import CoachConfig, DeathFactorThresholds

_DEATH = 100.0
_Factor = DeathImportanceFactorId


def _point(when: float, ally: int, opponent: int) -> PlayerCountPoint:
    return PlayerCountPoint(
        video_time=when, ally_alive_count=ally, opponent_alive_count=opponent
    )


def _unit(
    trajectory: list[PlayerCountPoint] | None = None,
    *,
    at_death: PlayerCountPoint | None = None,
    prev_death_gap: float | None = None,
    seconds_remaining: int | None = None,
) -> CoachInput:
    scenario = Scenario(
        scenario_id=f"death_episode:{_DEATH:.3f}",
        scenario_type=ScenarioType.DEATH_EPISODE,
        start_time=_DEATH,
        end_time=_DEATH + 8.0,
        event_ids=[f"DEATH@{_DEATH:.3f}"],
        confidence=1.0,
        outcome=ScenarioOutcome.DIED,
    )
    players = None
    if trajectory is not None or at_death is not None:
        players = PlayersEvidence(trajectory=trajectory or [], at_death=at_death)
    samples = []
    if seconds_remaining is not None:
        samples.append(
            GameClockSample(
                label="death",
                video_time=_DEATH,
                observation=GameClockObservation(
                    video_time=_DEATH,
                    seconds_remaining=seconds_remaining,
                    confidence=1.0,
                ),
                gap_seconds=0.0,
            )
        )
    context = ScenarioContext(
        scenario_id=scenario.scenario_id,
        timeline=TimelineContext(duration=8.0, time_since_previous_death=prev_death_gap),
        death_episode=DeathEpisodeContext(death_time=_DEATH, complete=True),
        players=players,
        relations=ScenarioRelations(),
    )
    return CoachInput(
        primary_scenario=scenario, primary_context=context, game_clock_samples=samples
    )


def _active(coach_input: CoachInput, **kwargs) -> set[str]:
    flags = detect_death_importance_factors(coach_input, **kwargs)
    return {factor.value for factor, on in flags.items() if on}


def test_pre_death_sample_ignores_points_after_the_cutoff() -> None:
    coach_input = _unit(
        [_point(95.0, 4, 4), _point(99.8, 3, 4)],
        at_death=_point(100.2, 3, 4),
    )
    sample = pre_death_roster(coach_input, offset_seconds=0.5)
    assert sample is not None
    assert (sample.ally_alive_count, sample.opponent_alive_count) == (4, 4)
    assert sample.source_path == "primary_context.players.trajectory[0]"
    assert _active(coach_input) == set()


def test_pre_death_offset_is_configurable() -> None:
    coach_input = _unit([_point(95.0, 4, 4), _point(99.8, 2, 4)])
    near = DeathFactorThresholds(roster_pre_death_offset_seconds=0.1)
    sample = pre_death_roster(coach_input, offset_seconds=0.1)
    assert sample is not None and sample.ally_alive_count == 2
    assert _active(coach_input) == set()
    assert _Factor.DEATH_WHILE_OUTNUMBERED.value in _active(coach_input, thresholds=near)


def test_outnumbered_before_death() -> None:
    active = _active(_unit([_point(97.0, 2, 4)]))
    assert active == {_Factor.DEATH_WHILE_OUTNUMBERED.value}


def test_ahead_in_numbers_before_death() -> None:
    active = _active(_unit([_point(97.0, 4, 2)]))
    assert active == {_Factor.DEATH_WHILE_AHEAD_IN_NUMBERS.value}


def test_roster_factors_use_the_pre_death_sample_not_at_death() -> None:
    counted_self = _unit([_point(97.0, 3, 4)], at_death=_point(100.3, 2, 4))
    assert _active(counted_self) == set()


def test_roster_gap_threshold() -> None:
    one_behind = _unit([_point(97.0, 3, 4)])
    one_ahead = _unit([_point(97.0, 4, 3)])
    assert _active(one_behind) == set()
    assert _active(one_ahead) == set()
    loose = DeathFactorThresholds(roster_min_gap=1)
    assert _active(one_behind, thresholds=loose) == {
        _Factor.DEATH_WHILE_OUTNUMBERED.value
    }
    assert _active(one_ahead, thresholds=loose) == {
        _Factor.DEATH_WHILE_AHEAD_IN_NUMBERS.value
    }
    strict = DeathFactorThresholds(roster_min_gap=3)
    assert _active(_unit([_point(97.0, 2, 4)]), thresholds=strict) == set()
    assert _active(_unit([_point(97.0, 1, 4)]), thresholds=strict) == {
        _Factor.DEATH_WHILE_OUTNUMBERED.value
    }


def test_even_numbers_activate_no_roster_factor() -> None:
    assert _active(_unit([_point(97.0, 3, 3)])) == set()


def test_missing_roster_evidence_leaves_roster_factors_false() -> None:
    assert _active(_unit()) == set()
    assert _active(_unit([], at_death=_point(100.0, 1, 4))) == set()
    assert _active(_unit([_point(99.9, 2, 4)])) == set()


def test_threshold_overrides_change_results() -> None:
    coach_input = _unit(prev_death_gap=12.0, seconds_remaining=40)
    assert _active(coach_input, match_duration_seconds=300) == set()
    loose = DeathFactorThresholds(redeath_max_gap_seconds=15.0, final_window_seconds=45)
    assert _active(coach_input, match_duration_seconds=300, thresholds=loose) == {
        _Factor.DEATH_REDEATH_LE_10S.value,
        _Factor.DEATH_FINAL_30S.value,
    }
    early = _unit(seconds_remaining=250)
    wide = DeathFactorThresholds(first_window_seconds=60)
    assert _active(early, match_duration_seconds=300) == set()
    assert _active(early, match_duration_seconds=300, thresholds=wide) == {
        _Factor.DEATH_FIRST_30S.value
    }


def test_statements_quote_the_configured_window() -> None:
    unit = score_death_candidate(
        _unit(seconds_remaining=40),
        thresholds=DeathFactorThresholds(final_window_seconds=45),
    )
    final = next(f for f in unit.factors if f.factor_id == "death_final_30s")
    assert final.statement_player == (
        "Death occurred during the final 45 seconds of the match."
    )


def test_roster_statements_are_factual_only() -> None:
    unit = score_death_candidate(_unit([_point(97.0, 2, 4)]))
    factor = next(f for f in unit.factors if f.active)
    assert factor.factor_id == "death_while_outnumbered"
    assert factor.statement_player == (
        "Just before you were splatted, your team had 2 players alive "
        "and the opponents had 4."
    )
    assert "gap >= 2" in (factor.statement_internal or "")
    assert factor.interpretation is None
    assert factor.recommendation is None
    assert unit.importance_score == pytest.approx(2.5)


def test_llm_view_carries_the_pre_death_sample() -> None:
    coach_input = _unit([_point(97.0, 3, 4)], at_death=_point(100.3, 2, 4))
    unit = score_death_candidate(coach_input)
    view = build_coach_llm_view(coach_input, unit)
    assert view.roster is not None
    before = view.roster["before_death"]
    assert (before["ally_alive_count"], before["opponent_alive_count"]) == (3, 4)
    assert before["source_path"] == "primary_context.players.trajectory[0]"
    assert any(fact.label == "Roster before death" for fact in view.supporting_evidence)


def test_negative_weight_fails_config_validation() -> None:
    with pytest.raises(ValidationError, match="must not be negative"):
        CoachConfig(death_importance_weights={"death_while_outnumbered": -1.0})


def test_threshold_defaults_match_catalog_constants() -> None:
    limits = DeathFactorThresholds()
    assert limits.redeath_max_gap_seconds == REDEATH_MAX_GAP_SECONDS
    assert limits.final_window_seconds == FINAL_30S_REMAINING
    assert limits.first_window_seconds == FIRST_30S_ELAPSED
    assert limits.roster_pre_death_offset_seconds == ROSTER_PRE_DEATH_OFFSET_SECONDS
    assert limits.roster_min_gap == ROSTER_MIN_GAP


def test_last_ally_alive_is_not_a_factor() -> None:
    assert "death_last_ally_alive" not in {f.value for f in DeathImportanceFactorId}
    assert "death_last_ally_alive" not in CoachConfig().death_importance_weights


def test_config_hash_is_stable_and_tracks_changes() -> None:
    base = CoachConfig()
    first = importance_config_hash(importance_config_payload(base, max_llm_units=3))
    again = importance_config_hash(
        importance_config_payload(CoachConfig(), max_llm_units=3)
    )
    assert first == again
    weights = dict(base.death_importance_weights, death_while_outnumbered=3.0)
    reweighted = CoachConfig(death_importance_weights=weights)
    moved = CoachConfig(
        death_factor_thresholds=DeathFactorThresholds(final_window_seconds=45)
    )
    hashes = {
        importance_config_hash(importance_config_payload(cfg, max_llm_units=3))
        for cfg in (reweighted, moved)
    }
    assert first not in hashes and len(hashes) == 2
    other_top_n = importance_config_hash(importance_config_payload(base, max_llm_units=5))
    assert other_top_n != first


def test_config_payload_records_values_in_full() -> None:
    payload = importance_config_payload(CoachConfig(), max_llm_units=3)
    assert payload["death_importance_weights"]["death_while_outnumbered"] == 2.5
    assert payload["death_factor_thresholds"]["roster_pre_death_offset_seconds"] == 0.5
    assert payload["max_llm_units"] == 3
