"""Playback clock: how video seconds relate to observed match-timer seconds.

Video time stays the canonical clock for events and scenarios. This module
only classifies whether video seconds may be read as play (game) seconds and,
when they may not, derives game-clock durations for coaching and display.

Direction convention (never invert implicitly):

- ``game_seconds_per_video_second`` = -d(seconds_remaining) / d(video_time).
  A replay at 1.5x playback has ~1.5 game seconds per video second.
- ``video_seconds_per_game_second`` is its reciprocal.
- :func:`to_game_seconds` / :func:`to_video_seconds` are the only converters.
"""

from __future__ import annotations

from enum import StrEnum
from statistics import median
from typing import Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.coach.game_clock import (
    GameClock,
    GameClockObservation,
    find_non_monotonic_raw_reads,
)
from splatoon3_ai_coach.config.models import PlaybackClockConfig

RULE_ID = "playback_rate_median_pair_slope"

DurationClock = Literal["game", "video"]
DurationMethod = Literal["clock_samples", "rate_converted", "video_elapsed"]


class PlaybackClockStatus(StrEnum):
    """Whether elapsed video seconds may be read as elapsed game seconds."""

    NORMAL = "normal"
    RATE_MISMATCH = "rate_mismatch"
    INSUFFICIENT_CLOCK_EVIDENCE = "insufficient_clock_evidence"


class PlaybackClockInfo(BaseModel):
    """Match-level playback-rate classification from observed timer reads."""

    status: PlaybackClockStatus
    rule: str = RULE_ID
    game_seconds_per_video_second: float | None = None
    video_seconds_per_game_second: float | None = None
    rate_spread: float | None = Field(
        default=None,
        description="Median absolute deviation of pair slopes (game s / video s).",
    )
    observation_count: int = 0
    excluded_frozen: int = 0
    excluded_non_monotonic: int = 0
    pair_count: int = 0
    span_video_seconds: float = 0.0
    tolerance: float = 0.10
    reason: str


class ClockedDuration(BaseModel):
    """An elapsed interval with an explicit clock.

    ``video_seconds`` is always the raw video difference. ``value`` is the
    play-time estimate and is ``None`` when it cannot be established.
    """

    video_seconds: float
    value: float | None = None
    clock: DurationClock | None = None
    method: DurationMethod | None = None
    uncertainty_seconds: float | None = None
    start_gap_seconds: float | None = None
    end_gap_seconds: float | None = None


def to_game_seconds(video_seconds: float, info: PlaybackClockInfo) -> float | None:
    """Video seconds → game seconds using the estimated rate."""
    rate = info.game_seconds_per_video_second
    return None if rate is None else float(video_seconds) * rate


def to_video_seconds(game_seconds: float, info: PlaybackClockInfo) -> float | None:
    """Game seconds → video seconds using the estimated rate."""
    rate = info.video_seconds_per_game_second
    return None if rate is None else float(game_seconds) * rate


def estimate_playback_clock(
    game_clock: GameClock | None,
    config: PlaybackClockConfig | None = None,
) -> PlaybackClockInfo:
    """Classify playback rate from timer observations (median pair slope).

    Frozen runs and non-monotonic reads are excluded first; the rate is the
    median slope over pairs at least ``min_pair_span_seconds`` apart, so one
    bad read cannot decide the match.
    """
    cfg = config or PlaybackClockConfig()
    observations = list(game_clock.observations) if game_clock is not None else []
    flowing, frozen = _drop_frozen_runs(observations, cfg.frozen_run_seconds)
    kept, non_monotonic = _drop_non_monotonic(flowing)
    span = kept[-1].video_time - kept[0].video_time if len(kept) >= 2 else 0.0
    base = {
        "observation_count": len(kept),
        "excluded_frozen": frozen,
        "excluded_non_monotonic": non_monotonic,
        "span_video_seconds": round(span, 3),
        "tolerance": cfg.tolerance,
    }
    if len(kept) < cfg.min_observations or span < cfg.min_span_video_seconds:
        return PlaybackClockInfo(
            status=PlaybackClockStatus.INSUFFICIENT_CLOCK_EVIDENCE,
            reason=(
                f"{len(kept)} usable timer reads over {span:.1f} video seconds; "
                f"need >= {cfg.min_observations} over "
                f">= {cfg.min_span_video_seconds:g}s."
            ),
            **base,
        )
    slopes = _pair_slopes(kept, cfg.min_pair_span_seconds)
    positive = [s for s in slopes if s > 0]
    if not positive:
        return PlaybackClockInfo(
            status=PlaybackClockStatus.INSUFFICIENT_CLOCK_EVIDENCE,
            reason="No timer read pairs with a decreasing countdown.",
            pair_count=len(slopes),
            **base,
        )
    rate = median(slopes)
    spread = median(abs(s - rate) for s in slopes)
    mismatch = rate <= 0 or abs(rate - 1.0) > cfg.tolerance
    status = (
        PlaybackClockStatus.RATE_MISMATCH if mismatch else PlaybackClockStatus.NORMAL
    )
    return PlaybackClockInfo(
        status=status,
        game_seconds_per_video_second=round(rate, 4) if rate > 0 else None,
        video_seconds_per_game_second=round(1.0 / rate, 4) if rate > 0 else None,
        rate_spread=round(spread, 4),
        pair_count=len(slopes),
        reason=(
            f"Median of {len(slopes)} timer pair slopes: {rate:.3f} game s "
            f"per video s (tolerance {cfg.tolerance:g})."
        ),
        **base,
    )


