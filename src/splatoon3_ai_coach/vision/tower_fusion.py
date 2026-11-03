"""Mode-gated storage of direct Tower Control HUD evidence."""

from splatoon3_ai_coach.vision.models import (
    MatchPhase,
    TowerControlPresentation,
    TowerControlReading,
    TowerMarkerReading,
    TowerScorePodReading,
    VisionFrameResult,
)


def fuse_tower_observation(
    frame: VisionFrameResult,
    battle_mode_id: str | None,
    match_phase: MatchPhase,
    *,
    map_overlay_present: bool | None = None,
) -> tuple[
    list[TowerScorePodReading],
    TowerMarkerReading | None,
    TowerControlPresentation,
    list[str],
]:
    """Copy one usable Tower observation; never hold or derive motion."""
    if (
        battle_mode_id != "tower_control"
        or match_phase != "in_match"
        or map_overlay_present
    ):
        return [], None, "unknown", []
    candidates = [
        detection
        for detection in frame.detections
        if detection.detector_name == "tower_control"
        and isinstance(detection.reading, TowerControlReading)
    ]
    if not candidates:
        return [], None, "unknown", []
    result = max(candidates, key=lambda item: item.confidence)
    reading = result.reading
    assert isinstance(reading, TowerControlReading)
    pods = [pod for pod in reading.score_pods if pod.usable]
    # Legacy marker/team fields on stored frames are deliberately not fused.
    return pods, None, "unknown", [result.id] if pods else []
