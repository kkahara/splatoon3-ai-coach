"""Deterministic MATCH_END decision from raw timer readings.

Runs offline over the whole cadence scan (look-ahead allowed). The game clock
reaching 0:00 is authoritative (``clock_zero``); the FINISH screen would only
be visual confirmation and has no detector yet.

Ranked overtime replaces the digits with an OVERTIME banner (1 → no reading,
never 0:00) while play continues, and a knockout ends with time left. Neither
yields a trustworthy 0:00, so by default there is no MATCH_END and the domain
stays the whole video. The opt-in ``hud_loss`` fallback (last timer reading
before the timer is gone for good) would cut real overtime play.

The 0:00 frame itself is the boundary, not gameplay: the last gameplay frame
is the last cadence frame strictly before ``MatchEndDecision.time``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from splatoon3_ai_coach.config.models import (
    LifecycleFusionConfig,
    MatchEndConfig,
    TimerDetectorConfig,
)
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    GameEvent,
    GameEventReason,
    GameEventSource,
    GameEventType,
    SourceFrameReference,
    TimerReading,
    VisionFrameResult,
)


@dataclass(frozen=True)
class MatchEndDecision:
    """Where the analysis domain ends and why."""

    time: float
    reason: GameEventReason
    frame: VisionFrameResult
    detection: DetectorResult


@dataclass(frozen=True)
class _TimerPoint:
    frame: VisionFrameResult
    detection: DetectorResult
    seconds: float


def detect_match_end(
    frame_results: Sequence[VisionFrameResult],
    timer_config: TimerDetectorConfig,
    lifecycle_config: LifecycleFusionConfig,
    config: MatchEndConfig,
) -> MatchEndDecision | None:
    """Return the match-end boundary, or ``None`` when the video ends first."""
    if not config.enabled or not frame_results:
        return None
    points = _in_match_points(frame_results, timer_config, lifecycle_config)
    if not points:
        return None
    zero = _clock_zero(points, config)
    if zero is not None:
        return _decision(zero, GameEventReason.CLOCK_ZERO)
    if not config.hud_loss_fallback:
        return None
    video_end = max(frame.timestamp for frame in frame_results)
    last = points[-1]
    if video_end - last.frame.timestamp > lifecycle_config.match_context_hold_seconds:
        return _decision(last, GameEventReason.HUD_LOSS)
    return None


def _in_match_points(
    frame_results: Sequence[VisionFrameResult],
    timer_config: TimerDetectorConfig,
    lifecycle_config: LifecycleFusionConfig,
) -> list[_TimerPoint]:
    """Usable timer readings from the first ticking (non-opening) clock on."""
    opening = {float(value) for value in lifecycle_config.opening_clock_seconds}
    points: list[_TimerPoint] = []
    ticking = False
    for frame in sorted(frame_results, key=lambda item: item.timestamp):
        detection = _best_timer(frame)
        if detection is None or detection.confidence < timer_config.min_usable_confidence:
            continue
        assert isinstance(detection.reading, TimerReading)
        seconds = float(detection.reading.seconds_remaining)
        if not ticking and seconds in opening:
            continue
        ticking = True
        points.append(_TimerPoint(frame, detection, seconds))
    return points


def _clock_zero(points: list[_TimerPoint], config: MatchEndConfig) -> _TimerPoint | None:
    """First trustworthy 0:00 with no clock reading after the tail; else ``None``.

    A 0:00 jump from far above ``zero_max_previous_seconds`` is a misread and
    is skipped. A trustworthy 0:00 still followed by clock readings is
    overtime (or a misread), which defers to the ``hud_loss`` fallback.
    """
    for index, point in enumerate(points):
        if point.seconds != 0.0 or index == 0:
            continue
        if points[index - 1].seconds > config.zero_max_previous_seconds:
            continue
        deadline = point.frame.timestamp + config.zero_tail_seconds
        if any(later.frame.timestamp > deadline for later in points[index + 1 :]):
            return None
        return point
    return None


def _decision(point: _TimerPoint, reason: GameEventReason) -> MatchEndDecision:
    """Freeze a timer point into a decision."""
    return MatchEndDecision(
        time=point.frame.timestamp,
        reason=reason,
        frame=point.frame,
        detection=point.detection,
    )


def match_end_event(decision: MatchEndDecision) -> GameEvent:
    """One-shot ``MATCH_END`` GameEvent with the deciding timer provenance."""
    frame = decision.frame
    return GameEvent(
        start_time=decision.time,
        event_type=GameEventType.MATCH_END,
        source=GameEventSource.LIFECYCLE,
        reason=decision.reason,
        confidence=decision.detection.confidence,
        evidence_ids=[decision.detection.id],
        source_frames=[
            SourceFrameReference(
                frame_id=frame.frame_id,
                source_frame_index=frame.source_frame_index,
                timestamp=frame.timestamp,
                source_pts=frame.source_pts,
                source_time_base_num=frame.source_time_base_num,
                source_time_base_den=frame.source_time_base_den,
                frame_path=frame.frame_path,
            )
        ],
    )


def _best_timer(frame: VisionFrameResult) -> DetectorResult | None:
    """Highest-confidence timer result on a frame, if any."""
    results = [
        detection
        for detection in frame.detections
        if detection.detector_name == "timer"
        and isinstance(detection.reading, TimerReading)
    ]
    return max(results, key=lambda item: item.confidence) if results else None
