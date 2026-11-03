"""Build mode-neutral temporal objective evidence from persisted snapshots."""

from __future__ import annotations

from splatoon3_ai_coach.analysis.objective_progress import (
    ObjectiveProgressEvidence,
    ObjectiveTemporalSample,
)
from splatoon3_ai_coach.analysis.player_count_series import (
    assess_roster_plausibility,
    observations_from_snapshots,
)
from splatoon3_ai_coach.analysis.splat_zones_objective_progress import (
    SPLAT_ZONES,
    splat_zones_objective_observations,
)
from splatoon3_ai_coach.vision.models import GameStateSnapshot


def build_objective_progress_evidence(
    snapshots: list[GameStateSnapshot],
    *,
    battle_mode_id: str | None,
    window_start: float,
    window_end: float,
    max_gap_seconds: float,
    min_respawn_seconds: float,
    plausibility_window_seconds: float,
) -> ObjectiveProgressEvidence | None:
    """Build generic temporal samples using the one implemented mode adapter."""
    if battle_mode_id != SPLAT_ZONES:
        return None
    ordered = sorted(snapshots, key=lambda item: item.timestamp)
    unreliable = assess_roster_plausibility(
        observations_from_snapshots(ordered),
        min_respawn_seconds=min_respawn_seconds,
        window_seconds=plausibility_window_seconds,
    )
    by_time = {float(item.timestamp): item for item in ordered}
    observations = splat_zones_objective_observations(
        ordered, max_gap_seconds=max_gap_seconds
    )
    samples: list[ObjectiveTemporalSample] = []
    for obs in observations:
        if not window_start <= obs.video_time <= window_end:
            continue
        snap = by_time[obs.video_time]
        has_roster = (
            snap.ally_alive_count is not None and snap.opponent_alive_count is not None
        )
        reliable = has_roster and not any(
            item.contains(obs.video_time) for item in unreliable
        )
        samples.append(
            ObjectiveTemporalSample(
                objective=obs,
                ally_alive_count=snap.ally_alive_count,
                opponent_alive_count=snap.opponent_alive_count,
                roster_reliable=reliable,
                roster_evidence_ids=list(snap.evidence_ids) if has_roster else [],
                player_lifecycle=snap.player_lifecycle,
                lifecycle_evidence_ids=list(snap.evidence_ids),
            )
        )
    return (
        ObjectiveProgressEvidence(observations=samples, max_gap_seconds=max_gap_seconds)
        if samples
        else None
    )
