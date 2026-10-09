"""Compact LLM-facing projection of CoachInput (no new facts).

Full ``CoachInput`` remains the internal evidence object. ``CoachLlmView`` is a
deterministic omit/reshape of existing fields for ``complete()``. Every exposed
value must be copyable from CoachInput / CoachingUnitResult; absent stays absent.

``source_path`` is an **audit reference** into CoachInput / ScenarioContext
vocabulary (stable dotted labels). It is not required to be a machine-evaluated
JSONPath — it names where the projected value came from for ``evidence_used``.

Locked statement / interpretation / recommendation triad copy stays on
``CoachingUnitResult`` / VMV. It is **pre-interpreted catalog material** and is
not included in ``CoachLlmView`` (the model should word from evidence + factor
ids, not canned triad text). ``claim_basis`` frames are different: they are
the contract's phrasing templates for authorized claims (``claim_basis``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from splatoon3_ai_coach.coach.claim_basis import (
    build_claim_basis,
    build_undetermined,
)
from splatoon3_ai_coach.coach.claim_catalog import (
    CANDIDATE_TYPE_DEATH_EPISODE,
    CoachingUnitResult,
)
from splatoon3_ai_coach.coach.coach_input import CoachInput, UnitDurations
from splatoon3_ai_coach.coach.coaching_candidates import active_factor_ids
from splatoon3_ai_coach.coach.death_importance import (
    death_game_clock_sample,
    factor_threshold_entries,
    same_life_last_ready,
)
from splatoon3_ai_coach.coach.playback_clock import (
    ClockedDuration,
    PlaybackClockInfo,
    clock_video_span,
)
from splatoon3_ai_coach.coach.score_facts import ScoreFacts
from splatoon3_ai_coach.coach.zone_control_facts import ZoneControlFacts
from splatoon3_ai_coach.config.models import DeathFactorThresholds

LLM_VIEW_SCHEMA_VERSION = 3
"""v3: ``claim_basis`` / ``undetermined`` and the roster capability flag.

