"""Roster plausibility: rises no earlier death explains are withheld downstream."""

from __future__ import annotations

from dataclasses import dataclass

from public_site.results import _roster_line

from splatoon3_ai_coach.analysis.player_count_series import (
    PlayerCountObservation,
    assess_roster_plausibility,
)
from splatoon3_ai_coach.analysis.scenario_context import build_scenario_context
from splatoon3_ai_coach.analysis.scenario_evidence import ScenarioEvidencePack
from splatoon3_ai_coach.analysis.scenario_models import ScenarioType
from splatoon3_ai_coach.analysis.scenarios import build_scenarios
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.death_importance import (
    build_supporting_evidence,
    score_death_candidate,
)
from splatoon3_ai_coach.coach.evidence_limits import collect_evidence_limits
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.coach.player_count_clock import (
    PlayerCountObservation as ClockObservation,
)
from splatoon3_ai_coach.coach.player_count_clock import PlayerCountWindowPoint
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig
from splatoon3_ai_coach.vision.models import (
    GameEvent,
    GameEventReason,
    GameEventType,
    GameStateSnapshot,
)

_UNRELIABLE = "Roster information around this death was unreliable."
_ROSTER_FACTORS = {"death_while_outnumbered", "death_while_ahead_in_numbers"}


def _counts(changes: list[tuple[float, int]], t: float) -> int:
    value = changes[0][1]
    for when, count in changes:
        if when <= t:
            value = count
    return value


def _snapshots(
    ally: list[tuple[float, int]], opponent: list[tuple[float, int]], end: float = 60.0
) -> list[GameStateSnapshot]:
    out = []
    for step in range(int(end * 2) + 1):
        t = step / 2
        out.append(
            GameStateSnapshot(
                timestamp=t,
                ally_alive_count=_counts(ally, t),
                opponent_alive_count=_counts(opponent, t),
                player_count_confidence=0.99,
            )
        )
    return out


def _event(t: float, kind: GameEventType, reason: GameEventReason) -> GameEvent:
    return GameEvent(start_time=t, event_type=kind, reason=reason, confidence=1.0)


@dataclass
class _Outcome:
    reliable: bool
    at_death: object
    numbers: object
    limit: bool
    factors: set[str]
    support: dict[str, str]
    view_window_counts: list[object]
    public: str


def _run(
    ally: list[tuple[float, int]],
    opponent: list[tuple[float, int]],
    death: float,
) -> _Outcome:
    events = [
        _event(death, GameEventType.DEATH, GameEventReason.ALIVE_TO_DEAD),
        _event(death + 6, GameEventType.RESPAWN, GameEventReason.COUNTDOWN_PLATE_ENDED),
        _event(
            death + 8,
            GameEventType.ACTIVE_AGAIN,
            GameEventReason.AWAITING_CONTROL_TO_ALIVE,
        ),
    ]
    config = ScenarioBuilderConfig()
    scenario = next(
        s
        for s in build_scenarios(events, config)
        if s.scenario_type is ScenarioType.DEATH_EPISODE
    )
    pack = ScenarioEvidencePack(state_snapshots=_snapshots(ally, opponent))
    context = build_scenario_context(events, scenario, config, evidence=pack)
    limits = collect_evidence_limits(scenario, context, [])
    window = [
        PlayerCountWindowPoint(
            offset_seconds=offset,
            video_time=death + offset,
            observation=ClockObservation(
                video_time=death + offset,
                ally_alive_count=_counts(ally, death + offset),
                opponent_alive_count=_counts(opponent, death + offset),
                confidence=0.99,
            ),
            gap_seconds=0.0,
        )
        for offset in (-10.0, -1.0, 0.0)
        if death + offset >= 0
    ]
    coach_input = CoachInput(
        primary_scenario=scenario,
        primary_context=context,
        evidence_limits=limits,
        player_count_window=window,
    )
    unit = score_death_candidate(coach_input)
    view = build_coach_llm_view(coach_input, unit)
    players = context.players
    return _Outcome(
        reliable=not (players is not None and players.unreliable_at(death)),
        at_death=players.at_death if players else None,
        numbers=context.death_episode.numbers_state_at_death,
        limit=any(item.code == "player_count_implausible" for item in limits),
        factors={f.factor_id for f in unit.factors if f.active},
        support={i.label: i.value for i in build_supporting_evidence(coach_input)},
        view_window_counts=[
            (p["ally_alive_count"], p["reliable"])
            for p in (view.roster or {}).get("player_count_window", [])
        ],
        public=_roster_line({"primary_context": context.model_dump(mode="json")}),
    )


