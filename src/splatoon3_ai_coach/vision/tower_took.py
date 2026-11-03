"""Direct Tower acquisition cue observations and conservative display grouping."""

from collections.abc import Iterable

from splatoon3_ai_coach.vision.models import (
    SourceFrameReference,
    TowerControlReading,
    TowerTookObservation,
    TowerTookSample,
    VisionFrameResult,
)

_CUE_NAMES = {"ally_took": "ally_took_tower", "opponent_took": "opponent_took_tower"}


def collect_took_observations(
    frame_results: Iterable[VisionFrameResult], max_gap_seconds: float = 1.0
) -> list[TowerTookObservation]:
    """Group adjacent direct acquisition detections without inferring missed cues."""
    if max_gap_seconds <= 0:
        raise ValueError("max_gap_seconds must be positive")
    grouped: list[TowerTookObservation] = []
    active: TowerTookObservation | None = None
    for frame in sorted(frame_results, key=lambda item: item.timestamp):
        sample = _sample(frame)
        if sample is None:
            if _other_control_cue(frame):
                active = None
            continue
        cue_name = _CUE_NAMES[sample.cue.cue_type]
        if (
            active is None
            or active.cue != cue_name
            or sample.video_time - active.last_video_time > max_gap_seconds
        ):
            active = TowerTookObservation(
                cue=cue_name,
                first_video_time=sample.video_time,
                last_video_time=sample.video_time,
                max_gap_seconds=max_gap_seconds,
                samples=[sample],
            )
            grouped.append(active)
        else:
            active = active.model_copy(
                update={
                    "last_video_time": sample.video_time,
                    "samples": [*active.samples, sample],
                }
            )
            grouped[-1] = active
    return grouped


def _sample(frame: VisionFrameResult) -> TowerTookSample | None:
    """Extract one unambiguous acquisition sample from a matching frame."""
    matches = [
        result
        for result in frame.detections
        if result.detector_name == "tower_control"
        and isinstance(result.reading, TowerControlReading)
        and result.reading.timestamp == frame.timestamp
        and result.reading.control_cue is not None
        and result.reading.control_cue.cue_type in _CUE_NAMES
    ]
    if len(matches) != 1:
        return None
    result = matches[0]
    reading = result.reading
    assert isinstance(reading, TowerControlReading) and reading.control_cue is not None
    cue = reading.control_cue
    expected_team = "ally" if cue.cue_type == "ally_took" else "opponent"
    if cue.team != expected_team:
        return None
    return TowerTookSample(
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
        cue=cue,
    )


def _other_control_cue(frame: VisionFrameResult) -> bool:
    """Return whether a different explicit message bounds a possible display."""
    return any(
        result.detector_name == "tower_control"
        and isinstance(result.reading, TowerControlReading)
        and result.reading.timestamp == frame.timestamp
        and result.reading.control_cue is not None
        and result.reading.control_cue.cue_type not in _CUE_NAMES
        for result in frame.detections
    )
