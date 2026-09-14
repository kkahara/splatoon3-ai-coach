"""Player-count window / context / trajectory derivation for coaching."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.player_count_series import (
    compress_player_count_observations,
)
from splatoon3_ai_coach.analysis.scenario_context import ScenarioContext
from splatoon3_ai_coach.analysis.scenario_models import Scenario, ScenarioType
from splatoon3_ai_coach.coach.player_count_clock import (
    NumbersState,
    PlayerCountClock,
    PlayerCountObservation,
    PlayerCountWindowPoint,
    format_avb,
    numbers_state,
)

DEFAULT_PLAYER_COUNT_WINDOW_OFFSETS: tuple[float, ...] = (-5.0, -2.0, 0.0, 3.0, 6.0)


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


def tactical_anchor_time(
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


def player_count_window_and_context(
    scenario: Scenario,
    context: ScenarioContext,
    clock: PlayerCountClock,
    *,
    offsets: Sequence[float],
    max_gap_seconds: float,
    context_lookback_seconds: float,
) -> tuple[list[PlayerCountWindowPoint], PlayerCountContext | None]:
    """Build presentation window + derived present_by context for the anchor."""
    anchor = tactical_anchor_time(scenario, context)
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
    derived = derive_player_count_context(
        anchor_time=anchor,
        window=window,
        range_observations=range_obs,
        valid_point_count=valid,
        max_gap_seconds=max_gap_seconds,
    )
    return window, derived


def derive_player_count_context(
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
        present_by = state_present_by(
            range_observations,
            anchor_time=anchor_time,
            target_state=state_name,
            max_gap_seconds=max_gap_seconds,
        )
        if present_by is not None:
            duration = float(anchor_time) - float(present_by)
    trajectory = compress_trajectory(
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


def state_present_by(
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


def compress_trajectory(
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
        points = dedupe_adjacent_trajectory(points)
    return points


def dedupe_adjacent_trajectory(
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
