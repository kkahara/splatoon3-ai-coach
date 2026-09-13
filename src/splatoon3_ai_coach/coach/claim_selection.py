"""Eligible claims → select 0–N → coaching points + supporting evidence.

Does not mutate ScenarioContext. Primary units are expected to be
``DEATH_EPISODE``; other types yield empty selection.
"""

from __future__ import annotations

from collections.abc import Sequence

from splatoon3_ai_coach.analysis.scenario_models import ScenarioType
from splatoon3_ai_coach.coach.claim_catalog import (
    CLAIM_TEMPLATES,
    DEFAULT_MATCH_DURATION_CANDIDATES,
    DEFAULT_MAX_COACHING_POINTS,
    FINAL_30S_REMAINING,
    FIRST_30S_ELAPSED,
    REDEATH_MAX_GAP_SECONDS,
    ClaimId,
    ClaimPriority,
    ClaimTemplate,
    CoachingPoint,
    CoachingUnitResult,
    SupportingEvidenceItem,
)
from splatoon3_ai_coach.analysis.player_count_series import format_avb
from splatoon3_ai_coach.coach.coach_input import CoachInput, GameClockSample
from splatoon3_ai_coach.coach.game_clock import GameClock


def resolve_match_duration_seconds(
    game_clock: GameClock | None,
    *,
    candidates: Sequence[int] = DEFAULT_MATCH_DURATION_CANDIDATES,
) -> int | None:
    """Infer match length D from observed timer peaks in ``candidates``.

    Uses the maximum observed ``seconds_remaining`` when it is an allowed
    opening duration. Never invents ``300 - video_time``.
    """
    if game_clock is None or not game_clock.observations:
        return None
    allowed = {int(value) for value in candidates}
    peak = max(int(obs.seconds_remaining) for obs in game_clock.observations)
    if peak in allowed:
        return peak
    return None


def death_game_clock_sample(coach_input: CoachInput) -> GameClockSample | None:
    """Return the death-labeled game-clock sample when present."""
    for sample in coach_input.game_clock_samples:
        if sample.label == "death":
            return sample
    return None


def eligible_claim_ids(
    coach_input: CoachInput,
    *,
    match_duration_seconds: int | None = None,
) -> list[ClaimId]:
    """Return claim IDs whose evidence gates pass (unordered)."""
    if coach_input.primary_scenario.scenario_type is not ScenarioType.DEATH_EPISODE:
        return []
    found: list[ClaimId] = []
    ctx = coach_input.primary_context

    players = ctx.players
    if (
        players is not None
        and players.at_death is not None
        and int(players.at_death.ally_alive_count) == 1
    ):
        found.append(ClaimId.DEATH_LAST_ALLY_ALIVE)

    timeline = ctx.timeline
    if (
        timeline is not None
        and timeline.time_since_previous_death is not None
        and float(timeline.time_since_previous_death) <= REDEATH_MAX_GAP_SECONDS
    ):
        found.append(ClaimId.DEATH_REDEATH_LE_10S)

    special = ctx.special
    if (
        special is not None
        and special.nearest_before_anchor is not None
        and special.nearest_before_anchor.ready is True
    ):
        found.append(ClaimId.DEATH_SPECIAL_READY)

    clock = death_game_clock_sample(coach_input)
    rem = (
        clock.observation.seconds_remaining
        if clock is not None and clock.observation is not None
        else None
    )
    if rem is not None and int(rem) <= FINAL_30S_REMAINING:
        found.append(ClaimId.DEATH_FINAL_30S)
    if (
        rem is not None
        and match_duration_seconds is not None
        and int(rem) >= int(match_duration_seconds) - FIRST_30S_ELAPSED
    ):
        found.append(ClaimId.DEATH_FIRST_30S)

    map_ctx = ctx.map
    if map_ctx is not None and map_ctx.map_check_before_death is False:
        found.append(ClaimId.DEATH_MAP_OVERLAY_BEFORE_FALSE)

    return found


