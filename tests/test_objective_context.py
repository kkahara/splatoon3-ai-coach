"""Additive objective context, provenance, sampling, and consumer compatibility."""

import pytest
from pydantic import ValidationError

from splatoon3_ai_coach.analysis import scenario_context
from splatoon3_ai_coach.analysis.objective_context import (
    RainmakerObjectiveContext,
    SplatZonesObjectiveContext,
    TowerControlObjectiveContext,
)
from splatoon3_ai_coach.analysis.scenario_context import ScenarioContext
from splatoon3_ai_coach.analysis.scenario_evidence import ScenarioEvidencePack
from splatoon3_ai_coach.analysis.scenario_models import Scenario
from splatoon3_ai_coach.coach.coach import serialize_coach_input_user_prompt
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.death_importance import score_death_candidate
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.coach.objective_temporal_facts import (
    derive_objective_temporal_facts,
)
from splatoon3_ai_coach.config.models import DeathFactorThresholds, ScenarioBuilderConfig
from splatoon3_ai_coach.vision.models import (
    GameEvent,
    GameStateSnapshot,
    RainmakerCheckpointObservation,
    RainmakerCheckpointReading,
    RainmakerCheckpointSample,
    RainmakerCheckpointState,
    RainmakerPossessionEvidence,
    RainmakerPossessionObservation,
    RainmakerPossessionSample,
    SourceFrameReference,
    TowerControlCue,
    TowerControlEvidence,
    TowerTookObservation,
    TowerTookSample,
)

CONFIG = ScenarioBuilderConfig(
    context_max_gap_seconds=1,
    score_pre_death_offset_seconds=0.5,
    score_lookback_seconds=1,
)
SCENARIO = Scenario(
    scenario_id="death:10",
    scenario_type="death_episode",
    start_time=10,
    end_time=12,
    confidence=1,
    outcome="incomplete",
    event_ids=[],
)


def _source(t: float) -> SourceFrameReference:
    return SourceFrameReference(
        frame_id=f"frame-{t}",
        timestamp=t,
        source_frame_index=int(t * 60),
        source_pts=int(t * 600),
        source_time_base_num=1,
        source_time_base_den=600,
    )


def _snap(t: float, **fields: object) -> GameStateSnapshot:
    return GameStateSnapshot(
        timestamp=t,
        match_phase="in_match",
        source_frame=_source(t),
        **fields,
    )


def _context(mode: str | None, snapshots: list[GameStateSnapshot]) -> ScenarioContext:
    return scenario_context.build_scenario_context(
        [GameEvent(start_time=10, event_type="death", confidence=1)],
        SCENARIO,
        CONFIG,
        evidence=ScenarioEvidencePack(state_snapshots=snapshots, battle_mode_id=mode),
    )


def _tower(t: float = 9.5) -> TowerControlEvidence:
    cue = TowerControlCue(
        cue_type="ally_took", team="ally", template_score=1, language="en"
    )
    return TowerControlEvidence(
        video_time=t,
        source_frame=_source(t),
        detector_evidence_id="tower-cue",
        cue=cue,
        took_observation=TowerTookObservation(
            cue="ally_took_tower",
            first_video_time=t,
            last_video_time=t,
            max_gap_seconds=1,
            samples=[
                TowerTookSample(
                    video_time=t,
                    source_frame=_source(t),
                    evidence_id="tower-cue",
                    cue=cue,
                )
            ],
        ),
    )


def _possession(t: float = 9.5) -> RainmakerPossessionEvidence:
    observation = RainmakerPossessionObservation(
        cue="ally_acquired_rainmaker",
        first_video_time=t,
        last_video_time=t,
        max_gap_seconds=1,
        samples=[
            RainmakerPossessionSample(
                video_time=t,
                source_frame=_source(t),
                evidence_id="rain-cue",
                cue=dict(
                    cue="ally_acquired_rainmaker",
                    language="ja",
                    template_score=1,
                    template_name="ally_acquired",
                ),
            )
        ],
    )
    return RainmakerPossessionEvidence(
        state="ally_possession",
        video_time=t,
        source_frame=_source(t),
        detector_evidence_ids=["rain-cue"],
        cue_observation=observation,
    )


def _checkpoint(t: float = 9.5) -> RainmakerCheckpointState:
    observation = RainmakerCheckpointObservation(
        team="ally",
        first_video_time=t,
        last_video_time=t,
        max_gap_seconds=1,
        samples=[
            RainmakerCheckpointSample(
                video_time=t,
                source_frame=_source(t),
                evidence_id="breakthrough",
                reading=RainmakerCheckpointReading(
                    team="ally",
                    language="ja",
                    template_name="ally",
                    template_score=1,
                    timestamp=t,
                ),
            )
        ],
    )
    return RainmakerCheckpointState(ally="cleared", ally_evidence=observation)


