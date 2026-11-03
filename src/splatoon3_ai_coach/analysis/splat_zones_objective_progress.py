"""Splat Zones adapter for the generic objective-progress evidence boundary."""

from __future__ import annotations

from splatoon3_ai_coach.analysis.objective_progress import ObjectiveProgressObservation
from splatoon3_ai_coach.vision.ids import result_detector_name
from splatoon3_ai_coach.vision.models import GameStateSnapshot

SPLAT_ZONES = "splat_zones"


def splat_zones_objective_observations(
    snapshots: list[GameStateSnapshot], *, max_gap_seconds: float
) -> list[ObjectiveProgressObservation]:
    """Translate fresh remaining counters to normalized objective observations."""
    out: list[ObjectiveProgressObservation] = []
    previous: dict[str, tuple[float, int]] = {}
    for snapshot in sorted(snapshots, key=lambda item: item.timestamp):
        for team, value, quality in (
            ("ally", snapshot.ally_remaining, snapshot.ally_score_quality),
            ("opponent", snapshot.opponent_remaining, snapshot.opponent_score_quality),
        ):
            if quality != "observed" or value is None:
                continue
            prior = previous.get(team)
            state, amount = "unknown", None
            if prior is not None and snapshot.timestamp - prior[0] <= max_gap_seconds:
                amount = float(prior[1] - int(value))
                state = "progress" if amount > 0 else "no_progress"
            previous[team] = (float(snapshot.timestamp), int(value))
            out.append(
                ObjectiveProgressObservation(
                    game_mode_id=SPLAT_ZONES,
                    team=team,  # type: ignore[arg-type]
                    video_time=float(snapshot.timestamp),
                    value_kind="remaining_count",
                    value_label=f"{team} remaining count",
                    original_value=int(value),
                    progress_state_from_previous=state,  # type: ignore[arg-type]
                    normalized_progress_amount=amount,
                    objective_evidence_ids=[
                        item
                        for item in snapshot.evidence_ids
                        if result_detector_name(item) == "score"
                    ],
                )
            )
    return out