def _assert_reliable(outcome: _Outcome) -> None:
    assert outcome.reliable
    assert outcome.at_death is not None
    assert outcome.numbers is not None
    assert not outcome.limit
    assert "Roster at death" in outcome.support
    assert outcome.public.startswith("Roster at death:")


def _assert_unreliable(outcome: _Outcome) -> None:
    assert not outcome.reliable
    assert outcome.at_death is None
    assert outcome.numbers is None
    assert outcome.limit
    assert not outcome.factors & _ROSTER_FACTORS
    assert "Roster at death" not in outcome.support
    assert "Numbers state" not in outcome.support
    assert "Roster before death" not in outcome.support
    assert outcome.public == _UNRELIABLE


def test_respawn_after_valid_delay_is_reliable() -> None:
    _assert_reliable(_run([(0, 4), (30, 3), (36, 4)], [(0, 4)], death=40.0))


def test_respawn_too_fast_is_unreliable() -> None:
    outcome = _run([(0, 4), (38, 3), (39, 4)], [(0, 2)], death=40.0)
    _assert_unreliable(outcome)
    # 4v2 at the death would otherwise activate "ahead in numbers".
    assert "death_while_ahead_in_numbers" not in outcome.factors


def test_two_to_one_to_two_depends_on_timing() -> None:
    _assert_reliable(_run([(0, 4), (30, 2), (35, 1), (38.5, 2)], [(0, 4)], death=40.0))
    _assert_unreliable(
        _run([(0, 4), (37.5, 2), (38, 1), (38.5, 2)], [(0, 4)], death=40.0)
    )


def test_simultaneous_valid_respawns_are_reliable() -> None:
    _assert_reliable(_run([(0, 4), (30, 2), (36, 4)], [(0, 4)], death=40.0))


def test_one_reading_oscillation_is_unreliable_only_nearby() -> None:
    ally = [(0, 4), (20, 3), (20.5, 4)]
    _assert_reliable(_run(ally, [(0, 4)], death=40.0))
    _assert_unreliable(_run(ally, [(0, 4)], death=21.0))


def test_initial_deficit_counts_from_series_start() -> None:
    observations = [
        PlayerCountObservation(
            video_time=t, ally_alive_count=a, opponent_alive_count=4, confidence=0.99
        )
        for t, a in [(0.0, 3), (2.0, 4)]
    ]
    assert assess_roster_plausibility(observations, min_respawn_seconds=3.0)
    later = [o.model_copy(update={"video_time": 5.0}) if o.video_time else o
             for o in observations]
    assert assess_roster_plausibility(later, min_respawn_seconds=3.0) == []
    _assert_unreliable(_run([(0, 3), (2, 4)], [(0, 4)], death=3.0))
    _assert_reliable(_run([(0, 3), (5, 4)], [(0, 4)], death=12.0))


def test_recovery_after_interval() -> None:
    ally = [(0, 4), (19.5, 3), (20, 4)]
    _assert_unreliable(_run(ally, [(0, 4)], death=22.0))
    _assert_reliable(_run(ally, [(0, 4)], death=24.0))


def test_death_exactly_on_interval_boundary_is_unreliable() -> None:
    _assert_unreliable(_run([(0, 4), (19.5, 3), (20, 4)], [(0, 4)], death=23.0))


def test_view_window_points_are_nulled_inside_interval() -> None:
    outcome = _run([(0, 4), (38, 3), (39, 4)], [(0, 4)], death=40.0)
    # Offsets -10, -1, 0 → 30.0 (outside [36, 42]), 39.0 and 40.0 (inside).
    assert outcome.view_window_counts == [(4, True), (None, False), (None, False)]
