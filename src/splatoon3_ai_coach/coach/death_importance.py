"""Death-episode candidate scoring (first coaching domain).

Produces ``CoachingUnitResult`` / ``CoachingCandidate`` with death-scoped
importance factors. Match-wide top-N ranking lives in ``coaching_candidates``.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

from splatoon3_ai_coach.analysis.map_coverage import MapCheckStatus
from splatoon3_ai_coach.analysis.player_count_series import format_avb
from splatoon3_ai_coach.analysis.scenario_evidence import (
    SpecialEvidence,
    SpecialGaugeState,
    SpecialReading,
    special_gauge_state,
)
from splatoon3_ai_coach.analysis.scenario_models import ScenarioType
from splatoon3_ai_coach.coach.claim_catalog import (
    CANDIDATE_TYPE_DEATH_EPISODE,
    DEFAULT_DEATH_IMPORTANCE_WEIGHTS,
    DEFAULT_DEATH_MODIFIER_FACTORS,
    DEFAULT_DEATH_RANKING_EXCLUDED_FACTORS,
    DEFAULT_MATCH_DURATION_CANDIDATES,
    FACTOR_ANNOTATIONS,
    CoachingUnitResult,
    DeathImportanceFactorId,
    RosterSample,
    SupportingEvidenceItem,
)
from splatoon3_ai_coach.coach.coach_input import CoachInput, GameClockSample
from splatoon3_ai_coach.coach.coaching_candidates import (
    CoachingCandidate,
    ImportanceFactorContribution,
)
from splatoon3_ai_coach.coach.game_clock import GameClock
from splatoon3_ai_coach.coach.playback_clock import (
    ClockedDuration,
    PlaybackClockInfo,
    clock_video_span,
)
from splatoon3_ai_coach.coach.score_facts import ScoreFacts
from splatoon3_ai_coach.config.models import DeathFactorThresholds


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


def previous_death_gap(coach_input: CoachInput) -> ClockedDuration | None:
    """Clocked gap since the previous death (``value`` None when unverified)."""
    durations = coach_input.durations
    return durations.time_since_previous_death if durations is not None else None


def own_splat_gap(coach_input: CoachInput) -> float | None:
    """Video seconds from the player's last splat to this death, when observed."""
    death = coach_input.primary_context.death_episode
    gap = death.time_since_previous_splat if death is not None else None
    return None if gap is None else float(gap)


def current_life_start(coach_input: CoachInput) -> float | None:
    """Video time of the previous death (start of this life), when known.

    Gauge readings at or before it belong to an earlier life: special charge
    is not carried across a death, so they never describe this death.
    """
    timeline = coach_input.primary_context.timeline
    gap = timeline.time_since_previous_death if timeline is not None else None
    if gap is None:
        return None
    return float(coach_input.primary_scenario.start_time) - float(gap)


def same_life_last_ready(coach_input: CoachInput) -> SpecialReading | None:
    """``special.last_ready_before_anchor`` only when it is from this life."""
    special = coach_input.primary_context.special
    ready = special.last_ready_before_anchor if special is not None else None
    start = current_life_start(coach_input)
    if ready is None or (start is not None and float(ready.video_time) <= start):
        return None
    return ready


def format_clocked(duration: ClockedDuration) -> str:
    """Human text that never presents unverified video seconds as play time."""
    if duration.value is None:
        return f"{duration.video_seconds:.1f}s of video (playback speed unverified)"
    if duration.clock == "game":
        return f"{duration.value:.0f}s (game clock)"
    return f"{duration.value:.1f}s"


def pre_death_roster(
    coach_input: CoachInput,
    *,
    offset_seconds: float,
) -> RosterSample | None:
    """Last roster trajectory point at or before ``death_time - offset_seconds``.

    ``players.at_death`` is nearest by ``|Δt|`` and can land just after the
    death, already counting the player's own roster X marker. This never
    looks past the cutoff. ``None`` when no trajectory point qualifies.
    """
    ctx = coach_input.primary_context
    death = ctx.death_episode
    if ctx.players is None or death is None or death.death_time is None:
        return None
    cutoff = float(death.death_time) - float(offset_seconds)
    chosen = None
    for index, point in enumerate(ctx.players.trajectory):
        if float(point.video_time) > cutoff:
            continue
        if chosen is None or float(point.video_time) >= float(chosen[1].video_time):
            chosen = (index, point)
    if chosen is None:
        return None
    index, point = chosen
    if not point.reliable or ctx.players.unreliable_at(death.death_time):
        return None
    return RosterSample(
        video_time=float(point.video_time),
        ally_alive_count=int(point.ally_alive_count),
        opponent_alive_count=int(point.opponent_alive_count),
        source_path=f"primary_context.players.trajectory[{index}]",
    )


