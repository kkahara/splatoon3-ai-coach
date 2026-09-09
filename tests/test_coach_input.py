"""CoachInput contract: one primary scenario unit; evidence only."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from splatoon3_ai_coach.analysis.scenario_context import build_scenario_contexts
from splatoon3_ai_coach.analysis.scenario_models import ScenarioType
from splatoon3_ai_coach.analysis.scenarios import build_scenarios, event_id
from splatoon3_ai_coach.coach.coach_input import (
    CoachInput,
    build_coach_input_for_scenario,
)
from splatoon3_ai_coach.coach.evidence_contract import claim_contains_prohibited_language
from splatoon3_ai_coach.coach.game_clock import GameClock, GameClockObservation, build_game_clock
from splatoon3_ai_coach.coach.player_count_clock import PlayerCountClock, build_player_count_clock
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    GameEvent,
    GameEventReason,
    GameEventType,
    GameStateSnapshot,
    TimerReading,
    VisionFrameResult,
)

_REPO = Path(__file__).resolve().parents[1]
_MANIFEST_180224 = _REPO / "analysis" / "2026-07-05 18-02-24" / "vision_manifest.json"

_JUDGMENT_KEYS = (
    "good_play",
    "bad_play",
    "overextended",
    "fight_quality",
    "should_have",
    "won_fight",
    "lost_fight",
)


def _cfg(**overrides: float) -> ScenarioBuilderConfig:
    return ScenarioBuilderConfig(**overrides)


def _event(
    timestamp: float,
    event_type: GameEventType,
    *,
    reason: GameEventReason | None = None,
    fingerprint: str | None = None,
) -> GameEvent:
    return GameEvent(
        start_time=timestamp,
        event_type=event_type,
        reason=reason,
        splat_fingerprint=fingerprint,
        confidence=1.0,
    )


def _death(timestamp: float) -> GameEvent:
    return _event(timestamp, GameEventType.DEATH, reason=GameEventReason.ALIVE_TO_DEAD)


def _respawn(timestamp: float) -> GameEvent:
    return _event(
        timestamp, GameEventType.RESPAWN, reason=GameEventReason.COUNTDOWN_PLATE_ENDED
    )


def _active(timestamp: float) -> GameEvent:
    return _event(
        timestamp,
        GameEventType.ACTIVE_AGAIN,
        reason=GameEventReason.AWAITING_CONTROL_TO_ALIVE,
    )


def _splat(timestamp: float, fingerprint: str) -> GameEvent:
    return _event(
        timestamp,
        GameEventType.SPLAT,
        reason=GameEventReason.SPLAT_INSTANCE_OPENED,
        fingerprint=fingerprint,
    )


def _frame(timestamp: float, seconds: float) -> VisionFrameResult:
    display = f"{int(seconds) // 60}:{int(seconds) % 60:02d}"
    return VisionFrameResult(
        frame_id=f"f{timestamp}",
        timestamp=timestamp,
        source="cadence",
        detections=[
            DetectorResult(
                id=f"timer:{timestamp}",
                detector_name="timer",
                detector_version="timer@test",
                confidence=0.9,
                reading=TimerReading(display=display, seconds_remaining=seconds),
            )
        ],
    )


def _unit(
    events: list[GameEvent],
    primary_type: ScenarioType,
    *,
    clock: GameClock | None = None,
    player_count_clock: PlayerCountClock | None = None,
    max_gap: float = 1.0,
    **cfg: float,
) -> CoachInput:
    config = _cfg(**cfg)
    scenarios = build_scenarios(events, config)
    contexts = build_scenario_contexts(events, scenarios, config)
    primary = next(s for s in scenarios if s.scenario_type is primary_type)
    return build_coach_input_for_scenario(
        primary.scenario_id,
        scenarios,
        contexts,
        clock or GameClock(),
        max_gap_seconds=max_gap,
        player_count_clock=player_count_clock,
        player_count_max_gap_seconds=max_gap,
    )


def test_death_episode_pulls_related_engagements() -> None:
    events = [
        _splat(150.0, "aa" * 8),
        _death(151.0),
        _respawn(158.0),
        _active(160.0),
        _splat(165.0, "bb" * 8),
    ]
    unit = _unit(
        events,
        ScenarioType.DEATH_EPISODE,
        engagement_include_following_death_seconds=2.0,
    )
    roles = {item.role for item in unit.related}
    assert "preceded_by_engagement" in roles
    assert "next_engagement" in roles
    assert unit.primary_scenario.scenario_type is ScenarioType.DEATH_EPISODE


def test_engagement_singleton_gets_not_complete_fight_limit() -> None:
    unit = _unit([_splat(10.0, "aa" * 8)], ScenarioType.ENGAGEMENT)
    codes = {item.code for item in unit.evidence_limits}
    assert "engagement_not_complete_fight" in codes
    statement = next(
        item.statement
        for item in unit.evidence_limits
        if item.code == "engagement_not_complete_fight"
    )
    assert "complete fight" in statement.lower() or "splat observation" in statement.lower()
    assert "won" not in statement.lower()
    assert claim_contains_prohibited_language(statement) is False


def test_leads_to_limit_is_association_never_causation() -> None:
    unit = _unit(
        [_splat(150.0, "aa" * 8), _death(151.0)],
        ScenarioType.ENGAGEMENT,
        engagement_include_following_death_seconds=2.0,
    )
    assert unit.primary_context.relations.leads_to_death_episode_id is not None
    limit = next(
        item
        for item in unit.evidence_limits
        if item.code == "leads_to_association_not_causation"
    )
    assert "associated" in limit.statement.lower() or "temporal" in limit.statement.lower()
    assert "caused" not in limit.statement.lower()
    assert claim_contains_prohibited_language(limit.statement) is False


def test_game_clock_sample_provenance_and_missing() -> None:
    events = [_death(87.0), _respawn(94.0), _active(96.0)]
    clock = build_game_clock([_frame(87.0, 153.0)], min_usable_confidence=0.5)
    unit = _unit(events, ScenarioType.DEATH_EPISODE, clock=clock, max_gap=1.0)
    by_label = {sample.label: sample for sample in unit.game_clock_samples}
    assert "death" in by_label
    death_sample = by_label["death"]
    assert death_sample.observation is not None
    assert death_sample.observation.source == "timer_detection"
    assert death_sample.observation.seconds_remaining == 153
    assert death_sample.gap_seconds == pytest.approx(0.0)

    # No timer near active_again → missing observation + evidence limit.
    active = by_label["active_again"]
    assert active.observation is None
    assert any(
        item.code == "game_clock_missing" and "96.0" in item.statement
        for item in unit.evidence_limits
    )


def test_coach_input_has_no_judgment_fields() -> None:
    unit = _unit([_splat(10.0, "aa" * 8)], ScenarioType.ENGAGEMENT)
    dumped = unit.model_dump(mode="json")
    blob = str(dumped).lower()
    for key in _JUDGMENT_KEYS:
        assert key not in dumped
        assert key not in CoachInput.model_fields
    assert "good_play" not in blob
    assert "bad_play" not in blob


def test_building_coach_input_does_not_change_scenario_membership() -> None:
    events = [
        _splat(150.0, "aa" * 8),
        _death(151.0),
        _respawn(158.0),
        _active(160.0),
    ]
    config = _cfg(engagement_include_following_death_seconds=2.0)
    scenarios = build_scenarios(events, config)
    contexts = build_scenario_contexts(events, scenarios, config)
    before = [item.model_dump(mode="json") for item in scenarios]
    owners_before = Counter(eid for s in scenarios for eid in s.event_ids)

    primary = next(s for s in scenarios if s.scenario_type is ScenarioType.DEATH_EPISODE)
    build_coach_input_for_scenario(
        primary.scenario_id,
        scenarios,
        contexts,
        GameClock(
            observations=(
                GameClockObservation(
                    video_time=151.0,
                    seconds_remaining=200,
                    confidence=0.9,
                    display="3:20",
                ),
            )
        ),
        max_gap_seconds=1.0,
    )

    after = [item.model_dump(mode="json") for item in scenarios]
    assert before == after
    owners_after = Counter(eid for s in scenarios for eid in s.event_ids)
    assert owners_before == owners_after
    assert set(owners_after) == {event_id(item) for item in events}
    assert max(owners_after.values()) == 1


@pytest.mark.skipif(not _MANIFEST_180224.is_file(), reason="18-02-24 manifest missing")
def test_180224_coach_input_freeze_regression() -> None:
    from splatoon3_ai_coach.vision.models import VisionManifest

    config = load_config(default_config_path())
    manifest = VisionManifest.model_validate_json(_MANIFEST_180224.read_text())
    events = list(manifest.game_events)
    scenarios = build_scenarios(events, config.scenarios)
    contexts = build_scenario_contexts(events, scenarios, config.scenarios)
    clock = build_game_clock(
        list(manifest.frame_results),
        min_usable_confidence=config.vision.timer.min_usable_confidence,
    )
    before_types = Counter(s.scenario_type.value for s in scenarios)
    for scenario in scenarios:
        unit = build_coach_input_for_scenario(
            scenario.scenario_id,
            scenarios,
            contexts,
            clock,
            max_gap_seconds=config.coach.game_clock_max_lookup_gap_seconds,
        )
        assert unit.primary_scenario.scenario_id == scenario.scenario_id
        for limit in unit.evidence_limits:
            assert claim_contains_prohibited_language(limit.statement) is False
            assert "caused" not in limit.statement.lower() or "not" in limit.statement.lower()

    after = build_scenarios(events, config.scenarios)
    assert Counter(s.scenario_type.value for s in after) == before_types
    assert before_types["death_episode"] == 2
    assert before_types["engagement"] == 3


def test_player_count_samples_align_with_game_clock_labels() -> None:
    events = [_death(87.0), _respawn(94.0), _active(96.0)]
    pc_clock = build_player_count_clock(
        [
            GameStateSnapshot(
                timestamp=87.0,
                ally_alive_count=3,
                opponent_alive_count=4,
                player_count_confidence=0.91,
            )
        ]
    )
    unit = _unit(
        events,
        ScenarioType.DEATH_EPISODE,
        player_count_clock=pc_clock,
        max_gap=1.0,
    )
    clock_labels = [s.label for s in unit.game_clock_samples]
    pc_labels = [s.label for s in unit.player_count_samples]
    assert clock_labels == pc_labels
    death = next(s for s in unit.player_count_samples if s.label == "death")
    assert death.observation is not None
    assert death.observation.ally_alive_count == 3
    assert death.observation.opponent_alive_count == 4
    assert death.observation.source == "player_count_fusion"
    assert any(
        item.code == "player_count_missing" and "96.0" in item.statement
        for item in unit.evidence_limits
    )


def test_player_count_missing_when_clock_empty() -> None:
    unit = _unit([_death(10.0), _respawn(17.0), _active(19.0)], ScenarioType.DEATH_EPISODE)
    assert unit.player_count_samples
    assert all(sample.observation is None for sample in unit.player_count_samples)
    assert any(item.code == "player_count_missing" for item in unit.evidence_limits)


def test_scenario_context_schema_unchanged_by_player_count() -> None:
    from splatoon3_ai_coach.analysis.scenario_context import ScenarioContext

    assert "ally_alive_count" not in ScenarioContext.model_fields
    assert "opponent_alive_count" not in ScenarioContext.model_fields
    assert "player_count_samples" not in ScenarioContext.model_fields
    assert "player_count_window" not in ScenarioContext.model_fields
    assert "player_count_context" not in ScenarioContext.model_fields


def _pc_snaps(*rows: tuple[float, int, int]) -> list[GameStateSnapshot]:
    return [
        GameStateSnapshot(
            timestamp=t,
            ally_alive_count=ally,
            opponent_alive_count=opp,
            player_count_confidence=0.9,
        )
        for t, ally, opp in rows
    ]


def test_player_count_window_around_death_anchor() -> None:
    events = [_death(48.0), _respawn(55.0), _active(57.0)]
    pc_clock = build_player_count_clock(
        _pc_snaps(
            (40.0, 4, 4),
            (43.0, 3, 4),
            (46.0, 3, 4),
            (48.0, 2, 4),
            (51.0, 2, 3),
            (54.0, 3, 3),
        )
    )
    unit = _unit(
        events,
        ScenarioType.DEATH_EPISODE,
        player_count_clock=pc_clock,
        max_gap=1.0,
    )
    assert len(unit.player_count_window) == 5
    assert [p.offset_seconds for p in unit.player_count_window] == [
        -5.0,
        -2.0,
        0.0,
        3.0,
        6.0,
    ]
    assert unit.player_count_context is not None
    assert unit.player_count_context.anchor_video_time == pytest.approx(48.0)
    assert unit.player_count_context.state_at_anchor == "2v4"
    assert unit.player_count_context.numbers_state_at_anchor == "disadvantage"
    assert unit.player_count_samples  # labeled samples still present


def test_present_by_sparse_vs_dense_continuity_bound() -> None:
    """present_by ≠ continuous disadvantaged time; gaps end the contiguous run.

    Sparse (holes > max_gap near the anchor):
      40 4v4, 41–42 3v4, 44 3v4, 48 3v4 → present_by=48, duration=0
      (44→48 gap of 4s breaks continuity; do not claim 7s from 41.)

    Dense (all consecutive gaps ≤ max_gap):
      40 4v4, 41…48 3v4 at 1s → present_by=41, duration=7
      Even then, duration_since_present_by must be cited as
      “observed by 41.0, 7.0s before the anchor,” not continuous time down.
    """
    events = [_death(48.0), _respawn(55.0), _active(57.0)]
    sparse = _unit(
        events,
        ScenarioType.DEATH_EPISODE,
        player_count_clock=build_player_count_clock(
            _pc_snaps(
                (40.0, 4, 4),
                (41.0, 3, 4),
                (42.0, 3, 4),
                (44.0, 3, 4),
                (48.0, 3, 4),
            )
        ),
        max_gap=1.0,
    )
    dense = _unit(
        events,
        ScenarioType.DEATH_EPISODE,
        player_count_clock=build_player_count_clock(
            _pc_snaps(
                (40.0, 4, 4),
                (41.0, 3, 4),
                (42.0, 3, 4),
                (43.0, 3, 4),
                (44.0, 3, 4),
                (45.0, 3, 4),
                (46.0, 3, 4),
                (47.0, 3, 4),
                (48.0, 3, 4),
            )
        ),
        max_gap=1.0,
    )
    assert sparse.player_count_context is not None
    assert dense.player_count_context is not None
    assert sparse.player_count_context.state_present_by == pytest.approx(48.0)
    assert sparse.player_count_context.duration_since_present_by == pytest.approx(0.0)
    assert dense.player_count_context.state_present_by == pytest.approx(41.0)
    assert dense.player_count_context.duration_since_present_by == pytest.approx(7.0)

    limit = next(
        item
        for item in dense.evidence_limits
        if item.code == "player_count_not_causal"
    )
    lower = limit.statement.lower()
    assert "observed by" in lower
    assert "continuously" in lower
    assert "disadvantaged for that entire interval" in lower
    # Field name remains duration_since_present_by; wording must not overclaim.
    assert dense.player_count_context.duration_since_present_by == 7.0
    assert "continuously disadvantaged for 7" not in lower


def test_present_by_disadvantage_half_second_before_anchor() -> None:
    """Dense cadence: disadvantage observed 0.5s before death."""
    events = [_death(48.0), _respawn(55.0), _active(57.0)]
    unit = _unit(
        events,
        ScenarioType.DEATH_EPISODE,
        player_count_clock=build_player_count_clock(
            _pc_snaps((47.0, 4, 4), (47.5, 3, 4), (48.0, 3, 4))
        ),
        max_gap=1.0,
    )
    ctx = unit.player_count_context
    assert ctx is not None
    assert ctx.numbers_state_at_anchor == "disadvantage"
    assert ctx.state_at_anchor == "3v4"
    assert ctx.state_present_by == pytest.approx(47.5)
    assert ctx.duration_since_present_by == pytest.approx(0.5)


def test_present_by_disadvantage_five_seconds_before_anchor() -> None:
    """Dense cadence: disadvantage observed for ~5s before death."""
    events = [_death(48.0), _respawn(55.0), _active(57.0)]
    unit = _unit(
        events,
        ScenarioType.DEATH_EPISODE,
        player_count_clock=build_player_count_clock(
            _pc_snaps(
                (42.5, 4, 4),
                (43.0, 3, 4),
                (44.0, 3, 4),
                (45.0, 3, 4),
                (46.0, 3, 4),
                (47.0, 3, 4),
                (48.0, 3, 4),
            )
        ),
        max_gap=1.0,
    )
    ctx = unit.player_count_context
    assert ctx is not None
    assert ctx.state_present_by == pytest.approx(43.0)
    assert ctx.duration_since_present_by == pytest.approx(5.0)


def test_present_by_does_not_bridge_large_observation_gap() -> None:
    """Same AvB on both sides of a hole must not invent continuous duration."""
    events = [_death(48.0), _respawn(55.0), _active(57.0)]
    unit = _unit(
        events,
        ScenarioType.DEATH_EPISODE,
        player_count_clock=build_player_count_clock(
            _pc_snaps((43.0, 3, 4), (48.0, 3, 4))  # 5s hole > max_gap 1.0
        ),
        max_gap=1.0,
    )
    ctx = unit.player_count_context
    assert ctx is not None
    assert ctx.state_at_anchor == "3v4"
    assert ctx.state_present_by == pytest.approx(48.0)
    assert ctx.duration_since_present_by == pytest.approx(0.0)
    # Hole prevents stitching earlier 3v4 into a multi-second duration.
    assert ctx.valid_point_count == 2
    assert any(item.code == "player_count_not_causal" for item in unit.evidence_limits)


def test_present_by_even_to_disadvantage_transition() -> None:
    events = [_death(48.0), _respawn(55.0), _active(57.0)]
    unit = _unit(
        events,
        ScenarioType.DEATH_EPISODE,
        player_count_clock=build_player_count_clock(
            _pc_snaps(
                (43.0, 4, 4),
                (43.5, 4, 4),
                (44.0, 3, 4),
                (45.0, 3, 4),
                (46.0, 3, 4),
                (47.0, 3, 4),
                (48.0, 3, 4),
            )
        ),
        max_gap=1.0,
    )
    ctx = unit.player_count_context
    assert ctx is not None
    assert ctx.numbers_state_at_anchor == "disadvantage"
    assert ctx.state_present_by == pytest.approx(44.0)
    assert ctx.duration_since_present_by == pytest.approx(4.0)
    traj = [(p.ally_alive_count, p.opponent_alive_count) for p in ctx.trajectory]
    assert traj[0] == (4, 4)
    assert (3, 4) in traj


def test_present_by_keeps_disadvantage_across_worsening_avb() -> None:
    """3v4 → 2v4 is still disadvantage; present_by tracks numbers_state, not AvB."""
    events = [_death(48.0), _respawn(55.0), _active(57.0)]
    unit = _unit(
        events,
        ScenarioType.DEATH_EPISODE,
        player_count_clock=build_player_count_clock(
            _pc_snaps(
                (43.0, 3, 4),
                (44.0, 3, 4),
                (45.0, 3, 4),
                (46.0, 2, 4),
                (47.0, 2, 4),
                (48.0, 2, 4),
            )
        ),
        max_gap=1.0,
    )
    ctx = unit.player_count_context
    assert ctx is not None
    assert ctx.state_at_anchor == "2v4"
    assert ctx.numbers_state_at_anchor == "disadvantage"
    assert ctx.state_present_by == pytest.approx(43.0)
    assert ctx.duration_since_present_by == pytest.approx(5.0)
    traj = [(p.ally_alive_count, p.opponent_alive_count, p.numbers_state) for p in ctx.trajectory]
    assert (3, 4, "disadvantage") in traj
    assert (2, 4, "disadvantage") in traj


def test_coach_input_composition_leaves_scenario_untouched() -> None:
    events = [_death(48.0), _respawn(55.0), _active(57.0)]
    config = _cfg()
    scenarios = build_scenarios(events, config)
    contexts = build_scenario_contexts(events, scenarios, config)
    before_scenarios = [s.model_dump(mode="json") for s in scenarios]
    before_contexts = [c.model_dump(mode="json") for c in contexts]
    pc_clock = build_player_count_clock(_pc_snaps((43.0, 4, 4), (48.0, 3, 4)))
    primary = next(s for s in scenarios if s.scenario_type is ScenarioType.DEATH_EPISODE)
    unit = build_coach_input_for_scenario(
        primary.scenario_id,
        scenarios,
        contexts,
        GameClock(),
        max_gap_seconds=1.0,
        player_count_clock=pc_clock,
        player_count_max_gap_seconds=1.0,
    )
    assert [s.model_dump(mode="json") for s in scenarios] == before_scenarios
    assert [c.model_dump(mode="json") for c in contexts] == before_contexts
    assert unit.player_count_window
    assert unit.player_count_context is not None
    assert "ally_alive_count" not in before_contexts[0]


def test_roster_transition_does_not_emit_game_event() -> None:
    """Count changes stay on CoachInput; scenario event_ids stay death lifecycle only."""
    events = [_death(48.0), _respawn(55.0), _active(57.0)]
    pc_clock = build_player_count_clock(
        _pc_snaps((43.0, 4, 4), (44.0, 3, 4), (48.0, 3, 4))
    )
    unit = _unit(
        events,
        ScenarioType.DEATH_EPISODE,
        player_count_clock=pc_clock,
        max_gap=1.0,
    )
    member_types = {
        eid.split(":")[0] for eid in unit.primary_scenario.event_ids
    }
    assert "death" in member_types
    assert "ally_death" not in member_types
    assert "player_count" not in member_types
    assert unit.player_count_context is not None
    assert unit.player_count_context.trajectory
    traj_states = [
        (p.ally_alive_count, p.opponent_alive_count) for p in unit.player_count_context.trajectory
    ]
    assert (4, 4) in traj_states
    assert (3, 4) in traj_states
    # No GameEvent invented from the 4v4→3v4 roster change.
    from splatoon3_ai_coach.vision.models import GameEventType

    assert not any(
        getattr(GameEventType, name, None) is not None and "PLAYER_COUNT" in name
        for name in dir(GameEventType)
    )
    blob = str(unit.model_dump(mode="json")).lower()
    assert "ally_death" not in blob
    assert "caused" not in blob or "not" in blob
