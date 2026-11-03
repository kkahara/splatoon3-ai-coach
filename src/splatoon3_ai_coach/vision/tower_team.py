"""Associate a Tower marker color with established match team colors."""

from splatoon3_ai_coach.config.models import TowerControlDetectorConfig
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    TowerControlReading,
    TowerTeamObservation,
)
from splatoon3_ai_coach.vision.team_color_calibration import (
    TeamColorCalibrationResult,
    circular_hue_distance,
)


def resolve_tower_team(
    detection: DetectorResult,
    calibration: TeamColorCalibrationResult | None,
    battle_mode_id: str | None,
    config: TowerControlDetectorConfig,
) -> TowerTeamObservation:
    """Resolve marker presentation only from a separated calibrated hue."""
    observation = TowerTeamObservation(result_id=detection.id)
    reading = detection.reading
    if (
        battle_mode_id != "tower_control"
        or calibration is None
        or not isinstance(reading, TowerControlReading)
        or reading.marker is None
        or reading.marker.appearance != "colored_presentation"
        or reading.marker.hue is None
    ):
        return observation
    distances = (
        circular_hue_distance(reading.marker.hue, calibration.ally_h),
        circular_hue_distance(reading.marker.hue, calibration.opponent_h),
    )
    closest = min(distances)
    margin = abs(distances[0] - distances[1])
    if closest > config.team_hue_tolerance or margin < config.team_hue_margin:
        return observation
    team = "ally" if distances[0] < distances[1] else "opponent"
    return TowerTeamObservation(
        result_id=detection.id,
        control_presentation=team,
        color_distance=closest,
        calibration_time=calibration.calibrated_at,
    )
