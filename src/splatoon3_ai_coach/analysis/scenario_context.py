"""Derive coaching-relevant facts from Scenarios and the GameEvent timeline.

Facts and measurements only. No coaching judgments. Does not import
detectors, snapshots, or ``vision.events``.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

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
    map_checks_after_active_again: int | None = None
    map_checks_during_death_episode: int | None = None
    map_check_before_death: bool | None = None
    seconds_since_map_check_before_death: float | None = None
    in_death_episode: bool | None = None


class CombatContext(BaseModel):
    """SPLAT timing and the configured SPLAT-to-DEATH window."""

    splat_count: int = 0
    time_to_first_splat: float | None = None
    time_to_last_splat: float | None = None
    splat_death_gap: float | None = None
    trade_candidate: bool = False


class RecoveryContext(BaseModel):
    """POST_DEATH_RECOVERY timings. Not Super Jump inference."""

    time_to_respawn: float | None = None
    time_to_active_again: float | None = None
    has_respawn: bool = False
    has_active_again: bool = False
    respawn_reason: str | None = None


class ScenarioContext(BaseModel):
    """Facts measured for one Scenario from the GameEvent timeline."""

    scenario_id: str
    timeline: TimelineContext | None = None
    map: MapContext | None = None
    combat: CombatContext | None = None
    recovery: RecoveryContext | None = None


_IMPLEMENTED = frozenset(
    {
        ScenarioType.POST_DEATH_RECOVERY,
        ScenarioType.ENGAGEMENT,
        ScenarioType.MAP_CHECK,
    }
)


def build_scenario_context(
    events: list[GameEvent],
    scenario: Scenario,
    config: ScenarioBuilderConfig,
) -> ScenarioContext:
    """Measure facts for one scenario using the full event timeline."""
    ordered = _ordered(events)
    return ScenarioContext(
        scenario_id=scenario.scenario_id,
        timeline=_timeline(ordered, scenario),
        map=_map_context(ordered, scenario, config),
        combat=_combat_context(ordered, scenario, config),
        recovery=_recovery_context(ordered, scenario),
    )


def build_scenario_contexts(
    events: list[GameEvent],
    scenarios: list[Scenario],
    config: ScenarioBuilderConfig,
) -> list[ScenarioContext]:
    """Build one ScenarioContext per scenario, preserving caller order."""
    return [build_scenario_context(events, scenario, config) for scenario in scenarios]


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


def _recovery_context(
    events: list[GameEvent], scenario: Scenario
) -> RecoveryContext | None:
    """Respawn / control-return timings for a death episode."""
    if scenario.scenario_type is not ScenarioType.POST_DEATH_RECOVERY:
        return None
    death = _event_at(events, GameEventType.DEATH, scenario.start_time)
    if death is None:
        return RecoveryContext()
    next_death = _next_death_time(events, death.start_time)
    respawn = _first_between(events, GameEventType.RESPAWN, death.start_time, next_death)
    active = _first_between(
        events, GameEventType.ACTIVE_AGAIN, death.start_time, next_death
    )
    reason = respawn.reason.value if respawn is not None and respawn.reason else None
    return RecoveryContext(
        time_to_respawn=_delta(respawn, death),
        time_to_active_again=_delta(active, death),
        has_respawn=respawn is not None,
        has_active_again=active is not None,
        respawn_reason=reason,
    )


def _map_context(
    events: list[GameEvent],
    scenario: Scenario,
    config: ScenarioBuilderConfig,
) -> MapContext | None:
    """MAP_OVERLAY counts and optional recovery / MAP_CHECK fields."""
    if scenario.scenario_type not in _IMPLEMENTED:
        return None
    overlays = _of_type(events, GameEventType.MAP_OVERLAY)
    during = [
        item
        for item in overlays
        if scenario.start_time <= item.start_time <= scenario.end_time
    ]
    before = [item for item in overlays if item.start_time < scenario.start_time]
    fields = _recovery_map_fields(events, scenario, config, overlays)
    in_episode = None
    if scenario.scenario_type is ScenarioType.MAP_CHECK:
        in_episode = _in_death_episode(
            scenario.start_time,
            _of_type(events, GameEventType.DEATH),
            _of_type(events, GameEventType.ACTIVE_AGAIN),
        )
    return MapContext(
        map_check_count=len(during),
        map_checks_before_start=len(before),
        map_checks_during_scenario=len(during),
        in_death_episode=in_episode,
        **fields,
    )


def _recovery_map_fields(
    events: list[GameEvent],
    scenario: Scenario,
    config: ScenarioBuilderConfig,
    overlays: list[GameEvent],
) -> dict:
    """Death-episode and pre-death overlay facts. Empty for other types."""
    if scenario.scenario_type is not ScenarioType.POST_DEATH_RECOVERY:
        return {}
    death = _event_at(events, GameEventType.DEATH, scenario.start_time)
    if death is None:
        return {
            "map_check_before_death": False,
            "seconds_since_map_check_before_death": None,
        }
    next_death = _next_death_time(events, death.start_time)
    active = _first_between(
        events, GameEventType.ACTIVE_AGAIN, death.start_time, next_death
    )
    preceding = _last_before(overlays, death.start_time)
    return {
        "map_checks_during_death_episode": _count_during_death_episode(
            overlays, death.start_time, active, next_death
        ),
        "map_checks_after_active_again": _count_after_active(
            overlays, active, next_death, config.post_death_follow_seconds
        ),
        "map_check_before_death": preceding is not None,
        "seconds_since_map_check_before_death": _delta(death, preceding),
    }


def _combat_context(
    events: list[GameEvent],
    scenario: Scenario,
    config: ScenarioBuilderConfig,
) -> CombatContext | None:
    """SPLAT counts and the configured trade window."""
    window = config.engagement_include_following_death_seconds
    if scenario.scenario_type is ScenarioType.POST_DEATH_RECOVERY:
        return _apply_trade_window(_recovery_combat(events, scenario, config), window)
    if scenario.scenario_type is ScenarioType.ENGAGEMENT:
        return _apply_trade_window(_engagement_combat(events, scenario), window)
    return None


def _recovery_combat(
    events: list[GameEvent],
    scenario: Scenario,
    config: ScenarioBuilderConfig,
) -> CombatContext:
    """Combat after ACTIVE_AGAIN inside the recovery follow window."""
    death = _event_at(events, GameEventType.DEATH, scenario.start_time)
    if death is None:
        return CombatContext()
    next_death = _next_death_time(events, death.start_time)
    active = _first_between(
        events, GameEventType.ACTIVE_AGAIN, death.start_time, next_death
    )
    if active is None:
        return CombatContext()
    follow_end = min(active.start_time + config.post_death_follow_seconds, next_death)
    splats = [
        item
        for item in _of_type(events, GameEventType.SPLAT)
        if active.start_time < item.start_time <= follow_end
    ]
    return _combat_from_splats(events, splats, origin=active)


def _engagement_combat(
    events: list[GameEvent], scenario: Scenario
) -> CombatContext:
    """Cluster splats; first-splat delay is not meaningful."""
    splats = [
        item
        for item in _of_type(events, GameEventType.SPLAT)
        if scenario.start_time <= item.start_time <= scenario.end_time
    ]
    combat = _combat_from_splats(events, splats, origin=None)
    if len(splats) >= 2:
        combat.time_to_last_splat = splats[-1].start_time - splats[0].start_time
    elif splats:
        combat.time_to_last_splat = 0.0
    combat.time_to_first_splat = None
    return combat


def _combat_from_splats(
    events: list[GameEvent],
    splats: list[GameEvent],
    *,
    origin: GameEvent | None,
) -> CombatContext:
    """Shared splat count, optional origin delay, and next-death gap."""
    first = splats[0] if splats else None
    last = splats[-1] if splats else None
    following = (
        _first_after(_of_type(events, GameEventType.DEATH), last.start_time)
        if last
        else None
    )
    return CombatContext(
        splat_count=len(splats),
        time_to_first_splat=_delta(first, origin) if origin is not None else None,
        time_to_last_splat=_delta(last, origin) if origin is not None else None,
        splat_death_gap=_delta(following, last),
        trade_candidate=False,
    )


def _apply_trade_window(
    combat: CombatContext, window_seconds: float
) -> CombatContext:
    """Mark a trade candidate when the splat-to-death gap is inside the window."""
    gap = combat.splat_death_gap
    combat.trade_candidate = gap is not None and gap <= window_seconds
    return combat


def _count_during_death_episode(
    overlays: list[GameEvent],
    death_at: float,
    active: GameEvent | None,
    next_death: float,
) -> int | None:
    """Overlays after DEATH and before ACTIVE_AGAIN, or next DEATH if incomplete."""
    if active is not None:
        end = active.start_time
    elif next_death < float("inf"):
        end = next_death
    else:
        return None
    return sum(1 for item in overlays if death_at < item.start_time < end)


def _count_after_active(
    overlays: list[GameEvent],
    active: GameEvent | None,
    next_death: float,
    follow_seconds: float,
) -> int | None:
    """Overlays after ACTIVE_AGAIN inside the recovery follow window."""
    if active is None:
        return None
    follow_end = min(active.start_time + follow_seconds, next_death)
    return sum(
        1 for item in overlays if active.start_time < item.start_time <= follow_end
    )


def _in_death_episode(
    timestamp: float,
    deaths: list[GameEvent],
    actives: list[GameEvent],
) -> bool:
    """True if ``timestamp`` is after a DEATH and before that episode's recovery."""
    prior = [death for death in deaths if death.start_time <= timestamp]
    if not prior:
        return False
    death_at = prior[-1].start_time
    next_death = _next_death_time(deaths, death_at)
    if timestamp >= next_death:
        return False
    recovered = [
        active.start_time
        for active in actives
        if death_at < active.start_time < next_death
    ]
    if recovered and timestamp >= recovered[0]:
        return False
    return True


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
