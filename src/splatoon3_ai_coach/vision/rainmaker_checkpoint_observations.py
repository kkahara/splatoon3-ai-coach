"""Discrete observations of directly detected Rainmaker breakthrough displays."""

from collections.abc import Iterable

from splatoon3_ai_coach.vision.models import (
    RainmakerCheckpointObservation,
    RainmakerCheckpointReading,
    RainmakerCheckpointSample,
    RainmakerPossessionReading,
    SourceFrameReference,
    VisionFrameResult,
)


def collect_rainmaker_checkpoint_observations(
    frame_results: Iterable[VisionFrameResult], max_gap_seconds: float = 1.0
) -> list[RainmakerCheckpointObservation]:
    """Group repeated positive frames without manufacturing missed milestones."""
    if max_gap_seconds <= 0:
        raise ValueError("max_gap_seconds must be positive")
    groups: list[RainmakerCheckpointObservation] = []
    active: RainmakerCheckpointObservation | None = None
    for frame in sorted(frame_results, key=lambda item: item.timestamp):
        sample = _sample(frame)
        if sample is None:
            if any(
                isinstance(
                    result.reading,
                    (RainmakerPossessionReading, RainmakerCheckpointReading),
                )
                and result.reading.timestamp == frame.timestamp
                for result in frame.detections
            ):
                active = None
            continue
        if (
            active is None
            or active.team != sample.reading.team
            or sample.video_time - active.last_video_time > max_gap_seconds
        ):
            active = RainmakerCheckpointObservation(
                team=sample.reading.team,
                first_video_time=sample.video_time,
                last_video_time=sample.video_time,
                max_gap_seconds=max_gap_seconds,
                samples=[sample],
            )
            groups.append(active)
        else:
            active = active.model_copy(
                update={
                    "last_video_time": sample.video_time,
                    "samples": [*active.samples, sample],
                }
            )
            groups[-1] = active
    return groups


def checkpoint_observations_by_frame(
    frame_results: Iterable[VisionFrameResult],
) -> dict[str, RainmakerCheckpointObservation]:
    """Expose only the observed prefix of each display to snapshot fusion."""
    mapped = {}
    for observation in collect_rainmaker_checkpoint_observations(frame_results):
        for index, sample in enumerate(observation.samples):
            mapped[sample.source_frame.frame_id] = observation.model_copy(
                update={
                    "samples": observation.samples[: index + 1],
                    "last_video_time": sample.video_time,
                }
            )
    return mapped


def _sample(frame: VisionFrameResult) -> RainmakerCheckpointSample | None:
    """Extract one timestamp-matched direct breakthrough result."""
    results = [
        result
        for result in frame.detections
        if result.detector_name == "rainmaker_checkpoint"
        and isinstance(result.reading, RainmakerCheckpointReading)
        and result.reading.timestamp == frame.timestamp
    ]
    if len(results) != 1:
        return None
    result = results[0]
    return RainmakerCheckpointSample(
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
        reading=result.reading,
    )
