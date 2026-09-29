"""Pre-death roster sampling, roster factors, thresholds, config identity."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from splatoon3_ai_coach.analysis.scenario_context import (
    DeathEpisodeContext,
    MapContext,
    ScenarioContext,
    ScenarioRelations,
    TimelineContext,
)
from splatoon3_ai_coach.analysis.scenario_evidence import (
    PlayerCountPoint,
    PlayersEvidence,
    SpecialEvidence,
    SpecialReading,
)
from splatoon3_ai_coach.analysis.scenario_models import (
    Scenario,
    ScenarioOutcome,
    ScenarioType,
)
from splatoon3_ai_coach.analysis.score_context import ScoreEvidence, ScoreSample
from splatoon3_ai_coach.coach.claim_catalog import (
    COUNT_MIN_DIFF,
    DEFAULT_DEATH_MODIFIER_FACTORS,
    DEFAULT_DEATH_RANKING_EXCLUDED_FACTORS,
    FINAL_30S_REMAINING,
    FIRST_30S_ELAPSED,
    REDEATH_MAX_GAP_SECONDS,
    ROSTER_MIN_GAP,
    ROSTER_PRE_DEATH_OFFSET_SECONDS,
    SPECIAL_READY_MIN_READINGS,
    DeathImportanceFactorId,
)
from splatoon3_ai_coach.coach.coach_input import CoachInput, GameClockSample
from splatoon3_ai_coach.coach.coaching_candidates import rank_candidates
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
from splatoon3_ai_coach.coach.score_facts import derive_score_facts
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
    coach_input = _unit(prev_death_gap=22.0, seconds_remaining=40)
    assert _active(coach_input, match_duration_seconds=300) == set()
    loose = DeathFactorThresholds(redeath_max_gap_seconds=25.0, final_window_seconds=45)
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


def test_redeath_default_covers_the_respawn_cycle() -> None:
    assert _active(_unit(prev_death_gap=12.0)) == {_Factor.DEATH_REDEATH_LE_10S.value}
    assert _active(_unit(prev_death_gap=20.5)) == set()


def _with_no_map_check(coach_input: CoachInput) -> CoachInput:
    context = coach_input.primary_context.model_copy(
        update={"map": MapContext(map_check_before_death=False)}
    )
    return coach_input.model_copy(update={"primary_context": context})


def test_no_map_check_is_not_counted_in_the_first_window() -> None:
    early = _with_no_map_check(_unit(seconds_remaining=290))
    later = _with_no_map_check(_unit(seconds_remaining=200))
    assert _active(early, match_duration_seconds=300) == {_Factor.DEATH_FIRST_30S.value}
    assert _active(later, match_duration_seconds=300) == {
        _Factor.DEATH_MAP_OVERLAY_BEFORE_FALSE.value
    }


def test_ranking_excluded_factors_stay_active_but_do_not_score() -> None:
    map_only = score_death_candidate(_with_no_map_check(_unit(seconds_remaining=200)))
    map_factor = next(
        f for f in map_only.factors if f.factor_id == "death_map_overlay_before_false"
    )
    assert map_factor.active is True
    assert map_factor.contribution == 0.0
    assert map_only.importance_score == 0.0

    special_only = score_death_candidate(_with_special(_unit(), [True, True]))
    special = next(
        f for f in special_only.factors if f.factor_id == "death_special_ready"
    )
    assert special.active is True
    assert special.contribution == 0.0
    assert special_only.importance_score == 0.0

    restored = score_death_candidate(
        _with_no_map_check(_unit(seconds_remaining=200)),
        ranking_excluded_factors=(),
    )
    assert restored.importance_score == pytest.approx(1.5)


def test_zero_score_candidates_are_not_selected_when_required() -> None:
    scored = score_death_candidate(_unit([_point(97.0, 2, 4)])).to_candidate()
    zero = score_death_candidate(
        _unit([_point(97.0, 3, 3)]).model_copy(
            update={
                "primary_scenario": _unit().primary_scenario.model_copy(
                    update={"scenario_id": "death_episode:50.000", "start_time": 50.0}
                )
            }
        )
    ).to_candidate()
    assert zero.importance_score == 0
    plain = rank_candidates([scored, zero], max_llm_units=3)
    strict = rank_candidates([scored, zero], max_llm_units=3, require_positive_score=True)
    assert [c.selected_for_llm for c in plain] == [True, True]
    assert [c.selected_for_llm for c in strict] == [True, False]


def test_positive_score_rule_is_part_of_the_config_hash() -> None:
    base = CoachConfig()
    assert base.llm_units_require_positive_score is True
    loose = base.model_copy(update={"llm_units_require_positive_score": False})
    assert importance_config_hash(
        importance_config_payload(base, max_llm_units=3)
    ) != importance_config_hash(importance_config_payload(loose, max_llm_units=3))


def test_clock_windows_only_add_to_a_supported_death() -> None:
    alone = score_death_candidate(_unit(seconds_remaining=20))
    final = next(f for f in alone.factors if f.factor_id == "death_final_30s")
    assert final.active is True
    assert final.contribution == 0.0
    assert alone.importance_score == 0.0
    supported = score_death_candidate(_unit([_point(97.0, 2, 4)], seconds_remaining=20))
    assert supported.importance_score == pytest.approx(2.5 + 1.0)
    standalone = score_death_candidate(_unit(seconds_remaining=20), modifier_factors=())
    assert standalone.importance_score == pytest.approx(1.0)


def _with_special(coach_input: CoachInput, ready: list[bool]) -> CoachInput:
    readings = [
        SpecialReading(video_time=_DEATH - len(ready) + i, visible=True, ready=on)
        for i, on in enumerate(ready)
    ]
    special = SpecialEvidence(observations=readings, nearest_before_anchor=readings[-1])
    context = coach_input.primary_context.model_copy(update={"special": special})
    return coach_input.model_copy(update={"primary_context": context})


def test_special_ready_needs_consecutive_readings() -> None:
    ready = _Factor.DEATH_SPECIAL_READY.value
    assert ready not in _active(_with_special(_unit(), [False, True]))
    assert ready in _active(_with_special(_unit(), [False, True, True]))
    assert ready not in _active(_with_special(_unit(), [True, True, False]))
    single = DeathFactorThresholds(special_ready_min_readings=1)
    assert ready in _active(_with_special(_unit(), [False, True]), thresholds=single)


def _with_count(
    coach_input: CoachInput,
    ally: int,
    opponent: int,
    *,
    mode: str = "splat_zones",
    lookback_opponent: int | None = None,
) -> CoachInput:
    pre = ScoreSample(
        video_time=_DEATH - 0.5,
        ally_remaining=ally,
        ally_score_quality="observed",
        opponent_remaining=opponent,
        opponent_score_quality="observed",
    )
    lookback = None
    if lookback_opponent is not None:
        lookback = pre.model_copy(
            update={"video_time": _DEATH - 5.5, "opponent_remaining": lookback_opponent}
        )
    context = coach_input.primary_context.model_copy(
        update={"score": ScoreEvidence(pre_death=pre, lookback=lookback)}
    )
    facts = derive_score_facts(context, battle_mode_id=mode)
    return coach_input.model_copy(
        update={"primary_context": context, "score_facts": facts}
    )


def test_count_factors_are_observed_but_excluded() -> None:
    behind = _with_count(_unit(), 60, 40, lookback_opponent=44)
    assert _active(behind) == {
        _Factor.DEATH_WHILE_BEHIND_IN_COUNT.value,
        _Factor.DEATH_OPPONENT_COUNTER_TICKED.value,
    }
    unit = score_death_candidate(behind)
    assert unit.importance_score == 0.0
    factor = next(f for f in unit.factors if f.factor_id == "death_while_behind_in_count")
    assert factor.active and factor.contribution == 0.0
    assert factor.statement_player == (
        "Just before you were splatted, your team needed 60 more counts "
        "and the opponents needed 40."
    )
    assert _Factor.DEATH_WHILE_AHEAD_IN_COUNT.value in _active(
        _with_count(_unit(), 30, 45)
    )
    assert _active(_with_count(_unit(), 40, 45)) == set()


def test_count_factors_are_mode_scoped() -> None:
    assert _active(_with_count(_unit(), 60, 40, mode="tower_control")) == set()


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
    assert limits.special_ready_min_readings == SPECIAL_READY_MIN_READINGS
    assert limits.count_min_diff == COUNT_MIN_DIFF
    assert tuple(CoachConfig().death_modifier_factors) == DEFAULT_DEATH_MODIFIER_FACTORS
    assert (
        tuple(CoachConfig().death_ranking_excluded_factors)
        == DEFAULT_DEATH_RANKING_EXCLUDED_FACTORS
    )


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
    assert payload["death_ranking_excluded_factors"] == sorted(
        DEFAULT_DEATH_RANKING_EXCLUDED_FACTORS
    )
