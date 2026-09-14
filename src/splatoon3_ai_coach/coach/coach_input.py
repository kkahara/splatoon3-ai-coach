"""CoachInput: one coaching unit of evidence (no judgments).

Unit of coaching
----------------
One primary ``Scenario`` plus relation-linked neighbors from
``ScenarioContext.relations``, with GameClock and PlayerCount samples at
labeled video times, an optional event-relative player-count window, and
explicit ``EvidenceLimit`` non-claims.

``CoachInput`` is the composition point: Scenario / ScenarioContext stay
unchanged; ``PlayerCountClock`` supplies fused roster retrieval only.

``CoachInput`` describes evidence. It does not define good or bad play.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.scenario_context import ScenarioContext
from splatoon3_ai_coach.analysis.scenario_models import Scenario
from splatoon3_ai_coach.coach.coach_samples import (
    GameClockSample,
    PlayerCountSample,
    attach_game_clock_to_event_times,
    game_clock_samples,
    labeled_video_times,
    player_count_samples,
)
from splatoon3_ai_coach.coach.evidence_limits import (
    EvidenceLimit,
    collect_evidence_limits,
)
from splatoon3_ai_coach.coach.game_clock import GameClock
from splatoon3_ai_coach.coach.player_count_clock import (
    PlayerCountClock,
    PlayerCountWindowPoint,
)
from splatoon3_ai_coach.coach.player_count_context import (
    DEFAULT_PLAYER_COUNT_WINDOW_OFFSETS,
    PlayerCountContext,
    PlayerCountTrajectoryPoint,
    player_count_window_and_context,
)

RelationRole = Literal[
    "preceded_by_engagement",
    "leads_to_death_episode",
    "follows_death_episode",
    "next_engagement",
]

_RELATION_FIELDS: tuple[tuple[RelationRole, str], ...] = (
    ("preceded_by_engagement", "preceded_by_engagement_id"),
    ("leads_to_death_episode", "leads_to_death_episode_id"),
    ("follows_death_episode", "follows_death_episode_id"),
    ("next_engagement", "next_engagement_id"),
)


class RelatedScenarioEvidence(BaseModel):
    """A neighbor scenario linked only via ScenarioContext.relations."""

    role: RelationRole
    scenario: Scenario
    context: ScenarioContext


class CoachInput(BaseModel):
    """One coaching unit. Evidence only; no judgments or advice fields."""

    primary_scenario: Scenario
    primary_context: ScenarioContext
    related: list[RelatedScenarioEvidence] = Field(default_factory=list)
    game_clock_samples: list[GameClockSample] = Field(default_factory=list)
    player_count_samples: list[PlayerCountSample] = Field(default_factory=list)
    player_count_window: list[PlayerCountWindowPoint] = Field(default_factory=list)
    player_count_context: PlayerCountContext | None = None
    evidence_limits: list[EvidenceLimit] = Field(default_factory=list)


def build_coach_input_for_scenario(
    primary_id: str,
    scenarios: list[Scenario],
    contexts: list[ScenarioContext],
    game_clock: GameClock,
    *,
    max_gap_seconds: float,
    player_count_clock: PlayerCountClock | None = None,
    player_count_max_gap_seconds: float | None = None,
    player_count_window_offsets_seconds: Sequence[float] | None = None,
    player_count_context_lookback_seconds: float | None = None,
) -> CoachInput:
    """Assemble one coaching unit for ``primary_id``.

    Related scenarios come only from ``primary_context.relations``.
    Does not mutate ``scenarios`` or ``contexts``.
    """
    by_scenario = {item.scenario_id: item for item in scenarios}
    by_context = {item.scenario_id: item for item in contexts}
    if primary_id not in by_scenario:
        raise KeyError(f"unknown primary scenario_id: {primary_id}")
    if primary_id not in by_context:
        raise KeyError(f"missing ScenarioContext for: {primary_id}")

    primary_scenario = by_scenario[primary_id]
    primary_context = by_context[primary_id]
    related = _related_evidence(primary_context, by_scenario, by_context)
    labeled_times = labeled_video_times(primary_scenario, primary_context)
    samples = game_clock_samples(
        labeled_times, game_clock, max_gap_seconds=max_gap_seconds
    )
    pc_clock = player_count_clock or PlayerCountClock()
    pc_gap = (
        max_gap_seconds
        if player_count_max_gap_seconds is None
        else player_count_max_gap_seconds
    )
    pc_samples = player_count_samples(
        labeled_times, pc_clock, max_gap_seconds=pc_gap
    )
    offsets = (
        tuple(float(x) for x in player_count_window_offsets_seconds)
        if player_count_window_offsets_seconds is not None
        else DEFAULT_PLAYER_COUNT_WINDOW_OFFSETS
    )
    context_lookback = (
        8.0
        if player_count_context_lookback_seconds is None
        else float(player_count_context_lookback_seconds)
    )
    player_count_window, player_count_context = player_count_window_and_context(
        primary_scenario,
        primary_context,
        pc_clock,
        offsets=offsets,
        max_gap_seconds=pc_gap,
        context_lookback_seconds=context_lookback,
    )
    limits = collect_evidence_limits(
        primary_scenario,
        primary_context,
        samples,
        pc_samples,
        player_count_window=player_count_window,
        player_count_context=player_count_context,
    )
    return CoachInput(
        primary_scenario=primary_scenario,
        primary_context=primary_context,
        related=related,
        game_clock_samples=samples,
        player_count_samples=pc_samples,
        player_count_window=player_count_window,
        player_count_context=player_count_context,
        evidence_limits=limits,
    )


def _related_evidence(
    primary_context: ScenarioContext,
    by_scenario: dict[str, Scenario],
    by_context: dict[str, ScenarioContext],
) -> list[RelatedScenarioEvidence]:
    """Resolve relation IDs already stored on the primary context."""
    related: list[RelatedScenarioEvidence] = []
    relations = primary_context.relations
    for role, attr in _RELATION_FIELDS:
        target_id = getattr(relations, attr)
        if not isinstance(target_id, str) or not target_id:
            continue
        scenario = by_scenario.get(target_id)
        context = by_context.get(target_id)
        if scenario is None or context is None:
            continue
        related.append(
            RelatedScenarioEvidence(role=role, scenario=scenario, context=context)
        )
    return related


__all__ = [
    "CoachInput",
    "EvidenceLimit",
    "GameClockSample",
    "PlayerCountContext",
    "PlayerCountSample",
    "PlayerCountTrajectoryPoint",
    "RelatedScenarioEvidence",
    "attach_game_clock_to_event_times",
    "build_coach_input_for_scenario",
    "collect_evidence_limits",
]
