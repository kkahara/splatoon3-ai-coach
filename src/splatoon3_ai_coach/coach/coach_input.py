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
    playback_evidence_limits,
)
from splatoon3_ai_coach.coach.game_clock import GameClock
from splatoon3_ai_coach.coach.playback_clock import (
    ClockedDuration,
    PlaybackClockInfo,
    PlaybackClockStatus,
    clocked_duration,
)
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
from splatoon3_ai_coach.coach.score_facts import ScoreFacts, derive_score_facts
from splatoon3_ai_coach.coach.zone_control_facts import (
    ZoneControlFacts,
    derive_zone_control_facts,
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


class UnitDurations(BaseModel):
    """Primary-unit intervals with explicit clocks (see ``playback_clock``)."""

    death_to_respawn: ClockedDuration | None = None
    death_to_active_again: ClockedDuration | None = None
    time_since_previous_death: ClockedDuration | None = None
    scenario_duration: ClockedDuration | None = None


class CoachInput(BaseModel):
    """One coaching unit. Evidence only; no judgments or advice fields."""

    primary_scenario: Scenario
    primary_context: ScenarioContext
    related: list[RelatedScenarioEvidence] = Field(default_factory=list)
    game_clock_samples: list[GameClockSample] = Field(default_factory=list)
    player_count_samples: list[PlayerCountSample] = Field(default_factory=list)
    player_count_window: list[PlayerCountWindowPoint] = Field(default_factory=list)
    player_count_context: PlayerCountContext | None = None
    score_facts: ScoreFacts | None = None
    zone_control_facts: ZoneControlFacts | None = None
    playback_clock: PlaybackClockInfo | None = None
    durations: UnitDurations | None = None
    evidence_limits: list[EvidenceLimit] = Field(default_factory=list)
    roster_evidence: bool = Field(
        default=True,
        description=(
            "False when the roster capability is off: roster nests and "
            "player_count_* were stripped and must not be claimed."
        ),
    )


ROSTER_EVIDENCE_DISABLED = EvidenceLimit(
    code="roster_evidence_disabled",
    statement=(
        "Roster (ally/opponent alive count) evidence is not used for coaching: "
        "it has not passed the reliability bar. Do not state alive counts, "
        "numbers advantage or disadvantage, teammate availability, or who was "
        "last alive."
    ),
)


def without_roster(context: ScenarioContext) -> ScenarioContext:
    """Copy of ``context`` with every roster-derived field removed."""
    update: dict = {"players": None}
    death = context.death_episode
    if death is not None:
        update["death_episode"] = death.model_copy(
            update={
                "numbers_state_at_death": None,
                "roster_changed_before_death": None,
                "seconds_since_roster_change": None,
            }
        )
    return context.model_copy(update=update)


def strip_roster_evidence(coach_input: CoachInput) -> CoachInput:
    """Copy of a built unit behind the roster capability boundary (off).

    For units persisted before the boundary existed; new units are built with
    ``roster_evidence=False`` instead.
    """
    related = [
        item.model_copy(update={"context": without_roster(item.context)})
        for item in coach_input.related
    ]
    limits = list(coach_input.evidence_limits)
    if all(limit.code != ROSTER_EVIDENCE_DISABLED.code for limit in limits):
        limits.append(ROSTER_EVIDENCE_DISABLED)
    return coach_input.model_copy(
        update={
            "primary_context": without_roster(coach_input.primary_context),
            "related": related,
            "player_count_samples": [],
            "player_count_window": [],
            "player_count_context": None,
            "evidence_limits": limits,
            "roster_evidence": False,
        }
    )


def unit_durations(
    context: ScenarioContext,
    scenario: Scenario,
    *,
    info: PlaybackClockInfo,
    game_clock: GameClock | None,
    max_gap_seconds: float,
) -> UnitDurations:
    """Clocked death / timeline intervals for one unit."""

    def span(start: float | None, end: float | None) -> ClockedDuration | None:
        return clocked_duration(
            start,
            end,
            info=info,
            game_clock=game_clock,
            max_gap_seconds=max_gap_seconds,
        )

    death = context.death_episode
    death_time = death.death_time if death is not None else None
    previous = None
    timeline = context.timeline
    if timeline is not None and timeline.time_since_previous_death is not None:
        previous = float(scenario.start_time) - float(timeline.time_since_previous_death)
    return UnitDurations(
        death_to_respawn=span(death_time, death.respawn_time if death else None),
        death_to_active_again=span(
            death_time, death.active_again_time if death else None
        ),
        time_since_previous_death=span(previous, float(scenario.start_time))
        if previous is not None
        else None,
        scenario_duration=span(float(scenario.start_time), float(scenario.end_time)),
    )


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
    battle_mode_id: str | None = None,
    include_score_facts: bool = False,
    include_zone_control_facts: bool = False,
    playback_clock: PlaybackClockInfo | None = None,
    roster_evidence: bool = True,
) -> CoachInput:
    """Assemble one coaching unit for ``primary_id``.

    Related scenarios come only from ``primary_context.relations``.
    Does not mutate ``scenarios`` or ``contexts``. With ``include_score_facts``
    the unit carries Splat Zones count facts (and their limits) for
    ``battle_mode_id``. Without ``playback_clock`` the playback rate is
    unverified, so durations carry video seconds only. With
    ``roster_evidence=False`` every roster field is stripped (capability off).
    """
    info = playback_clock or PlaybackClockInfo(
        status=PlaybackClockStatus.INSUFFICIENT_CLOCK_EVIDENCE,
        reason="Playback rate was not estimated for this unit.",
    )
    by_scenario = {item.scenario_id: item for item in scenarios}
    by_context = {item.scenario_id: item for item in contexts}
    if primary_id not in by_scenario:
        raise KeyError(f"unknown primary scenario_id: {primary_id}")
    if primary_id not in by_context:
        raise KeyError(f"missing ScenarioContext for: {primary_id}")

    if not roster_evidence:
        by_context = {key: without_roster(ctx) for key, ctx in by_context.items()}
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
    pc_samples = player_count_samples(labeled_times, pc_clock, max_gap_seconds=pc_gap)
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
    if not roster_evidence:
        pc_samples, player_count_window, player_count_context = [], [], None
    score_facts = (
        derive_score_facts(primary_context, battle_mode_id=battle_mode_id)
        if include_score_facts
        else None
    )
    zone_control_facts = (
        derive_zone_control_facts(primary_context, battle_mode_id=battle_mode_id)
        if include_zone_control_facts
        else None
    )
    durations = unit_durations(
        primary_context,
        primary_scenario,
        info=info,
        game_clock=game_clock,
        max_gap_seconds=max_gap_seconds,
    )
    limits = collect_evidence_limits(
        primary_scenario,
        primary_context,
        samples,
        pc_samples,
        player_count_window=player_count_window,
        player_count_context=player_count_context,
        score_facts=score_facts,
        zone_control_facts=zone_control_facts,
    )
    limits.extend(playback_evidence_limits(info, durations))
    if not roster_evidence:
        limits.append(ROSTER_EVIDENCE_DISABLED)
    return CoachInput(
        primary_scenario=primary_scenario,
        primary_context=primary_context,
        related=related,
        game_clock_samples=samples,
        player_count_samples=pc_samples,
        player_count_window=player_count_window,
        player_count_context=player_count_context,
        score_facts=score_facts,
        zone_control_facts=zone_control_facts,
        playback_clock=info,
        durations=durations,
        evidence_limits=limits,
        roster_evidence=roster_evidence,
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
    "ROSTER_EVIDENCE_DISABLED",
    "strip_roster_evidence",
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
