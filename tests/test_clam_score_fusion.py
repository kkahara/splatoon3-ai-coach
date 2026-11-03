"""Clam score association, provenance, persistence, and objective isolation."""

import cv2
import pytest

from splatoon3_ai_coach.analysis.clam_objective_progress import (
    clam_objective_observations,
)
from splatoon3_ai_coach.analysis.objective_context import ClamBlitzObjectiveContext
from splatoon3_ai_coach.analysis.scenario_context import (
    ScenarioContext,
    build_scenario_context,
    serialize_scenario_contexts,
)
from splatoon3_ai_coach.analysis.scenario_evidence import ScenarioEvidencePack
from splatoon3_ai_coach.analysis.scenario_models import Scenario
from splatoon3_ai_coach.coach.coach import serialize_coach_input_user_prompt
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig, ScoreDetectorConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT, default_config_path
from splatoon3_ai_coach.vision.clam_score_fusion import fuse_clam_score
from splatoon3_ai_coach.vision.models import (
    AnalysisIdentity,
    DetectorResult,
    GameEvent,
    GameStateSnapshot,
    MatchIntroReading,
    ScoreReading,
    ScoreSideReading,
    TimerReading,
    VisionFrameResult,
    VisionManifest,
)
from splatoon3_ai_coach.vision.pipeline import refuse_vision_manifest
from splatoon3_ai_coach.vision.score import ClamScoreDetector
from splatoon3_ai_coach.vision.state import fuse_game_state


def _side(value: int | None) -> ScoreSideReading:
    return ScoreSideReading(
        value=value,
        visible=value is not None,
        digit_scores=[0.9] * len(str(value)) if value is not None else [],
    )


def _reading(ally: int | None, opponent: int | None, **kwargs) -> ScoreReading:
    return ScoreReading(
        battle_mode_id="clam_blitz",
        left=_side(ally),
        right=_side(opponent),
        **kwargs,
    )


def _frame(t: float, reading: ScoreReading | None) -> VisionFrameResult:
    detections = (
        []
        if reading is None
        else [
            DetectorResult(
                id=f"clam-{t}",
                detector_name="clam_score",
                detector_version="test",
                confidence=0.9,
                reading=reading,
            )
        ]
    )
    return VisionFrameResult(
        frame_id=f"frame-{t}",
        timestamp=t,
        source="cadence",
        source_frame_index=int(t * 60),
        detections=detections,
    )


def test_direct_mapping_penalty_and_full_native_range() -> None:
    config = ScoreDetectorConfig()
    reading = _reading(
        100,
        0,
        left_penalty=16,
        left_penalty_scores=[0.9, 0.8],
        penalty_evaluated=True,
    )
    state = fuse_clam_score(_frame(1, reading), "clam_blitz", "in_match", False, config)
    assert (state.ally_remaining, state.opponent_remaining) == (100, 0)
    assert (state.ally_penalty, state.opponent_penalty) == (16, None)
    assert state.association_basis == "fixed_hud_sides" and state.evidence_id == "clam-1"


def test_unreadable_side_and_penalty_never_become_zero() -> None:
    config = ScoreDetectorConfig()
    reading = _reading(72, None, right_penalty=10, right_penalty_scores=[0.9, 0.2])
    state = fuse_clam_score(_frame(1, reading), "clam_blitz", "in_match", False, config)
    assert state.ally_remaining == 72 and state.opponent_remaining is None
    assert state.ally_penalty is None and state.opponent_penalty is None
    reading.left.digit_scores = []
    assert (
        fuse_clam_score(_frame(1, reading), "clam_blitz", "in_match", False, config)
        is None
    )


def test_visible_penalty_can_be_observed_without_a_main_count() -> None:
    reading = _reading(None, None, right_penalty=19, right_penalty_scores=[0.9, 0.8])
    frame = _frame(1, reading)
    frame.detections[0].confidence = 0  # Overall confidence describes main counters.
    state = fuse_clam_score(frame, "clam_blitz", "in_match", False, ScoreDetectorConfig())
    assert state.opponent_remaining is None and state.opponent_penalty == 19


@pytest.mark.parametrize(
    "mode,phase,overlay",
    [
        (None, "in_match", False),
        ("rainmaker", "in_match", False),
        ("clam_blitz", "post_match", False),
        ("clam_blitz", "in_match", True),
    ],
)
def test_mode_phase_overlay_gates(mode: str | None, phase: str, overlay: bool) -> None:
    assert (
        fuse_clam_score(
            _frame(1, _reading(72, 68)), mode, phase, overlay, ScoreDetectorConfig()
        )
        is None
    )


def test_only_unique_clam_result_with_valid_mode_and_provenance_is_accepted() -> None:
    frame = _frame(1, _reading(72, 68))
    frame.detections.append(frame.detections[0].model_copy(update={"id": "other"}))
    assert (
        fuse_clam_score(frame, "clam_blitz", "in_match", False, ScoreDetectorConfig())
        is None
    )
    frame.detections.pop()
    frame.detections[0].reading.battle_mode_id = "splat_zones"
    assert (
        fuse_clam_score(frame, "clam_blitz", "in_match", False, ScoreDetectorConfig())
        is None
    )


