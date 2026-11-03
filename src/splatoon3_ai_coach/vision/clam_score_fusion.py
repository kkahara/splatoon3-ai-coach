"""Clam Blitz score observations from the fixed left/right HUD layout."""

from splatoon3_ai_coach.config.models import ScoreDetectorConfig
from splatoon3_ai_coach.vision.models import (
    ClamScoreObservation,
    ScoreReading,
    ScoreSideReading,
    VisionFrameResult,
)


def fuse_clam_score(
    frame: VisionFrameResult,
    battle_mode_id: str | None,
    match_phase: str,
    map_overlay_present: bool | None,
    config: ScoreDetectorConfig,
) -> ClamScoreObservation | None:
    """Associate only fresh, usable Clam readings; never hold or infer scores."""
    if battle_mode_id != "clam_blitz" or match_phase != "in_match" or map_overlay_present:
        return None
    results = [
        item
        for item in frame.detections
        if item.detector_name == "clam_score"
        and isinstance(item.reading, ScoreReading)
        and item.reading.battle_mode_id == "clam_blitz"
    ]
    if len(results) != 1:
        return None
    result = results[0]
    if not result.id:
        return None
    reading = result.reading
    assert isinstance(reading, ScoreReading)
    score = ClamScoreObservation(
        ally_remaining=_score(reading.left, config),
        opponent_remaining=_score(reading.right, config),
        ally_penalty=_penalty(reading.left_penalty, reading.left_penalty_scores, config),
        opponent_penalty=_penalty(
            reading.right_penalty, reading.right_penalty_scores, config
        ),
        evidence_id=result.id,
    )
    return (
        score
        if any(
            value is not None
            for value in (
                score.ally_remaining,
                score.opponent_remaining,
                score.ally_penalty,
                score.opponent_penalty,
            )
        )
        else None
    )


def _score(side: ScoreSideReading, config: ScoreDetectorConfig) -> int | None:
    if not side.visible or side.value is None:
        return None
    return side.value if _usable_digits(side.value, side.digit_scores, config) else None


def _penalty(
    value: int | None, scores: list[float], config: ScoreDetectorConfig
) -> int | None:
    if value is None or not 0 < value <= config.penalty_max_value:
        return None
    return value if _usable_digits(value, scores, config) else None


def _usable_digits(value: int, scores: list[float], config: ScoreDetectorConfig) -> bool:
    """Require all reported digits to have passed the configured matching floor."""
    return len(scores) == len(str(value)) and all(
        score >= config.match_threshold for score in scores
    )
