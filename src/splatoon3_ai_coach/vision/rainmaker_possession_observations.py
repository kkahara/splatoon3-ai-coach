"""Group direct Rainmaker message detections into displayed cue observations."""

from collections.abc import Iterable

from splatoon3_ai_coach.vision.models import (
    RainmakerPossessionObservation,
    RainmakerPossessionReading,
    RainmakerPossessionSample,
    SourceFrameReference,
    VisionFrameResult,
)


def collect_rainmaker_possession_observations(
    frame_results: Iterable[VisionFrameResult], max_gap_seconds: float = 1.0
) -> list[RainmakerPossessionObservation]:
    """Group adjacent same-cue detections without reconstructing missed cues."""
    if max_gap_seconds <= 0:
        raise ValueError("max_gap_seconds must be positive")
    grouped: list[RainmakerPossessionObservation] = []
    active: RainmakerPossessionObservation | None = None
    for frame in sorted(frame_results, key=lambda item: item.timestamp):
        sample = _sample(frame)
        if sample is None:
            continue
        cue = sample.cue.cue
        if (active is None or active.cue != cue
                or sample.video_time - active.last_video_time > max_gap_seconds):
            active = RainmakerPossessionObservation(
                cue=cue,
                first_video_time=sample.video_time,
                last_video_time=sample.video_time,
                max_gap_seconds=max_gap_seconds,
                samples=[sample],
            )
            grouped.append(active)
        else:
            active = active.model_copy(update={
                "last_video_time": sample.video_time,
                "samples": [*active.samples, sample],
            })
            grouped[-1] = active
    return grouped


def _sample(frame: VisionFrameResult) -> RainmakerPossessionSample | None:
    """Return a single timestamp-matched cue reading from one frame."""
    matches = [
        result for result in frame.detections
        if result.detector_name == "rainmaker_possession"
        and isinstance(result.reading, RainmakerPossessionReading)
        and result.reading.timestamp == frame.timestamp
    ]
    if len(matches) != 1:
        return None
    result = matches[0]
    reading = result.reading
    assert isinstance(reading, RainmakerPossessionReading)
    return RainmakerPossessionSample(
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
        cue=reading.cue,
    )
