"""ENGAGEMENT combat measurements (SPLAT timing + trade window flag).

``DEATH_EPISODE`` does not invent an empty combat nest; combat facts live on
``ENGAGEMENT`` only. ``trade_candidate`` is a temporal window match, not proof.
"""

from __future__ import annotations

from splatoon3_ai_coach.analysis.scenario_models import Scenario, ScenarioType
from splatoon3_ai_coach.analysis.scenarios import event_id
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig
from splatoon3_ai_coach.vision.models import GameEvent, GameEventType


def combat_context(
    events: list[GameEvent],
    scenario: Scenario,
    config: ScenarioBuilderConfig,
):
    """SPLAT counts and the configured trade window.

    Combat evidence is optional. ``DEATH_EPISODE`` does not invent an empty
    combat nest; combat facts live on ``ENGAGEMENT``.
    """
    window = config.engagement_include_following_death_seconds
    if scenario.scenario_type is ScenarioType.DEATH_EPISODE:
        return None
    if scenario.scenario_type is ScenarioType.ENGAGEMENT:
        return apply_trade_window(engagement_combat(events, scenario), window)
    return None


def engagement_combat(events: list[GameEvent], scenario: Scenario):
    """Combat facts from SPLAT members only (DEATH is never an ENGAGEMENT member)."""
    from splatoon3_ai_coach.analysis.scenario_context import (
        CombatContext,
        _delta,
        _first_after,
        _of_type,
    )

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


def apply_trade_window(combat, window_seconds: float):
    """Mark a trade candidate when ``0 < splat_death_gap <= window``.

    ``_first_after`` already requires a strictly later death (positive gap).
    The lower bound is stated here so the invariant is explicit. This flag
    is a temporal rule match, not proof that a trade occurred.
    """
    gap = combat.splat_death_gap
    combat.trade_candidate = gap is not None and 0.0 < gap <= window_seconds
    return combat