def detect_death_importance_factors(
    coach_input: CoachInput,
    *,
    match_duration_seconds: int | None = None,
    thresholds: DeathFactorThresholds | None = None,
) -> dict[DeathImportanceFactorId, bool]:
    """Evaluate death-scoped importance factors (True when evidence supports).

    Factors name factual circumstances only; they are never verdicts.
    No map check is not counted for a death in the first window: there has
    hardly been time to open the map.
    """
    flags = {factor_id: False for factor_id in DeathImportanceFactorId}
    if coach_input.primary_scenario.scenario_type is not ScenarioType.DEATH_EPISODE:
        return flags
    limits = thresholds or DeathFactorThresholds()
    roster = pre_death_roster(
        coach_input, offset_seconds=limits.roster_pre_death_offset_seconds
    )
    flags.update(_roster_flags(roster, limits.roster_min_gap))
    flags.update(_clock_flags(coach_input, match_duration_seconds, limits))
    flags.update(_count_flags(coach_input.score_facts, limits.count_min_diff))

    ctx = coach_input.primary_context
    gap = previous_death_gap(coach_input)
    if (
        gap is not None
        and gap.value is not None
        and float(gap.value) <= limits.redeath_max_gap_seconds
    ):
        flags[DeathImportanceFactorId.DEATH_QUICK_REDEATH] = True

    if _special_ready_run(coach_input) >= limits.special_ready_min_readings:
        flags[DeathImportanceFactorId.DEATH_SPECIAL_READY] = True

    splat_gap = own_splat_gap(coach_input)
    if splat_gap is not None and splat_gap <= limits.own_splat_max_gap_seconds:
        flags[DeathImportanceFactorId.DEATH_OWN_SPLAT_BEFORE] = True

    map_ctx = ctx.map
    if (
        map_ctx is not None
        and map_ctx.map_check_status == MapCheckStatus.NOT_OBSERVED
        and not flags[DeathImportanceFactorId.DEATH_FIRST_30S]
    ):
        flags[DeathImportanceFactorId.DEATH_MAP_OVERLAY_BEFORE_FALSE] = True
    return flags


def factor_threshold_entries(
    coach_input: CoachInput,
    thresholds: DeathFactorThresholds | None = None,
) -> dict[str, dict]:
    """Configured thresholds each death factor reads, active or not.

    Only values a factor's rule actually uses; factor IDs keep historical
    names (e.g. ``_le_10s``), so the number comes from here, not the ID.
    """
    limits = thresholds or DeathFactorThresholds()
    gap = previous_death_gap(coach_input)
    roster = {
        "min_gap": limits.roster_min_gap,
        "pre_death_offset_seconds": limits.roster_pre_death_offset_seconds,
    }
    count = {"min_diff": limits.count_min_diff}
    ids = DeathImportanceFactorId
    return {
        ids.DEATH_QUICK_REDEATH.value: {
            "threshold_seconds": limits.redeath_max_gap_seconds,
            "observed_gap_seconds": gap.value if gap is not None else None,
            "clock": gap.clock if gap is not None else None,
        },
        ids.DEATH_FIRST_30S.value: {"window_seconds": limits.first_window_seconds},
        ids.DEATH_FINAL_30S.value: {"window_seconds": limits.final_window_seconds},
        ids.DEATH_WHILE_OUTNUMBERED.value: dict(roster),
        ids.DEATH_WHILE_AHEAD_IN_NUMBERS.value: dict(roster),
        ids.DEATH_WHILE_BEHIND_IN_COUNT.value: dict(count),
        ids.DEATH_WHILE_AHEAD_IN_COUNT.value: dict(count),
        ids.DEATH_SPECIAL_READY.value: {
            "min_readings": limits.special_ready_min_readings
        },
        ids.DEATH_OWN_SPLAT_BEFORE.value: {
            "max_gap_seconds": limits.own_splat_max_gap_seconds,
            "observed_gap_seconds": own_splat_gap(coach_input),
        },
    }


