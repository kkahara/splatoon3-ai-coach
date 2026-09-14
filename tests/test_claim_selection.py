"""Death importance scoring, type-agnostic top-N, and VMV."""

from __future__ import annotations

from pathlib import Path

import pytest

from splatoon3_ai_coach.analysis.scenario_context import (
    DeathEpisodeContext,
    MapContext,
    ScenarioContext,
    ScenarioRelations,
    TimelineContext,
)
from splatoon3_ai_coach.analysis.scenario_evidence import (
    PlayersEvidence,
    PlayerCountPoint,
    SpecialEvidence,
    SpecialReading,
)
from splatoon3_ai_coach.analysis.scenario_models import (
    Scenario,
    ScenarioOutcome,
    ScenarioType,
)
from splatoon3_ai_coach.coach.claim_catalog import (
    DEFAULT_DEATH_IMPORTANCE_WEIGHTS,
    NO_RECOMMENDATION_MESSAGE,
    ClaimId,
    DeathImportanceFactorId,
)
from splatoon3_ai_coach.coach.coaching_candidates import (
    CoachingCandidate,
    rank_candidates,
)
from splatoon3_ai_coach.coach.death_importance import (
    apply_candidate_ranking,
    detect_death_importance_factors,
    resolve_match_duration_seconds,
    score_death_candidate,
)
from splatoon3_ai_coach.coach.coach_input import CoachInput, GameClockSample
from splatoon3_ai_coach.coach.game_clock import GameClock, GameClockObservation
from splatoon3_ai_coach.coach.load_analysis import (
    load_coach_analysis_bundle,
    select_primary_scenario_ids,
)
from splatoon3_ai_coach.coach.vmv import format_vmv_developer, format_vmv_player
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.coach.coach_input import build_coach_input_for_scenario

_REPO = Path(__file__).resolve().parents[1]
_ANALYSIS_SEP10 = _REPO / "analysis" / "2026-09-10 15-58-36"


def _death_unit(
    *,
    start: float = 100.0,
    ally: int | None = 4,
    opponent: int | None = 4,
    prev_death_gap: float | None = None,
    special_ready: bool = False,
    map_before: bool | None = True,
    seconds_remaining: int | None = 200,
) -> CoachInput:
    scenario = Scenario(
        scenario_id=f"death_episode:{start:.3f}",
        scenario_type=ScenarioType.DEATH_EPISODE,
        start_time=start,
        end_time=start + 8.0,
        event_ids=[f"DEATH@{start:.3f}"],
        confidence=1.0,
        outcome=ScenarioOutcome.DIED,
    )
    players = None
    if ally is not None and opponent is not None:
        players = PlayersEvidence(
            at_death=PlayerCountPoint(
                video_time=start,
                ally_alive_count=ally,
                opponent_alive_count=opponent,
            )
        )
    special = None
    if special_ready:
        special = SpecialEvidence(
            nearest_before_anchor=SpecialReading(
                video_time=start - 0.5,
                visible=True,
                ready=True,
                fill_fraction=1.0,
            )
        )
    samples: list[GameClockSample] = []
    if seconds_remaining is not None:
        samples.append(
            GameClockSample(
                label="death",
                video_time=start,
                observation=GameClockObservation(
                    video_time=start,
                    seconds_remaining=seconds_remaining,
                    confidence=1.0,
                    display=f"{seconds_remaining // 60}:{seconds_remaining % 60:02d}",
                ),
                gap_seconds=0.0,
            )
        )
    context = ScenarioContext(
        scenario_id=scenario.scenario_id,
        timeline=TimelineContext(
            duration=8.0,
            time_since_previous_death=prev_death_gap,
        ),
        map=MapContext(map_check_before_death=map_before),
        death_episode=DeathEpisodeContext(
            death_time=start,
            complete=True,
            is_first_death=prev_death_gap is None,
        ),
        players=players,
        special=special,
        relations=ScenarioRelations(),
    )
    return CoachInput(
        primary_scenario=scenario,
        primary_context=context,
        game_clock_samples=samples,
    )


def _active_ids(unit) -> set[str]:
    return {f.factor_id for f in unit.factors if f.active}


def test_last_ally_factor_and_annotation() -> None:
    unit = score_death_candidate(_death_unit(ally=1, opponent=3))
    assert DeathImportanceFactorId.DEATH_LAST_ALLY_ALIVE.value in _active_ids(unit)
    assert unit.importance_score == pytest.approx(2.5)
    factor = next(f for f in unit.factors if f.active)
    assert "last ally alive" in (factor.statement_player or "").lower()
    assert factor.interpretation is not None
    assert factor.recommendation is not None


