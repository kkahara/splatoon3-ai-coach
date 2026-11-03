"""Rainmaker native remaining counts translated to shared objective evidence."""

from splatoon3_ai_coach.analysis.objective_progress import ObjectiveProgressObservation
from splatoon3_ai_coach.vision.models import GameStateSnapshot


def rainmaker_objective_observations(
    snapshots: list[GameStateSnapshot],
    *,
    max_gap_seconds: float,
) -> list[ObjectiveProgressObservation]:
    """Translate fresh same-team count decreases to positive progress units."""
    out: list[ObjectiveProgressObservation] = []
    previous: dict[str, tuple[float, int]] = {}
    for snapshot in sorted(snapshots, key=lambda item: item.timestamp):
        if snapshot.match_phase != "in_match":
            previous.clear()
            continue
        for team in ("ally", "opponent"):
            value = getattr(snapshot, f"rainmaker_{team}_remaining")
            if value is None or not snapshot.rainmaker_score_evidence_ids:
                continue
            prior = previous.get(team)
            amount = None
            state = "unknown"
            if prior is not None and 0 < snapshot.timestamp - prior[0] <= max_gap_seconds:
                if value <= prior[1]:
                    amount = float(prior[1] - value)
                    state = "progress" if amount > 0 else "no_progress"
            previous[team] = (snapshot.timestamp, value)
            out.append(
                ObjectiveProgressObservation(
                    game_mode_id="rainmaker",
                    team=team,
                    video_time=snapshot.timestamp,
                    value_kind="remaining_count",
                    value_label=f"{team} remaining count",
                    original_value=value,
                    progress_state_from_previous=state,
                    normalized_progress_amount=amount,
                    objective_evidence_ids=snapshot.rainmaker_score_evidence_ids,
                )
            )
    return out
