"""Derive coaching-relevant facts from Scenarios and the GameEvent timeline.

Facts and measurements only. No coaching judgments. Scenario grouping stays
in ``scenarios.py``. Sparse secondary evidence (map ink, roster, special)
comes from persisted artifacts via ``ScenarioEvidencePack`` — not detectors.
LOW_INK intervals come from the event timeline under an explicit overlap
rule (``low_ink_context``) and never become scenario members.

Measurement helpers live in sibling modules; this module owns the
``ScenarioContext`` models and the build/serialize facade.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.combat_context import combat_context as _combat_context
from splatoon3_ai_coach.analysis.death_episode_context import (
    death_episode_context as _death_episode_context,
)
from splatoon3_ai_coach.analysis.death_episode_context import (
    enrich_death_episode_level2 as _enrich_death_episode_level2,
)
from splatoon3_ai_coach.analysis.low_ink_context import (
    LowInkEvidence,
    build_low_ink_evidence,
)
from splatoon3_ai_coach.analysis.map_overlay_context import map_context as _map_context
from splatoon3_ai_coach.analysis.player_count_series import NumbersState
from splatoon3_ai_coach.analysis.scenario_evidence import (
    MapInkEvidence,
    PlayersEvidence,
    ScenarioEvidencePack,
    SpecialEvidence,
    build_map_ink_evidence,
    build_players_evidence,
    build_special_evidence,
    scenario_anchor_time,
    scenario_window,
)
from splatoon3_ai_coach.analysis.scenario_models import Scenario, ScenarioType
from splatoon3_ai_coach.analysis.scenario_relations import (
    attach_relations as _attach_relations,
)
from splatoon3_ai_coach.analysis.scenarios import event_id
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig
from splatoon3_ai_coach.media.video_source import Observability
from splatoon3_ai_coach.vision.models import (
    GameEvent,
    GameEventType,
    GameStateSnapshot,
    MatchPhase,
)


class TimelineContext(BaseModel):
    """Elapsed duration and neighboring DEATH distances."""

    duration: float
    time_since_previous_death: float | None = None
    time_to_next_death: float | None = None


class MapContext(BaseModel):
    """MAP_OVERLAY relationships. Counts are facts, not usage quality.

    ``ink`` is sparse map-observation evidence and never sets overlay
    ``map_check_*`` fields.
    """

    map_check_count: int = 0
    map_checks_before_start: int = 0
    map_checks_during_scenario: int = 0
    map_checks_during_death_episode: int | None = None
    map_check_before_death: bool | None = None
    seconds_since_map_check_before_death: float | None = None
    map_checked_while_dead: bool | None = None
    last_map_before_death_event_id: str | None = None
    map_event_ids_during_episode: list[str] | None = None
    in_death_episode: bool | None = None
    ink: MapInkEvidence | None = None


class CombatContext(BaseModel):
    """SPLAT timing for an ``ENGAGEMENT``. Absent (null) on death episodes."""

    splat_count: int = 0
    first_splat_time: float | None = None
    last_splat_time: float | None = None
    duration: float | None = None
    # Legacy: unused for engagements; kept for older serializers/tests.
    time_to_first_splat: float | None = None
    # Legacy alias of ``duration`` for engagements (first→last splat span).
    time_to_last_splat: float | None = None
    splat_death_gap: float | None = None
    trade_candidate: bool = False


class DeathEpisodeContext(BaseModel):
    """Lifecycle timings + Level 2 factual convenience for a ``DEATH_EPISODE``.

    Level 1 durations stay arithmetic over owned event timestamps and remain
    ``None`` when an endpoint is missing. Level 2 fields are convenience
    lookups only — not coaching judgments. Special usage stays unresolved.
    """

    death_time: float | None = None
    respawn_time: float | None = None
    active_again_time: float | None = None
    awaiting_start: float | None = None
    awaiting_end: float | None = None
    awaiting_duration: float | None = None
    death_to_respawn: float | None = None
    death_to_active_again: float | None = None
    respawn_to_active_again: float | None = None
    has_respawn: bool = False
    has_active_again: bool = False
    complete: bool = False
    respawn_reason: str | None = None
    # Level 2 convenience facts (death episodes only).
    is_first_death: bool | None = None
    time_since_previous_splat: float | None = None
    match_phase_at_death: MatchPhase | None = None
    numbers_state_at_death: NumbersState | None = None
    roster_changed_before_death: bool | None = None
    seconds_since_roster_change: float | None = None
    preceded_by_trade_candidate: bool | None = None


class ScenarioRelations(BaseModel):
    """Semantic links between coaching episodes. Not generic adjacency.

    Authoritative for engagement↔death-episode relationships. Engagement
    ``Scenario.context['following_death_id']`` remains as a GameEvent-level
    compatibility field only.
    """

    leads_to_death_episode_id: str | None = None
    follows_death_episode_id: str | None = None
    preceded_by_engagement_id: str | None = None
    next_engagement_id: str | None = None


# Backwards-compatible alias for imports that still say RecoveryContext.
RecoveryContext = DeathEpisodeContext


class ScenarioContext(BaseModel):
    """Facts measured for one Scenario from events + sparse secondary evidence."""

    scenario_id: str
    timeline: TimelineContext | None = None
    map: MapContext | None = None
    combat: CombatContext | None = None
    death_episode: DeathEpisodeContext | None = None
    players: PlayersEvidence | None = None
    special: SpecialEvidence | None = None
    low_ink: LowInkEvidence | None = None
    relations: ScenarioRelations = Field(default_factory=ScenarioRelations)


_IMPLEMENTED = frozenset(
    {
        ScenarioType.DEATH_EPISODE,
        ScenarioType.ENGAGEMENT,
        ScenarioType.MAP_CHECK,
    }
)


def build_scenario_context(
    events: list[GameEvent],
    scenario: Scenario,
    config: ScenarioBuilderConfig,
    evidence: ScenarioEvidencePack | None = None,
    *,
    map_overlay_observability: Observability = Observability.OBSERVABLE,
) -> ScenarioContext:
    """Measure facts for one scenario (relations filled by the batch builder)."""
    ordered = _ordered(events)
    timeline = _timeline(ordered, scenario)
    death_episode = _death_episode_context(ordered, scenario)
    map_ctx = _map_context(
        ordered, scenario, map_overlay_observability=map_overlay_observability
    )
    players = None
    special = None
    snapshots: list[GameStateSnapshot] = []
    if evidence is not None and scenario.scenario_type in _IMPLEMENTED:
        window_start, window_end = scenario_window(scenario, config)
        death_time = (
            death_episode.death_time if death_episode is not None else None
        )
        anchor = scenario_anchor_time(scenario, death_time=death_time)
        max_gap = float(config.context_max_gap_seconds)
        snapshots = list(evidence.state_snapshots)
        ink = build_map_ink_evidence(
            evidence.map_observations,
            window_start=window_start,
            window_end=window_end,
            anchor=anchor,
            max_gap_seconds=max_gap,
        )
        if map_ctx is None and ink is not None:
            map_ctx = MapContext(ink=ink)
        elif map_ctx is not None:
            map_ctx = map_ctx.model_copy(update={"ink": ink})
        players = build_players_evidence(
            evidence.state_snapshots,
            window_start=window_start,
            window_end=window_end,
            anchor=anchor,
            death_time=death_time
            if scenario.scenario_type is ScenarioType.DEATH_EPISODE
            else None,
            max_gap_seconds=max_gap,
        )
        special = build_special_evidence(
            evidence.special_readings,
            window_start=window_start,
            window_end=window_end,
            anchor=anchor,
            max_gap_seconds=max_gap,
        )
    if death_episode is not None:
        death_episode = _enrich_death_episode_level2(
            death_episode,
            timeline=timeline,
            events=ordered,
            players=players,
            snapshots=snapshots,
            config=config,
        )
    return ScenarioContext(
        scenario_id=scenario.scenario_id,
        timeline=timeline,
        map=map_ctx,
        combat=_combat_context(ordered, scenario, config),
        death_episode=death_episode,
        players=players,
        special=special,
        low_ink=build_low_ink_evidence(ordered, scenario),
        relations=ScenarioRelations(),
    )


def build_scenario_contexts(
    events: list[GameEvent],
    scenarios: list[Scenario],
    config: ScenarioBuilderConfig,
    evidence: ScenarioEvidencePack | None = None,
    *,
    map_overlay_observability: Observability = Observability.OBSERVABLE,
) -> list[ScenarioContext]:
    """Build contexts then attach semantic engagement↔death-episode relations."""
    contexts = [
        build_scenario_context(
            events,
            scenario,
            config,
            evidence=evidence,
            map_overlay_observability=map_overlay_observability,
        )
        for scenario in scenarios
    ]
    return _attach_relations(contexts, scenarios, config)


def serialize_scenario_contexts(contexts: list[ScenarioContext]) -> list[dict]:
    """Deterministic JSON-ready dumps with explicit nulls."""
    return [item.model_dump(mode="json") for item in contexts]


def _timeline(events: list[GameEvent], scenario: Scenario) -> TimelineContext:
    """Duration and neighboring deaths relative to scenario start."""
    deaths = _of_type(events, GameEventType.DEATH)
    previous = _last_before(deaths, scenario.start_time)
    following = _first_after(deaths, scenario.start_time)
    return TimelineContext(
        duration=scenario.end_time - scenario.start_time,
        time_since_previous_death=_delta(scenario.start_time, previous),
        time_to_next_death=_delta(following, scenario.start_time),
    )


def _ordered(events: Iterable[GameEvent]) -> list[GameEvent]:
    """Stable order: time, type, derived id."""
    return sorted(
        events,
        key=lambda event: (event.start_time, event.event_type.value, event_id(event)),
    )


def _of_type(events: list[GameEvent], event_type: GameEventType) -> list[GameEvent]:
    """Events of one type, preserving caller order."""
    return [event for event in events if event.event_type is event_type]


def _event_at(
    events: list[GameEvent], event_type: GameEventType, timestamp: float
) -> GameEvent | None:
    """First event of ``event_type`` at exactly ``timestamp``."""
    for event in events:
        if event.event_type is event_type and event.start_time == timestamp:
            return event
    return None


def _next_death_time(events: list[GameEvent], timestamp: float) -> float:
    """Start of the next DEATH after ``timestamp``, or +inf."""
    following = _first_after(_of_type(events, GameEventType.DEATH), timestamp)
    if following is None:
        return float("inf")
    return following.start_time


def _first_between(
    events: list[GameEvent],
    event_type: GameEventType,
    start: float,
    end: float,
) -> GameEvent | None:
    """First event of ``event_type`` with ``start < t < end``."""
    for event in events:
        if event.event_type is event_type and start < event.start_time < end:
            return event
    return None


def _last_before(events: list[GameEvent], timestamp: float) -> GameEvent | None:
    """Last event whose start is strictly before ``timestamp``."""
    prior = [event for event in events if event.start_time < timestamp]
    if not prior:
        return None
    return prior[-1]


def _first_after(events: list[GameEvent], timestamp: float) -> GameEvent | None:
    """First event whose start is strictly after ``timestamp``."""
    later = [event for event in events if event.start_time > timestamp]
    if not later:
        return None
    return later[0]


def _delta(
    later: GameEvent | float | None, earlier: GameEvent | float | None
) -> float | None:
    """``later - earlier`` when both sides exist."""
    if later is None or earlier is None:
        return None
    later_at = later if isinstance(later, float) else later.start_time
    earlier_at = earlier if isinstance(earlier, float) else earlier.start_time
    return later_at - earlier_at