v2: clocked durations, map-check status/coverage, factor thresholds."""


class LlmFact(BaseModel):
    """One citeable projected fact for the model-facing view."""

    label: str
    value: str | int | float | bool | None
    source_path: str = Field(
        description=(
            "Audit reference into CoachInput vocabulary (dotted label). "
            "Not necessarily a JSONPath expression."
        )
    )


class LlmImportanceFactor(BaseModel):
    """Active importance factor ids/weights only (attention, not canned advice)."""

    factor_id: str
    weight: float
    contribution: float


class LlmRosterWindowPoint(BaseModel):
    """Compact roster sample from an existing player_count_window point."""

    offset_seconds: float
    video_time: float
    ally_alive_count: int | None = None
    opponent_alive_count: int | None = None
    numbers_state: str | None = None
    gap_seconds: float | None = None
    reliable: bool = True
    source_path: str


class CoachLlmView(BaseModel):
    """Model-facing coaching context (semantic compression of CoachInput)."""

    schema_version: int = LLM_VIEW_SCHEMA_VERSION
    roster_evidence: bool = Field(
        default=False,
        description="Roster capability; when False, roster claims are undetermined.",
    )
    unit: dict[str, Any]
    claim_basis: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Authorized claims (level A/B/C) the model may word.",
    )
    undetermined: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Topics the model must not state, even hedged.",
    )
    importance: dict[str, Any]
    playback: dict[str, Any] | None = None
    death: dict[str, Any] | None = None
    clock: dict[str, Any] | None = None
    timeline: dict[str, Any] | None = None
    roster: dict[str, Any] | None = None
    special: dict[str, Any] | None = None
    low_ink: dict[str, Any] | None = None
    map: dict[str, Any] | None = None
    score: dict[str, Any] | None = None
    zone_control: dict[str, Any] | None = None
    related: list[dict[str, Any]] = Field(default_factory=list)
    evidence_limits: list[dict[str, Any]] = Field(default_factory=list)
    supporting_evidence: list[LlmFact] = Field(default_factory=list)


def build_coach_llm_view(
    coach_input: CoachInput,
    unit: CoachingUnitResult,
    thresholds: DeathFactorThresholds | None = None,
) -> CoachLlmView:
    """Dispatch domain-specific LLM view builders by ``unit.candidate_type`` only.

    ``candidate_type`` is authoritative. Scenario type is not used as a fallback.
    Raises ``ValueError`` for unsupported candidate types (no silent full dump).
    """
    candidate_type = unit.candidate_type
    if candidate_type == CANDIDATE_TYPE_DEATH_EPISODE:
        return build_death_llm_view(coach_input, unit, thresholds)
    raise ValueError(f"No CoachLlmView builder for candidate_type={candidate_type!r}")


def build_death_llm_view(
    coach_input: CoachInput,
    unit: CoachingUnitResult,
    thresholds: DeathFactorThresholds | None = None,
) -> CoachLlmView:
    """Project a death-episode CoachInput into a compact LLM view.

    Projection only: omit series / reshape existing fields. Does not infer.
    Does not embed locked triad statement/interpretation/recommendation text.
    No roster block when the roster capability is off.
    """
    if unit.candidate_type != CANDIDATE_TYPE_DEATH_EPISODE:
        raise ValueError(
            "build_death_llm_view requires candidate_type="
            f"{CANDIDATE_TYPE_DEATH_EPISODE!r}, got {unit.candidate_type!r}"
        )

    scenario = coach_input.primary_scenario
    ctx = coach_input.primary_context

    active_factors = [
        LlmImportanceFactor(
            factor_id=factor.factor_id,
            weight=factor.weight,
            contribution=factor.contribution,
        )
        for factor in unit.factors
        if factor.active
    ]

    death_block = _project_death(
        ctx.death_episode, coach_input.durations, coach_input.playback_clock
    )
    clock_block = _project_clock(coach_input)
    roster_block = (
        _project_roster(coach_input, unit) if coach_input.roster_evidence else None
    )
    special_block = _project_special(coach_input)
    map_block = _project_map(ctx.map)
    related_blocks = [
        _project_related(index, item) for index, item in enumerate(coach_input.related)
    ]
    limits = [
        {"code": limit.code, "statement": limit.statement}
        for limit in coach_input.evidence_limits
    ]
    support = [
        LlmFact(
            label=item.label,
            value=item.value,
            source_path=item.path or "supporting_evidence",
        )
        for item in unit.supporting_evidence
    ]

    basis = build_claim_basis(coach_input, unit)
    undetermined = build_undetermined(coach_input, basis)
    return CoachLlmView(
        roster_evidence=coach_input.roster_evidence,
        claim_basis=[entry.model_dump(mode="json") for entry in basis],
        undetermined=[entry.model_dump(mode="json") for entry in undetermined],
        unit={
            "candidate_id": unit.candidate_id,
            "candidate_type": unit.candidate_type,
            "scenario_id": scenario.scenario_id,
            "scenario_type": scenario.scenario_type.value,
            "video_time": float(unit.video_time),
            "outcome": scenario.outcome.value if scenario.outcome is not None else None,
        },
        importance={
            "importance_score": unit.importance_score,
            "rank": unit.rank,
            "selected_for_llm": unit.selected_for_llm,
            "match_duration_seconds": unit.match_duration_seconds,
            "active_factor_ids": active_factor_ids(unit.to_candidate()),
            "active_factors": [f.model_dump(mode="json") for f in active_factors],
            "factor_thresholds": factor_threshold_entries(coach_input, thresholds),
        },
        playback=_project_playback(coach_input.playback_clock),
        death=death_block,
        clock=clock_block,
        timeline=_project_timeline(coach_input),
        roster=roster_block,
        special=special_block,
        low_ink=_project_low_ink(coach_input),
        map=map_block,
        score=_project_score(coach_input.score_facts),
        zone_control=_project_zone_control(coach_input.zone_control_facts),
        related=related_blocks,
        evidence_limits=limits,
        supporting_evidence=support,
    )


def _clocked(value: ClockedDuration | None) -> dict[str, Any] | None:
    return None if value is None else value.model_dump(mode="json")


def _project_playback(info: PlaybackClockInfo | None) -> dict[str, Any] | None:
    if info is None:
        return None
    return {
        "status": info.status.value,
        "game_seconds_per_video_second": info.game_seconds_per_video_second,
        "video_seconds_per_game_second": info.video_seconds_per_game_second,
        "observation_count": info.observation_count,
        "reason": info.reason,
        "source_path": "playback_clock",
    }


def _project_death(
    death: Any,
    durations: UnitDurations | None,
    playback: PlaybackClockInfo | None,
) -> dict[str, Any] | None:
    if death is None:
        return None
    projected = _project_death_fields(death, durations, playback)
    if getattr(death, "end_reason", None) == "match_end":
        projected["ended_by_match_end"] = True
    return projected


def _project_death_fields(
    death: Any,
    durations: UnitDurations | None,
    playback: PlaybackClockInfo | None,
) -> dict[str, Any]:
    return {
        "death_time_video": death.death_time,
        "respawn_time_video": death.respawn_time,
        "active_again_time_video": death.active_again_time,
        "death_to_respawn": _clocked(durations.death_to_respawn if durations else None),
        "death_to_active_again": _clocked(
            durations.death_to_active_again if durations else None
        ),
        "complete": death.complete,
        "has_respawn": death.has_respawn,
        "has_active_again": death.has_active_again,
        "respawn_reason": death.respawn_reason,
        "is_first_death": death.is_first_death,
        "numbers_state_at_death": _enum_or_str(death.numbers_state_at_death),
        "match_phase_at_death": _enum_or_str(death.match_phase_at_death),
        "roster_changed_before_death": death.roster_changed_before_death,
        "seconds_since_roster_change": _clocked(
            clock_video_span(death.seconds_since_roster_change, playback)
        ),
        "preceded_by_trade_candidate": death.preceded_by_trade_candidate,
        "time_since_previous_splat": _clocked(
            clock_video_span(death.time_since_previous_splat, playback)
        ),
        "source_path": "primary_context.death_episode",
    }


def _enum_or_str(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw)


def _project_clock(coach_input: CoachInput) -> dict[str, Any] | None:
    sample = death_game_clock_sample(coach_input)
    if sample is None:
        return None
    obs = sample.observation
    return {
        "label": sample.label,
        "video_time": sample.video_time,
        "gap_seconds": sample.gap_seconds,
        "seconds_remaining": (obs.seconds_remaining if obs is not None else None),
        "display": obs.display if obs is not None else None,
        "confidence": obs.confidence if obs is not None else None,
        "source_path": "game_clock_samples[label=death]",
    }


def _project_roster(
    coach_input: CoachInput, unit: CoachingUnitResult
) -> dict[str, Any] | None:
    ctx = coach_input.primary_context
    before = unit.roster_before_death
    before_death = (
        None
        if before is None
        else {
            "video_time": before.video_time,
            "ally_alive_count": before.ally_alive_count,
            "opponent_alive_count": before.opponent_alive_count,
            "source_path": before.source_path,
        }
    )
    at_death = None
    if ctx.players is not None and ctx.players.at_death is not None:
        point = ctx.players.at_death
        at_death = {
            "video_time": point.video_time,
            "ally_alive_count": point.ally_alive_count,
            "opponent_alive_count": point.opponent_alive_count,
            "source_path": "primary_context.players.at_death",
        }

    players = ctx.players
    window_points: list[dict[str, Any]] = []
    for index, point in enumerate(coach_input.player_count_window):
        obs = point.observation
        reliable = players is None or not (
            players.unreliable_at(point.video_time)
            or (obs is not None and players.unreliable_at(obs.video_time))
        )
        if not reliable:
            obs = None
        window_points.append(
            LlmRosterWindowPoint(
                offset_seconds=point.offset_seconds,
                video_time=point.video_time,
                ally_alive_count=(obs.ally_alive_count if obs is not None else None),
                opponent_alive_count=(
                    obs.opponent_alive_count if obs is not None else None
                ),
                numbers_state=_enum_or_str(point.numbers_state) if reliable else None,
                gap_seconds=point.gap_seconds,
                reliable=reliable,
                source_path=f"player_count_window[{index}]",
            ).model_dump(mode="json")
        )

    present = None
    pc_ctx = coach_input.player_count_context
    anchor_unreliable = players is not None and pc_ctx is not None and (
        players.unreliable_at(pc_ctx.anchor_video_time)
    )
    if pc_ctx is not None and not anchor_unreliable:
        present = {
            "anchor_video_time": pc_ctx.anchor_video_time,
            "valid_point_count": pc_ctx.valid_point_count,
            "window_point_count": pc_ctx.window_point_count,
            "numbers_state_at_anchor": _enum_or_str(pc_ctx.numbers_state_at_anchor),
            "state_at_anchor": pc_ctx.state_at_anchor,
            "state_present_by": pc_ctx.state_present_by,
            "duration_since_present_by": pc_ctx.duration_since_present_by,
            "source_path": "player_count_context",
        }

    if not window_points and all(
        item is None for item in (before_death, at_death, present)
    ):
        return None
    return {
        "before_death": before_death,
        "at_death": at_death,
        "player_count_window": window_points,
        "player_count_context": present,
    }


def _project_timeline(coach_input: CoachInput) -> dict[str, Any] | None:
    """Unit duration and death spacing; independent of roster evidence."""
    if coach_input.primary_context.timeline is None:
        return None
    durations = coach_input.durations
    return {
        "scenario_duration": _clocked(durations.scenario_duration if durations else None),
        "time_since_previous_death": _clocked(
            durations.time_since_previous_death if durations else None
        ),
        "source_path": "durations",
    }


def _project_special(coach_input: CoachInput) -> dict[str, Any] | None:
    special = coach_input.primary_context.special
    if special is None or special.nearest_before_anchor is None:
        return None
    block: dict[str, Any] = {
        "state_at_anchor": special.state_at_anchor.value,
        "nearest_before_anchor": _special_reading_block(
            special.nearest_before_anchor,
            "primary_context.special.nearest_before_anchor",
        ),
    }
    ready = same_life_last_ready(coach_input)
    if ready is not None and ready.video_time != special.nearest_before_anchor.video_time:
        block["last_ready"] = _special_reading_block(
            ready, "primary_context.special.last_ready_before_anchor"
        )
    last = special.last_visible_before_anchor
    if last is not None and last.video_time != special.nearest_before_anchor.video_time:
        block["last_visible_before_anchor"] = _special_reading_block(
            last, "primary_context.special.last_visible_before_anchor"
        )
    return block


def _special_reading_block(reading: Any, source_path: str) -> dict[str, Any]:
    return {
        "video_time": reading.video_time,
        "state": reading.state.value,
        "visible": reading.visible,
        "ready": reading.ready,
        "fill_fraction": reading.fill_fraction,
        "confidence": reading.confidence,
        "source_path": source_path,
    }


def _project_low_ink(coach_input: CoachInput) -> dict[str, Any] | None:
    """LOW_INK intervals overlapping the unit; ``at_death`` when one had begun."""
    ctx = coach_input.primary_context
    low_ink = ctx.low_ink
    if low_ink is None or not low_ink.intervals:
        return None
    death = ctx.death_episode
    death_time = death.death_time if death is not None else None
    at_death = death_time is not None and any(
        i.start_time <= float(death_time) <= i.end_time for i in low_ink.intervals
    )
    return {
        "at_death": at_death,
        "intervals": [i.model_dump(mode="json") for i in low_ink.intervals],
        "source_path": "primary_context.low_ink",
    }


def _project_score(facts: ScoreFacts | None) -> dict[str, Any] | None:
    """Splat Zones count facts; absent outside Splat Zones or with no sample."""
    if facts is None or not facts.is_splat_zones or facts.video_time is None:
        return None
    return {
        "sample_label": facts.sample_label,
        "video_time": facts.video_time,
        "ally_remaining": facts.ally_remaining,
        "opponent_remaining": facts.opponent_remaining,
        "remaining_diff": facts.remaining_diff,
        "ally_penalty": facts.ally_penalty,
        "ally_penalty_quality": facts.ally_penalty_quality,
        "opponent_penalty": facts.opponent_penalty,
        "opponent_penalty_quality": facts.opponent_penalty_quality,
        "ally_count_before_progress": facts.ally_count_before_progress,
        "opponent_count_before_progress": facts.opponent_count_before_progress,
        "ally_work_remaining": facts.ally_work_remaining,
        "opponent_work_remaining": facts.opponent_work_remaining,
        "lookback_video_time": facts.lookback_video_time,
        "ally_counter_decreased_before_death": facts.ally_counter_decreased_before_death,
        "opponent_counter_decreased_before_death": (
            facts.opponent_counter_decreased_before_death
        ),
        "source_path": facts.source_path,
    }


def _project_zone_control(
    facts: ZoneControlFacts | None,
) -> dict[str, Any] | None:
    """Project observed team-level control facts without causal language."""
    if facts is None or not facts.is_splat_zones:
        return None
    if (
        facts.control_at_death is None
        and facts.control_before_death is None
        and not facts.transition_times
    ):
        return None
    return {
        "control_at_death": facts.control_at_death,
        "control_before_death": facts.control_before_death,
        "control_became_neutral": facts.control_became_neutral,
        "ally_regained_after_neutral": facts.ally_regained_after_neutral,
        "opponent_regained_after_neutral": facts.opponent_regained_after_neutral,
        "transition_times": facts.transition_times,
        "source_path": facts.source_path,
    }


def _project_map(map_ctx: Any) -> dict[str, Any] | None:
    if map_ctx is None:
        return None
    return {
        "map_check_before_death": _map_check_status_block(map_ctx),
        "seconds_since_map_check_before_death": (
            map_ctx.seconds_since_map_check_before_death
        ),
        "map_check_count": map_ctx.map_check_count,
        "map_checked_while_dead": map_ctx.map_checked_while_dead,
        "source_path": "primary_context.map",
    }


def _map_check_status_block(map_ctx: Any) -> dict[str, Any] | None:
    """Status object for map_check_before_death; never a bare boolean."""
    status = map_ctx.map_check_status
    if status is None:
        return None
    match = map_ctx.map_coverage
    episode = map_ctx.episode_map_observability
    reason = map_ctx.map_check_unknown_reason
    return {
        "status": status.value,
        "unknown_reason": reason.value if reason is not None else None,
        "coverage": {
            "rule": match.rule if match is not None else None,
            "match": match.status.value if match is not None else None,
            "completed_respawns": match.completed_respawns if match else None,
            "respawns_with_overlay": match.respawns_with_overlay if match else None,
            "episode": episode.status.value if episode is not None else None,
            "pre_death_frame_fraction": (
                episode.pre_death_frame_fraction if episode is not None else None
            ),
            "own_respawn_overlay": (
                episode.own_respawn_overlay if episode is not None else None
            ),
        },
        "source_path": "primary_context.map.map_check_status",
    }


def _project_related(index: int, item: Any) -> dict[str, Any]:
    scenario = item.scenario
    context = item.context
    combat = None
    if context.combat is not None:
        c = context.combat
        combat = {
            "splat_count": c.splat_count,
            "first_splat_time": c.first_splat_time,
            "last_splat_time": c.last_splat_time,
            "duration": c.duration,
            "splat_death_gap": c.splat_death_gap,
            "trade_candidate": c.trade_candidate,
            "source_path": f"related[{index}].context.combat",
        }
    relations = context.relations
    return {
        "role": item.role,
        "scenario_id": scenario.scenario_id,
        "scenario_type": scenario.scenario_type.value,
        "start_time": scenario.start_time,
        "end_time": scenario.end_time,
        "outcome": scenario.outcome.value if scenario.outcome is not None else None,
        "combat": combat,
        "relations": {
            "leads_to_death_episode_id": relations.leads_to_death_episode_id,
            "preceded_by_engagement_id": relations.preceded_by_engagement_id,
            "follows_death_episode_id": relations.follows_death_episode_id,
            "next_engagement_id": relations.next_engagement_id,
            "source_path": f"related[{index}].context.relations",
        },
        "source_path": f"related[{index}]",
    }