def _drop_frozen_runs(
    observations: list[GameClockObservation], max_hold_seconds: float
) -> tuple[list[GameClockObservation], int]:
    """Remove runs of one value held longer than ``max_hold_seconds``."""
    ordered = sorted(observations, key=lambda o: o.video_time)
    kept: list[GameClockObservation] = []
    dropped = 0
    index = 0
    while index < len(ordered):
        end = index
        value = ordered[index].seconds_remaining
        while end + 1 < len(ordered) and ordered[end + 1].seconds_remaining == value:
            end += 1
        run = ordered[index : end + 1]
        if run[-1].video_time - run[0].video_time > max_hold_seconds:
            dropped += len(run)
        else:
            kept.extend(run)
        index = end + 1
    return kept, dropped


def _drop_non_monotonic(
    observations: list[GameClockObservation],
) -> tuple[list[GameClockObservation], int]:
    """Drop the later read of every consecutive pair where the countdown rose."""
    anomalies = find_non_monotonic_raw_reads(GameClock(observations=tuple(observations)))
    bad = {(later_vt, later_sec) for _, _, later_vt, later_sec in anomalies}
    kept = [o for o in observations if (o.video_time, o.seconds_remaining) not in bad]
    return kept, len(observations) - len(kept)


def _pair_slopes(
    observations: list[GameClockObservation], min_pair_span: float
) -> list[float]:
    slopes: list[float] = []
    for i, first in enumerate(observations):
        for second in observations[i + 1 :]:
            dt = second.video_time - first.video_time
            if dt < min_pair_span:
                continue
            slopes.append((first.seconds_remaining - second.seconds_remaining) / dt)
    return slopes


def clocked_duration(
    start_time: float | None,
    end_time: float | None,
    *,
    info: PlaybackClockInfo,
    game_clock: GameClock | None,
    max_gap_seconds: float,
) -> ClockedDuration | None:
    """Elapsed time between two video timestamps with an explicit clock.

    ``normal``: the video difference is play time (``clock="video"``).
    ``rate_mismatch``: game seconds from nearest timer reads (no
    interpolation; endpoint gaps widen the uncertainty), else rate-converted.
    ``insufficient_clock_evidence``: only ``video_seconds``; ``value`` is None.
    """
    if start_time is None or end_time is None:
        return None
    video = round(float(end_time) - float(start_time), 3)
    if info.status is PlaybackClockStatus.NORMAL:
        return ClockedDuration(
            video_seconds=video, value=video, clock="video", method="video_elapsed"
        )
    if info.status is not PlaybackClockStatus.RATE_MISMATCH:
        return ClockedDuration(video_seconds=video)
    sampled = _sampled_game_duration(
        start_time, end_time, video, info, game_clock, max_gap_seconds
    )
    if sampled is not None:
        return sampled
    converted = to_game_seconds(video, info)
    if converted is None:
        return ClockedDuration(video_seconds=video)
    spread = info.rate_spread or 0.0
    return ClockedDuration(
        video_seconds=video,
        value=round(converted, 2),
        clock="game",
        method="rate_converted",
        uncertainty_seconds=round(abs(video) * spread + 1.0, 2),
    )


def clock_video_span(
    video_seconds: float | None, info: PlaybackClockInfo | None
) -> ClockedDuration | None:
    """Label a bare video-seconds gap (no endpoint timer lookup)."""
    if video_seconds is None:
        return None
    video = round(float(video_seconds), 3)
    if info is None or info.status is PlaybackClockStatus.INSUFFICIENT_CLOCK_EVIDENCE:
        return ClockedDuration(video_seconds=video)
    if info.status is PlaybackClockStatus.NORMAL:
        return ClockedDuration(
            video_seconds=video, value=video, clock="video", method="video_elapsed"
        )
    converted = to_game_seconds(video, info)
    if converted is None:
        return ClockedDuration(video_seconds=video)
    return ClockedDuration(
        video_seconds=video,
        value=round(converted, 2),
        clock="game",
        method="rate_converted",
        uncertainty_seconds=round(abs(video) * (info.rate_spread or 0.0) + 1.0, 2),
    )


def _sampled_game_duration(
    start_time: float,
    end_time: float,
    video: float,
    info: PlaybackClockInfo,
    game_clock: GameClock | None,
    max_gap_seconds: float,
) -> ClockedDuration | None:
    if game_clock is None:
        return None
    start = game_clock.at(float(start_time), max_gap_seconds=max_gap_seconds)
    end = game_clock.at(float(end_time), max_gap_seconds=max_gap_seconds)
    if start is None or end is None:
        return None
    game = start.seconds_remaining - end.seconds_remaining
    if game < 0:
        return None
    start_gap = abs(start.video_time - float(start_time))
    end_gap = abs(end.video_time - float(end_time))
    rate = info.game_seconds_per_video_second or 1.0
    return ClockedDuration(
        video_seconds=video,
        value=float(game),
        clock="game",
        method="clock_samples",
        uncertainty_seconds=round(1.0 + rate * (start_gap + end_gap), 2),
        start_gap_seconds=round(start_gap, 3),
        end_gap_seconds=round(end_gap, 3),
    )