def test_zones_reuses_legacy_evidence_and_full_context_stays_compatible(
    monkeypatch,
) -> None:
    snaps = [
        _snap(
            t,
            ally_remaining=value,
            ally_score_quality="observed",
            opponent_remaining=55,
            opponent_score_quality="observed",
            ally_penalty=3,
            ally_penalty_quality="observed",
            zone_control_state="ally_control",
            zone_control_quality="observed",
        )
        for t, value in [(8.5, 72), (9.5, 70), (10, 68)]
    ]
    ctx = _context("splat_zones", snaps)
    objective = ctx.objective
    assert isinstance(objective, SplatZonesObjectiveContext)
    assert objective.score is ctx.score
    assert objective.control is ctx.zone_control
    assert objective.progress is ctx.objective_progress
    monkeypatch.setattr(
        scenario_context, "build_objective_context", lambda *a, **kw: None
    )
    baseline = _context("splat_zones", snaps)
    assert ctx.model_dump(exclude={"objective"}) == baseline.model_dump(
        exclude={"objective"}
    )


def test_tower_observed_and_held_keep_original_direct_evidence() -> None:
    evidence = _tower()
    ctx = _context(
        "tower_control",
        [
            _snap(
                t,
                tower_control_state="ally_control",
                tower_control_evidence=evidence,
                # Historical fields cannot populate semantic score/progress.
                tower_checkpoint_identity=2,
                tower_control_presentation="opponent",
            )
            for t in (9.5, 10)
        ],
    )
    obj = ctx.objective
    assert isinstance(obj, TowerControlObjectiveContext)
    assert obj.progress is None
    assert obj.control.pre_death.quality == "observed"
    held = obj.control.at_anchor
    assert held.quality == "held" and held.sample_video_time == 10
    assert held.state_established_video_time == 9.5
    assert held.evidence == evidence
    assert held.evidence.source_frame.source_pts == 5700
    assert ctx.score is ctx.zone_control is ctx.objective_progress is None
    assert set(obj.model_dump()) == {"mode", "progress", "control"}


def test_rainmaker_independent_streams_progress_and_breakthrough_provenance() -> None:
    possession, checkpoint = _possession(), _checkpoint()
    snaps = [
        _snap(
            t,
            rainmaker_ally_remaining=value,
            rainmaker_opponent_remaining=46,
            rainmaker_score_evidence_ids=[f"count-{t}"],
            rainmaker_possession_state="ally_possession",
            rainmaker_possession_evidence=possession,
            rainmaker_checkpoint_state=checkpoint,
        )
        for t, value in [(9.5, 72), (10, 68)]
    ]
    ctx = _context("rainmaker", snaps)
    obj = ctx.objective
    assert isinstance(obj, RainmakerObjectiveContext)
    assert obj.counts.at_anchor.ally_remaining == 68
    assert obj.counts.at_anchor.opponent_remaining == 46
    assert obj.counts.at_anchor.evidence_ids == ["count-10"]
    ally = [s.objective for s in obj.progress.observations if s.objective.team == "ally"]
    assert ally[-1].normalized_progress_amount == 4
    assert ally[-1].progress_state_from_previous == "progress"
    assert obj.possession.pre_death.quality == "observed"
    assert obj.possession.at_anchor.quality == "held"
    assert obj.possession.at_anchor.state_established_video_time == 9.5
    assert obj.possession.at_anchor.evidence == possession
    state = obj.checkpoint.at_anchor.state
    assert state.ally == "cleared" and state.opponent == "unknown"
    assert state.ally_evidence.samples[0].reading.language == "ja"
    assert state.ally_evidence.samples[0].evidence_id == "breakthrough"
    assert ctx.score is ctx.zone_control is ctx.objective_progress is None


@pytest.mark.parametrize("mode", ["tower_control", "rainmaker"])
def test_latest_unknown_is_not_replaced_by_older_known_or_future_state(mode: str) -> None:
    known = _snap(
        9.5,
        tower_control_state="ally_control",
        tower_control_evidence=_tower(),
        rainmaker_possession_state="ally_possession",
        rainmaker_possession_evidence=_possession(),
    )
    ctx = _context(mode, [known.model_copy(update={"timestamp": 10.5}), known, _snap(10)])
    series = (
        ctx.objective.control if mode == "tower_control" else ctx.objective.possession
    )
    assert series.at_anchor.state == "unknown"
    assert series.at_anchor.evidence is None
    assert series.at_anchor.quality == "unknown"


@pytest.mark.parametrize("mode", ["tower_control", "rainmaker"])
def test_gap_and_out_of_match_do_not_create_state_samples(mode: str) -> None:
    ctx = _context(mode, [_snap(7)])
    series = (
        ctx.objective.control if mode == "tower_control" else ctx.objective.possession
    )
    assert series.at_anchor is None
    ctx = _context(
        mode, [_snap(9.5), _snap(10).model_copy(update={"match_phase": "post_match"})]
    )
    series = (
        ctx.objective.control if mode == "tower_control" else ctx.objective.possession
    )
    assert series.at_anchor is None