def select_coaching_unit(
    coach_input: CoachInput,
    *,
    match_duration_seconds: int | None = None,
    max_points: int = DEFAULT_MAX_COACHING_POINTS,
) -> CoachingUnitResult:
    """Select 0–N coaching points and collect supporting evidence."""
    scenario = coach_input.primary_scenario
    eligible = eligible_claim_ids(
        coach_input, match_duration_seconds=match_duration_seconds
    )
    selected = _select_claim_ids(eligible, max_points=max_points)
    points = [
        _build_point(claim_id, coach_input) for claim_id in selected
    ]
    support = build_supporting_evidence(coach_input, selected_ids=selected)
    return CoachingUnitResult(
        scenario_id=scenario.scenario_id,
        scenario_type=scenario.scenario_type.value,
        video_time=float(scenario.start_time),
        eligible_claim_ids=eligible,
        coaching_points=points,
        supporting_evidence=support,
        match_duration_seconds=match_duration_seconds,
    )


def build_supporting_evidence(
    coach_input: CoachInput,
    *,
    selected_ids: Sequence[ClaimId] | None = None,
) -> list[SupportingEvidenceItem]:
    """Compact facts for VMV 'why' — not coaching cards."""
    _ = selected_ids
    items: list[SupportingEvidenceItem] = []
    ctx = coach_input.primary_context
    death_time = (
        ctx.death_episode.death_time
        if ctx.death_episode is not None
        else coach_input.primary_scenario.start_time
    )
    items.append(
        SupportingEvidenceItem(
            label="Death anchor",
            value=_fmt_mmss(float(death_time)),
            path="primary_scenario.start_time",
        )
    )

    if ctx.players is not None and ctx.players.at_death is not None:
        at = ctx.players.at_death
        items.append(
            SupportingEvidenceItem(
                label="Roster at death",
                value=format_avb(at.ally_alive_count, at.opponent_alive_count),
                path="primary_context.players.at_death",
            )
        )
        items.append(
            SupportingEvidenceItem(
                label="Ally alive",
                value=str(at.ally_alive_count),
                path="primary_context.players.at_death.ally_alive_count",
            )
        )
        items.append(
            SupportingEvidenceItem(
                label="Opponent alive",
                value=str(at.opponent_alive_count),
                path="primary_context.players.at_death.opponent_alive_count",
            )
        )

    if ctx.death_episode is not None and ctx.death_episode.numbers_state_at_death:
        items.append(
            SupportingEvidenceItem(
                label="Numbers state",
                value=str(ctx.death_episode.numbers_state_at_death),
                path="primary_context.death_episode.numbers_state_at_death",
            )
        )

    if ctx.special is not None and ctx.special.nearest_before_anchor is not None:
        nb = ctx.special.nearest_before_anchor
        ready = "ready" if nb.ready else "not ready"
        fill = (
            f", fill={nb.fill_fraction:.2f}"
            if nb.fill_fraction is not None
            else ""
        )
        items.append(
            SupportingEvidenceItem(
                label="Special",
                value=f"{ready}{fill}",
                path="primary_context.special.nearest_before_anchor",
            )
        )

    clock = death_game_clock_sample(coach_input)
    if clock is not None and clock.observation is not None:
        rem = int(clock.observation.seconds_remaining)
        items.append(
            SupportingEvidenceItem(
                label="Game clock",
                value=_fmt_mmss(float(rem)),
                path="game_clock_samples[label=death].observation.seconds_remaining",
            )
        )

    if (
        ctx.timeline is not None
        and ctx.timeline.time_since_previous_death is not None
    ):
        items.append(
            SupportingEvidenceItem(
                label="Since previous death",
                value=f"{float(ctx.timeline.time_since_previous_death):.1f}s",
                path="primary_context.timeline.time_since_previous_death",
            )
        )

    if ctx.map is not None and ctx.map.map_check_before_death is not None:
        gap = ctx.map.seconds_since_map_check_before_death
        gap_txt = f" (gap={gap:.1f}s)" if gap is not None else ""
        items.append(
            SupportingEvidenceItem(
                label="Map overlay before death",
                value=f"{ctx.map.map_check_before_death}{gap_txt}",
                path="primary_context.map.map_check_before_death",
            )
        )

    return items


