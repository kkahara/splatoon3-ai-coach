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

from collections.abc import Iterable, Sequence
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
from splatoon3_ai_coach.analysis.player_count_series import (
    compress_player_count_observations,
)
from splatoon3_ai_coach.coach.player_count_clock import (
    NumbersState,
    PlayerCountClock,
    PlayerCountObservation,
    PlayerCountWindowPoint,
    format_avb,
    numbers_state,
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


class PlayerCountTrajectoryPoint(BaseModel):
    """Compressed roster change (or anchor hold) for coaching display."""

    video_time: float = Field(ge=0)
    ally_alive_count: int = Field(ge=0, le=4)
    opponent_alive_count: int = Field(ge=0, le=4)
    numbers_differential: int
    numbers_state: NumbersState
    is_anchor: bool = False


class PlayerCountContext(BaseModel):
    """Event-relative roster evidence derived in the coaching layer.

    Three layers stay separate:

    * ``PlayerCountObservation`` — authoritative fused state at a time
    * ``PlayerCountWindowPoint`` — presentation sampling around the anchor
    * this context — coaching-layer temporal derivation

    ``state_present_by`` is the start of the contiguous same-``numbers_state``
    observation run ending at/near the anchor (holes larger than the configured
    max lookup gap end the run). ``duration_since_present_by`` is only
    ``anchor - state_present_by``: cite it as “state was observed by T,
    Δt before the anchor,” never as continuous time spent disadvantaged.
    """

    anchor_video_time: float = Field(ge=0)
    valid_point_count: int = Field(ge=0)
    window_point_count: int = Field(ge=0)
    numbers_state_at_anchor: NumbersState | None = None
    state_at_anchor: str | None = None
    state_present_by: float | None = Field(
        default=None,
        description=(
            "Video time of the earliest observation in the contiguous "
            "numbers_state run ending at the anchor."
        ),
    )
    duration_since_present_by: float | None = Field(
        default=None,
        description=(
            "anchor_video_time - state_present_by. Phrase as "
            "'observed by state_present_by, duration_since_present_by seconds "
            "before the anchor' — not as continuous disadvantaged time."
        ),
    )
    trajectory: list[PlayerCountTrajectoryPoint] = Field(default_factory=list)


class EvidenceLimit(BaseModel):
    """Structured non-claim: what this unit's evidence cannot establish."""

    code: str
    statement: str


_DEFAULT_PLAYER_COUNT_WINDOW_OFFSETS: tuple[float, ...] = (-5.0, -2.0, 0.0, 3.0, 6.0)


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
    offsets = (
        tuple(float(x) for x in player_count_window_offsets_seconds)
        if player_count_window_offsets_seconds is not None
        else _DEFAULT_PLAYER_COUNT_WINDOW_OFFSETS
    )
    context_lookback = (
        8.0
        if player_count_context_lookback_seconds is None
        else float(player_count_context_lookback_seconds)
    )
    player_count_window, player_count_context = _player_count_window_and_context(
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
        player_count_samples,
        player_count_window=player_count_window,
        player_count_context=player_count_context,
    )
    return CoachInput(
        primary_scenario=primary_scenario,
        primary_context=primary_context,
        related=related,
        game_clock_samples=samples,
        player_count_samples=player_count_samples,
        player_count_window=player_count_window,
        player_count_context=player_count_context,
        evidence_limits=limits,
    )


def collect_evidence_limits(
    scenario: Scenario,
    context: ScenarioContext,
    game_clock_samples: list[GameClockSample],
    player_count_samples: list[PlayerCountSample] | None = None,
    *,
    player_count_window: list[PlayerCountWindowPoint] | None = None,
    player_count_context: PlayerCountContext | None = None,
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

    if player_count_context is not None:
        valid = player_count_context.valid_point_count
        total = player_count_context.window_point_count
        if total > 0 and valid <= 1:
            limits.append(
                EvidenceLimit(
                    code="player_count_window_sparse",
                    statement=(
                        f"Player-count window has {valid}/{total} valid points; "
                        "do not treat it as a reliable roster trajectory. "
                        "A fused roster change is not proof of a particular "
                        "teammate death or trade."
                    ),
                )
            )
        elif player_count_window:
            limits.append(
                EvidenceLimit(
                    code="player_count_not_causal",
                statement=(
                    "Fused roster count changes describe team alive totals "
                    "only; they do not identify which player died, prove a "
                    "trade, or establish fight quality. "
                    "duration_since_present_by means the numbers_state was "
                    "observed by state_present_by that many seconds before "
                    "the anchor — not that the team was continuously "
                    "disadvantaged for that entire interval."
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


def _tactical_anchor_time(
    scenario: Scenario,
    context: ScenarioContext,
) -> float | None:
    """Resolve the coaching-layer player-count window anchor, if defined."""
    if scenario.scenario_type is ScenarioType.DEATH_EPISODE:
        death = context.death_episode
        if death is not None and death.death_time is not None:
            return float(death.death_time)
        return float(scenario.start_time)
    if scenario.scenario_type is ScenarioType.ENGAGEMENT:
        combat = context.combat
        if combat is not None and combat.first_splat_time is not None:
            return float(combat.first_splat_time)
        return float(scenario.start_time)
    return None


def _player_count_window_and_context(
    scenario: Scenario,
    context: ScenarioContext,
    clock: PlayerCountClock,
    *,
    offsets: Sequence[float],
    max_gap_seconds: float,
    context_lookback_seconds: float,
) -> tuple[list[PlayerCountWindowPoint], PlayerCountContext | None]:
    """Build presentation window + derived present_by context for the anchor."""
    anchor = _tactical_anchor_time(scenario, context)
    if anchor is None:
        return [], None
    window = clock.window(anchor, offsets, max_gap_seconds=max_gap_seconds)
    valid = sum(1 for point in window if point.observation is not None)
    window_lookback = abs(min((float(o) for o in offsets), default=0.0))
    lookback = max(window_lookback, float(context_lookback_seconds))
    look_ahead = max((float(o) for o in offsets), default=0.0)
    range_obs = clock.observations_between(
        max(0.0, anchor - lookback),
        anchor + max(0.0, look_ahead),
    )
    derived = _derive_player_count_context(
        anchor_time=anchor,
        window=window,
        range_observations=range_obs,
        valid_point_count=valid,
        max_gap_seconds=max_gap_seconds,
    )
    return window, derived


def _derive_player_count_context(
    *,
    anchor_time: float,
    window: list[PlayerCountWindowPoint],
    range_observations: list[PlayerCountObservation],
    valid_point_count: int,
    max_gap_seconds: float,
) -> PlayerCountContext:
    """Interpret retrieved observations into present_by / trajectory evidence."""
    anchor_point = next((p for p in window if p.offset_seconds == 0.0), None)
    anchor_obs = anchor_point.observation if anchor_point is not None else None
    if anchor_obs is None:
        # Prefer an exact/near observation from the range scan at the anchor.
        for obs in range_observations:
            if abs(obs.video_time - anchor_time) < 1e-9:
                anchor_obs = obs
                break
    state_at_anchor = format_avb(anchor_obs) if anchor_obs is not None else None
    state_name = numbers_state(anchor_obs)
    present_by: float | None = None
    duration: float | None = None
    if state_name is not None and anchor_obs is not None:
        present_by = _state_present_by(
            range_observations,
            anchor_time=anchor_time,
            target_state=state_name,
            max_gap_seconds=max_gap_seconds,
        )
        if present_by is not None:
            duration = float(anchor_time) - float(present_by)
    trajectory = _compress_trajectory(
        range_observations,
        anchor_time=anchor_time,
        anchor_obs=anchor_obs,
    )
    return PlayerCountContext(
        anchor_video_time=float(anchor_time),
        valid_point_count=valid_point_count,
        window_point_count=len(window),
        numbers_state_at_anchor=state_name,
        state_at_anchor=state_at_anchor,
        state_present_by=present_by,
        duration_since_present_by=duration,
        trajectory=trajectory,
    )


def _state_present_by(
    observations: list[PlayerCountObservation],
    *,
    anchor_time: float,
    target_state: NumbersState,
    max_gap_seconds: float,
) -> float | None:
    """Earliest contiguous observation of ``target_state`` ending at/near anchor.

    Walks backward from the last observation at or before the anchor while the
    numbers_state matches **and** consecutive samples are within
    ``max_gap_seconds``. A larger hole ends the run: we do not invent continuity
    across missing observations. Returns that first observed time
    (``present_by``), not a claimed exact transition instant.
    """
    before_or_at = [obs for obs in observations if obs.video_time <= anchor_time + 1e-9]
    if not before_or_at:
        return None
    end_idx = len(before_or_at) - 1
    if numbers_state(before_or_at[end_idx]) != target_state:
        return None
    start_idx = end_idx
    while start_idx > 0:
        prev = before_or_at[start_idx - 1]
        curr = before_or_at[start_idx]
        if numbers_state(prev) != target_state:
            break
        # Contiguous evidence only — do not bridge sparse holes.
        if (curr.video_time - prev.video_time) > float(max_gap_seconds):
            break
        start_idx -= 1
    return float(before_or_at[start_idx].video_time)


def _compress_trajectory(
    observations: list[PlayerCountObservation],
    *,
    anchor_time: float,
    anchor_obs: PlayerCountObservation | None,
) -> list[PlayerCountTrajectoryPoint]:
    """Collapse equal consecutive AvB runs; ensure the anchor time appears once.

    Sparse AvB compression uses the shared
    ``compress_player_count_observations`` helper. Anchor insertion is
    coaching-presentation only and does not invent roster values beyond the
    held ``anchor_obs``.
    """
    points: list[PlayerCountTrajectoryPoint] = []
    for obs in compress_player_count_observations(observations):
        state = numbers_state(obs)
        assert state is not None
        is_anchor = abs(obs.video_time - anchor_time) < 1e-9
        points.append(
            PlayerCountTrajectoryPoint(
                video_time=obs.video_time,
                ally_alive_count=obs.ally_alive_count,
                opponent_alive_count=obs.opponent_alive_count,
                numbers_differential=obs.ally_alive_count - obs.opponent_alive_count,
                numbers_state=state,
                is_anchor=is_anchor,
            )
        )
    if anchor_obs is not None and not any(p.is_anchor for p in points):
        state = numbers_state(anchor_obs)
        assert state is not None
        # Insert/replace nearest hold so the UI can mark the anchor.
        insert_at = 0
        for i, point in enumerate(points):
            if point.video_time <= anchor_time:
                insert_at = i + 1
        points.insert(
            insert_at,
            PlayerCountTrajectoryPoint(
                video_time=float(anchor_time),
                ally_alive_count=anchor_obs.ally_alive_count,
                opponent_alive_count=anchor_obs.opponent_alive_count,
                numbers_differential=(
                    anchor_obs.ally_alive_count - anchor_obs.opponent_alive_count
                ),
                numbers_state=state,
                is_anchor=True,
            ),
        )
        # Re-collapse accidental duplicates of the same AvB adjacent to the insert.
        points = _dedupe_adjacent_trajectory(points)
    return points


def _dedupe_adjacent_trajectory(
    points: list[PlayerCountTrajectoryPoint],
) -> list[PlayerCountTrajectoryPoint]:
    """Keep anchor rows; drop other adjacent equal AvB duplicates."""
    if not points:
        return points
    out: list[PlayerCountTrajectoryPoint] = [points[0]]
    for point in points[1:]:
        prev = out[-1]
        same = (
            prev.ally_alive_count == point.ally_alive_count
            and prev.opponent_alive_count == point.opponent_alive_count
        )
        if same and not point.is_anchor and not prev.is_anchor:
            continue
        if same and point.is_anchor and not prev.is_anchor:
            out[-1] = point
            continue
        if same and prev.is_anchor and not point.is_anchor:
            continue
        out.append(point)
    return out
