"""Derive coaching-relevant facts from Scenarios and the GameEvent timeline.

Facts and measurements only. No coaching judgments. Does not import
detectors, snapshots, or ``vision.events``.

Scenario grouping stays in ``scenarios.py``. This module measures facts and
semantic relationships (engagement ↔ death episode) for the coaching layer.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.scenario_models import Scenario, ScenarioType
from splatoon3_ai_coach.analysis.scenarios import event_id
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig
from splatoon3_ai_coach.vision.models import GameEvent, GameEventType


class TimelineContext(BaseModel):
    """Elapsed duration and neighboring DEATH distances."""

    duration: float
    time_since_previous_death: float | None = None
    time_to_next_death: float | None = None


class MapContext(BaseModel):
    """MAP_OVERLAY relationships. Counts are facts, not usage quality."""

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
    """Lifecycle timings for a ``DEATH_EPISODE``. Not Super Jump inference."""

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
    """Facts measured for one Scenario from the GameEvent timeline."""

    scenario_id: str
    timeline: TimelineContext | None = None
    map: MapContext | None = None
    combat: CombatContext | None = None
    death_episode: DeathEpisodeContext | None = None
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
) -> ScenarioContext:
    """Measure facts for one scenario (relations filled by the batch builder)."""
    ordered = _ordered(events)
    return ScenarioContext(
        scenario_id=scenario.scenario_id,
        timeline=_timeline(ordered, scenario),
        map=_map_context(ordered, scenario),
        combat=_combat_context(ordered, scenario, config),
        death_episode=_death_episode_context(ordered, scenario),
        relations=ScenarioRelations(),
    )


def build_scenario_contexts(
    events: list[GameEvent],
    scenarios: list[Scenario],
    config: ScenarioBuilderConfig,
) -> list[ScenarioContext]:
    """Build contexts then attach semantic engagement↔death-episode relations."""
    contexts = [
        build_scenario_context(events, scenario, config) for scenario in scenarios
    ]
    return _attach_relations(contexts, scenarios, config)


def serialize_scenario_contexts(contexts: list[ScenarioContext]) -> list[dict]:
    """Deterministic JSON-ready dumps with explicit nulls."""
    return [item.model_dump(mode="json") for item in contexts]


def _attach_relations(
    contexts: list[ScenarioContext],
    scenarios: list[Scenario],
    config: ScenarioBuilderConfig,
) -> list[ScenarioContext]:
    """Wire ENGAGEMENT ↔ DEATH_EPISODE links. No grouping changes."""
    by_id = {item.scenario_id: item for item in contexts}
    deaths = [
        item for item in scenarios if item.scenario_type is ScenarioType.DEATH_EPISODE
    ]
    engagements = [
        item for item in scenarios if item.scenario_type is ScenarioType.ENGAGEMENT
    ]
    death_by_time = {item.start_time: item.scenario_id for item in deaths}
    window = config.engagement_include_following_death_seconds

    leads: dict[str, str] = {}
    for engagement in engagements:
        death_episode_id = _engagement_leads_to_death(
            engagement, by_id.get(engagement.scenario_id), death_by_time, window
        )
        if death_episode_id is not None:
            leads[engagement.scenario_id] = death_episode_id

    preceded: dict[str, str] = {}
    for eng_id, death_id in leads.items():
        # Prefer the latest engagement when multiple map to one death.
        preceded[death_id] = eng_id

    next_eng: dict[str, str] = {}
    follows: dict[str, str] = {}
    for death in deaths:
        ctx = by_id.get(death.scenario_id)
        active_at = (
            ctx.death_episode.active_again_time
            if ctx is not None and ctx.death_episode is not None
            else None
        )
        if active_at is None:
            continue
        nxt = _first_engagement_at_or_after(engagements, active_at)
        if nxt is None:
            continue
        next_eng[death.scenario_id] = nxt.scenario_id
        follows[nxt.scenario_id] = death.scenario_id

    updated: list[ScenarioContext] = []
    for ctx in contexts:
        sid = ctx.scenario_id
        updated.append(
            ctx.model_copy(
                update={
                    "relations": ScenarioRelations(
                        leads_to_death_episode_id=leads.get(sid),
                        follows_death_episode_id=follows.get(sid),
                        preceded_by_engagement_id=preceded.get(sid),
                        next_engagement_id=next_eng.get(sid),
                    )
                }
            )
        )
    return updated


def _engagement_leads_to_death(
    engagement: Scenario,
    context: ScenarioContext | None,
    death_by_time: dict[float, str],
    window: float,
) -> str | None:
    """Death episode whose death falls in (last_splat, last_splat + window]."""
    last_splat = None
    if context is not None and context.combat is not None:
        last_splat = context.combat.last_splat_time
    if last_splat is None:
        last_splat = _last_splat_time_from_ids(engagement.event_ids)
    if last_splat is None:
        return None
    following = engagement.context.get("following_death_id")
    if isinstance(following, str) and following.startswith("death:"):
        death_at = _timestamp_from_event_id(following)
        if death_at is not None and death_at in death_by_time:
            if last_splat < death_at <= last_splat + window:
                return death_by_time[death_at]
    for death_at, death_id in sorted(death_by_time.items()):
        if last_splat < death_at <= last_splat + window:
            return death_id
    return None


def _first_engagement_at_or_after(
    engagements: list[Scenario], timestamp: float
) -> Scenario | None:
    """Earliest engagement whose start is at or after ``timestamp``."""
    later = [item for item in engagements if item.start_time >= timestamp]
    if not later:
        return None
    return min(later, key=lambda item: (item.start_time, item.scenario_id))


def _last_splat_time_from_ids(event_ids: list[str]) -> float | None:
    """Parse the latest splat timestamp from membership ids."""
    times: list[float] = []
    for eid in event_ids:
        if not eid.startswith(f"{GameEventType.SPLAT.value}:"):
            continue
        stamp = _timestamp_from_event_id(eid)
        if stamp is not None:
            times.append(stamp)
    if not times:
        return None
    return max(times)


def _timestamp_from_event_id(eid: str) -> float | None:
    """Extract ``start_time`` from ``type:time:reason:fp`` keys."""
    parts = eid.split(":")
    if len(parts) < 2:
        return None
    try:
        return float(parts[1])
    except ValueError:
        return None


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


def _death_episode_context(
    events: list[GameEvent], scenario: Scenario
) -> DeathEpisodeContext | None:
    """Lifecycle timings for a death episode."""
    if scenario.scenario_type is not ScenarioType.DEATH_EPISODE:
        return None
    death = _event_at(events, GameEventType.DEATH, scenario.start_time)
    if death is None:
        return DeathEpisodeContext()
    next_death = _next_death_time(events, death.start_time)
    respawn = _first_between(events, GameEventType.RESPAWN, death.start_time, next_death)
    active = _first_between(
        events, GameEventType.ACTIVE_AGAIN, death.start_time, next_death
    )
    reason = respawn.reason.value if respawn is not None and respawn.reason else None
    awaiting_end = (
        respawn.start_time
        if respawn is not None
        else (active.start_time if active is not None else None)
    )
    awaiting_start = death.start_time
    return DeathEpisodeContext(
        death_time=death.start_time,
        respawn_time=respawn.start_time if respawn is not None else None,
        active_again_time=active.start_time if active is not None else None,
        awaiting_start=awaiting_start,
        awaiting_end=awaiting_end,
        awaiting_duration=_delta(awaiting_end, awaiting_start),
        death_to_respawn=_delta(respawn, death),
        death_to_active_again=_delta(active, death),
        respawn_to_active_again=_delta(active, respawn),
        has_respawn=respawn is not None,
        has_active_again=active is not None,
        complete=active is not None,
        respawn_reason=reason,
    )


def _map_context(events: list[GameEvent], scenario: Scenario) -> MapContext | None:
    """MAP_OVERLAY counts and optional death-episode / MAP_CHECK fields."""
    if scenario.scenario_type not in _IMPLEMENTED:
        return None
    overlays = _of_type(events, GameEventType.MAP_OVERLAY)
    during = [
        item
        for item in overlays
        if scenario.start_time <= item.start_time <= scenario.end_time
    ]
    before = [item for item in overlays if item.start_time < scenario.start_time]
    fields = _death_map_fields(events, scenario, overlays)
    in_episode = None
    if scenario.scenario_type is ScenarioType.MAP_CHECK:
        in_episode = False
    return MapContext(
        map_check_count=len(during),
        map_checks_before_start=len(before),
        map_checks_during_scenario=len(during),
        in_death_episode=in_episode,
        **fields,
    )


def _death_map_fields(
    events: list[GameEvent],
    scenario: Scenario,
    overlays: list[GameEvent],
) -> dict:
    """Death-episode overlay facts. Empty for other types."""
    if scenario.scenario_type is not ScenarioType.DEATH_EPISODE:
        return {}
    death = _event_at(events, GameEventType.DEATH, scenario.start_time)
    if death is None:
        return {
            "map_check_before_death": False,
            "seconds_since_map_check_before_death": None,
            "map_checks_during_death_episode": 0,
            "map_checked_while_dead": False,
            "last_map_before_death_event_id": None,
            "map_event_ids_during_episode": [],
        }
    next_death = _next_death_time(events, death.start_time)
    active = _first_between(
        events, GameEventType.ACTIVE_AGAIN, death.start_time, next_death
    )
    during_events = _overlays_during_death_episode(
        overlays, death.start_time, active, next_death
    )
    preceding = _last_before(overlays, death.start_time)
    return {
        "map_checks_during_death_episode": len(during_events),
        "map_checked_while_dead": bool(during_events),
        "map_check_before_death": preceding is not None,
        "seconds_since_map_check_before_death": _delta(death, preceding),
        "last_map_before_death_event_id": (
            event_id(preceding) if preceding is not None else None
        ),
        "map_event_ids_during_episode": [event_id(item) for item in during_events],
    }


def _combat_context(
    events: list[GameEvent],
    scenario: Scenario,
    config: ScenarioBuilderConfig,
) -> CombatContext | None:
    """SPLAT counts and the configured trade window.

    Combat evidence is optional. ``DEATH_EPISODE`` does not invent an empty
    combat nest; combat facts live on ``ENGAGEMENT``.
    """
    window = config.engagement_include_following_death_seconds
    if scenario.scenario_type is ScenarioType.DEATH_EPISODE:
        return None
    if scenario.scenario_type is ScenarioType.ENGAGEMENT:
        return _apply_trade_window(_engagement_combat(events, scenario), window)
    return None


def _engagement_combat(
    events: list[GameEvent], scenario: Scenario
) -> CombatContext:
    """Combat facts from SPLAT members only (DEATH is never an ENGAGEMENT member)."""
    member_ids = set(scenario.event_ids)
    splats = [
        item
        for item in _of_type(events, GameEventType.SPLAT)
        if event_id(item) in member_ids
    ]
    if not splats:
        splats = [
            item
            for item in _of_type(events, GameEventType.SPLAT)
            if scenario.start_time <= item.start_time <= scenario.end_time
        ]
    first = splats[0] if splats else None
    last = splats[-1] if splats else None
    duration = _delta(last, first) if first is not None and last is not None else None
    following = (
        _first_after(_of_type(events, GameEventType.DEATH), last.start_time)
        if last
        else None
    )
    return CombatContext(
        splat_count=len(splats),
        first_splat_time=first.start_time if first is not None else None,
        last_splat_time=last.start_time if last is not None else None,
        duration=duration,
        time_to_first_splat=None,
        time_to_last_splat=duration,
        splat_death_gap=_delta(following, last),
        trade_candidate=False,
    )


def _apply_trade_window(
    combat: CombatContext, window_seconds: float
) -> CombatContext:
    """Mark a trade candidate when ``0 < splat_death_gap <= window``.

    ``_first_after`` already requires a strictly later death (positive gap).
    The lower bound is stated here so the invariant is explicit. This flag
    is a temporal rule match, not proof that a trade occurred.
    """
    gap = combat.splat_death_gap
    combat.trade_candidate = (
        gap is not None and 0.0 < gap <= window_seconds
    )
    return combat


def _overlays_during_death_episode(
    overlays: list[GameEvent],
    death_at: float,
    active: GameEvent | None,
    next_death: float,
) -> list[GameEvent]:
    """Overlays after DEATH through ACTIVE_AGAIN (inclusive), else before next DEATH."""
    if active is not None:
        end = active.start_time
        return [item for item in overlays if death_at < item.start_time <= end]
    if next_death < float("inf"):
        return [item for item in overlays if death_at < item.start_time < next_death]
    return [item for item in overlays if item.start_time > death_at]


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