def _special_ready_run(coach_input: CoachInput) -> int:
    """Consecutive ready gauge readings ending at the nearest one before death.

    Zero unless ``special.nearest_before_anchor`` (the recency-bounded reading
    before the death) is itself ready.
    """
    ctx = coach_input.primary_context
    special = ctx.special
    if special is None or special.nearest_before_anchor is None:
        return 0
    nearest = special.nearest_before_anchor
    if special_gauge_state(nearest) is not SpecialGaugeState.READY:
        return 0
    start = current_life_start(coach_input)
    if start is not None and float(nearest.video_time) <= start:
        return 0
    run = 0
    for reading in sorted(special.observations, key=lambda r: -float(r.video_time)):
        if float(reading.video_time) > float(nearest.video_time):
            continue
        if start is not None and float(reading.video_time) <= start:
            break
        if reading.coaching_valid is False:
            continue
        if special_gauge_state(reading) is not SpecialGaugeState.READY:
            break
        run += 1
    return max(run, 1)


def _roster_flags(
    roster: RosterSample | None,
    min_gap: int,
) -> dict[DeathImportanceFactorId, bool]:
    if roster is None:
        return {}
    gap = roster.ally_alive_count - roster.opponent_alive_count
    return {
        DeathImportanceFactorId.DEATH_WHILE_OUTNUMBERED: gap <= -min_gap,
        DeathImportanceFactorId.DEATH_WHILE_AHEAD_IN_NUMBERS: gap >= min_gap,
    }


def _count_flags(
    facts: ScoreFacts | None, min_diff: int
) -> dict[DeathImportanceFactorId, bool]:
    """Splat Zones count circumstances at the pre-death sample (mode-scoped)."""
    if facts is None or not facts.is_splat_zones or facts.sample_label != "pre_death":
        return {}
    flags = {
        DeathImportanceFactorId.DEATH_OPPONENT_COUNTER_TICKED: (
            facts.opponent_counter_decreased_before_death is True
        )
    }
    diff = facts.remaining_diff
    if diff is not None:
        flags[DeathImportanceFactorId.DEATH_WHILE_BEHIND_IN_COUNT] = diff <= -min_diff
        flags[DeathImportanceFactorId.DEATH_WHILE_AHEAD_IN_COUNT] = diff >= min_diff
    return flags


def _clock_flags(
    coach_input: CoachInput,
    match_duration_seconds: int | None,
    limits: DeathFactorThresholds,
) -> dict[DeathImportanceFactorId, bool]:
    clock = death_game_clock_sample(coach_input)
    if clock is None or clock.observation is None:
        return {}
    rem = int(clock.observation.seconds_remaining)
    first = (
        match_duration_seconds is not None
        and rem >= int(match_duration_seconds) - limits.first_window_seconds
    )
    return {
        DeathImportanceFactorId.DEATH_FINAL_30S: rem <= limits.final_window_seconds,
        DeathImportanceFactorId.DEATH_FIRST_30S: first,
    }


def score_death_candidate(
    coach_input: CoachInput,
    *,
    match_duration_seconds: int | None = None,
    weights: Mapping[str, float] | None = None,
    thresholds: DeathFactorThresholds | None = None,
    modifier_factors: Collection[str] | None = None,
    ranking_excluded_factors: Collection[str] | None = None,
) -> CoachingUnitResult:
    """Build a scored death-episode coaching unit (not yet match-ranked).

    A modifier factor stays active as a fact but contributes its weight only
    when some non-modifier factor is active too.
    """
    scenario = coach_input.primary_scenario
    if scenario.scenario_type is not ScenarioType.DEATH_EPISODE:
        raise ValueError(
            f"score_death_candidate expects DEATH_EPISODE, got {scenario.scenario_type}"
        )
    limits = thresholds or DeathFactorThresholds()
    weight_map = dict(DEFAULT_DEATH_IMPORTANCE_WEIGHTS)
    if weights:
        weight_map.update({str(k): float(v) for k, v in weights.items()})

    flags = detect_death_importance_factors(
        coach_input,
        match_duration_seconds=match_duration_seconds,
        thresholds=limits,
    )
    modifiers = set(
        DEFAULT_DEATH_MODIFIER_FACTORS if modifier_factors is None else modifier_factors
    )
    excluded = set(
        DEFAULT_DEATH_RANKING_EXCLUDED_FACTORS
        if ranking_excluded_factors is None
        else ranking_excluded_factors
    )
    supported = any(
        on and f.value not in modifiers and f.value not in excluded
        for f, on in flags.items()
    )
    factors = [
        _factor_contribution(
            factor_id,
            active=bool(flags[factor_id]),
            counts=(
                factor_id.value not in excluded
                and (supported or factor_id.value not in modifiers)
            ),
            weight=float(weight_map.get(factor_id.value, 0.0)),
            coach_input=coach_input,
            limits=limits,
        )
        for factor_id in DeathImportanceFactorId
    ]
    roster = pre_death_roster(
        coach_input, offset_seconds=limits.roster_pre_death_offset_seconds
    )
    return CoachingUnitResult(
        candidate_id=scenario.scenario_id,
        candidate_type=CANDIDATE_TYPE_DEATH_EPISODE,
        video_time=float(scenario.start_time),
        importance_score=sum(f.contribution for f in factors),
        factors=factors,
        rank=None,
        selected_for_llm=False,
        supporting_evidence=build_supporting_evidence(coach_input, roster=roster),
        match_duration_seconds=match_duration_seconds,
        roster_before_death=roster,
    )


