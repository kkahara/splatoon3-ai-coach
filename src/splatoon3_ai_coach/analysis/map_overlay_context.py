"""MAP_OVERLAY fact measurement for ScenarioContext.

Counts and death-episode overlay relationships only. Map ink is separate
secondary evidence and never sets ``map_check_*`` fields.
"""

from __future__ import annotations

from splatoon3_ai_coach.analysis.scenario_models import Scenario, ScenarioType
from splatoon3_ai_coach.analysis.scenarios import event_id
from splatoon3_ai_coach.media.source_capabilities import absence_is_reliable_negative
from splatoon3_ai_coach.media.video_source import Observability
from splatoon3_ai_coach.vision.models import GameEvent, GameEventType

_IMPLEMENTED = frozenset(
    {
        ScenarioType.DEATH_EPISODE,
        ScenarioType.ENGAGEMENT,
        ScenarioType.MAP_CHECK,
    }
)


def map_context(
    events: list[GameEvent],
    scenario: Scenario,
    *,
    map_overlay_observability: Observability = Observability.OBSERVABLE,
):
    """MAP_OVERLAY counts and optional death-episode / MAP_CHECK fields."""
    from splatoon3_ai_coach.analysis.scenario_context import MapContext, _of_type

    if scenario.scenario_type not in _IMPLEMENTED:
        return None
    overlays = _of_type(events, GameEventType.MAP_OVERLAY)
    during = [
        item
        for item in overlays
        if scenario.start_time <= item.start_time <= scenario.end_time
    ]
    before = [item for item in overlays if item.start_time < scenario.start_time]
    fields = death_map_fields(
        events,
        scenario,
        overlays,
        map_overlay_observability=map_overlay_observability,
    )
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


def death_map_fields(
    events: list[GameEvent],
    scenario: Scenario,
    overlays: list[GameEvent],
    *,
    map_overlay_observability: Observability = Observability.OBSERVABLE,
) -> dict:
    """Death-episode overlay facts. Empty for other types.

    When MAP_OVERLAY is not ``observable``, absence stays ``None`` (unknown /
    not assertable) rather than ``False``.
    """
    from splatoon3_ai_coach.analysis.scenario_context import (
        _delta,
        _event_at,
        _first_between,
        _last_before,
        _next_death_time,
    )

    if scenario.scenario_type is not ScenarioType.DEATH_EPISODE:
        return {}
    reliable_absence = absence_is_reliable_negative(map_overlay_observability)
    death = _event_at(events, GameEventType.DEATH, scenario.start_time)
    if death is None:
        return {
            "map_check_before_death": False if reliable_absence else None,
            "seconds_since_map_check_before_death": None,
            "map_checks_during_death_episode": 0,
            "map_checked_while_dead": False if reliable_absence else None,
            "last_map_before_death_event_id": None,
            "map_event_ids_during_episode": [],
        }
    next_death = _next_death_time(events, death.start_time)
    active = _first_between(
        events, GameEventType.ACTIVE_AGAIN, death.start_time, next_death
    )
    during_events = overlays_during_death_episode(
        overlays, death.start_time, active, next_death
    )
    preceding = _last_before(overlays, death.start_time)
    before = preceding is not None
    while_dead = bool(during_events)
    return {
        "map_checks_during_death_episode": len(during_events),
        "map_checked_while_dead": (
            True if while_dead else (False if reliable_absence else None)
        ),
        "map_check_before_death": (
            True if before else (False if reliable_absence else None)
        ),
        "seconds_since_map_check_before_death": _delta(death, preceding),
        "last_map_before_death_event_id": (
            event_id(preceding) if preceding is not None else None
        ),
        "map_event_ids_during_episode": [event_id(item) for item in during_events],
    }


def overlays_during_death_episode(
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
