"""Build coaching situations from the authoritative GameEvent timeline.

Consumes ``GameEvent`` records only. Does not import detectors, snapshots,
or ``vision.events``.
"""

from __future__ import annotations

from collections.abc import Iterable

from splatoon3_ai_coach.analysis.scenario_models import (
    Scenario,
    ScenarioOutcome,
    ScenarioType,
)
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig
from splatoon3_ai_coach.vision.models import GameEvent, GameEventType

_FOLLOW_TYPES = frozenset({GameEventType.SPLAT, GameEventType.MAP_OVERLAY})


def event_id(event: GameEvent) -> str:
    """Deterministic key for a GameEvent. Does not mutate the event."""
    reason = event.reason.value if event.reason is not None else "-"
    fingerprint = event.splat_fingerprint or "-"
    return f"{event.event_type.value}:{event.start_time:.3f}:{reason}:{fingerprint}"


def build_scenarios(
    events: list[GameEvent],
    config: ScenarioBuilderConfig,
) -> list[Scenario]:
    """Group GameEvents into deterministic scenarios.

    Does not rewrite events. Overlap is allowed. Detector debounce is unused.
    """
    ordered = _sorted_events(events)
    scenarios = [
        *_build_post_death_recoveries(ordered, config),
        *_build_map_checks(ordered),
        *_build_engagements(ordered, config),
    ]
    scenarios.sort(
        key=lambda item: (item.start_time, item.scenario_type.value, item.scenario_id)
    )
    return scenarios


def format_scenario_timeline(
    events: list[GameEvent],
    scenarios: list[Scenario],
) -> str:
    """Readable event + scenario dump for video comparison. No coaching text."""
    lines = [_format_event_line(event) for event in _sorted_events(events)]
    if lines and scenarios:
        lines.append("")
    for scenario in scenarios:
        lines.extend(_format_scenario_block(scenario))
        lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if scenarios or events else "")


def _sorted_events(events: Iterable[GameEvent]) -> list[GameEvent]:
    """Stable order: time, type, derived id."""
    return sorted(
        events,
        key=lambda event: (event.start_time, event.event_type.value, event_id(event)),
    )


def _build_post_death_recoveries(
    events: list[GameEvent],
    config: ScenarioBuilderConfig,
) -> list[Scenario]:
    """One POST_DEATH_RECOVERY per DEATH."""
    deaths = _of_type(events, GameEventType.DEATH)
    scenarios: list[Scenario] = []
    for index, death in enumerate(deaths):
        next_death = (
            deaths[index + 1].start_time if index + 1 < len(deaths) else float("inf")
        )
        scenarios.append(
            _one_post_death_recovery(death, events, next_death, config)
        )
    return scenarios


def _one_post_death_recovery(
    death: GameEvent,
    events: list[GameEvent],
    next_death: float,
    config: ScenarioBuilderConfig,
) -> Scenario:
    """Close one death episode; missing RESPAWN / ACTIVE_AGAIN is allowed."""
    members = [death]
    respawn = _first_between(
        events, GameEventType.RESPAWN, death.start_time, next_death
    )
    active = _first_between(
        events, GameEventType.ACTIVE_AGAIN, death.start_time, next_death
    )
    if respawn is not None:
        members.append(respawn)
    if active is not None:
        members.append(active)
        follow_end = min(active.start_time + config.post_death_follow_seconds, next_death)
        members.extend(
            event
            for event in events
            if event.event_type in _FOLLOW_TYPES
            and death.start_time < event.start_time <= follow_end
        )
    members = _unique_members(members)
    if len(members) == 1:
        end_time = death.start_time + config.post_death_max_seconds
    else:
        end_time = max(_event_end(item) for item in members)
    reason = respawn.reason.value if respawn is not None and respawn.reason else None
    return _scenario(
        ScenarioType.POST_DEATH_RECOVERY,
        members,
        end_time=end_time,
        outcome=(
            ScenarioOutcome.RECOVERED
            if active is not None
            else ScenarioOutcome.INCOMPLETE
        ),
        context={
            "has_respawn": respawn is not None,
            "has_active_again": active is not None,
            "respawn_reason": reason,
            "splat_count": _count_type(members, GameEventType.SPLAT),
            "map_check_count": _count_type(members, GameEventType.MAP_OVERLAY),
        },
    )


def _build_map_checks(events: list[GameEvent]) -> list[Scenario]:
    """One MAP_CHECK per MAP_OVERLAY interval."""
    deaths = _of_type(events, GameEventType.DEATH)
    actives = _of_type(events, GameEventType.ACTIVE_AGAIN)
    scenarios: list[Scenario] = []
    for overlay in _of_type(events, GameEventType.MAP_OVERLAY):
        end_time = overlay.end_time if overlay.end_time is not None else overlay.start_time
        scenarios.append(
            _scenario(
                ScenarioType.MAP_CHECK,
                [overlay],
                end_time=end_time,
                outcome=ScenarioOutcome.OBSERVED,
                context={
                    "in_death_episode": _in_death_episode(
                        overlay.start_time, deaths, actives
                    )
                },
            )
        )
    return scenarios


