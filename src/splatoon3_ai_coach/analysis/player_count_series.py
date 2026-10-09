"""Sparse fused roster samples — dependency-neutral for analysis + coach.

Built from persisted ``GameStateSnapshot`` alive counts only. No detectors,
no fusion, no coaching interpretation. Coach ``PlayerCountClock`` wraps this
for lookup APIs; ScenarioContext uses the same observations + compression.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.vision.models import GameStateSnapshot

PlayerCountSource = Literal["player_count_fusion"]
NumbersState = Literal["even", "advantage", "disadvantage"]


class PlayerCountObservation(BaseModel):
    """One fused roster alive-count sample at a canonical video timestamp."""

    video_time: float = Field(ge=0)
    ally_alive_count: int = Field(ge=0, le=4)
    opponent_alive_count: int = Field(ge=0, le=4)
    confidence: float = Field(ge=0, le=1)
    source: PlayerCountSource = "player_count_fusion"
    evidence_ids: tuple[str, ...] = ()


def observations_from_snapshots(
    snapshots: Sequence[GameStateSnapshot],
) -> list[PlayerCountObservation]:
    """Collect fused snapshots that assert both ally and opponent alive counts.

    Does not re-fuse detector readings. Snapshots with either count ``None``
    are skipped (unknown / not observed). Ordered by ``video_time``.
    """
    observations: list[PlayerCountObservation] = []
    for snapshot in sorted(snapshots, key=lambda item: item.timestamp):
        if snapshot.ally_alive_count is None or snapshot.opponent_alive_count is None:
            continue
        confidence = (
            float(snapshot.player_count_confidence)
            if snapshot.player_count_confidence is not None
            else 1.0
        )
        observations.append(
            PlayerCountObservation(
                video_time=float(snapshot.timestamp),
                ally_alive_count=int(snapshot.ally_alive_count),
                opponent_alive_count=int(snapshot.opponent_alive_count),
                confidence=confidence,
                source="player_count_fusion",
                evidence_ids=tuple(snapshot.evidence_ids),
            )
        )
    return observations


def compress_player_count_observations(
    observations: Sequence[PlayerCountObservation],
) -> list[PlayerCountObservation]:
    """Keep the first sample of each consecutive equal AvB run.

    Sparse semantics (authoritative):
    - consecutive equal ``(ally_alive_count, opponent_alive_count)`` collapse
    - the first observed point of a new state is retained
    - no interpolation / no invented state between samples
    """
    if not observations:
        return []
    ordered = sorted(observations, key=lambda o: o.video_time)
    out: list[PlayerCountObservation] = []
    prev_key: tuple[int, int] | None = None
    for obs in ordered:
        key = (int(obs.ally_alive_count), int(obs.opponent_alive_count))
        if key == prev_key:
            continue
        out.append(obs)
        prev_key = key
    return out


def nearest_player_count_observation(
    observations: Sequence[PlayerCountObservation],
    video_time: float,
    *,
    max_gap_seconds: float,
) -> PlayerCountObservation | None:
    """Nearest observation with ``|Δt| <= max_gap_seconds``; else ``None``.

    Exact matches win. Does not extrapolate or invent values across gaps.
    """
    if not observations or max_gap_seconds < 0:
        return None
    best: PlayerCountObservation | None = None
    best_gap = float("inf")
    for obs in observations:
        gap = abs(float(obs.video_time) - float(video_time))
        if gap > max_gap_seconds:
            continue
        if gap < best_gap:
            best = obs
            best_gap = gap
            if gap == 0.0:
                return obs
    return best


def observations_between(
    observations: Sequence[PlayerCountObservation],
    start_time: float,
    end_time: float,
) -> list[PlayerCountObservation]:
    """Inclusive range over observations (retrieval only)."""
    lo = min(float(start_time), float(end_time))
    hi = max(float(start_time), float(end_time))
    return [obs for obs in observations if lo <= float(obs.video_time) <= hi]


def numbers_differential(
    ally_alive_count: int | None,
    opponent_alive_count: int | None,
) -> int | None:
    """Ally minus opponent alive count; ``None`` when either side is missing."""
    if ally_alive_count is None or opponent_alive_count is None:
        return None
    return int(ally_alive_count) - int(opponent_alive_count)


def numbers_state(
    ally_alive_count: int | None,
    opponent_alive_count: int | None,
) -> NumbersState | None:
    """Roster numbers relative to the player's team; not fight quality."""
    diff = numbers_differential(ally_alive_count, opponent_alive_count)
    if diff is None:
        return None
    if diff > 0:
        return "advantage"
    if diff < 0:
        return "disadvantage"
    return "even"


