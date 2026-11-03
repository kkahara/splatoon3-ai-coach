"""Parallel four-mode factual context built from persisted snapshots only.

Sampling uses the latest in-match snapshot at or before each target within
the configured gap. Held state keeps its original cue evidence. Sampling
never searches past an unknown snapshot to recover an older known state.
"""

from collections.abc import Callable
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.clam_objective_progress import (
    clam_objective_observations,
)
from splatoon3_ai_coach.analysis.objective_progress import (
    ObjectiveProgressEvidence,
    ObjectiveProgressObservation,
    ObjectiveTemporalSample,
)
from splatoon3_ai_coach.analysis.player_count_series import (
    assess_roster_plausibility,
    observations_from_snapshots,
)
from splatoon3_ai_coach.analysis.rainmaker_objective_progress import (
    rainmaker_objective_observations,
)
from splatoon3_ai_coach.analysis.score_context import ScoreEvidence
from splatoon3_ai_coach.analysis.zone_control_context import ZoneControlEvidence
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig
from splatoon3_ai_coach.vision.models import (
    ClamScoreObservation,
    GameStateSnapshot,
    RainmakerCheckpointState,
    RainmakerPossessionEvidence,
    RainmakerScoreAssociation,
    SourceFrameReference,
    TowerControlEvidence,
)

T = TypeVar("T", bound=BaseModel)


class SparseObjectiveSamples(BaseModel, Generic[T]):
    """Bounded samples at the anchor and existing pre-death/lookback targets."""

    at_anchor: T | None = None
    pre_death: T | None = None
    lookback: T | None = None


class TowerControlSample(BaseModel):
    """Persisted control, with the time and direct evidence establishing it."""

    sample_video_time: float = Field(ge=0)
    source_frame: SourceFrameReference | None = None
    state: Literal["unknown", "ally_control", "opponent_control", "returning"]
    quality: Literal["unknown", "observed", "held"] = "unknown"
    state_established_video_time: float | None = None
    evidence: TowerControlEvidence | None = None


class RainmakerPossessionSample(BaseModel):
    """Persisted possession independently of counts and checkpoint facts."""

    sample_video_time: float = Field(ge=0)
    source_frame: SourceFrameReference | None = None
    state: Literal["unknown", "neutral", "ally_possession", "opponent_possession"]
    quality: Literal["unknown", "observed", "held"] = "unknown"
    state_established_video_time: float | None = None
    evidence: RainmakerPossessionEvidence | None = None


class RainmakerCountSample(BaseModel):
    """Fresh team-associated native counts and their detector provenance."""

    video_time: float = Field(ge=0)
    source_frame: SourceFrameReference | None = None
    ally_remaining: int | None = Field(default=None, ge=0, le=100)
    opponent_remaining: int | None = Field(default=None, ge=0, le=100)
    evidence_ids: list[str] = Field(default_factory=list)
    association: RainmakerScoreAssociation | None = None


class RainmakerCheckpointSample(BaseModel):
    """Per-team cleared/unknown facts, retaining direct breakthrough evidence."""

    sample_video_time: float = Field(ge=0)
    source_frame: SourceFrameReference | None = None
    state: RainmakerCheckpointState


class ClamScoreSample(BaseModel):
    """Fresh team-associated primary scores and visible penalties."""

    video_time: float = Field(ge=0)
    source_frame: SourceFrameReference | None = None
    score: ClamScoreObservation


class SplatZonesObjectiveContext(BaseModel):
    """Existing Zones evidence reused without changing its interpretation."""

    mode: Literal["splat_zones"] = "splat_zones"
    score: ScoreEvidence | None = None
    control: ZoneControlEvidence | None = None
    progress: ObjectiveProgressEvidence | None = None


class TowerControlObjectiveContext(BaseModel):
    """Only the active explicit-message control path is exposed."""

    mode: Literal["tower_control"] = "tower_control"
    progress: None = None
    control: SparseObjectiveSamples[TowerControlSample]


