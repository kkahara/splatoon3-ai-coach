"""Death-episode candidate scoring (first coaching domain).

Produces ``CoachingUnitResult`` / ``CoachingCandidate`` with death-scoped
importance factors. Match-wide top-N ranking lives in ``coaching_candidates``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from splatoon3_ai_coach.analysis.player_count_series import format_avb
from splatoon3_ai_coach.analysis.scenario_models import ScenarioType
from splatoon3_ai_coach.coach.claim_catalog import (
    CANDIDATE_TYPE_DEATH_EPISODE,
    DEFAULT_DEATH_IMPORTANCE_WEIGHTS,
    DEFAULT_MATCH_DURATION_CANDIDATES,
    FACTOR_ANNOTATIONS,
    FINAL_30S_REMAINING,
    FIRST_30S_ELAPSED,
    REDEATH_MAX_GAP_SECONDS,
    CoachingUnitResult,
    DeathImportanceFactorId,
    SupportingEvidenceItem,
)
from splatoon3_ai_coach.coach.coach_input import CoachInput, GameClockSample
from splatoon3_ai_coach.coach.coaching_candidates import (
    CoachingCandidate,
    ImportanceFactorContribution,
)
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


def detect_death_importance_factors(
    coach_input: CoachInput,
    *,
    match_duration_seconds: int | None = None,
) -> dict[DeathImportanceFactorId, bool]:
    """Evaluate death-scoped importance factors (True when evidence supports)."""
    flags = {factor_id: False for factor_id in DeathImportanceFactorId}
    if coach_input.primary_scenario.scenario_type is not ScenarioType.DEATH_EPISODE:
        return flags

    ctx = coach_input.primary_context
    players = ctx.players
    if (
        players is not None
        and players.at_death is not None
        and int(players.at_death.ally_alive_count) == 1
    ):
        flags[DeathImportanceFactorId.DEATH_LAST_ALLY_ALIVE] = True

    timeline = ctx.timeline
    if (
        timeline is not None
        and timeline.time_since_previous_death is not None
        and float(timeline.time_since_previous_death) <= REDEATH_MAX_GAP_SECONDS
    ):
        flags[DeathImportanceFactorId.DEATH_REDEATH_LE_10S] = True

    special = ctx.special
    if (
        special is not None
        and special.nearest_before_anchor is not None
        and special.nearest_before_anchor.ready is True
    ):
        flags[DeathImportanceFactorId.DEATH_SPECIAL_READY] = True

    clock = death_game_clock_sample(coach_input)
    rem = (
        clock.observation.seconds_remaining
        if clock is not None and clock.observation is not None
        else None
    )
    if rem is not None and int(rem) <= FINAL_30S_REMAINING:
        flags[DeathImportanceFactorId.DEATH_FINAL_30S] = True
    if (
        rem is not None
        and match_duration_seconds is not None
        and int(rem) >= int(match_duration_seconds) - FIRST_30S_ELAPSED
    ):
        flags[DeathImportanceFactorId.DEATH_FIRST_30S] = True

    map_ctx = ctx.map
    if map_ctx is not None and map_ctx.map_check_before_death is False:
        flags[DeathImportanceFactorId.DEATH_MAP_OVERLAY_BEFORE_FALSE] = True

    return flags


def score_death_candidate(
    coach_input: CoachInput,
    *,
    match_duration_seconds: int | None = None,
    weights: Mapping[str, float] | None = None,
) -> CoachingUnitResult:
    """Build a scored death-episode coaching unit (not yet match-ranked)."""
    scenario = coach_input.primary_scenario
    if scenario.scenario_type is not ScenarioType.DEATH_EPISODE:
        raise ValueError(
            f"score_death_candidate expects DEATH_EPISODE, got {scenario.scenario_type}"
        )

    weight_map = dict(DEFAULT_DEATH_IMPORTANCE_WEIGHTS)
    if weights:
        weight_map.update({str(k): float(v) for k, v in weights.items()})

    flags = detect_death_importance_factors(
        coach_input, match_duration_seconds=match_duration_seconds
    )
    factors: list[ImportanceFactorContribution] = []
    score = 0.0
    for factor_id in DeathImportanceFactorId:
        active = bool(flags[factor_id])
        weight = float(weight_map.get(factor_id.value, 0.0))
        contribution = weight if active else 0.0
        score += contribution
        annotation = FACTOR_ANNOTATIONS[factor_id]
        statement_player = annotation.statement_player
        statement_internal = annotation.statement_internal
        if active and factor_id is DeathImportanceFactorId.DEATH_REDEATH_LE_10S:
            statement_player, statement_internal = _redeath_statements(coach_input)
        factors.append(
            ImportanceFactorContribution(
                factor_id=factor_id.value,
                weight=weight,
                contribution=contribution,
                active=active,
                statement_player=statement_player if active else None,
                statement_internal=statement_internal if active else None,
                interpretation=annotation.interpretation if active else None,
                recommendation=annotation.recommendation if active else None,
            )
        )

    return CoachingUnitResult(
        candidate_id=scenario.scenario_id,
        candidate_type=CANDIDATE_TYPE_DEATH_EPISODE,
        video_time=float(scenario.start_time),
        importance_score=score,
        factors=factors,
        rank=None,
        selected_for_llm=False,
        supporting_evidence=build_supporting_evidence(coach_input),
        match_duration_seconds=match_duration_seconds,
    )


def apply_candidate_ranking(
    units: Sequence[CoachingUnitResult],
    ranked: Sequence[CoachingCandidate],
) -> list[CoachingUnitResult]:
    """Merge type-agnostic ranking back onto scored units by candidate_id."""
    by_id = {c.candidate_id: c for c in ranked}
    merged: list[CoachingUnitResult] = []
    for unit in units:
        candidate = by_id.get(unit.candidate_id)
        if candidate is None:
            merged.append(unit)
            continue
        merged.append(unit.with_ranking(candidate))
    return merged


def build_supporting_evidence(
    coach_input: CoachInput,
) -> list[SupportingEvidenceItem]:
    """Compact facts for VMV 'why' — not coaching cards."""
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


def _redeath_statements(coach_input: CoachInput) -> tuple[str, str]:
    timeline = coach_input.primary_context.timeline
    if timeline is None or timeline.time_since_previous_death is None:
        return (
            "Two consecutive deaths occurred within 10 seconds.",
            f"timeline.time_since_previous_death <= {REDEATH_MAX_GAP_SECONDS}",
        )
    gap = float(timeline.time_since_previous_death)
    return (
        f"Two consecutive deaths occurred {gap:.1f} seconds apart.",
        f"timeline.time_since_previous_death={gap:.1f} "
        f"(<= {REDEATH_MAX_GAP_SECONDS})",
    )


def _fmt_mmss(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"
