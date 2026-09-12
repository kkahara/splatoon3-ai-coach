"""Player-count clock: video time → fused roster alive counts for coaching.

Canonical event times remain video timestamps. This module never feeds
scenario construction or event fusion. Observations come from fused
``GameStateSnapshot`` alive counts via ``analysis.player_count_series``.

The clock retrieves authoritative state only. Trajectory / present_by /
duration interpretation belongs in ``coach_input``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.player_count_series import (
    NumbersState,
    PlayerCountObservation,
    PlayerCountSource,
    format_avb as _format_avb_counts,
    nearest_player_count_observation,
    numbers_differential_from_observation,
    numbers_state_from_observation,
    observations_between as _observations_between,
    observations_from_snapshots,
)
from splatoon3_ai_coach.vision.models import GameStateSnapshot

# Re-export for coach consumers that import observation types from this module.
__all__ = [
    "NumbersState",
    "PlayerCountClock",
    "PlayerCountObservation",
    "PlayerCountSource",
    "PlayerCountWindowPoint",
    "build_player_count_clock",
    "format_avb",
    "numbers_differential",
    "numbers_state",
]


class PlayerCountWindowPoint(BaseModel):
    """One relative-offset lookup around a coaching anchor (presentation sample)."""

    offset_seconds: float
    video_time: float = Field(ge=0)
    observation: PlayerCountObservation | None = None
    gap_seconds: float | None = None
    numbers_differential: int | None = None
    numbers_state: NumbersState | None = None


class PlayerCountClock(BaseModel):
    """Ordered fused roster samples for nearest-in-gap and range lookup."""

    observations: tuple[PlayerCountObservation, ...] = ()

    def at(
        self, video_time: float, *, max_gap_seconds: float
    ) -> PlayerCountObservation | None:
        """Nearest observation with ``|Δt| <= max_gap_seconds``; else ``None``.

        Exact matches win. Does not extrapolate or invent values across gaps.
        """
        return nearest_player_count_observation(
            self.observations,
            video_time,
            max_gap_seconds=max_gap_seconds,
        )

    def window(
        self,
        anchor_time: float,
        offsets: list[float] | tuple[float, ...],
        *,
        max_gap_seconds: float,
    ) -> list[PlayerCountWindowPoint]:
        """Lookup fused roster at ``anchor_time + offset`` for each offset.

        Missing lookups stay missing. Derived differential/state are filled
        only when an observation is found.
        """
        points: list[PlayerCountWindowPoint] = []
        for offset in offsets:
            video_time = float(anchor_time) + float(offset)
            if video_time < 0:
                points.append(
                    PlayerCountWindowPoint(
                        offset_seconds=float(offset),
                        video_time=max(0.0, video_time),
                        observation=None,
                        gap_seconds=None,
                        numbers_differential=None,
                        numbers_state=None,
                    )
                )
                continue
            obs = self.at(video_time, max_gap_seconds=max_gap_seconds)
            gap = None if obs is None else abs(obs.video_time - video_time)
            points.append(
                PlayerCountWindowPoint(
                    offset_seconds=float(offset),
                    video_time=video_time,
                    observation=obs,
                    gap_seconds=gap,
                    numbers_differential=numbers_differential(obs),
                    numbers_state=numbers_state(obs),
                )
            )
        return points

    def observations_between(
        self, start_time: float, end_time: float
    ) -> list[PlayerCountObservation]:
        """Inclusive range over ordered observations (retrieval only)."""
        return _observations_between(self.observations, start_time, end_time)


def numbers_differential(obs: PlayerCountObservation | None) -> int | None:
    """Ally minus opponent alive count; ``None`` when observation is missing."""
    return numbers_differential_from_observation(obs)


def numbers_state(obs: PlayerCountObservation | None) -> NumbersState | None:
    """Roster numbers relative to the player's team; not fight quality."""
    return numbers_state_from_observation(obs)


def format_avb(obs: PlayerCountObservation) -> str:
    """Compact ``AvB`` label from a fused observation."""
    return _format_avb_counts(obs.ally_alive_count, obs.opponent_alive_count)


def build_player_count_clock(
    snapshots: list[GameStateSnapshot],
) -> PlayerCountClock:
    """Build a clock from fused snapshots (shared series builder)."""
    return PlayerCountClock(
        observations=tuple(observations_from_snapshots(snapshots))
    )