class RainmakerObjectiveContext(BaseModel):
    """Independent count, possession and checkpoint evidence streams."""

    mode: Literal["rainmaker"] = "rainmaker"
    progress: ObjectiveProgressEvidence | None = None
    counts: SparseObjectiveSamples[RainmakerCountSample]
    possession: SparseObjectiveSamples[RainmakerPossessionSample]
    checkpoint: SparseObjectiveSamples[RainmakerCheckpointSample]


class ClamBlitzObjectiveContext(BaseModel):
    """Clam primary scores and progress, independent of basket state."""

    mode: Literal["clam_blitz"] = "clam_blitz"
    score: SparseObjectiveSamples[ClamScoreSample]
    progress: ObjectiveProgressEvidence | None = None


ObjectiveContext = Annotated[
    SplatZonesObjectiveContext
    | TowerControlObjectiveContext
    | RainmakerObjectiveContext
    | ClamBlitzObjectiveContext,
    Field(discriminator="mode"),
]


def build_objective_context(
    snapshots: list[GameStateSnapshot],
    *,
    battle_mode_id: str | None,
    anchor: float,
    death_time: float | None,
    window_start: float,
    window_end: float,
    config: ScenarioBuilderConfig,
    score: ScoreEvidence | None,
    control: ZoneControlEvidence | None,
    progress: ObjectiveProgressEvidence | None,
) -> ObjectiveContext | None:
    """Build the additive inspection path; legacy builders/fields are untouched."""
    if battle_mode_id == "splat_zones":
        return SplatZonesObjectiveContext(score=score, control=control, progress=progress)
    if battle_mode_id not in ("tower_control", "rainmaker", "clam_blitz"):
        return None
    ordered = sorted(snapshots, key=lambda item: item.timestamp)
    targets = {"at_anchor": anchor}
    if death_time is not None:
        target = death_time - config.score_pre_death_offset_seconds
        targets.update(pre_death=target, lookback=target - config.score_lookback_seconds)

    def samples(
        convert: Callable[[GameStateSnapshot], T | None],
    ) -> SparseObjectiveSamples[T]:
        selected = {}
        for label, target in targets.items():
            candidates = [
                s
                for s in ordered
                if target - config.context_max_gap_seconds <= s.timestamp <= target
            ]
            if candidates and candidates[-1].match_phase == "in_match":
                selected[label] = convert(candidates[-1])
        return SparseObjectiveSamples(**selected)

    if battle_mode_id == "tower_control":
        return TowerControlObjectiveContext(control=samples(_tower_sample))
    if battle_mode_id == "clam_blitz":
        return ClamBlitzObjectiveContext(
            score=samples(_clam_score_sample),
            progress=_progress_evidence(
                ordered,
                window_start,
                window_end,
                config,
                clam_objective_observations(
                    ordered, max_gap_seconds=config.context_max_gap_seconds
                ),
            ),
        )
    return RainmakerObjectiveContext(
        counts=samples(_count_sample),
        possession=samples(_possession_sample),
        checkpoint=samples(_checkpoint_sample),
        progress=_rainmaker_progress(ordered, window_start, window_end, config),
    )


def _quality(
    snapshot: GameStateSnapshot,
    evidence: TowerControlEvidence | RainmakerPossessionEvidence,
) -> Literal["observed", "held"]:
    """Only a supporting current frame is observed; all later copies are held."""
    sources = [evidence.source_frame]
    for name in ("cue_observation", "took_observation", "opponent_lost_observation"):
        observation = getattr(evidence, name, None)
        if observation is not None:
            sources.extend(sample.source_frame for sample in observation.samples)
    return (
        "observed"
        if any(
            source.timestamp == snapshot.timestamp
            and snapshot.source_frame is not None
            and source.frame_id == snapshot.source_frame.frame_id
            for source in sources
        )
        else "held"
    )


def _tower_sample(snap: GameStateSnapshot) -> TowerControlSample:
    evidence = snap.tower_control_evidence
    known = snap.tower_control_state != "unknown" and evidence is not None
    if known and evidence.video_time > snap.timestamp:
        known = False
    return TowerControlSample(
        sample_video_time=snap.timestamp,
        source_frame=snap.source_frame,
        state=snap.tower_control_state if known else "unknown",
        quality=_quality(snap, evidence) if known else "unknown",
        state_established_video_time=evidence.video_time if known else None,
        evidence=evidence if known else None,
    )


