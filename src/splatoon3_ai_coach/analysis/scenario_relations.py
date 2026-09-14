"""Semantic ENGAGEMENT ↔ DEATH_EPISODE relation attachment.

Temporal association only. Does not change Scenario ownership / event_ids.
"""

from __future__ import annotations

from splatoon3_ai_coach.analysis.scenario_models import Scenario, ScenarioType
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig
from splatoon3_ai_coach.vision.models import GameEventType


def attach_relations(
    contexts: list,
    scenarios: list[Scenario],
    config: ScenarioBuilderConfig,
) -> list:
    """Wire ENGAGEMENT ↔ DEATH_EPISODE links. No grouping changes."""
    from splatoon3_ai_coach.analysis.scenario_context import (
        ScenarioContext,
        ScenarioRelations,
    )

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
        death_episode_id = engagement_leads_to_death(
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
        nxt = first_engagement_at_or_after(engagements, active_at)
        if nxt is None:
            continue
        next_eng[death.scenario_id] = nxt.scenario_id
        follows[nxt.scenario_id] = death.scenario_id

    updated: list[ScenarioContext] = []
    for ctx in contexts:
        sid = ctx.scenario_id
        relations = ScenarioRelations(
            leads_to_death_episode_id=leads.get(sid),
            follows_death_episode_id=follows.get(sid),
            preceded_by_engagement_id=preceded.get(sid),
            next_engagement_id=next_eng.get(sid),
        )
        death_episode = ctx.death_episode
        if death_episode is not None:
            trade = False
            eng_id = relations.preceded_by_engagement_id
            if eng_id is not None:
                eng_ctx = by_id.get(eng_id)
                if (
                    eng_ctx is not None
                    and eng_ctx.combat is not None
                    and eng_ctx.combat.trade_candidate
                ):
                    trade = True
            death_episode = death_episode.model_copy(
                update={"preceded_by_trade_candidate": trade}
            )
        updated.append(
            ctx.model_copy(
                update={
                    "relations": relations,
                    "death_episode": death_episode,
                }
            )
        )
    return updated


def engagement_leads_to_death(
    engagement: Scenario,
    context,
    death_by_time: dict[float, str],
    window: float,
) -> str | None:
    """Death episode whose death falls in (last_splat, last_splat + window]."""
    last_splat = None
    if context is not None and context.combat is not None:
        last_splat = context.combat.last_splat_time
    if last_splat is None:
        last_splat = last_splat_time_from_ids(engagement.event_ids)
    if last_splat is None:
        return None
    following = engagement.context.get("following_death_id")
    if isinstance(following, str) and following.startswith("death:"):
        death_at = timestamp_from_event_id(following)
        if death_at is not None and death_at in death_by_time:
            if last_splat < death_at <= last_splat + window:
                return death_by_time[death_at]
    for death_at, death_id in sorted(death_by_time.items()):
        if last_splat < death_at <= last_splat + window:
            return death_id
    return None


def first_engagement_at_or_after(
    engagements: list[Scenario], timestamp: float
) -> Scenario | None:
    """Earliest engagement whose start is at or after ``timestamp``."""
    later = [item for item in engagements if item.start_time >= timestamp]
    if not later:
        return None
    return min(later, key=lambda item: (item.start_time, item.scenario_id))


def last_splat_time_from_ids(event_ids: list[str]) -> float | None:
    """Parse the latest splat timestamp from membership ids."""
    times: list[float] = []
    for eid in event_ids:
        if not eid.startswith(f"{GameEventType.SPLAT.value}:"):
            continue
        stamp = timestamp_from_event_id(eid)
        if stamp is not None:
            times.append(stamp)
    if not times:
        return None
    return max(times)


def timestamp_from_event_id(eid: str) -> float | None:
    """Extract ``start_time`` from ``type:time:reason:fp`` keys."""
    parts = eid.split(":")
    if len(parts) < 2:
        return None
    try:
        return float(parts[1])
    except ValueError:
        return None
