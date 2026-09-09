"""Game clock: video time → observed match countdown for coaching.

Canonical event times remain video timestamps. This module never feeds
scenario construction or fusion. Observations come from raw usable
``TimerReading`` detections on ``VisionFrameResult``, not from held or
smoothed ``GameStateSnapshot.match_time_remaining``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    TimerReading,
    VisionFrameResult,
)

GameClockSource = Literal["timer_detection"]


class GameClockObservation(BaseModel):
    """One observed match-timer sample at a canonical video timestamp."""

    video_time: float = Field(ge=0)
    seconds_remaining: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    display: str | None = None
    source: GameClockSource = "timer_detection"


class GameClock(BaseModel):
    """Ordered observed timer samples for nearest-in-gap lookup."""

    observations: tuple[GameClockObservation, ...] = ()

    def at(
        self, video_time: float, *, max_gap_seconds: float
    ) -> GameClockObservation | None:
        """Nearest observation with ``|Δt| <= max_gap_seconds``; else ``None``.

        Exact matches win. Does not extrapolate or invent values across gaps.
        """
        if not self.observations or max_gap_seconds < 0:
            return None
        best: GameClockObservation | None = None
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


def build_game_clock(
    frame_results: list[VisionFrameResult],
    *,
    min_usable_confidence: float,
) -> GameClock:
    """Collect raw usable timer detections from cadence frames.

    Skips frames without a timer reading, below ``min_usable_confidence``,
    or with a non-integral ``seconds_remaining`` (no near-integer rounding).
    """
    observations: list[GameClockObservation] = []
    for frame in sorted(frame_results, key=lambda item: item.timestamp):
        best = _best_usable_timer(frame, min_usable_confidence)
        if best is None:
            continue
        reading = best.reading
        assert isinstance(reading, TimerReading)
        seconds = _integral_seconds(reading.seconds_remaining)
        if seconds is None:
            continue
        observations.append(
            GameClockObservation(
                video_time=frame.timestamp,
                seconds_remaining=seconds,
                confidence=float(best.confidence),
                display=reading.display or None,
                source="timer_detection",
            )
        )
    return GameClock(observations=tuple(observations))


def _best_usable_timer(
    frame: VisionFrameResult, min_usable_confidence: float
) -> DetectorResult | None:
    """Highest-confidence timer detection meeting the confidence floor."""
    candidates = [
        detection
        for detection in frame.detections
        if detection.detector_name == "timer"
        and isinstance(detection.reading, TimerReading)
        and detection.confidence >= min_usable_confidence
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.confidence)


def _integral_seconds(value: float) -> int | None:
    """Preserve whole-second timer values; reject non-integers without rounding."""
    if not float(value).is_integer():
        return None
    return int(value)


def find_non_monotonic_raw_reads(
    clock: GameClock,
) -> list[tuple[float, int, float, int]]:
    """Diagnostic: consecutive observations where countdown increased.

    Does not filter GameClock. Used to report OCR/template glitches.
    """
    anomalies: list[tuple[float, int, float, int]] = []
    prev: GameClockObservation | None = None
    for obs in clock.observations:
        if prev is not None and obs.seconds_remaining > prev.seconds_remaining:
            anomalies.append(
                (
                    prev.video_time,
                    prev.seconds_remaining,
                    obs.video_time,
                    obs.seconds_remaining,
                )
            )
        prev = obs
    return anomalies