def _factor_contribution(
    factor_id: DeathImportanceFactorId,
    *,
    active: bool,
    counts: bool,
    weight: float,
    coach_input: CoachInput,
    limits: DeathFactorThresholds,
) -> ImportanceFactorContribution:
    annotation = FACTOR_ANNOTATIONS[factor_id]
    statement_player = annotation.statement_player
    statement_internal = annotation.statement_internal
    if active:
        custom = _threshold_statements(factor_id, coach_input, limits)
        if custom is not None:
            statement_player, statement_internal = custom
    return ImportanceFactorContribution(
        factor_id=factor_id.value,
        weight=weight,
        contribution=weight if active and counts else 0.0,
        active=active,
        statement_player=statement_player if active else None,
        statement_internal=statement_internal if active else None,
        interpretation=annotation.interpretation if active else None,
        recommendation=annotation.recommendation if active else None,
    )


def _threshold_statements(
    factor_id: DeathImportanceFactorId,
    coach_input: CoachInput,
    limits: DeathFactorThresholds,
) -> tuple[str, str] | None:
    """Copy that quotes the configured threshold rather than a fixed number."""
    if factor_id is DeathImportanceFactorId.DEATH_QUICK_REDEATH:
        return _redeath_statements(coach_input, limits.redeath_max_gap_seconds)
    if factor_id is DeathImportanceFactorId.DEATH_FINAL_30S:
        seconds = limits.final_window_seconds
        return (
            f"Death occurred during the final {seconds} seconds of the match.",
            f"Death-labeled game clock seconds_remaining <= {seconds}.",
        )
    if factor_id is DeathImportanceFactorId.DEATH_FIRST_30S:
        seconds = limits.first_window_seconds
        return (
            f"Death occurred during the first {seconds} seconds of the match.",
            f"Death-labeled game clock seconds_remaining >= D - {seconds} "
            "(match duration D known).",
        )
    if factor_id is DeathImportanceFactorId.DEATH_OWN_SPLAT_BEFORE:
        return _own_splat_statements(coach_input, limits.own_splat_max_gap_seconds)
    if factor_id in (
        DeathImportanceFactorId.DEATH_WHILE_OUTNUMBERED,
        DeathImportanceFactorId.DEATH_WHILE_AHEAD_IN_NUMBERS,
    ):
        return _roster_statements(coach_input, limits)
    if factor_id in (
        DeathImportanceFactorId.DEATH_WHILE_BEHIND_IN_COUNT,
        DeathImportanceFactorId.DEATH_WHILE_AHEAD_IN_COUNT,
    ):
        return _count_statements(coach_input.score_facts, limits.count_min_diff)
    return None


def _count_statements(facts: ScoreFacts | None, min_diff: int) -> tuple[str, str] | None:
    if facts is None or facts.ally_remaining is None or facts.opponent_remaining is None:
        return None
    ally, opponent = facts.ally_remaining, facts.opponent_remaining
    return (
        f"Just before you were splatted, your team needed {ally} more counts "
        f"and the opponents needed {opponent}.",
        f"Splat Zones pre-death sample at {facts.video_time:.1f}s: remaining "
        f"{ally} vs {opponent} (|diff| >= {min_diff}; {facts.source_path}).",
    )


