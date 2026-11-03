"""Fresh Rainmaker score evidence, gated by mode, visibility and team binding."""

from splatoon3_ai_coach.vision.models import RainmakerScoreReading, VisionFrameResult


def fuse_rainmaker_score(
    frame: VisionFrameResult,
    battle_mode_id: str | None,
    match_phase: str,
    map_overlay_present: bool | None,
) -> dict[str, object]:
    """Copy supported counts without holding, interpolation or trajectory rules."""
    if battle_mode_id != "rainmaker" or match_phase != "in_match" or map_overlay_present:
        return {}
    results = [
        d
        for d in frame.detections
        if d.detector_name == "rainmaker_score"
        and isinstance(d.reading, RainmakerScoreReading)
        and d.reading.timestamp == frame.timestamp
    ]
    if len(results) != 1:
        return {}
    result = results[0]
    reading = result.reading
    assert isinstance(reading, RainmakerScoreReading)
    out: dict[str, object] = {
        "rainmaker_score_reading": reading,
        "rainmaker_score_evidence_ids": [result.id],
    }
    association = frame.rainmaker_score_association
    if association is None or association.evidence_id != result.id:
        return out
    out["rainmaker_score_association"] = association
    for team in ("ally", "opponent"):
        index = getattr(association, f"{team}_pod")
        if index is not None and 0 <= index < len(reading.pods):
            pod = reading.pods[index]
            if pod.usable:
                out[f"rainmaker_{team}_remaining"] = pod.value
    return out
