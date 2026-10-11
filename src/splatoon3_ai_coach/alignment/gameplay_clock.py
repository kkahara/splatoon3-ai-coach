"""Layer 1: gameplay video time to remaining clock, from the HUD timer only.

Contract: ``ALIGNMENT_CONTRACT.md``. The HUD changing from ``k`` to ``k - 1``
between reads at ``t_a < t_b`` means the remaining clock crossed ``k - 1`` in
``(t_a, t_b]``; with ``video_t = c - remaining`` that constrains
``c`` to ``(t_a + k - 1, t_b + k - 1]``. The Review axis plays no part here.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.match_bounds import before_match_end, match_end_time
from splatoon3_ai_coach.coach.game_clock import GameClock, build_game_clock
from splatoon3_ai_coach.coach.playback_clock import (
    PlaybackClockStatus,
    estimate_playback_clock,
)
from splatoon3_ai_coach.config.models import AlignmentConfig, PlaybackClockConfig
from splatoon3_ai_coach.vision.models import VisionManifest

FitReason = Literal["insufficient_gameplay_clock", "inconsistent_gameplay_clock"]


class ClockEdge(BaseModel):
    """One HUD second change: remaining crossed ``remaining`` in ``(t_a, t_b]``."""

    t_a: float
    t_b: float
    remaining: int

    @property
    def c_bounds(self) -> tuple[float, float]:
        """Half-open ``(lo, hi]`` constraint on ``c``."""
        return self.t_a + self.remaining, self.t_b + self.remaining


class GameplayClockCalibration(BaseModel):
    """``video_t = c - remaining`` with ``c`` in ``c_interval`` (gameplay only)."""

    fit_status: Literal["calibrated", "unknown"]
    reason: FitReason | None = None
    c_interval: tuple[float, float] | None = None
    edge_count: int = Field(default=0, ge=0)
    consistent_edge_count: int = Field(default=0, ge=0)
    inconsistent_edge_share: float | None = None
    consistent_remaining: list[int] = Field(default_factory=list)
    raw_edge_remaining_span: tuple[int, int] | None = None
    lowest_hud_read: int | None = None
    highest_hud_read: int | None = None
    playback_status: str
    game_seconds_per_video_second: float | None = None


def hud_edges(clock: GameClock) -> list[ClockEdge]:
    """Consecutive reads whose value drops by exactly one second."""
    obs = clock.observations
    return [
        ClockEdge(t_a=a.video_time, t_b=b.video_time, remaining=b.seconds_remaining)
        for a, b in zip(obs, obs[1:], strict=False)
        if a.seconds_remaining - b.seconds_remaining == 1 and b.video_time > a.video_time
    ]


def max_overlap(edges: list[ClockEdge]) -> tuple[list[ClockEdge], bool]:
    """Largest set of edges whose ``c`` constraints share a point.

    Returns the members and whether a different set of the same size also
    exists (two incompatible clusters: the offset is ambiguous).
    """
    best: list[ClockEdge] = []
    tie = False
    for p in sorted({e.c_bounds[1] for e in edges}):
        members = [e for e in edges if e.c_bounds[0] < p <= e.c_bounds[1]]
        if len(members) > len(best):
            best, tie = members, False
        elif members and len(members) == len(best) and members != best:
            tie = True
    return best, tie


def calibrate_gameplay_clock(
    manifest: VisionManifest,
    *,
    min_usable_confidence: float,
    config: AlignmentConfig,
    playback: PlaybackClockConfig | None = None,
) -> GameplayClockCalibration:
    """Fit the ``c`` interval from HUD edges before ``MATCH_END``."""
    end = match_end_time(manifest.game_events)
    frames = before_match_end(manifest.frame_results, lambda f: f.timestamp, end)
    clock = build_game_clock(frames, min_usable_confidence=min_usable_confidence)
    return calibrate_from_clock(clock, config=config, playback=playback)


def calibrate_from_clock(
    clock: GameClock,
    *,
    config: AlignmentConfig,
    playback: PlaybackClockConfig | None = None,
) -> GameplayClockCalibration:
    """Fit from an already built ``GameClock`` (pure)."""
    reads = [o.seconds_remaining for o in clock.observations]
    rate = estimate_playback_clock(clock, playback)
    edges = hud_edges(clock)
    base = {
        "edge_count": len(edges),
        "raw_edge_remaining_span": (
            (min(e.remaining for e in edges), max(e.remaining for e in edges) + 1)
            if edges
            else None
        ),
        "lowest_hud_read": min(reads, default=None),
        "highest_hud_read": max(reads, default=None),
        "playback_status": rate.status.value,
        "game_seconds_per_video_second": rate.game_seconds_per_video_second,
    }
    if len(edges) < config.min_edges or (
        rate.status is PlaybackClockStatus.INSUFFICIENT_CLOCK_EVIDENCE
    ):
        return GameplayClockCalibration(
            fit_status="unknown", reason="insufficient_gameplay_clock", **base
        )
    members, tie = max_overlap(edges)
    share = 1 - len(members) / len(edges)
    fitted = {
        "consistent_edge_count": len(members),
        "inconsistent_edge_share": round(share, 4),
        "consistent_remaining": sorted(e.remaining for e in members),
    }
    if (
        tie
        or share > config.max_inconsistent_edge_share
        or (rate.status is PlaybackClockStatus.RATE_MISMATCH)
    ):
        return GameplayClockCalibration(
            fit_status="unknown",
            reason="inconsistent_gameplay_clock",
            **base,
            **fitted,
        )
    lo = max(e.c_bounds[0] for e in members)
    hi = min(e.c_bounds[1] for e in members)
    return GameplayClockCalibration(
        fit_status="calibrated", c_interval=(lo, hi), **base, **fitted
    )
