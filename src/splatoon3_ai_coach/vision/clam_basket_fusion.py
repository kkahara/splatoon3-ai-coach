"""Mode-gated fusion of per-frame Clam basket observations."""

from splatoon3_ai_coach.vision.models import (
    ClamBasketStateReading,
    MatchPhase,
    VisionFrameResult,
)

# Accepts the detector's categorical recognized-state signal (0.7).
_MIN_RECOGNIZED_STATE_CONFIDENCE = 0.5


def fuse_basket_state(
    frame: VisionFrameResult,
    battle_mode_id: str | None,
    match_phase: MatchPhase,
    *,
    map_overlay_present: bool | None = None,
) -> tuple[str, list[str]]:
    """Return the observed state and evidence; no hold or inferred transition."""
    if battle_mode_id != "clam_blitz" or match_phase != "in_match" or map_overlay_present:
        return "unknown", []
    candidates = [
        result
        for result in frame.detections
        if result.detector_name == "clam_basket_state"
        and isinstance(result.reading, ClamBasketStateReading)
        and result.reading.visual_state != "unknown"
        and result.confidence >= _MIN_RECOGNIZED_STATE_CONFIDENCE
    ]
    if not candidates:
        return "unknown", []
    result = max(candidates, key=lambda item: item.confidence)
    assert isinstance(result.reading, ClamBasketStateReading)
    return result.reading.visual_state, [result.id]