def test_fusion_progress_context_and_refusion_preserve_separate_legacy_fields() -> None:
    config = load_config(default_config_path())
    frames = [
        _frame(9, _reading(72, 100)),
        _frame(9.5, _reading(68, 100)),
        _frame(10, None),
    ]
    frames[0].detections.extend(
        [
            DetectorResult(
                id="intro",
                detector_name="match_intro",
                detector_version="test",
                confidence=1,
                reading=MatchIntroReading(
                    battle_mode_id="clam_blitz", battle_mode_template_score=1
                ),
            ),
            DetectorResult(
                id="timer",
                detector_name="timer",
                detector_version="test",
                confidence=1,
                reading=TimerReading(display="2:50", seconds_remaining=170),
            ),
        ]
    )
    snapshots = fuse_game_state(frames, config.vision.timer, config.vision.state_fusion)
    assert [s.clam_score.ally_remaining if s.clam_score else None for s in snapshots] == [
        72,
        68,
        None,
    ]
    assert all(
        s.ally_remaining is None and s.opponent_remaining is None for s in snapshots
    )
    assert snapshots[1].source_frame.frame_id == "frame-9.5"
    assert snapshots[1].clam_score.evidence_id == "clam-9.5"
    manifest = VisionManifest(
        analysis=AnalysisIdentity(
            analysis_id="test",
            package_version="test",
            vision_config_sha256="test",
            video_identity="video",
            language="en",
        ),
        video_identity="video",
        frame_results=frames,
        state_snapshots=snapshots,
    )
    restored = VisionManifest.model_validate_json(manifest.model_dump_json())
    assert restored.state_snapshots[1].clam_score == snapshots[1].clam_score
    refused = refuse_vision_manifest(restored, config)
    assert refused.state_snapshots[1].clam_score == snapshots[1].clam_score

    scenario = Scenario(
        scenario_id="death:9.5",
        scenario_type="death_episode",
        start_time=9.5,
        end_time=10,
        confidence=1,
        outcome="incomplete",
    )
    event = GameEvent(start_time=9.5, event_type="death", confidence=1)
    context = build_scenario_context(
        [event],
        scenario,
        ScenarioBuilderConfig(),
        evidence=ScenarioEvidencePack(
            state_snapshots=snapshots, battle_mode_id="clam_blitz"
        ),
    )
    assert isinstance(context.objective, ClamBlitzObjectiveContext)
    assert context.objective.score.at_anchor.score.ally_remaining == 68
    assert (
        context.objective.progress.observations[2].objective.normalized_progress_amount
        == 4
    )
    assert context.objective.progress.observations[
        2
    ].objective.objective_evidence_ids == ["clam-9.5"]
    assert context.score is context.zone_control is context.objective_progress is None
    payload = serialize_scenario_contexts([context])[0]
    assert ScenarioContext.model_validate(payload).objective == context.objective
    inp = CoachInput(primary_scenario=scenario, primary_context=context)
    without = inp.model_copy(
        update={"primary_context": context.model_copy(update={"objective": None})}
    )
    assert serialize_coach_input_user_prompt(inp) == serialize_coach_input_user_prompt(
        without
    )


def test_progress_keeps_gaps_increases_and_match_reset_unknown() -> None:
    config = ScoreDetectorConfig()
    snapshots = []
    for t, value, phase in [
        (1, 100, "in_match"),
        (2, 80, "in_match"),
        (4, 70, "in_match"),
        (5, 75, "in_match"),
        (6, None, "post_match"),
        (7, 70, "in_match"),
    ]:
        score = fuse_clam_score(
            _frame(t, _reading(value, None)), "clam_blitz", phase, False, config
        )
        snapshots.append(
            GameStateSnapshot(timestamp=t, match_phase=phase, clam_score=score)
        )
    observations = clam_objective_observations(snapshots, max_gap_seconds=1)
    assert [o.normalized_progress_amount for o in observations] == [
        None,
        20,
        None,
        None,
        None,
    ]
    assert [o.progress_state_from_previous for o in observations] == [
        "unknown",
        "progress",
        "unknown",
        "unknown",
        "unknown",
    ]


def test_full_count_range_and_basket_do_not_change_progress() -> None:
    config = ScoreDetectorConfig()
    snapshots = [
        GameStateSnapshot(
            timestamp=t,
            match_phase="in_match",
            basket_state=basket,
            clam_score=fuse_clam_score(
                _frame(t, _reading(value, None)), "clam_blitz", "in_match", False, config
            ),
        )
        for t, value, basket in [(1, 100, "unknown"), (2, 0, "closed")]
    ]
    observations = clam_objective_observations(snapshots, max_gap_seconds=1)
    assert observations[-1].original_value == 0
    assert observations[-1].normalized_progress_amount == 100
    assert observations[-1].progress_state_from_previous == "progress"
    changed = [s.model_copy(update={"basket_state": "open"}) for s in snapshots]
    assert clam_objective_observations(changed, max_gap_seconds=1) == observations


@pytest.mark.parametrize(
    "name,expected",
    [
        ("en_humpback/t00169.0.png", (68, 68, 16, None)),
        ("ja_hammerhead/t00303.5.png", (80, 32, None, 19)),
    ],
)
def test_development_frames_read_main_counts_and_visible_penalties(
    name, expected
) -> None:
    path = PROJECT_ROOT / "analysis/clam_blitz_score_survey/samples" / name
    if not path.exists():
        pytest.skip("Local development frames unavailable")
    config = load_config(default_config_path()).vision.score.model_copy(
        update={"battle_mode_id": "clam_blitz"}
    )
    reading, _ = ClamScoreDetector(config).detect(cv2.imread(str(path)))
    score = fuse_clam_score(_frame(1, reading), "clam_blitz", "in_match", False, config)
    assert (
        score.ally_remaining,
        score.opponent_remaining,
        score.ally_penalty,
        score.opponent_penalty,
    ) == expected
