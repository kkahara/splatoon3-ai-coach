"""CoachInput: one coaching unit of evidence (no judgments).

Unit of coaching
----------------
One primary ``Scenario`` plus relation-linked neighbors from
``ScenarioContext.relations``, with GameClock and PlayerCount samples at
labeled video times and explicit ``EvidenceLimit`` non-claims.

``CoachInput`` describes evidence. It does not define good or bad play.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.scenario_context import ScenarioContext
from splatoon3_ai_coach.analysis.scenario_models import Scenario, ScenarioOutcome, ScenarioType
from splatoon3_ai_coach.coach.evidence_contract import (
    describe_leads_to_association,
    describe_trade_candidate,
    engagement_proves_complete_fight,
)
from splatoon3_ai_coach.coach.game_clock import GameClock, GameClockObservation
from splatoon3_ai_coach.coach.player_count_clock import (
    PlayerCountClock,
    PlayerCountObservation,
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


class GameClockSample(BaseModel):
    """Observed (or missing) match countdown at a labeled video time."""

    label: str
    video_time: float = Field(ge=0)
    observation: GameClockObservation | None = None
    gap_seconds: float | None = None


class PlayerCountSample(BaseModel):
    """Observed (or missing) fused roster alive counts at a labeled video time."""

    label: str
    video_time: float = Field(ge=0)
    observation: PlayerCountObservation | None = None
    gap_seconds: float | None = None


class EvidenceLimit(BaseModel):
    """Structured non-claim: what this unit's evidence cannot establish."""

    code: str
    statement: str


class CoachInput(BaseModel):
    """One coaching unit. Evidence only; no judgments or advice fields."""

    primary_scenario: Scenario
    primary_context: ScenarioContext
    related: list[RelatedScenarioEvidence] = Field(default_factory=list)
    game_clock_samples: list[GameClockSample] = Field(default_factory=list)
    player_count_samples: list[PlayerCountSample] = Field(default_factory=list)
    evidence_limits: list[EvidenceLimit] = Field(default_factory=list)


def attach_game_clock_to_event_times(
    video_times: Iterable[float],
    clock: GameClock,
    *,
    max_gap_seconds: float,
) -> dict[float, GameClockObservation | None]:
    """Map each canonical video time to a nearby observed game-clock sample."""
    return {
        float(video_time): clock.at(float(video_time), max_gap_seconds=max_gap_seconds)
        for video_time in video_times
    }