def _build_engagements(
    events: list[GameEvent],
    config: ScenarioBuilderConfig,
) -> list[Scenario]:
    """Cluster SPLAT events; optionally attach a nearby following DEATH."""
    splats = _of_type(events, GameEventType.SPLAT)
    deaths = _of_type(events, GameEventType.DEATH)
    clusters = _cluster_splats(splats, config.engagement_gap_seconds)
    scenarios: list[Scenario] = []
    for cluster in clusters:
        last_splat = cluster[-1].start_time
        death = _first_between(
            deaths,
            GameEventType.DEATH,
            last_splat,
            last_splat + config.engagement_include_following_death_seconds,
            inclusive_start=False,
            inclusive_end=True,
        )
        members = list(cluster)
        if death is not None:
            members.append(death)
        scenarios.append(
            _scenario(
                ScenarioType.ENGAGEMENT,
                members,
                end_time=max(_event_end(item) for item in members),
                outcome=(
                    ScenarioOutcome.DIED if death is not None else ScenarioOutcome.FRAGGED
                ),
                context={"splat_count": len(cluster)},
            )
        )
    return scenarios


def _cluster_splats(
    splats: list[GameEvent], gap_seconds: float
) -> list[list[GameEvent]]:
    """Group consecutive SPLATs whose starts are within ``gap_seconds``."""
    if not splats:
        return []
    clusters: list[list[GameEvent]] = [[splats[0]]]
    for splat in splats[1:]:
        previous = clusters[-1][-1]
        if splat.start_time - previous.start_time <= gap_seconds:
            clusters[-1].append(splat)
        else:
            clusters.append([splat])
    return clusters


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
    next_deaths = [death.start_time for death in deaths if death.start_time > death_at]
    next_death = next_deaths[0] if next_deaths else float("inf")
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


def _scenario(
    scenario_type: ScenarioType,
    members: list[GameEvent],
    *,
    end_time: float,
    outcome: ScenarioOutcome,
    context: dict,
) -> Scenario:
    """Assemble one scenario from already-selected events."""
    members = _sorted_events(_unique_members(members))
    start_time = members[0].start_time
    return Scenario(
        scenario_id=f"{scenario_type.value}:{start_time:.3f}",
        scenario_type=scenario_type,
        start_time=start_time,
        end_time=end_time,
        event_ids=[event_id(event) for event in members],
        confidence=min((event.confidence for event in members), default=1.0),
        context=context,
        outcome=outcome,
    )


def _of_type(events: list[GameEvent], event_type: GameEventType) -> list[GameEvent]:
    """Events of one type, preserving caller order."""
    return [event for event in events if event.event_type is event_type]


def _count_type(events: list[GameEvent], event_type: GameEventType) -> int:
    """Count members of one type."""
    return sum(1 for event in events if event.event_type is event_type)


def _first_between(
    events: list[GameEvent],
    event_type: GameEventType,
    start: float,
    end: float,
    *,
    inclusive_start: bool = False,
    inclusive_end: bool = False,
) -> GameEvent | None:
    """First event of ``event_type`` in an open or half-open time window."""
    for event in events:
        if event.event_type is not event_type:
            continue
        after_start = (
            event.start_time >= start if inclusive_start else event.start_time > start
        )
        before_end = (
            event.start_time <= end if inclusive_end else event.start_time < end
        )
        if after_start and before_end:
            return event
    return None


def _event_end(event: GameEvent) -> float:
    """Interval end, or the instant start when ``end_time`` is missing."""
    if event.end_time is not None:
        return event.end_time
    return event.start_time


def _unique_members(members: list[GameEvent]) -> list[GameEvent]:
    """Drop duplicate object identities while keeping first-seen order."""
    seen: set[int] = set()
    unique: list[GameEvent] = []
    for event in members:
        marker = id(event)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(event)
    return unique


def _format_ts(seconds: float) -> str:
    """Format analysis seconds as ``M:SS.s``."""
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes:02d}:{remainder:04.1f}"


def _format_event_line(event: GameEvent) -> str:
    """One timeline row for a GameEvent."""
    return f"{_format_ts(event.start_time)} — {event.event_type.name}"


def _format_scenario_block(scenario: Scenario) -> list[str]:
    """Multi-line dump of one scenario."""
    return [
        "Scenario:",
        scenario.scenario_type.name,
        f"start={_format_ts(scenario.start_time)}",
        f"end={_format_ts(scenario.end_time)}",
        f"outcome={scenario.outcome.value}",
        f"events={', '.join(scenario.event_ids)}",
    ]
