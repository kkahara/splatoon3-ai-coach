"""MAP_OVERLAY fact measurement for ScenarioContext.

Counts and death-episode overlay relationships only. Map ink is separate
secondary evidence and never sets ``map_check_*`` fields.
"""

from __future__ import annotations

from splatoon3_ai_coach.analysis.map_coverage import (
    CoverageStatus,
    EpisodeMapObservability,
    MapCheckStatus,
    MapOverlayCoverage,
    MapOverlaySample,
    episode_map_observability,
    map_check_status,
    match_map_coverage,
)
from splatoon3_ai_coach.analysis.scenario_models import Scenario, ScenarioType
from splatoon3_ai_coach.analysis.scenarios import event_id
from splatoon3_ai_coach.config.models import MapCoverageConfig
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
    match_coverage: MapOverlayCoverage | None = None,
    samples: list[MapOverlaySample] | None = None,
    cadence_times: list[float] | None = None,
    coverage_config: MapCoverageConfig | None = None,
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
        match_coverage=match_coverage,
        samples=samples,
        cadence_times=cadence_times,
        coverage_config=coverage_config,
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
    match_coverage: MapOverlayCoverage | None = None,
    samples: list[MapOverlaySample] | None = None,
    cadence_times: list[float] | None = None,
    coverage_config: MapCoverageConfig | None = None,
) -> dict:
    """Death-episode overlay facts. Empty for other types.

    Absence becomes ``False`` / ``not_observed`` only under rule
    ``respawn_map_coverage`` (source + match + episode coverage); otherwise it
    stays ``None`` / ``unknown`` with a reason code.
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
    config = coverage_config or MapCoverageConfig()
    if match_coverage is None:
        match_coverage = match_map_coverage(
            events, source_observability=map_overlay_observability, config=config
        )
    death = _event_at(events, GameEventType.DEATH, scenario.start_time)
    if death is None:
        return _status_fields(
            observed_before=False,
            while_dead=False,
            match=match_coverage,
            episode=None,
        ) | {
            "seconds_since_map_check_before_death": None,
            "map_checks_during_death_episode": 0,
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
    previous_active = _last_before(
        [e for e in events if e.event_type is GameEventType.ACTIVE_AGAIN],
        death.start_time,
    )
    episode = episode_map_observability(
        death_time=death.start_time,
        segment_start=(
            previous_active.start_time if previous_active is not None else None
        ),
        episode_complete=active is not None,
        overlays_during_episode=len(during_events),
        samples=samples,
        cadence_times=cadence_times,
        config=config,
    )
    preceding = _last_before(overlays, death.start_time)
    return _status_fields(
        observed_before=preceding is not None,
        while_dead=bool(during_events),
        match=match_coverage,
        episode=episode,
    ) | {
        "map_checks_during_death_episode": len(during_events),
        "seconds_since_map_check_before_death": _delta(death, preceding),
        "last_map_before_death_event_id": (
            event_id(preceding) if preceding is not None else None
        ),
        "map_event_ids_during_episode": [event_id(item) for item in during_events],
    }


def _status_fields(
    *,
    observed_before: bool,
    while_dead: bool,
    match: MapOverlayCoverage,
    episode: EpisodeMapObservability | None,
) -> dict:
    """Status, reason, coverage blocks and the derived tri-state booleans."""
    status, reason = map_check_status(
        observed_before=observed_before, match=match, episode=episode
    )
    reliable = status is MapCheckStatus.NOT_OBSERVED or (
        status is MapCheckStatus.OBSERVED
        and match.status is CoverageStatus.SUFFICIENT
        and episode is not None
        and episode.status is CoverageStatus.SUFFICIENT
    )
    return {
        "map_check_status": status,
        "map_check_unknown_reason": reason,
        "map_coverage": match,
        "episode_map_observability": episode,
        "map_check_before_death": (
            True if observed_before else (False if reliable else None)
        ),
        "map_checked_while_dead": True if while_dead else (False if reliable else None),
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