def _roster_statements(
    coach_input: CoachInput, limits: DeathFactorThresholds
) -> tuple[str, str] | None:
    roster = pre_death_roster(
        coach_input, offset_seconds=limits.roster_pre_death_offset_seconds
    )
    if roster is None:
        return None
    ally, opponent = roster.ally_alive_count, roster.opponent_alive_count
    players = "player" if ally == 1 else "players"
    return (
        f"Just before you were splatted, your team had {ally} {players} alive "
        f"and the opponents had {opponent}.",
        f"Pre-death roster sample at {roster.video_time:.1f}s: {ally} vs "
        f"{opponent} alive (gap >= {limits.roster_min_gap}; {roster.source_path}).",
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
    *,
    roster: RosterSample | None = None,
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

    if roster is not None:
        items.append(
            SupportingEvidenceItem(
                label="Roster before death",
                value=format_avb(roster.ally_alive_count, roster.opponent_alive_count),
                path=roster.source_path,
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
        items.append(
            _special_item(
                ctx.special,
                last_ready=same_life_last_ready(coach_input),
                death_time=float(death_time),
                playback=coach_input.playback_clock,
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

    gap = previous_death_gap(coach_input)
    if gap is not None:
        items.append(
            SupportingEvidenceItem(
                label="Since previous death",
                value=format_clocked(gap),
                path="durations.time_since_previous_death",
            )
        )

    if ctx.map is not None and ctx.map.map_check_status is not None:
        gap = ctx.map.seconds_since_map_check_before_death
        gap_txt = ""
        if gap is not None:
            span = clock_video_span(gap, coach_input.playback_clock)
            gap_txt = f" ({format_clocked(span)} earlier)"
        reason = ctx.map.map_check_unknown_reason
        reason_txt = f" ({reason.value})" if reason is not None else ""
        items.append(
            SupportingEvidenceItem(
                label="Map overlay before death",
                value=f"{ctx.map.map_check_status.value}{gap_txt}{reason_txt}",
                path="primary_context.map.map_check_status",
            )
        )

    return items


_SPECIAL_STATE_TEXT = {
    SpecialGaugeState.READY: "ready",
    SpecialGaugeState.NOT_READY: "not ready",
    SpecialGaugeState.NOT_OBSERVED: "not observed",
}


def _special_item(
    special: SpecialEvidence,
    *,
    last_ready: SpecialReading | None,
    death_time: float,
    playback: PlaybackClockInfo | None,
) -> SupportingEvidenceItem:
    """Gauge state at the death; names the last ready / last visible reading.

    A last-ready reading is a gauge observation, never a usage event, and is
    named only when it is from the same life as the death.
    """
    nearest = special.nearest_before_anchor
    state = special_gauge_state(nearest)
    value = _SPECIAL_STATE_TEXT[state]
    path = "primary_context.special.nearest_before_anchor"

    def before(reading: SpecialReading) -> str:
        gap = clock_video_span(death_time - float(reading.video_time), playback)
        return f"{format_clocked(gap)} before"

    if state is not SpecialGaugeState.NOT_OBSERVED and nearest is not None:
        if nearest.fill_fraction is not None:
            value += f", fill={nearest.fill_fraction:.2f}"
        if state is not SpecialGaugeState.READY and last_ready is not None:
            value += f" (last ready {before(last_ready)})"
        return SupportingEvidenceItem(label="Special", value=value, path=path)
    last = special.last_visible_before_anchor
    if last is not None:
        value += (
            f" (last observed {before(last)}: "
            f"{_SPECIAL_STATE_TEXT[special_gauge_state(last)]})"
        )
    return SupportingEvidenceItem(label="Special", value=value, path=path)


def _own_splat_statements(
    coach_input: CoachInput, max_gap_seconds: float
) -> tuple[str, str] | None:
    gap = own_splat_gap(coach_input)
    clocked = clock_video_span(gap, coach_input.playback_clock)
    if gap is None or clocked is None:
        return None
    span = format_clocked(clocked)
    return (
        f"You got a splat {span} before you were splatted.",
        f"death_episode.time_since_previous_splat={gap:.1f} <= {max_gap_seconds:g} "
        "(association only, never a trade).",
    )


def _redeath_statements(
    coach_input: CoachInput, max_gap_seconds: float
) -> tuple[str, str]:
    gap = previous_death_gap(coach_input)
    if gap is None or gap.value is None:
        return (
            f"Two consecutive deaths occurred within {max_gap_seconds:g} seconds.",
            f"durations.time_since_previous_death <= {max_gap_seconds}",
        )
    clock = "game-clock " if gap.clock == "game" else ""
    return (
        f"Two consecutive deaths occurred {gap.value:.1f} {clock}seconds apart.",
        f"durations.time_since_previous_death={gap.value:.1f} "
        f"(clock={gap.clock}; <= {max_gap_seconds})",
    )


def _fmt_mmss(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60}:{total % 60:02d}"