def test_special_ready_statement_only_annotation() -> None:
    unit = score_death_candidate(_death_unit(special_ready=True))
    assert ClaimId.DEATH_SPECIAL_READY.value in _active_ids(unit)
    factor = next(f for f in unit.factors if f.active)
    assert factor.interpretation is None
    assert factor.recommendation is None
    ranked = rank_candidates([unit.to_candidate()], max_llm_units=1)
    unit = unit.with_ranking(ranked[0])
    player = format_vmv_player(unit)
    assert NO_RECOMMENDATION_MESSAGE in player
    dev = format_vmv_developer(unit)
    assert "death_special_ready" in dev
    assert "interpretation: null" in dev


def test_weighted_sum_multiple_factors() -> None:
    unit = score_death_candidate(
        _death_unit(ally=1, opponent=3, special_ready=True, seconds_remaining=20),
        match_duration_seconds=300,
    )
    active = _active_ids(unit)
    assert ClaimId.DEATH_LAST_ALLY_ALIVE.value in active
    assert ClaimId.DEATH_SPECIAL_READY.value in active
    assert ClaimId.DEATH_FINAL_30S.value in active
    expected = 2.5 + 2.0 + 1.0
    assert unit.importance_score == pytest.approx(expected)


def test_redeath_and_clock_factors() -> None:
    unit = score_death_candidate(
        _death_unit(prev_death_gap=8.0, seconds_remaining=15),
        match_duration_seconds=300,
    )
    active = _active_ids(unit)
    assert ClaimId.DEATH_REDEATH_LE_10S.value in active
    assert ClaimId.DEATH_FINAL_30S.value in active
    redeath = next(
        f for f in unit.factors if f.factor_id == ClaimId.DEATH_REDEATH_LE_10S.value
    )
    assert "8.0 seconds" in (redeath.statement_player or "")
    assert unit.importance_score == pytest.approx(3.0 + 1.0)


def test_first_30s_requires_match_duration() -> None:
    without_d = detect_death_importance_factors(
        _death_unit(seconds_remaining=290),
        match_duration_seconds=None,
    )
    assert not without_d[DeathImportanceFactorId.DEATH_FIRST_30S]
    with_d = detect_death_importance_factors(
        _death_unit(seconds_remaining=290),
        match_duration_seconds=300,
    )
    assert with_d[DeathImportanceFactorId.DEATH_FIRST_30S]


def test_map_false_factor_when_observable_false() -> None:
    unit = score_death_candidate(_death_unit(map_before=False))
    assert ClaimId.DEATH_MAP_OVERLAY_BEFORE_FALSE.value in _active_ids(unit)
    assert unit.importance_score == pytest.approx(1.5)


def test_zero_score_still_a_candidate() -> None:
    unit = score_death_candidate(_death_unit())
    assert _active_ids(unit) == set()
    assert unit.importance_score == 0.0
    ranked = rank_candidates([unit.to_candidate()], max_llm_units=3)
    assert ranked[0].selected_for_llm is True
    unit = unit.with_ranking(ranked[0])
    assert "selected_for_llm: True" in format_vmv_developer(unit)


def test_rank_candidates_type_agnostic_top_n() -> None:
    mixed = [
        CoachingCandidate(
            candidate_id="death_episode:1.000",
            candidate_type="death_episode",
            video_time=1.0,
            importance_score=5.9,
            factors=[],
        ),
        CoachingCandidate(
            candidate_id="opponent_awareness:3.000",
            candidate_type="opponent_awareness_episode",
            video_time=3.0,
            importance_score=9.2,
            factors=[],
        ),
        CoachingCandidate(
            candidate_id="aggressiveness:2.000",
            candidate_type="aggressiveness_episode",
            video_time=2.0,
            importance_score=7.8,
            factors=[],
        ),
        CoachingCandidate(
            candidate_id="death_episode:7.000",
            candidate_type="death_episode",
            video_time=7.0,
            importance_score=8.5,
            factors=[],
        ),
    ]
    ranked = rank_candidates(mixed, max_llm_units=3)
    selected = [c.candidate_id for c in ranked if c.selected_for_llm]
    assert selected == [
        "opponent_awareness:3.000",
        "death_episode:7.000",
        "aggressiveness:2.000",
    ]
    assert ranked[0].rank == 1
    assert sum(1 for c in ranked if c.selected_for_llm) == 3