def numbers_state_from_observation(
    obs: PlayerCountObservation | None,
) -> NumbersState | None:
    """``numbers_state`` for a fused observation (coach clock convenience)."""
    if obs is None:
        return None
    return numbers_state(obs.ally_alive_count, obs.opponent_alive_count)


def numbers_differential_from_observation(
    obs: PlayerCountObservation | None,
) -> int | None:
    """``numbers_differential`` for a fused observation."""
    if obs is None:
        return None
    return numbers_differential(obs.ally_alive_count, obs.opponent_alive_count)


def format_avb(ally_alive_count: int, opponent_alive_count: int) -> str:
    """Compact ``AvB`` label from alive counts."""
    return f"{int(ally_alive_count)}v{int(opponent_alive_count)}"


TEAM_SIZE = 4
RosterSide = Literal["ally", "opponent"]


class RosterInterval(BaseModel):
    """Closed video-time interval where roster counts are not plausible."""

    start: float
    end: float
    side: RosterSide
    increase_at: float
    reason: str

    def contains(self, video_time: float) -> bool:
        """Closed-interval membership (boundaries count as unreliable)."""
        return self.start <= float(video_time) <= self.end


def assess_roster_plausibility(
    observations: Sequence[PlayerCountObservation],
    *,
    min_respawn_seconds: float,
    window_seconds: float | None = None,
) -> list[RosterInterval]:
    """Intervals around alive-count increases no earlier death can explain.

    Per side and queue-based: each decrease records pending deaths; an
    increase must consume pending deaths at least ``min_respawn_seconds`` old.
    The deficit present when the series starts counts as pending from the
    series start. Detector confidence is never consulted or changed.
    """
    window = min_respawn_seconds if window_seconds is None else window_seconds
    ordered = sorted(observations, key=lambda o: o.video_time)
    intervals: list[RosterInterval] = []
    for side in ("ally", "opponent"):
        intervals.extend(
            _side_intervals(ordered, side, min_respawn_seconds, window)
        )
    return sorted(intervals, key=lambda item: (item.start, item.side))


def _side_intervals(
    ordered: Sequence[PlayerCountObservation],
    side: RosterSide,
    min_respawn_seconds: float,
    window: float,
) -> list[RosterInterval]:
    """Replay one side's counts against a FIFO queue of pending deaths."""
    attr = f"{side}_alive_count"
    if not ordered:
        return []
    previous = int(getattr(ordered[0], attr))
    pending = [float(ordered[0].video_time)] * max(0, TEAM_SIZE - previous)
    out: list[RosterInterval] = []
    for obs in ordered[1:]:
        count, t = int(getattr(obs, attr)), float(obs.video_time)
        if count < previous:
            pending.extend([t] * (previous - count))
        elif count > previous:
            needed = count - previous
            eligible = [p for p in pending if t - p >= min_respawn_seconds]
            for stamp in eligible[:needed]:
                pending.remove(stamp)
            missing = needed - min(needed, len(eligible))
            if missing:
                # Still consume the youngest pending deaths so the queue
                # tracks the observed count after the implausible step.
                del pending[len(pending) - min(missing, len(pending)) :]
                out.append(
                    RosterInterval(
                        start=max(0.0, t - window),
                        end=t + window,
                        side=side,
                        increase_at=t,
                        reason=(
                            f"{side} alive count rose by {needed} with only "
                            f"{needed - missing} death(s) at least "
                            f"{min_respawn_seconds:g}s earlier"
                        ),
                    )
                )
        previous = count
    return out
