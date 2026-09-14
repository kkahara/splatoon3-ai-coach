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
ids, not canned triad text).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from splatoon3_ai_coach.coach.claim_catalog import (
    CANDIDATE_TYPE_DEATH_EPISODE,
    CoachingUnitResult,
)
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.coaching_candidates import active_factor_ids
from splatoon3_ai_coach.coach.death_importance import death_game_clock_sample


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
    source_path: str


class CoachLlmView(BaseModel):
    """Model-facing coaching context (semantic compression of CoachInput)."""

    schema_version: int = 1
    unit: dict[str, Any]
    importance: dict[str, Any]
    death: dict[str, Any] | None = None
    clock: dict[str, Any] | None = None
    roster: dict[str, Any] | None = None
    special: dict[str, Any] | None = None
    map: dict[str, Any] | None = None
    related: list[dict[str, Any]] = Field(default_factory=list)
    evidence_limits: list[dict[str, Any]] = Field(default_factory=list)
    supporting_evidence: list[LlmFact] = Field(default_factory=list)


def build_coach_llm_view(
    coach_input: CoachInput,
    unit: CoachingUnitResult,
) -> CoachLlmView:
    """Dispatch domain-specific LLM view builders by ``unit.candidate_type`` only.

    ``candidate_type`` is authoritative. Scenario type is not used as a fallback.
    Raises ``ValueError`` for unsupported candidate types (no silent full dump).
    """
    candidate_type = unit.candidate_type
    if candidate_type == CANDIDATE_TYPE_DEATH_EPISODE:
        return build_death_llm_view(coach_input, unit)
    raise ValueError(
        f"No CoachLlmView builder for candidate_type={candidate_type!r}"
    )


def build_death_llm_view(
    coach_input: CoachInput,
    unit: CoachingUnitResult,
) -> CoachLlmView:
    """Project a death-episode CoachInput into a compact LLM view.

    Projection only: omit series / reshape existing fields. Does not infer.
    Does not embed locked triad statement/interpretation/recommendation text.
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

    death_block = _project_death(ctx.death_episode)
    clock_block = _project_clock(coach_input)
    roster_block = _project_roster(coach_input)
    special_block = _project_special(ctx.special)
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

    return CoachLlmView(
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
        },
        death=death_block,
        clock=clock_block,
        roster=roster_block,
        special=special_block,
        map=map_block,
        related=related_blocks,
        evidence_limits=limits,
        supporting_evidence=support,
    )


def _project_death(death: Any) -> dict[str, Any] | None:
    if death is None:
        return None
    return {
        "death_time": death.death_time,
        "respawn_time": death.respawn_time,
        "active_again_time": death.active_again_time,
        "death_to_respawn": death.death_to_respawn,
        "death_to_active_again": death.death_to_active_again,
        "complete": death.complete,
        "has_respawn": death.has_respawn,
        "has_active_again": death.has_active_again,
        "respawn_reason": death.respawn_reason,
        "is_first_death": death.is_first_death,
        "numbers_state_at_death": _enum_or_str(death.numbers_state_at_death),
        "match_phase_at_death": _enum_or_str(death.match_phase_at_death),
        "roster_changed_before_death": death.roster_changed_before_death,
        "seconds_since_roster_change": death.seconds_since_roster_change,
        "preceded_by_trade_candidate": death.preceded_by_trade_candidate,
        "time_since_previous_splat": death.time_since_previous_splat,
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
        "seconds_remaining": (
            obs.seconds_remaining if obs is not None else None
        ),
        "display": obs.display if obs is not None else None,
        "confidence": obs.confidence if obs is not None else None,
        "source_path": "game_clock_samples[label=death]",
    }


def _project_roster(coach_input: CoachInput) -> dict[str, Any] | None:
    ctx = coach_input.primary_context
    at_death = None
    if ctx.players is not None and ctx.players.at_death is not None:
        point = ctx.players.at_death
        at_death = {
            "video_time": point.video_time,
            "ally_alive_count": point.ally_alive_count,
            "opponent_alive_count": point.opponent_alive_count,
            "source_path": "primary_context.players.at_death",
        }

    window_points: list[dict[str, Any]] = []
    for index, point in enumerate(coach_input.player_count_window):
        obs = point.observation
        window_points.append(
            LlmRosterWindowPoint(
                offset_seconds=point.offset_seconds,
                video_time=point.video_time,
                ally_alive_count=(
                    obs.ally_alive_count if obs is not None else None
                ),
                opponent_alive_count=(
                    obs.opponent_alive_count if obs is not None else None
                ),
                numbers_state=_enum_or_str(point.numbers_state),
                gap_seconds=point.gap_seconds,
                source_path=f"player_count_window[{index}]",
            ).model_dump(mode="json")
        )

    present = None
    pc_ctx = coach_input.player_count_context
    if pc_ctx is not None:
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

    timeline_gap = None
    if ctx.timeline is not None:
        timeline_gap = {
            "duration": ctx.timeline.duration,
            "time_since_previous_death": ctx.timeline.time_since_previous_death,
            "source_path": "primary_context.timeline",
        }

    if at_death is None and not window_points and present is None and timeline_gap is None:
        return None
    return {
        "at_death": at_death,
        "player_count_window": window_points,
        "player_count_context": present,
        "timeline": timeline_gap,
    }


def _project_special(special: Any) -> dict[str, Any] | None:
    if special is None or special.nearest_before_anchor is None:
        return None
    nb = special.nearest_before_anchor
    return {
        "nearest_before_anchor": {
            "video_time": nb.video_time,
            "visible": nb.visible,
            "ready": nb.ready,
            "fill_fraction": nb.fill_fraction,
            "confidence": nb.confidence,
            "source_path": "primary_context.special.nearest_before_anchor",
        }
    }


def _project_map(map_ctx: Any) -> dict[str, Any] | None:
    if map_ctx is None:
        return None
    return {
        "map_check_before_death": map_ctx.map_check_before_death,
        "seconds_since_map_check_before_death": (
            map_ctx.seconds_since_map_check_before_death
        ),
        "map_check_count": map_ctx.map_check_count,
        "map_checked_while_dead": map_ctx.map_checked_while_dead,
        "source_path": "primary_context.map",
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
