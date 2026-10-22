"""Where the timeline cursor went: endpoints, passes and per-pass coverage.

Pure functions over adapter samples. The Review clock counts down from the
regulation length at the left edge to 0:00 at the right; pre-GO ``-M:SS``
labels sit left of it and overtime ``+M:SS`` labels right of it. Positions
below order those labels left to right; they are never a time conversion.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.review.clock import ClockPhase, clock_phase
from splatoon3_ai_coach.review.video_adapter import VideoSample

TIMELINE_STATUSES = frozenset({"clocked_timeline", "unclocked_timeline"})
REGULATION_BY_MODE = {
    "turf_war": 180,
    "splat_zones": 300,
    "tower_control": 300,
    "rainmaker": 300,
    "clam_blitz": 300,
}
_COUNTDOWN_OFFSET = 10_000


@dataclass(frozen=True)
class ClockSample:
    """One plausible clock label at a video time."""

    time: float
    value: int
    display: str
    phase: ClockPhase

    @property
    def position(self) -> int:
        """Left-to-right order on the match-history axis (larger = further right)."""
        if self.phase is ClockPhase.countdown:
            return -_COUNTDOWN_OFFSET + self.value
        # Regulation values fall toward 0:00; overtime values are negative.
        return -self.value

    @property
    def overtime_seconds(self) -> int:
        """Magnitude of an overtime label, else 0."""
        return abs(self.value) if self.phase is ClockPhase.overtime else 0


@dataclass(frozen=True)
class Sweep:
    """Video times of the left, furthest-right and return-left visits."""

    left_at: float | None
    turn_at: float | None
    turn_sample: ClockSample | None
    return_left_at: float | None
    # End of the uninterrupted left-edge visit that starts at ``return_left_at``.
    return_left_until: float | None = None


def plausible_samples(
    samples: Iterable[VideoSample], config: ReviewTimelineConfig
) -> list[ClockSample]:
    """Plausible clocked samples in video-time order (same rule as the importer)."""
    result = []
    for sample in samples:
        if sample.clock is None:
            continue
        phase = clock_phase(
            sample.clock.display,
            sample.clock.elapsed_seconds,
            regulation_seconds=config.regulation_seconds,
            max_overtime_seconds=config.max_overtime_seconds,
            max_countdown_seconds=config.max_countdown_seconds,
        )
        if phase is not None:
            result.append(
                ClockSample(
                    sample.source_video_time,
                    sample.clock.elapsed_seconds,
                    sample.clock.display.strip(),
                    phase,
                )
            )
    return sorted(result, key=lambda item: item.time)


def regulation_length(
    battle_mode_id: str | None, samples: Sequence[ClockSample]
) -> tuple[int | None, str]:
    """Regulation seconds from the observed mode, else from a 4:59+ reading."""
    if battle_mode_id in REGULATION_BY_MODE:
        return REGULATION_BY_MODE[battle_mode_id], "intro_mode"
    if any(s.phase is ClockPhase.regulation and s.value >= 299 for s in samples):
        return 300, "clock_reading"
    return None, "unknown"


def is_left_marker(sample: ClockSample, regulation: int | None) -> bool:
    """A pre-GO label, or a regulation label within 1 s of the left edge."""
    if sample.phase is ClockPhase.countdown:
        return True
    return (
        sample.phase is ClockPhase.regulation
        and regulation is not None
        and sample.value >= regulation - 1
    )


def find_sweep(samples: Sequence[ClockSample], regulation: int | None) -> Sweep:
    """First left visit, first furthest-right visit after it, then the return."""
    left = next((s for s in samples if is_left_marker(s, regulation)), None)
    after_left = [s for s in samples if left is None or s.time >= left.time]
    if not after_left:
        return Sweep(None, None, None, None)
    rightmost = max(s.position for s in after_left)
    turn = next(s for s in after_left if s.position == rightmost)
    back_index = next(
        (
            i
            for i, s in enumerate(samples)
            if s.time > turn.time and is_left_marker(s, regulation)
        ),
        None,
    )
    if back_index is None:
        return Sweep(left.time if left else None, turn.time, turn, None)
    until = back_index
    while until + 1 < len(samples) and is_left_marker(samples[until + 1], regulation):
        until += 1
    return Sweep(
        left.time if left else None,
        turn.time,
        turn,
        samples[back_index].time,
        samples[until].time,
    )


def split_passes(
    samples: Sequence[ClockSample], sweep: Sweep
) -> tuple[list[ClockSample], list[ClockSample]]:
    """Outbound ``[left_at, turn_at]`` and return ``[turn_at, return_left_until]``."""
    if sweep.turn_at is None:
        return [], []
    start = sweep.left_at if sweep.left_at is not None else samples[0].time
    end = (
        sweep.return_left_until
        if sweep.return_left_until is not None
        else samples[-1].time
    )
    outbound = [s for s in samples if start <= s.time <= sweep.turn_at]
    back = [s for s in samples if sweep.turn_at <= s.time <= end]
    return outbound, back


def expected_regulation_range(
    samples: Sequence[ClockSample], regulation: int | None
) -> tuple[int, int] | None:
    """Regulation seconds a full sweep shows.

    Down to 0:00 when 0:00/1 or any overtime label was read; otherwise down
    to the lowest reading (a knockout, or a sweep that stopped short).
    """
    if regulation is None:
        return None
    reg = [s.value for s in samples if s.phase is ClockPhase.regulation]
    reached_end = any(s.phase is ClockPhase.overtime for s in samples) or (
        bool(reg) and min(reg) <= 1
    )
    low = 0 if reached_end or not reg else min(reg)
    return low, regulation


def pass_coverage(
    samples: Sequence[ClockSample], expected: tuple[int, int] | None
) -> dict[str, Any]:
    """Missing regulation seconds plus overtime seconds seen in one pass."""
    seen = {s.value for s in samples if s.phase is ClockPhase.regulation}
    overtime = {s.overtime_seconds for s in samples if s.phase is ClockPhase.overtime}
    result: dict[str, Any] = {
        "samples": len(samples),
        "distinct_regulation_seconds": len(seen),
        "overtime_labels": sum(1 for s in samples if s.phase is ClockPhase.overtime),
        "overtime_max_seconds": max(overtime, default=None),
        "overtime_missing_seconds": _missing(overtime, 1, max(overtime, default=0)),
        "video_span_seconds": _span(samples),
        "clock_seconds_per_video_second": _sweep_rate(samples, seen),
    }
    if expected is not None:
        missing = _missing(seen, *expected)
        result["missing_regulation_seconds"] = len(missing)
        result["missing_regulation_ranges"] = _ranges(missing)
        result["longest_missing_run_seconds"] = max(
            (b - a + 1 for a, b in _ranges(missing)), default=0
        )
    return result


def _missing(seen: set[int], low: int, high: int) -> list[int]:
    return [value for value in range(low, high + 1) if value not in seen]


def _ranges(values: list[int]) -> list[list[int]]:
    ranges: list[list[int]] = []
    for value in sorted(values):
        if ranges and value == ranges[-1][1] + 1:
            ranges[-1][1] = value
        else:
            ranges.append([value, value])
    return ranges


def _span(samples: Sequence[ClockSample]) -> float | None:
    if not samples:
        return None
    return round(samples[-1].time - samples[0].time, 3)


def _sweep_rate(samples: Sequence[ClockSample], seen: set[int]) -> float | None:
    """Regulation seconds traversed per second of video (averaged over the pass)."""
    span = _span(samples)
    if not span or len(seen) < 2:
        return None
    return round((max(seen) - min(seen)) / span, 2)