def _select_claim_ids(
    eligible: Sequence[ClaimId],
    *,
    max_points: int,
) -> list[ClaimId]:
    """Prefer actionable claims; drop outranked support-only LOWs."""
    if max_points <= 0 or not eligible:
        return []
    ranked = sorted(
        eligible,
        key=lambda cid: (
            -int(CLAIM_TEMPLATES[cid].priority),
            cid.value,
        ),
    )
    best_priority = CLAIM_TEMPLATES[ranked[0]].priority
    selected: list[ClaimId] = []
    for claim_id in ranked:
        template = CLAIM_TEMPLATES[claim_id]
        if (
            template.support_only_when_outranked
            and template.priority < best_priority
        ):
            continue
        if (
            template.priority is ClaimPriority.LOW
            and best_priority > ClaimPriority.LOW
        ):
            continue
        selected.append(claim_id)
        if len(selected) >= max_points:
            break
    # If only LOW claims remain eligible after filters, keep at most one.
    if not selected and ranked:
        low_only = [
            cid
            for cid in ranked
            if CLAIM_TEMPLATES[cid].priority is ClaimPriority.LOW
        ]
        if low_only:
            return low_only[:1]
    return selected


def _build_point(claim_id: ClaimId, coach_input: CoachInput) -> CoachingPoint:
    template = CLAIM_TEMPLATES[claim_id]
    statement, internal, paths = _statement_for(claim_id, coach_input, template)
    # Catalog values are authoritative: locked copy or explicit None
    # (statement-only / candidate advice not yet locked).
    return CoachingPoint(
        claim_id=claim_id,
        statement=statement,
        statement_internal=internal,
        interpretation=template.interpretation,
        recommendation=template.recommendation,
        evidence_paths=paths,
    )


def _statement_for(
    claim_id: ClaimId,
    coach_input: CoachInput,
    template: ClaimTemplate,
) -> tuple[str, str | None, list[str]]:
    ctx = coach_input.primary_context
    if claim_id is ClaimId.DEATH_REDEATH_LE_10S:
        timeline = ctx.timeline
        if timeline is None or timeline.time_since_previous_death is None:
            raise ValueError("redeath claim selected without timeline gap")
        gap = float(timeline.time_since_previous_death)
        statement = (
            f"Two consecutive deaths occurred {gap:.1f} seconds apart."
        )
        internal = (
            f"timeline.time_since_previous_death={gap:.1f} "
            f"(<= {REDEATH_MAX_GAP_SECONDS})"
        )
        return (
            statement,
            internal,
            ["primary_context.timeline.time_since_previous_death"],
        )

    statement = template.statement_player or claim_id.value
    internal = template.statement_internal
    paths = _default_paths(claim_id)
    return statement, internal, paths


def _default_paths(claim_id: ClaimId) -> list[str]:
    return {
        ClaimId.DEATH_LAST_ALLY_ALIVE: [
            "primary_context.players.at_death.ally_alive_count",
        ],
        ClaimId.DEATH_SPECIAL_READY: [
            "primary_context.special.nearest_before_anchor.ready",
        ],
        ClaimId.DEATH_FINAL_30S: [
            "game_clock_samples[label=death].observation.seconds_remaining",
        ],
        ClaimId.DEATH_FIRST_30S: [
            "game_clock_samples[label=death].observation.seconds_remaining",
        ],
        ClaimId.DEATH_MAP_OVERLAY_BEFORE_FALSE: [
            "primary_context.map.map_check_before_death",
        ],
        ClaimId.DEATH_REDEATH_LE_10S: [
            "primary_context.timeline.time_since_previous_death",
        ],
    }[claim_id]


def _fmt_mmss(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"
