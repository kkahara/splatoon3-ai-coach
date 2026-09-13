"""Deterministic claim selection, VMV formatting, and unit selection policy."""

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
    NO_RECOMMENDATION_MESSAGE,
    ClaimId,
)
from splatoon3_ai_coach.coach.claim_selection import (
    resolve_match_duration_seconds,
    select_coaching_unit,
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


def test_last_ally_emits_full_triad() -> None:
    unit = select_coaching_unit(_death_unit(ally=1, opponent=3))
    assert unit.eligible_claim_ids == [ClaimId.DEATH_LAST_ALLY_ALIVE]
    assert len(unit.coaching_points) == 1
    point = unit.coaching_points[0]
    assert point.claim_id is ClaimId.DEATH_LAST_ALLY_ALIVE
    assert "last ally alive" in point.statement.lower()
    assert point.interpretation is not None
    assert point.recommendation is not None
    assert "disengaged" not in (point.recommendation or "").lower()


def test_special_ready_statement_only() -> None:
    unit = select_coaching_unit(_death_unit(special_ready=True))
    assert ClaimId.DEATH_SPECIAL_READY in unit.eligible_claim_ids
    assert len(unit.coaching_points) == 1
    point = unit.coaching_points[0]
    assert point.claim_id is ClaimId.DEATH_SPECIAL_READY
    assert point.interpretation is None
    assert point.recommendation is None
    player = format_vmv_player(unit)
    assert NO_RECOMMENDATION_MESSAGE in player
    assert "Claim:" not in player
    dev = format_vmv_developer(unit)
    assert "Claim: death_special_ready" in dev
    assert "Interpretation: null" in dev
    assert "Recommendation: null" in dev


def test_last_ally_outranks_special_ready() -> None:
    unit = select_coaching_unit(
        _death_unit(ally=1, opponent=3, special_ready=True, seconds_remaining=20)
    )
    assert ClaimId.DEATH_LAST_ALLY_ALIVE in unit.eligible_claim_ids
    assert ClaimId.DEATH_SPECIAL_READY in unit.eligible_claim_ids
    assert ClaimId.DEATH_FINAL_30S in unit.eligible_claim_ids
    selected = [p.claim_id for p in unit.coaching_points]
    assert selected == [ClaimId.DEATH_LAST_ALLY_ALIVE]
    labels = {item.label for item in unit.supporting_evidence}
    assert "Special" in labels
    assert "Game clock" in labels


def test_redeath_and_clock_gates() -> None:
    unit = select_coaching_unit(
        _death_unit(prev_death_gap=8.0, seconds_remaining=15),
        match_duration_seconds=300,
    )
    assert ClaimId.DEATH_REDEATH_LE_10S in unit.eligible_claim_ids
    assert ClaimId.DEATH_FINAL_30S in unit.eligible_claim_ids
    selected = [p.claim_id for p in unit.coaching_points]
    assert selected == [ClaimId.DEATH_REDEATH_LE_10S]
    assert unit.coaching_points[0].interpretation is None
    assert "8.0 seconds" in unit.coaching_points[0].statement


def test_first_30s_requires_match_duration() -> None:
    without_d = select_coaching_unit(
        _death_unit(seconds_remaining=290),
        match_duration_seconds=None,
    )
    assert ClaimId.DEATH_FIRST_30S not in without_d.eligible_claim_ids
    with_d = select_coaching_unit(
        _death_unit(seconds_remaining=290),
        match_duration_seconds=300,
    )
    assert ClaimId.DEATH_FIRST_30S in with_d.eligible_claim_ids


def test_map_false_eligible_when_observable_false() -> None:
    unit = select_coaching_unit(_death_unit(map_before=False))
    assert ClaimId.DEATH_MAP_OVERLAY_BEFORE_FALSE in unit.eligible_claim_ids


def test_zero_points_when_nothing_useful() -> None:
    unit = select_coaching_unit(_death_unit())
    assert unit.eligible_claim_ids == []
    assert unit.coaching_points == []
    assert "No coaching point selected" in format_vmv_player(unit)


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
def test_sep10_six_deaths_claim_selection() -> None:
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
    selected_by_id: dict[str, list[str]] = {}
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
        unit = select_coaching_unit(
            coach_input, match_duration_seconds=match_duration
        )
        selected_by_id[scenario_id] = [p.claim_id.value for p in unit.coaching_points]
        assert len(unit.coaching_points) <= 3
    assert selected_by_id["death_episode:235.000"] == [
        ClaimId.DEATH_LAST_ALLY_ALIVE.value
    ]
    # Other Sep-10 deaths have no locked high/medium claims in current evidence.
    for sid, selected in selected_by_id.items():
        if sid == "death_episode:235.000":
            continue
        assert selected == [], (sid, selected)
