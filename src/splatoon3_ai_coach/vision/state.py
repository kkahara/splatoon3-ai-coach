"""Pure temporal state fusion from vision frame results."""

from collections import deque

from splatoon3_ai_coach.config.models import StateFusionConfig, TimerDetectorConfig
from splatoon3_ai_coach.vision.models import (
    GameStateSnapshot,
    SourceFrameReference,
    TimerReading,
    VisionFrameResult,
)


def fuse_timer_state(
    frame_results: list[VisionFrameResult],
    timer_config: TimerDetectorConfig,
    fusion_config: StateFusionConfig,
) -> list[GameStateSnapshot]:
    """Convert timer observations into accepted game-state snapshots."""
    snapshots: list[GameStateSnapshot] = []
    accepted_values: deque[float] = deque(maxlen=fusion_config.smoothing_window)
    last_accepted_value: float | None = None
    last_accepted_at: float | None = None
    last_evidence_ids: list[str] = []
    last_source: SourceFrameReference | None = None

    for frame in sorted(frame_results, key=lambda item: item.timestamp):
        timer_results = [
            detection
            for detection in frame.detections
            if detection.detector_name == "timer"
            and isinstance(detection.reading, TimerReading)
        ]
        source = _source_reference(frame)

        if not timer_results:
            snapshots.append(
                _held_or_unknown(
                    frame.timestamp,
                    last_accepted_value,
                    last_accepted_at,
                    last_evidence_ids,
                    last_source,
                    fusion_config.max_hold_duration,
                )
            )
            continue

        best = max(timer_results, key=lambda item: item.confidence)
        reading = best.reading
        assert isinstance(reading, TimerReading)

        if best.confidence < timer_config.min_usable_confidence:
            snapshots.append(
                _held_or_unknown(
                    frame.timestamp,
                    last_accepted_value,
                    last_accepted_at,
                    last_evidence_ids,
                    last_source,
                    fusion_config.max_hold_duration,
                )
            )
            continue

        candidate = reading.seconds_remaining
        if not _temporally_valid(candidate, last_accepted_value):
            snapshots.append(
                _held_or_unknown(
                    frame.timestamp,
                    last_accepted_value,
                    last_accepted_at,
                    last_evidence_ids,
                    last_source,
                    fusion_config.max_hold_duration,
                )
            )
            continue

        accepted_values.append(candidate)
        smoothed = float(sum(accepted_values) / len(accepted_values))
        quality = "observed" if smoothed == candidate else "smoothed"
        last_accepted_value = smoothed
        last_accepted_at = frame.timestamp
        last_evidence_ids = [best.id]
        last_source = source

        snapshots.append(
            GameStateSnapshot(
                timestamp=frame.timestamp,
                match_time_remaining=smoothed,
                quality=quality,
                evidence_ids=[best.id],
                source_frame=source,
                last_observed_at=last_accepted_at,
                observation_age_seconds=0.0,
            )
        )

    return snapshots


def _temporally_valid(candidate: float, last_accepted: float | None) -> bool:
    """Return whether a timer reading is monotonic during normal play."""
    if last_accepted is None:
        return True
    return candidate <= last_accepted + 0.5


def _held_or_unknown(
    timestamp: float,
    last_value: float | None,
    last_observed_at: float | None,
    evidence_ids: list[str],
    source: SourceFrameReference | None,
    max_hold_duration: float,
) -> GameStateSnapshot:
    """Emit held or unknown state when no new observation is accepted."""
    if last_value is not None and last_observed_at is not None:
        age = timestamp - last_observed_at
        if age <= max_hold_duration:
            return GameStateSnapshot(
                timestamp=timestamp,
                match_time_remaining=last_value,
                quality="held",
                evidence_ids=list(evidence_ids),
                source_frame=source,
                last_observed_at=last_observed_at,
                observation_age_seconds=age,
            )

    return GameStateSnapshot(
        timestamp=timestamp,
        match_time_remaining=None,
        quality="unknown",
        evidence_ids=[],
        source_frame=source,
        last_observed_at=last_observed_at,
        observation_age_seconds=(
            None if last_observed_at is None else timestamp - last_observed_at
        ),
    )


def _source_reference(frame: VisionFrameResult) -> SourceFrameReference:
    """Build a source-frame reference from a vision frame result."""
    return SourceFrameReference(
        frame_id=frame.frame_id,
        source_frame_index=frame.source_frame_index,
        timestamp=frame.timestamp,
        source_pts=frame.source_pts,
        source_time_base_num=frame.source_time_base_num,
        source_time_base_den=frame.source_time_base_den,
        frame_path=frame.frame_path,
    )
