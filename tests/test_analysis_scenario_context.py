"""ScenarioContext: facts from GameEvents + Scenarios. No coaching text."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from splatoon3_ai_coach.analysis.pipeline import (
    SCENARIO_CONTEXTS_JSON_FILENAME,
    write_scenarios,
)
from splatoon3_ai_coach.analysis.scenario_context import (
    build_scenario_context,
    build_scenario_contexts,
    serialize_scenario_contexts,
)
from splatoon3_ai_coach.analysis.scenario_models import ScenarioType
from splatoon3_ai_coach.analysis.scenarios import build_scenarios, format_scenario_timeline
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
_CONTEXT_SRC = (
    _REPO / "src" / "splatoon3_ai_coach" / "analysis" / "scenario_context.py"
)
_COACHING_WORDS = (
    "you should",
    "reckless",
    "careless",
    "too aggressive",
    "not aggressive enough",
    "good map usage",
    "bad map usage",
    "too slow",
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
) -> GameEvent:
    return GameEvent(
        start_time=timestamp,
        end_time=end_time,
        event_type=event_type,
        reason=reason,
        splat_fingerprint=fingerprint,
        confidence=1.0,
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


def _context(events: list[GameEvent], scenario_type: ScenarioType, **cfg: float):
    config = _cfg(**cfg)
    scenarios = build_scenarios(events, config)
    chosen = _of_type(scenarios, scenario_type)[0]
    return build_scenario_context(events, chosen, config)


def test_context_module_does_not_import_pipeline_or_detectors() -> None:
    tree = ast.parse(_CONTEXT_SRC.read_text(encoding="utf-8"))
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


def test_recovery_timings() -> None:
    events = [_death(100.0), _respawn(107.5), _active(109.0)]
    ctx = _context(events, ScenarioType.POST_DEATH_RECOVERY)
    assert ctx.recovery is not None
    assert ctx.recovery.time_to_respawn == pytest.approx(7.5)
    assert ctx.recovery.time_to_active_again == pytest.approx(9.0)
    assert ctx.recovery.has_respawn is True
    assert ctx.recovery.has_active_again is True


def test_missing_recovery_events_are_none() -> None:
    ctx = _context([_death(100.0)], ScenarioType.POST_DEATH_RECOVERY)
    assert ctx.recovery is not None
    assert ctx.recovery.time_to_respawn is None
    assert ctx.recovery.time_to_active_again is None
    assert ctx.recovery.has_respawn is False
    assert ctx.recovery.has_active_again is False
    assert ctx.recovery.respawn_reason is None
    assert ctx.combat is not None
    assert ctx.combat.time_to_first_splat is None
    assert ctx.timeline is not None
    assert ctx.timeline.time_since_previous_death is None
    assert ctx.timeline.time_to_next_death is None


def test_map_check_before_death() -> None:
    events = [_map(98.0, 99.0), _death(100.0)]
    ctx = _context(events, ScenarioType.POST_DEATH_RECOVERY)
    assert ctx.map is not None
    assert ctx.map.map_check_before_death is True
    assert ctx.map.seconds_since_map_check_before_death == pytest.approx(2.0)


def test_map_during_death_episode() -> None:
    events = [_death(100.0), _map(101.0, 102.0), _respawn(107.0), _active(109.0)]
    ctx = _context(events, ScenarioType.POST_DEATH_RECOVERY)
    assert ctx.map is not None
    assert ctx.map.map_checks_during_death_episode == 1
    assert ctx.map.map_checks_after_active_again == 0
    maps = _of_type(build_scenarios(events, _cfg()), ScenarioType.MAP_CHECK)
    map_ctx = build_scenario_context(events, maps[0], _cfg())
    assert map_ctx.map is not None
    assert map_ctx.map.in_death_episode is True


def test_map_after_active_again() -> None:
    events = [_death(100.0), _respawn(107.0), _active(109.0), _map(110.0, 111.0)]
    ctx = _context(events, ScenarioType.POST_DEATH_RECOVERY)
    assert ctx.map is not None
    assert ctx.map.map_checks_during_death_episode == 0
    assert ctx.map.map_checks_after_active_again == 1


def test_time_to_first_splat_after_active() -> None:
    events = [
        _death(100.0),
        _respawn(107.0),
        _active(109.0),
        _splat(113.0, "aa" * 8),
    ]
    ctx = _context(events, ScenarioType.POST_DEATH_RECOVERY)
    assert ctx.combat is not None
    assert ctx.combat.splat_count == 1
    assert ctx.combat.time_to_first_splat == pytest.approx(4.0)


def test_death_to_death_neighbors() -> None:
    events = [
        _death(100.0),
        _respawn(105.0),
        _active(106.0),
        _death(120.0),
        _respawn(125.0),
        _active(126.0),
    ]
    config = _cfg()
    recoveries = _of_type(
        build_scenarios(events, config), ScenarioType.POST_DEATH_RECOVERY
    )
    first = build_scenario_context(events, recoveries[0], config)
    second = build_scenario_context(events, recoveries[1], config)
    assert first.timeline is not None
    assert second.timeline is not None
    assert first.timeline.time_to_next_death == pytest.approx(20.0)
    assert second.timeline.time_since_previous_death == pytest.approx(20.0)
    assert first.timeline.time_since_previous_death is None
    assert second.timeline.time_to_next_death is None


def test_trade_candidate_inside_and_outside_window() -> None:
    inside = [_splat(150.0, "aa" * 8), _death(151.0)]
    outside = [_splat(150.0, "bb" * 8), _death(153.0)]
    config = _cfg(engagement_include_following_death_seconds=2.0)
    traded = _context(inside, ScenarioType.ENGAGEMENT)
    missed = _context(outside, ScenarioType.ENGAGEMENT)
    assert traded.combat is not None
    assert traded.combat.splat_death_gap == pytest.approx(1.0)
    assert traded.combat.trade_candidate is True
    assert missed.combat is not None
    assert missed.combat.splat_death_gap == pytest.approx(3.0)
    assert missed.combat.trade_candidate is False
    assert traded.combat.time_to_first_splat is None


def test_distinct_splat_fingerprints_stay_distinct() -> None:
    events = [_splat(8.0, "aa" * 8), _splat(8.0, "bb" * 8)]
    ctx = _context(events, ScenarioType.ENGAGEMENT)
    assert ctx.combat is not None
    assert ctx.combat.splat_count == 2


def test_insufficient_evidence_is_none_not_invented() -> None:
    events = [_death(50.0), _respawn(55.0)]
    ctx = _context(events, ScenarioType.POST_DEATH_RECOVERY)
    assert ctx.recovery is not None
    assert ctx.recovery.has_respawn is True
    assert ctx.recovery.has_active_again is False
    assert ctx.recovery.time_to_active_again is None
    assert ctx.combat is not None
    assert ctx.combat.splat_count == 0
    assert ctx.combat.time_to_first_splat is None
    assert ctx.combat.splat_death_gap is None
    assert ctx.combat.trade_candidate is False
    assert ctx.map is not None
    assert ctx.map.map_check_before_death is False
    assert ctx.map.seconds_since_map_check_before_death is None
    assert ctx.map.map_checks_after_active_again is None


def test_identical_input_is_deterministic() -> None:
    events = [
        _map(98.0, 99.0),
        _death(100.0),
        _respawn(107.5),
        _active(109.0),
        _splat(113.0, "aa" * 8),
        _death(120.0),
    ]
    config = _cfg()
    first_scenarios = build_scenarios(events, config)
    second_scenarios = build_scenarios(list(reversed(events)), config)
    first = serialize_scenario_contexts(
        build_scenario_contexts(events, first_scenarios, config)
    )
    second = serialize_scenario_contexts(
        build_scenario_contexts(list(reversed(events)), second_scenarios, config)
    )
    assert first == second
    assert [item["scenario_id"] for item in first] == [
        item["scenario_id"] for item in second
    ]


def test_serialized_context_has_no_coaching_language() -> None:
    events = [_death(100.0), _respawn(107.5, skip=True), _active(109.0)]
    ctx = _context(events, ScenarioType.POST_DEATH_RECOVERY)
    text = json.dumps(ctx.model_dump(mode="json")).lower()
    for phrase in _COACHING_WORDS:
        assert phrase not in text


def test_write_scenarios_persists_contexts(tmp_path: Path) -> None:
    config = load_config(default_config_path())
    events = [_death(10.0), _respawn(12.0), _active(13.0)]
    written = write_scenarios(events, config, tmp_path)
    payload = json.loads(
        (tmp_path / SCENARIO_CONTEXTS_JSON_FILENAME).read_text(encoding="utf-8")
    )
    assert len(payload) == len(written)
    assert payload[0]["scenario_id"] == written[0].scenario_id
    assert payload[0]["recovery"]["time_to_respawn"] == pytest.approx(2.0)


def test_140214_fixture_scenario_contexts() -> None:
    """Assert facts for the water-death slice. Dump JSON on failure."""
    config = load_config(default_config_path())
    events, scenarios = _events_and_scenarios_from_slice(config)
    contexts = build_scenario_contexts(events, scenarios, config.scenarios)
    by_id = {item.scenario_id: item for item in contexts}
    dump = _fixture_dump(events, scenarios, contexts)

    first = by_id.get("post_death_recovery:196.500")
    second = by_id.get("post_death_recovery:216.500")
    engagement = by_id.get("engagement:192.000")
    _assert_with_dump(first is not None, "missing post_death_recovery:196.500", dump)
    _assert_with_dump(second is not None, "missing post_death_recovery:216.500", dump)
    _assert_with_dump(engagement is not None, "missing engagement:192.000", dump)
    assert first is not None and second is not None and engagement is not None

    _assert_with_dump(first.recovery is not None, "first recovery nest missing", dump)
    _assert_with_dump(
        first.recovery.time_to_respawn == pytest.approx(7.5, abs=0.26),
        f"time_to_respawn={first.recovery.time_to_respawn}",
        dump,
    )
    _assert_with_dump(
        first.recovery.time_to_active_again == pytest.approx(9.0, abs=0.26),
        f"time_to_active_again={first.recovery.time_to_active_again}",
        dump,
    )
    _assert_with_dump(
        first.recovery.respawn_reason == "skip_countdown_control",
        f"respawn_reason={first.recovery.respawn_reason}",
        dump,
    )
    _assert_with_dump(first.map is not None, "first map nest missing", dump)
    _assert_with_dump(
        first.map.map_checks_during_death_episode == 2,
        f"during_death={first.map.map_checks_during_death_episode}",
        dump,
    )
    _assert_with_dump(
        first.map.map_checks_after_active_again == 2,
        f"after_active={first.map.map_checks_after_active_again}",
        dump,
    )
    _assert_with_dump(first.combat is not None, "first combat nest missing", dump)
    _assert_with_dump(
        first.combat.splat_count == 0, f"splat_count={first.combat.splat_count}", dump
    )
    _assert_with_dump(
        first.combat.time_to_first_splat is None,
        f"time_to_first_splat={first.combat.time_to_first_splat}",
        dump,
    )
    _assert_with_dump(first.timeline is not None, "first timeline missing", dump)
    _assert_with_dump(
        first.timeline.time_to_next_death == pytest.approx(20.0, abs=0.26),
        f"time_to_next_death={first.timeline.time_to_next_death}",
        dump,
    )
    _assert_with_dump(
        first.timeline.time_since_previous_death is None,
        f"time_since_previous_death={first.timeline.time_since_previous_death}",
        dump,
    )

    _assert_with_dump(second.recovery is not None, "second recovery nest missing", dump)
    _assert_with_dump(second.timeline is not None, "second timeline missing", dump)
    _assert_with_dump(
        second.timeline.time_since_previous_death == pytest.approx(20.0, abs=0.26),
        f"second since_previous={second.timeline.time_since_previous_death}",
        dump,
    )
    _assert_with_dump(
        second.recovery.respawn_reason == "countdown_plate_ended",
        f"second respawn_reason={second.recovery.respawn_reason}",
        dump,
    )

    _assert_with_dump(engagement.combat is not None, "engagement combat missing", dump)
    _assert_with_dump(
        engagement.combat.splat_count == 1,
        f"engagement splat_count={engagement.combat.splat_count}",
        dump,
    )
    _assert_with_dump(
        engagement.combat.splat_death_gap == pytest.approx(4.5, abs=0.26),
        f"splat_death_gap={engagement.combat.splat_death_gap}",
        dump,
    )
    _assert_with_dump(
        engagement.combat.trade_candidate is False,
        f"trade_candidate={engagement.combat.trade_candidate}",
        dump,
    )
    _assert_with_dump(
        engagement.combat.time_to_first_splat is None,
        f"engagement time_to_first_splat={engagement.combat.time_to_first_splat}",
        dump,
    )


def _assert_with_dump(condition: bool, message: str, dump: str) -> None:
    """Fail with the scenario timeline and ScenarioContext JSON."""
    if not condition:
        raise AssertionError(f"{message}\n\n{dump}")


def _fixture_dump(events, scenarios, contexts) -> str:
    """Readable timeline plus the three requested context objects."""
    wanted = {
        "post_death_recovery:196.500",
        "post_death_recovery:216.500",
        "engagement:192.000",
    }
    selected = [item for item in contexts if item.scenario_id in wanted]
    return (
        "Generated scenario timeline:\n"
        f"{format_scenario_timeline(events, scenarios)}\n"
        "ScenarioContext JSON:\n"
        f"{json.dumps(serialize_scenario_contexts(selected), indent=2, sort_keys=True)}"
    )


def _events_and_scenarios_from_slice(config: AppConfig):
    """Fuse the 14-02-14 recorded slice, then build unchanged scenarios."""
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