def test_tie_break_earlier_video_time() -> None:
    a = CoachingCandidate(
        candidate_id="death_episode:10.000",
        candidate_type="death_episode",
        video_time=10.0,
        importance_score=2.5,
    )
    b = CoachingCandidate(
        candidate_id="death_episode:5.000",
        candidate_type="death_episode",
        video_time=5.0,
        importance_score=2.5,
    )
    ranked = rank_candidates([a, b], max_llm_units=1)
    assert ranked[0].candidate_id == "death_episode:5.000"
    assert ranked[0].selected_for_llm is True
    assert ranked[1].selected_for_llm is False


def test_weight_override_changes_score() -> None:
    weights = dict(DEFAULT_DEATH_IMPORTANCE_WEIGHTS)
    weights["death_last_ally_alive"] = 10.0
    unit = score_death_candidate(
        _death_unit(ally=1, opponent=3),
        weights=weights,
    )
    assert unit.importance_score == pytest.approx(10.0)


def test_resolve_match_duration_from_clock_peak() -> None:
    clock = GameClock(
        observations=(
            GameClockObservation(
                video_time=10.0, seconds_remaining=300, confidence=1.0
            ),
            GameClockObservation(
                video_time=100.0, seconds_remaining=200, confidence=1.0
            ),
        )
    )
    assert resolve_match_duration_seconds(clock) == 300


def test_select_primary_death_only_by_default() -> None:
    scenarios = [
        Scenario(
            scenario_id="death_episode:1.000",
            scenario_type=ScenarioType.DEATH_EPISODE,
            start_time=1.0,
            end_time=2.0,
            event_ids=["a"],
            confidence=1.0,
            outcome=ScenarioOutcome.DIED,
        ),
        Scenario(
            scenario_id="engagement:3.000",
            scenario_type=ScenarioType.ENGAGEMENT,
            start_time=3.0,
            end_time=4.0,
            event_ids=["b"],
            confidence=1.0,
            outcome=ScenarioOutcome.FRAGGED,
        ),
        Scenario(
            scenario_id="map_check:5.000",
            scenario_type=ScenarioType.MAP_CHECK,
            start_time=5.0,
            end_time=6.0,
            event_ids=["c"],
            confidence=1.0,
            outcome=ScenarioOutcome.OBSERVED,
        ),
    ]
    ids = select_primary_scenario_ids(scenarios, limit=10)
    assert ids == ["death_episode:1.000"]
    filled = select_primary_scenario_ids(
        scenarios, limit=10, only_preferred=False
    )
    assert filled[0] == "death_episode:1.000"
    assert "engagement:3.000" in filled


@pytest.mark.skipif(
    not (_ANALYSIS_SEP10 / "vision_manifest.json").is_file(),
    reason="Sep-10 analysis missing",
)
def test_sep10_six_deaths_importance_top_n() -> None:
    config = load_config(default_config_path())
    bundle = load_coach_analysis_bundle(
        _ANALYSIS_SEP10,
        min_usable_confidence=config.vision.timer.min_usable_confidence,
    )
    ids = select_primary_scenario_ids(bundle.scenarios, limit=20)
    assert len(ids) == 6
    assert all(item.startswith("death_episode:") for item in ids)
    match_duration = resolve_match_duration_seconds(
        bundle.game_clock,
        candidates=tuple(config.vision.lifecycle.opening_clock_seconds),
    )
    assert match_duration == 300
    scored = []
    for scenario_id in ids:
        coach_input = build_coach_input_for_scenario(
            scenario_id,
            bundle.scenarios,
            bundle.contexts,
            bundle.game_clock,
            max_gap_seconds=config.coach.game_clock_max_lookup_gap_seconds,
            player_count_clock=bundle.player_count_clock,
            player_count_max_gap_seconds=(
                config.coach.player_count_max_lookup_gap_seconds
            ),
            player_count_window_offsets_seconds=(
                config.coach.player_count_window_offsets_seconds
            ),
            player_count_context_lookback_seconds=(
                config.coach.player_count_context_lookback_seconds
            ),
        )
        scored.append(
            score_death_candidate(
                coach_input,
                match_duration_seconds=match_duration,
                weights=config.coach.death_importance_weights,
            )
        )
    ranked = rank_candidates(
        [u.to_candidate() for u in scored],
        max_llm_units=config.coach.max_llm_units,
    )
    units = apply_candidate_ranking(scored, ranked)
    selected = [u for u in units if u.selected_for_llm]
    assert len(selected) == 3
    by_id = {u.candidate_id: u for u in units}
    assert by_id["death_episode:235.000"].importance_score >= 2.5
    assert ClaimId.DEATH_LAST_ALLY_ALIVE.value in _active_ids(
        by_id["death_episode:235.000"]
    )
    assert by_id["death_episode:235.000"].selected_for_llm is True
    # All deaths are candidates (scored), not gated out of existence.
    assert all(u.rank is not None for u in units)