def _possession_sample(snap: GameStateSnapshot) -> RainmakerPossessionSample:
    evidence = snap.rainmaker_possession_evidence
    known = (
        evidence is not None
        and evidence.state == snap.rainmaker_possession_state
        and evidence.video_time <= snap.timestamp
    )
    return RainmakerPossessionSample(
        sample_video_time=snap.timestamp,
        source_frame=snap.source_frame,
        state=snap.rainmaker_possession_state if known else "unknown",
        quality=_quality(snap, evidence) if known else "unknown",
        state_established_video_time=evidence.video_time if known else None,
        evidence=evidence if known else None,
    )


def _count_sample(snap: GameStateSnapshot) -> RainmakerCountSample:
    usable = bool(snap.rainmaker_score_evidence_ids)
    return RainmakerCountSample(
        video_time=snap.timestamp,
        source_frame=snap.source_frame,
        ally_remaining=snap.rainmaker_ally_remaining if usable else None,
        opponent_remaining=snap.rainmaker_opponent_remaining if usable else None,
        evidence_ids=snap.rainmaker_score_evidence_ids,
        association=snap.rainmaker_score_association,
    )


def _clam_score_sample(snap: GameStateSnapshot) -> ClamScoreSample | None:
    if snap.clam_score is None:
        return None
    return ClamScoreSample(
        video_time=snap.timestamp,
        source_frame=snap.source_frame,
        score=snap.clam_score,
    )


def _checkpoint_sample(snap: GameStateSnapshot) -> RainmakerCheckpointSample:
    state = RainmakerCheckpointState()
    original = snap.rainmaker_checkpoint_state
    if original is not None:
        for team in ("ally", "opponent"):
            evidence = getattr(original, f"{team}_evidence")
            if (
                getattr(original, team) == "cleared"
                and evidence is not None
                and evidence.team == team
                and evidence.first_video_time <= snap.timestamp
            ):
                setattr(state, team, "cleared")
                setattr(state, f"{team}_evidence", evidence)
    return RainmakerCheckpointSample(
        sample_video_time=snap.timestamp,
        source_frame=snap.source_frame,
        state=state,
    )


def _rainmaker_progress(
    snapshots: list[GameStateSnapshot],
    start: float,
    end: float,
    config: ScenarioBuilderConfig,
) -> ObjectiveProgressEvidence | None:
    """Reuse the Rainmaker adapter and existing roster reliability contract."""
    return _progress_evidence(
        snapshots,
        start,
        end,
        config,
        rainmaker_objective_observations(
            snapshots,
            max_gap_seconds=config.context_max_gap_seconds,
        ),
    )


def _progress_evidence(
    snapshots: list[GameStateSnapshot],
    start: float,
    end: float,
    config: ScenarioBuilderConfig,
    observations: list[ObjectiveProgressObservation],
) -> ObjectiveProgressEvidence | None:
    """Pair mode-adapter observations with existing roster and lifecycle evidence."""
    unreliable = assess_roster_plausibility(
        observations_from_snapshots(snapshots),
        min_respawn_seconds=config.roster_plausibility.min_respawn_seconds,
        window_seconds=config.roster_plausibility.window_seconds,
    )
    by_time = {s.timestamp: s for s in snapshots}
    out = []
    for obs in observations:
        if not start <= obs.video_time <= end:
            continue
        snap = by_time[obs.video_time]
        has_roster = (
            snap.ally_alive_count is not None and snap.opponent_alive_count is not None
        )
        out.append(
            ObjectiveTemporalSample(
                objective=obs,
                ally_alive_count=snap.ally_alive_count,
                opponent_alive_count=snap.opponent_alive_count,
                roster_reliable=has_roster
                and not any(u.contains(obs.video_time) for u in unreliable),
                roster_evidence_ids=list(snap.evidence_ids) if has_roster else [],
                player_lifecycle=snap.player_lifecycle,
                lifecycle_evidence_ids=list(snap.evidence_ids),
            )
        )
    return (
        ObjectiveProgressEvidence(
            observations=out,
            max_gap_seconds=config.context_max_gap_seconds,
        )
        if out
        else None
    )