def build_coach_input_for_scenario(
    primary_id: str,
    scenarios: list[Scenario],
    contexts: list[ScenarioContext],
    game_clock: GameClock,
    *,
    max_gap_seconds: float,
    player_count_clock: PlayerCountClock | None = None,
    player_count_max_gap_seconds: float | None = None,
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
    labeled_times = _labeled_video_times(primary_scenario, primary_context)
    samples = _game_clock_samples(
        labeled_times, game_clock, max_gap_seconds=max_gap_seconds
    )
    pc_clock = player_count_clock or PlayerCountClock()
    pc_gap = (
        max_gap_seconds
        if player_count_max_gap_seconds is None
        else player_count_max_gap_seconds
    )
    player_count_samples = _player_count_samples(
        labeled_times, pc_clock, max_gap_seconds=pc_gap
    )
    limits = collect_evidence_limits(
        primary_scenario,
        primary_context,
        samples,
        player_count_samples,
    )
    return CoachInput(
        primary_scenario=primary_scenario,
        primary_context=primary_context,
        related=related,
        game_clock_samples=samples,
        player_count_samples=player_count_samples,
        evidence_limits=limits,
    )


def collect_evidence_limits(
    scenario: Scenario,
    context: ScenarioContext,
    game_clock_samples: list[GameClockSample],
    player_count_samples: list[PlayerCountSample] | None = None,
) -> list[EvidenceLimit]:
    """Derive contract-safe non-claims for this coaching unit."""
    limits: list[EvidenceLimit] = []

    if scenario.scenario_type is ScenarioType.ENGAGEMENT:
        limits.append(
            EvidenceLimit(
                code="engagement_not_complete_fight",
                statement=(
                    "ENGAGEMENT is a splat observation cluster, not a proven "
                    "complete fight; splat observations do not establish win/lose."
                ),
            )
        )
        _ = engagement_proves_complete_fight(context.combat)

    if context.relations.leads_to_death_episode_id is not None:
        limits.append(
            EvidenceLimit(
                code="leads_to_association_not_causation",
                statement=describe_leads_to_association(),
            )
        )

    if context.combat is not None and context.combat.trade_candidate:
        limits.append(
            EvidenceLimit(
                code="trade_candidate_window_only",
                statement=describe_trade_candidate(),
            )
        )

    if context.map is not None and context.map.map_check_before_death is not None:
        limits.append(
            EvidenceLimit(
                code="map_check_before_death_unbounded",
                statement=(
                    "map_check_before_death means any map overlay occurred before "
                    "the death (unbounded lookback); it is not evidence of a "
                    "recent check or map-usage quality."
                ),
            )
        )

    if scenario.outcome in (ScenarioOutcome.FRAGGED, ScenarioOutcome.DIED):
        limits.append(
            EvidenceLimit(
                code="outcome_not_fight_quality",
                statement=(
                    f"Scenario outcome '{scenario.outcome.value}' is linkage "
                    "vocabulary only; it is not fight-quality evidence."
                ),
            )
        )

    for sample in game_clock_samples:
        if sample.observation is None:
            limits.append(
                EvidenceLimit(
                    code="game_clock_missing",
                    statement=(
                        f"Match remaining time unknown at video time "
                        f"{sample.video_time:.1f}s (label={sample.label})."
                    ),
                )
            )

    for sample in player_count_samples or []:
        if sample.observation is None:
            limits.append(
                EvidenceLimit(
                    code="player_count_missing",
                    statement=(
                        f"Roster alive counts unknown at video time "
                        f"{sample.video_time:.1f}s (label={sample.label}). "
                        "Do not infer alive counts from splat or death events."
                    ),
                )
            )

    return limits


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


def _labeled_video_times(
    scenario: Scenario,
    context: ScenarioContext,
) -> list[tuple[str, float]]:
    """Labeled primary scenario key video times (shared by clock channels)."""
    labeled: list[tuple[str, float]] = [
        ("primary_start", scenario.start_time),
        ("primary_end", scenario.end_time),
    ]
    death = context.death_episode
    if death is not None:
        if death.death_time is not None:
            labeled.append(("death", death.death_time))
        if death.respawn_time is not None:
            labeled.append(("respawn", death.respawn_time))
        if death.active_again_time is not None:
            labeled.append(("active_again", death.active_again_time))
    combat = context.combat
    if combat is not None:
        if combat.first_splat_time is not None:
            labeled.append(("first_splat", combat.first_splat_time))
        if combat.last_splat_time is not None:
            labeled.append(("last_splat", combat.last_splat_time))
    return labeled


def _game_clock_samples(
    labeled: list[tuple[str, float]],
    game_clock: GameClock,
    *,
    max_gap_seconds: float,
) -> list[GameClockSample]:
    """Labeled lookups for primary scenario key video times."""
    samples: list[GameClockSample] = []
    seen: set[tuple[str, float]] = set()
    for label, video_time in labeled:
        key = (label, video_time)
        if key in seen:
            continue
        seen.add(key)
        obs = game_clock.at(video_time, max_gap_seconds=max_gap_seconds)
        gap = None if obs is None else abs(obs.video_time - video_time)
        samples.append(
            GameClockSample(
                label=label,
                video_time=video_time,
                observation=obs,
                gap_seconds=gap,
            )
        )
    return samples


def _player_count_samples(
    labeled: list[tuple[str, float]],
    player_count_clock: PlayerCountClock,
    *,
    max_gap_seconds: float,
) -> list[PlayerCountSample]:
    """Labeled roster-count lookups at the same times as game-clock samples."""
    samples: list[PlayerCountSample] = []
    seen: set[tuple[str, float]] = set()
    for label, video_time in labeled:
        key = (label, video_time)
        if key in seen:
            continue
        seen.add(key)
        obs = player_count_clock.at(video_time, max_gap_seconds=max_gap_seconds)
        gap = None if obs is None else abs(obs.video_time - video_time)
        samples.append(
            PlayerCountSample(
                label=label,
                video_time=video_time,
                observation=obs,
                gap_seconds=gap,
            )
        )
    return samples
