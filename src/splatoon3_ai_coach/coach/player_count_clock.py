"""Player-count clock: video time → fused roster alive counts for coaching.

Canonical event times remain video timestamps. This module never feeds
scenario construction or event fusion. Observations come from fused
``GameStateSnapshot`` alive counts (persisted on the vision manifest),
not from raw ``PlayerCountReading`` detections.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.vision.models import GameStateSnapshot

PlayerCountSource = Literal["player_count_fusion"]


class PlayerCountObservation(BaseModel):
    """One fused roster alive-count sample at a canonical video timestamp."""

    video_time: float = Field(ge=0)
    ally_alive_count: int = Field(ge=0, le=4)
    opponent_alive_count: int = Field(ge=0, le=4)
    confidence: float = Field(ge=0, le=1)
    source: PlayerCountSource = "player_count_fusion"
    evidence_ids: tuple[str, ...] = ()


class PlayerCountClock(BaseModel):
    """Ordered fused roster samples for nearest-in-gap lookup."""

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
