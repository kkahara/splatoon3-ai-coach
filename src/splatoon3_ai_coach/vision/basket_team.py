"""Associate basket colors with previously calibrated match team colors."""

from splatoon3_ai_coach.vision.models import (
    BasketTeamObservation,
    ClamBasketStateReading,
    DetectorResult,
)
from splatoon3_ai_coach.vision.team_color_calibration import TeamColorCalibrationResult

# The initial state cue has not passed independent, multi-match validation.
# Do not publish a semantic team association until that validation is complete.
OPEN_STATE_VALIDATED = False


def resolve_basket_team(
    detection: DetectorResult,
    calibration: TeamColorCalibrationResult | None,
    battle_mode_id: str | None,
) -> BasketTeamObservation:
    """Resolve color conservatively; unknown mode, hue, or calibration stays unknown."""
    result = BasketTeamObservation(state_result_id=detection.id)
    reading = detection.reading
    if (
        battle_mode_id != "clam_blitz"
        or calibration is None
        or not isinstance(reading, ClamBasketStateReading)
        or reading.visual_state != "open"
        or not OPEN_STATE_VALIDATED
        or reading.hue is None
        or reading.hue_concentration < 0.8
    ):
        return result
    distances = [
        min(abs(reading.hue - h), 180 - abs(reading.hue - h))
        for h in (calibration.ally_h, calibration.opponent_h)
    ]
    closest = min(distances)
    team = "unknown"
    if closest <= 15 and abs(distances[0] - distances[1]) >= 8:
        team = "ally" if distances[0] < distances[1] else "opponent"
    return BasketTeamObservation(
        state_result_id=detection.id,
        scoring_team=team,
        color_distance=closest,
        calibration_time=calibration.calibrated_at,
    )
