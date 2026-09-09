"""Player-count clock: video time → fused roster alive counts for coaching.

Canonical event times remain video timestamps. This module never feeds
scenario construction or event fusion. Observations come from fused
``GameStateSnapshot`` alive counts (persisted on the vision manifest),
not from raw ``PlayerCountReading`` detections.

The clock retrieves authoritative state only. Trajectory / present_by /
duration interpretation belongs in ``coach_input``.
"""

from __future__ import annotations

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
        if not self.observations or max_gap_seconds < 0:
            return None
        best: PlayerCountObservation | None = None
        best_gap = float("inf")
        for obs in self.observations:
            gap = abs(obs.video_time - video_time)
            if gap > max_gap_seconds:
                continue
            if gap < best_gap:
                best = obs
                best_gap = gap
                if gap == 0.0:
                    return obs
        return best

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
        lo = min(float(start_time), float(end_time))
        hi = max(float(start_time), float(end_time))
        return [
            obs
            for obs in self.observations
            if lo <= obs.video_time <= hi
        ]


def numbers_differential(obs: PlayerCountObservation | None) -> int | None:
    """Ally minus opponent alive count; ``None`` when observation is missing."""
    if obs is None:
        return None
    return int(obs.ally_alive_count) - int(obs.opponent_alive_count)


def numbers_state(obs: PlayerCountObservation | None) -> NumbersState | None:
    """Roster numbers relative to the player's team; not fight quality."""
    diff = numbers_differential(obs)
    if diff is None:
        return None
    if diff > 0:
        return "advantage"
    if diff < 0:
        return "disadvantage"
    return "even"


def format_avb(obs: PlayerCountObservation) -> str:
    """Compact ``AvB`` label from a fused observation."""
    return f"{obs.ally_alive_count}v{obs.opponent_alive_count}"


def build_player_count_clock(
    snapshots: list[GameStateSnapshot],
) -> PlayerCountClock:
    """Collect fused snapshots that assert both ally and opponent alive counts.

    Does not re-fuse detector readings. Snapshots with either count ``None``
    are skipped (unknown / not observed).
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
                video_time=snapshot.timestamp,
                ally_alive_count=int(snapshot.ally_alive_count),
                opponent_alive_count=int(snapshot.opponent_alive_count),
                confidence=confidence,
                source="player_count_fusion",
                evidence_ids=tuple(snapshot.evidence_ids),
            )
        )
    return PlayerCountClock(observations=tuple(observations))
