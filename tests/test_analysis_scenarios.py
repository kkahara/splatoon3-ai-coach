"""ScenarioBuilder: GameEvent grouping only. No detector or coaching text."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from splatoon3_ai_coach.analysis.pipeline import (
    SCENARIO_CONTEXTS_JSON_FILENAME,
    SCENARIOS_JSON_FILENAME,
    SCENARIOS_TXT_FILENAME,
    write_scenarios,
)
from splatoon3_ai_coach.analysis.scenario_models import ScenarioOutcome, ScenarioType
from splatoon3_ai_coach.analysis.scenarios import (
    build_scenarios,
    event_id,
    format_scenario_timeline,
)
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig, ScenarioBuilderConfig
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.models import (
    GameEvent,
    GameEventReason,
    GameEventType,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.state import fuse_game_state

_REPO = Path(__file__).resolve().parents[1]
_SLICE_140214 = (
    _REPO
    / "tests"
    / "fixtures"
    / "vision_frames"
    / "2026-09-07_14-02-14_190-227.json"
)
_SCENARIOS_SRC = (
    _REPO / "src" / "splatoon3_ai_coach" / "analysis" / "scenarios.py"
)


def _cfg(**overrides: float) -> ScenarioBuilderConfig:
    return ScenarioBuilderConfig(**overrides)


def _event(
    timestamp: float,
    event_type: GameEventType,
    *,
    reason: GameEventReason | None = None,
    fingerprint: str | None = None,
    end_time: float | None = None,
    confidence: float = 1.0,
) -> GameEvent:
    return GameEvent(
        start_time=timestamp,
        end_time=end_time,
        event_type=event_type,
        reason=reason,
        splat_fingerprint=fingerprint,
        confidence=confidence,
    )


def _death(timestamp: float) -> GameEvent:
    return _event(timestamp, GameEventType.DEATH, reason=GameEventReason.ALIVE_TO_DEAD)


def _respawn(timestamp: float, *, skip: bool = False) -> GameEvent:
    reason = (
        GameEventReason.SKIP_COUNTDOWN_CONTROL
        if skip
        else GameEventReason.COUNTDOWN_PLATE_ENDED
    )
    return _event(timestamp, GameEventType.RESPAWN, reason=reason)


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


def _map(start: float, end: float | None = None) -> GameEvent:
    return _event(
        start,
        GameEventType.MAP_OVERLAY,
        reason=GameEventReason.MAP_OVERLAY_PRESENT,
        end_time=end,
    )


def _of_type(scenarios, scenario_type: ScenarioType):
    return [item for item in scenarios if item.scenario_type is scenario_type]


def test_scenarios_module_does_not_import_pipeline_or_detectors() -> None:
    tree = ast.parse(_SCENARIOS_SRC.read_text(encoding="utf-8"))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "splatoon3_ai_coach.vision.models" in modules
    forbidden = {
        "splatoon3_ai_coach.vision.events",
        "splatoon3_ai_coach.vision.state",
        "splatoon3_ai_coach.vision.death",
        "splatoon3_ai_coach.vision.respawn",
        "splatoon3_ai_coach.vision.splat",
        "splatoon3_ai_coach.vision.active_gameplay",
        "splatoon3_ai_coach.vision.map_overlay",
        "splatoon3_ai_coach.vision.pipeline",
    }
    assert modules.isdisjoint(forbidden)


def test_event_id_is_derived_and_distinguishes_fingerprints() -> None:
    first = _splat(1.0, "aa" * 8)
    second = _splat(1.0, "bb" * 8)
    assert event_id(first) != event_id(second)
    assert event_id(first) == "splat:1.000:splat_instance_opened:" + "aa" * 8


def test_post_death_recovery_starts_and_closes() -> None:
    events = [_death(10.0), _respawn(12.0), _active(13.0), _splat(14.0, "aa" * 8)]
    scenarios = build_scenarios(events, _cfg(post_death_follow_seconds=8.0))
    recoveries = _of_type(scenarios, ScenarioType.POST_DEATH_RECOVERY)
    assert len(recoveries) == 1
    recovery = recoveries[0]
    assert recovery.start_time == 10.0
    assert recovery.end_time == 14.0
    assert recovery.outcome is ScenarioOutcome.RECOVERED
    assert recovery.context["has_respawn"] is True
    assert recovery.context["has_active_again"] is True
    assert recovery.context["respawn_reason"] == "countdown_plate_ended"
    assert event_id(events[0]) in recovery.event_ids
    assert event_id(events[3]) in recovery.event_ids


def test_death_at_end_of_video_is_incomplete() -> None:
    events = [_death(100.0)]
    scenarios = build_scenarios(events, _cfg(post_death_max_seconds=30.0))
    recovery = _of_type(scenarios, ScenarioType.POST_DEATH_RECOVERY)[0]
    assert recovery.outcome is ScenarioOutcome.INCOMPLETE
    assert recovery.end_time == pytest.approx(130.0)
    assert recovery.event_ids == [event_id(events[0])]
    assert recovery.context["has_respawn"] is False
    assert recovery.context["has_active_again"] is False


def test_death_without_respawn_does_not_crash() -> None:
    events = [_death(5.0), _splat(20.0, "aa" * 8)]
    scenarios = build_scenarios(events, _cfg())
    recovery = _of_type(scenarios, ScenarioType.POST_DEATH_RECOVERY)[0]
    assert recovery.outcome is ScenarioOutcome.INCOMPLETE
    assert event_id(events[1]) not in recovery.event_ids


def test_death_with_respawn_but_no_active_again() -> None:
    events = [_death(5.0), _respawn(8.0, skip=True)]
    scenarios = build_scenarios(events, _cfg())
    recovery = _of_type(scenarios, ScenarioType.POST_DEATH_RECOVERY)[0]
    assert recovery.outcome is ScenarioOutcome.INCOMPLETE
    assert recovery.context["has_respawn"] is True
    assert recovery.context["has_active_again"] is False
    assert recovery.context["respawn_reason"] == "skip_countdown_control"
    assert event_id(events[1]) in recovery.event_ids


def test_multiple_death_episodes_stay_separate() -> None:
    events = [
        _death(10.0),
        _respawn(12.0),
        _active(13.0),
        _death(40.0),
        _respawn(42.0),
        _active(43.0),
    ]
    recoveries = _of_type(
        build_scenarios(events, _cfg()), ScenarioType.POST_DEATH_RECOVERY
    )
    assert [item.start_time for item in recoveries] == [10.0, 40.0]
    assert event_id(events[3]) not in recoveries[0].event_ids
    assert event_id(events[0]) not in recoveries[1].event_ids


def test_unrelated_later_splat_is_not_in_recovery() -> None:
    events = [_death(10.0), _respawn(12.0), _active(13.0), _splat(30.0, "aa" * 8)]
    recovery = _of_type(
        build_scenarios(events, _cfg(post_death_follow_seconds=8.0)),
        ScenarioType.POST_DEATH_RECOVERY,
    )[0]
    assert event_id(events[3]) not in recovery.event_ids


def test_map_check_during_and_outside_death_episode() -> None:
    events = [
        _map(1.0, 2.0),
        _death(10.0),
        _map(11.0, 12.0),
        _respawn(13.0),
        _active(14.0),
        _map(20.0, 21.0),
    ]
    maps = _of_type(build_scenarios(events, _cfg()), ScenarioType.MAP_CHECK)
    assert len(maps) == 3
    assert maps[0].context["in_death_episode"] is False
    assert maps[1].context["in_death_episode"] is True
    assert maps[2].context["in_death_episode"] is False
    assert maps[1].outcome is ScenarioOutcome.OBSERVED
    assert event_id(events[2]) in maps[1].event_ids
    recovery = _of_type(
        build_scenarios(events, _cfg()), ScenarioType.POST_DEATH_RECOVERY
    )[0]
    assert event_id(events[2]) in recovery.event_ids


def test_open_map_overlay_uses_start_as_end() -> None:
    events = [_map(5.0, None)]
    check = _of_type(build_scenarios(events, _cfg()), ScenarioType.MAP_CHECK)[0]
    assert check.end_time == 5.0


def test_same_timestamp_splat_fingerprints_stay_distinct_events() -> None:
    events = [_splat(8.0, "aa" * 8), _splat(8.0, "bb" * 8)]
    assert event_id(events[0]) != event_id(events[1])
    scenarios = build_scenarios(events, _cfg(engagement_gap_seconds=3.0))
    engagements = _of_type(scenarios, ScenarioType.ENGAGEMENT)
    assert len(engagements) == 1
    assert engagements[0].context["splat_count"] == 2
    assert event_id(events[0]) in engagements[0].event_ids
    assert event_id(events[1]) in engagements[0].event_ids


def test_splat_clusters_split_when_gap_exceeds_config() -> None:
    events = [_splat(1.0, "aa" * 8), _splat(5.0, "bb" * 8)]
    engagements = _of_type(
        build_scenarios(events, _cfg(engagement_gap_seconds=3.0)),
        ScenarioType.ENGAGEMENT,
    )
    assert len(engagements) == 2
    assert [item.start_time for item in engagements] == [1.0, 5.0]


def test_death_inside_and_outside_engagement_follow_window() -> None:
    inside = [_splat(10.0, "aa" * 8), _death(11.5)]
    outside = [_splat(20.0, "bb" * 8), _death(23.0)]
    config = _cfg(engagement_include_following_death_seconds=2.0)
    died = _of_type(build_scenarios(inside, config), ScenarioType.ENGAGEMENT)[0]
    fragged = _of_type(build_scenarios(outside, config), ScenarioType.ENGAGEMENT)[0]
    assert died.outcome is ScenarioOutcome.DIED
    assert event_id(inside[1]) in died.event_ids
    assert fragged.outcome is ScenarioOutcome.FRAGGED
    assert event_id(outside[1]) not in fragged.event_ids


def test_overlap_recovery_and_engagement_share_splat_id() -> None:
    events = [
        _death(10.0),
        _respawn(12.0),
        _active(13.0),
        _splat(14.0, "aa" * 8),
    ]
    scenarios = build_scenarios(events, _cfg(post_death_follow_seconds=8.0))
    recovery = _of_type(scenarios, ScenarioType.POST_DEATH_RECOVERY)[0]
    engagement = _of_type(scenarios, ScenarioType.ENGAGEMENT)[0]
    splat_key = event_id(events[3])
    assert splat_key in recovery.event_ids
    assert splat_key in engagement.event_ids
    keys = [(item.start_time, item.scenario_type.value, item.scenario_id) for item in scenarios]
    assert keys == sorted(keys)


def test_identical_input_is_deterministically_ordered() -> None:
    events = [
        _splat(8.0, "bb" * 8),
        _death(10.0),
        _map(11.0, 11.5),
        _splat(8.0, "aa" * 8),
        _respawn(12.0),
        _active(13.0),
    ]
    first = build_scenarios(events, _cfg())
    second = build_scenarios(list(reversed(events)), _cfg())
    assert [item.model_dump(mode="json") for item in first] == [
        item.model_dump(mode="json") for item in second
    ]
    assert [item.scenario_id for item in first] == [item.scenario_id for item in second]


def test_format_scenario_timeline_has_no_coaching_language() -> None:
    events = [_death(196.5), _respawn(204.0, skip=True), _active(205.5)]
    scenarios = build_scenarios(events, _cfg())
    text = format_scenario_timeline(events, scenarios)
    assert "03:16.5 — DEATH" in text
    assert "POST_DEATH_RECOVERY" in text
    assert "you should" not in text.lower()


def test_write_scenarios_persists_json_and_txt(tmp_path: Path) -> None:
    config = load_config(default_config_path())
    events = [_death(10.0), _respawn(12.0), _active(13.0)]
    written = write_scenarios(events, config, tmp_path)
    assert (tmp_path / SCENARIOS_JSON_FILENAME).exists()
    assert (tmp_path / SCENARIOS_TXT_FILENAME).exists()
    assert (tmp_path / SCENARIO_CONTEXTS_JSON_FILENAME).exists()
    payload = json.loads((tmp_path / SCENARIOS_JSON_FILENAME).read_text(encoding="utf-8"))
    assert len(payload) == len(written)
    assert payload[0]["scenario_type"] == "post_death_recovery"


def test_140214_fixture_failure_includes_generated_timeline() -> None:
    """Fixture assertions must dump the scenario timeline for video debugging."""
    config = load_config(default_config_path())
    events, scenarios = _events_and_scenarios_from_slice(config)
    timeline = format_scenario_timeline(events, scenarios)
    with pytest.raises(AssertionError) as exc:
        _assert_with_timeline(False, "forced mismatch", timeline)
    message = str(exc.value)
    assert "forced mismatch" in message
    assert "Generated scenario timeline:" in message
    assert timeline in message
    assert "POST_DEATH_RECOVERY" in message
    assert "03:16.5 — DEATH" in message


def test_140214_fixture_post_death_recoveries() -> None:
    """Water death@196.5 recovers before 216.5; second DEATH starts a new scenario.

    On failure the generated timeline is included so it can be compared to video.
    """
    config = load_config(default_config_path())
    events, scenarios = _events_and_scenarios_from_slice(config)
    timeline = format_scenario_timeline(events, scenarios)
    recoveries = _of_type(scenarios, ScenarioType.POST_DEATH_RECOVERY)

    _assert_with_timeline(
        len(recoveries) >= 2,
        f"expected two recoveries, got {len(recoveries)}",
        timeline,
    )
    first, second = recoveries[0], recoveries[1]
    _assert_with_timeline(
        first.start_time == pytest.approx(196.5, abs=0.26),
        f"first start {first.start_time}",
        timeline,
    )
    _assert_with_timeline(
        first.outcome is ScenarioOutcome.RECOVERED,
        f"first outcome {first.outcome}",
        timeline,
    )
    _assert_with_timeline(
        first.context.get("respawn_reason") == "skip_countdown_control",
        f"respawn_reason={first.context.get('respawn_reason')}",
        timeline,
    )
    _assert_with_timeline(
        first.context.get("has_active_again") is True,
        "missing ACTIVE_AGAIN on first",
        timeline,
    )
    active_ids = [eid for eid in first.event_ids if eid.startswith("active_again:")]
    _assert_with_timeline(
        bool(active_ids),
        "no ACTIVE_AGAIN event id on first recovery",
        timeline,
    )
    active_at = float(active_ids[0].split(":")[1])
    _assert_with_timeline(
        active_at < 216.5,
        f"ACTIVE_AGAIN@{active_at} not before 216.5",
        timeline,
    )
    _assert_with_timeline(
        second.start_time == pytest.approx(216.5, abs=0.26),
        f"second start {second.start_time}",
        timeline,
    )
    _assert_with_timeline(
        all(
            item.scenario_id == f"{item.scenario_type.value}:{item.start_time:.3f}"
            for item in scenarios
        ),
        "scenario_id is not deterministic",
        timeline,
    )


def _assert_with_timeline(condition: bool, message: str, timeline: str) -> None:
    """Fail with the generated scenario timeline so it can be compared to video."""
    if not condition:
        raise AssertionError(f"{message}\n\nGenerated scenario timeline:\n{timeline}")


def _events_and_scenarios_from_slice(config: AppConfig):
    """Fuse the 14-02-14 recorded slice, then build scenarios from GameEvents."""
    payload = json.loads(_SLICE_140214.read_text(encoding="utf-8"))
    frames = [VisionFrameResult.model_validate(frame) for frame in payload["frame_results"]]
    snaps = fuse_game_state(
        frames,
        config.vision.timer,
        config.vision.state_fusion,
        config.vision.death,
        config.vision.splat,
        config.vision.respawn,
        config.vision.active_gameplay,
        config.vision.lifecycle,
        config.vision.map_overlay,
    )
    events = infer_events(snaps, config.vision.events)
    return events, build_scenarios(events, config.scenarios)
