"""Group nearby positive opponent-lost HUD observations without game events."""

from splatoon3_ai_coach.vision.models import (
    SourceFrameReference,
    TowerControlReading,
    TowerOpponentLostObservation,
    TowerOpponentLostSample,
    VisionFrameResult,
)


def collect_opponent_lost_observations(
    frames: list[VisionFrameResult], *, max_gap_seconds: float = 1.0
) -> list[TowerOpponentLostObservation]:
    """Group nearby positive frames, preserving every source and evidence ID.

    This is a development grouping of sparse observations, not proof of a
    continuous display or an exact event time. The gap is a grouping parameter;
    two distinct messages close together may be grouped, and long gaps may split
    a display. Raw per-frame readings remain unchanged.
    """
    if max_gap_seconds <= 0:
        raise ValueError("max_gap_seconds must be positive")
    grouped: list[TowerOpponentLostObservation] = []
    active: TowerOpponentLostObservation | None = None
    for frame in sorted(frames, key=lambda item: item.timestamp):
        if _has_other_control_cue(frame):
            active = None
            continue
        sample = _sample(frame)
        if sample is None:
            continue
        if (
            active is not None
            and 0 <= sample.video_time - active.last_video_time <= max_gap_seconds
        ):
            active.samples.append(sample)
            active.last_video_time = sample.video_time
        else:
            active = TowerOpponentLostObservation(
                first_video_time=sample.video_time,
                last_video_time=sample.video_time,
                max_gap_seconds=max_gap_seconds,
                samples=[sample],
            )
            grouped.append(active)
    return grouped


def _has_other_control_cue(frame: VisionFrameResult) -> bool:
    """A separately observed control message bounds a preceding cue display."""
    return any(
        result.detector_name == "tower_control"
        and isinstance(result.reading, TowerControlReading)
        and result.reading.timestamp == frame.timestamp
        and result.reading.control_cue is not None
        and result.reading.control_cue.cue_type != "opponent_lost"
        for result in frame.detections
    )


def _sample(frame: VisionFrameResult) -> TowerOpponentLostSample | None:
    """Accept exactly one same-frame opponent-lost reading."""
    candidates = [
        detection
        for detection in frame.detections
        if detection.detector_name == "tower_control"
        and isinstance(detection.reading, TowerControlReading)
        and detection.reading.timestamp == frame.timestamp
        and detection.reading.control_cue is not None
        and detection.reading.control_cue.cue_type == "opponent_lost"
        and detection.reading.control_cue.team == "opponent"
    ]
    if len(candidates) != 1:
        return None
    result = candidates[0]
    reading = result.reading
    assert isinstance(reading, TowerControlReading) and reading.control_cue is not None
    return TowerOpponentLostSample(
        video_time=frame.timestamp,
        source_frame=SourceFrameReference(
            frame_id=frame.frame_id,
            timestamp=frame.timestamp,
            source_frame_index=frame.source_frame_index,
            source_pts=frame.source_pts,
            source_time_base_num=frame.source_time_base_num,
            source_time_base_den=frame.source_time_base_den,
            frame_path=frame.frame_path,
        ),
        evidence_id=result.id,
        language=reading.control_cue.language,
    )