def test_counts_alone_cannot_establish_possession_or_checkpoint() -> None:
    ctx = _context(
        "rainmaker",
        [
            _snap(
                t,
                rainmaker_ally_remaining=value,
                rainmaker_score_evidence_ids=[str(t)],
            )
            for t, value in [(9.5, 72), (10, 68)]
        ],
    )
    assert ctx.objective.possession.at_anchor.state == "unknown"
    assert ctx.objective.checkpoint.at_anchor.state == RainmakerCheckpointState()
    assert (
        derive_objective_temporal_facts(ctx, roster_evidence=True, roster_min_gap=2)
        is None
    )


@pytest.mark.parametrize("mode", ["splat_zones", "tower_control", "rainmaker"])
def test_union_round_trip_and_llm_projection_are_unchanged(mode: str) -> None:
    ctx = _context(
        mode,
        [
            _snap(
                10,
                ally_remaining=68,
                ally_score_quality="observed",
                rainmaker_ally_remaining=68,
                rainmaker_score_evidence_ids=["count"],
                rainmaker_possession_state="ally_possession",
                rainmaker_possession_evidence=_possession(),
                rainmaker_checkpoint_state=_checkpoint(),
                tower_control_state="ally_control",
                tower_control_evidence=_tower(),
            )
        ],
    )
    payload = scenario_context.serialize_scenario_contexts([ctx])[0]
    restored = ScenarioContext.model_validate(payload)
    assert restored == ctx and type(restored.objective) is type(ctx.objective)
    unit_input = CoachInput(primary_scenario=SCENARIO, primary_context=ctx)
    legacy_input = unit_input.model_copy(
        update={
            "primary_context": ctx.model_copy(update={"objective": None}),
        }
    )
    unit = score_death_candidate(unit_input, thresholds=DeathFactorThresholds())
    baseline_unit = score_death_candidate(
        legacy_input, thresholds=DeathFactorThresholds()
    )
    assert unit == baseline_unit
    assert unit_input.model_dump(mode="json") == legacy_input.model_dump(mode="json")
    assert serialize_coach_input_user_prompt(
        unit_input
    ) == serialize_coach_input_user_prompt(legacy_input)
    assert build_coach_llm_view(unit_input, unit) == build_coach_llm_view(
        legacy_input, baseline_unit
    )


def test_unknown_mode_and_old_artifacts_are_supported() -> None:
    assert _context(None, [_snap(10)]).objective is None
    assert _context("turf_war", [_snap(10)]).objective is None
    assert ScenarioContext.model_validate({"scenario_id": "old"}).objective is None
    with pytest.raises(ValidationError):
        ScenarioContext.model_validate(
            {"scenario_id": "bad", "objective": {"mode": "other"}}
        )
    with pytest.raises(ValidationError):
        TowerControlObjectiveContext(control={}, progress={"observations": []})


@pytest.mark.parametrize("mode", ["tower_control", "rainmaker"])
def test_supporting_group_frame_is_observed_without_changing_establishing_time(
    mode: str,
) -> None:
    tower, possession = _tower(), _possession()
    tower_group = tower.took_observation
    tower_group.samples.append(
        TowerTookSample(
            video_time=10,
            source_frame=_source(10),
            evidence_id="tower-repeat",
            cue=tower.cue,
        )
    )
    tower_group.last_video_time = 10
    rain_group = possession.cue_observation
    rain_group.samples.append(
        RainmakerPossessionSample(
            video_time=10,
            source_frame=_source(10),
            evidence_id="rain-repeat",
            cue=rain_group.samples[0].cue,
        )
    )
    rain_group.last_video_time = 10
    ctx = _context(
        mode,
        [
            _snap(
                10,
                tower_control_state="ally_control",
                tower_control_evidence=tower,
                rainmaker_possession_state="ally_possession",
                rainmaker_possession_evidence=possession,
            )
        ],
    )
    series = (
        ctx.objective.control if mode == "tower_control" else ctx.objective.possession
    )
    assert series.at_anchor.quality == "observed"
    assert series.at_anchor.state_established_video_time == 9.5
    assert series.at_anchor.sample_video_time == 10


def test_initial_neutral_and_unsupported_checkpoint_clear() -> None:
    evidence = RainmakerPossessionEvidence(
        state="neutral",
        video_time=9.5,
        source_frame=_source(9.5),
        initialization="observed_match_start",
    )
    ctx = _context(
        "rainmaker",
        [
            _snap(
                10,
                rainmaker_possession_state="neutral",
                rainmaker_possession_evidence=evidence,
                rainmaker_checkpoint_state=RainmakerCheckpointState(ally="cleared"),
            )
        ],
    )
    assert ctx.objective.possession.at_anchor.state == "neutral"
    assert ctx.objective.possession.at_anchor.quality == "held"
    assert (
        ctx.objective.possession.at_anchor.evidence.initialization
        == "observed_match_start"
    )
    assert ctx.objective.checkpoint.at_anchor.state.ally == "unknown"
