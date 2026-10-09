"""coach.roster_evidence is one capability boundary for roster claims."""

from __future__ import annotations

from splatoon3_ai_coach.analysis.scenario_context import build_scenario_contexts
from splatoon3_ai_coach.analysis.scenario_evidence import ScenarioEvidencePack
from splatoon3_ai_coach.analysis.scenario_models import ScenarioType
from splatoon3_ai_coach.analysis.scenarios import build_scenarios
from splatoon3_ai_coach.coach.coach_input import (
    CoachInput,
    build_coach_input_for_scenario,
)
from splatoon3_ai_coach.coach.death_importance import score_death_candidate
from splatoon3_ai_coach.coach.game_clock import GameClock
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.config.models import CoachConfig, ScenarioBuilderConfig
from splatoon3_ai_coach.vision.models import (
    GameEvent,
    GameEventReason,
    GameEventType,
    GameStateSnapshot,
)

_DEATH = 70.0


def _snap(t: float, ally: int, opp: int) -> GameStateSnapshot:
    return GameStateSnapshot(
        timestamp=t,
        match_phase="in_match",
        player_lifecycle="alive",
        ally_alive_count=ally,
        opponent_alive_count=opp,
        player_count_confidence=1.0,
    )


def _evidence() -> tuple[list, list, str]:
    events = [
        GameEvent(
            start_time=_DEATH,
            event_type=GameEventType.DEATH,
            reason=GameEventReason.ALIVE_TO_DEAD,
            confidence=1.0,
        )
    ]
    config = ScenarioBuilderConfig()
    scenarios = build_scenarios(events, config)
    pack = ScenarioEvidencePack(
        state_snapshots=[_snap(t, 2, 4) for t in (60.0, 64.0, 68.0, 69.0)]
    )
    contexts = build_scenario_contexts(events, scenarios, config, evidence=pack)
    primary = next(s for s in scenarios if s.scenario_type is ScenarioType.DEATH_EPISODE)
    return scenarios, contexts, primary.scenario_id


def _coach_input(*, roster_evidence: bool) -> CoachInput:
    scenarios, contexts, primary_id = _evidence()
    return build_coach_input_for_scenario(
        primary_id,
        scenarios,
        contexts,
        GameClock(),
        max_gap_seconds=1.0,
        roster_evidence=roster_evidence,
    )


def _active(coach_input: CoachInput) -> set[str]:
    unit = score_death_candidate(coach_input)
    return {f.factor_id for f in unit.factors if f.active}


def test_roster_capability_defaults_off() -> None:
    assert CoachConfig().roster_evidence is False


def test_capability_on_keeps_roster_factor_and_view_block() -> None:
    coach_input = _coach_input(roster_evidence=True)
    assert "death_while_outnumbered" in _active(coach_input)
    view = build_coach_llm_view(coach_input, score_death_candidate(coach_input))
    assert view.roster is not None
    assert view.roster_evidence is True


def test_capability_off_strips_every_roster_surface() -> None:
    coach_input = _coach_input(roster_evidence=False)
    assert coach_input.primary_context.players is None
    assert coach_input.player_count_samples == []
    assert coach_input.player_count_window == []
    assert coach_input.player_count_context is None
    assert "death_while_outnumbered" not in _active(coach_input)
    unit = score_death_candidate(coach_input)
    assert unit.roster_before_death is None
    assert not any("oster" in item.label for item in unit.supporting_evidence)
    view = build_coach_llm_view(coach_input, unit)
    assert view.roster is None
    assert view.roster_evidence is False
    codes = [limit["code"] for limit in view.evidence_limits]
    assert "roster_evidence_disabled" in codes
    assert "player_count_missing" not in codes


def test_capability_off_does_not_mutate_scenario_contexts() -> None:
    scenarios, contexts, primary_id = _evidence()
    build_coach_input_for_scenario(
        primary_id,
        scenarios,
        contexts,
        GameClock(),
        max_gap_seconds=1.0,
        roster_evidence=False,
    )
    primary = next(c for c in contexts if c.scenario_id == primary_id)
    assert primary.players is not None
