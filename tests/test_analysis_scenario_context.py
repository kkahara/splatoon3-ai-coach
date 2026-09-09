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
    contexts = build_scenario_contexts(events, scenarios, config)
    chosen = [item for item in contexts if item.scenario_id.startswith(scenario_type.value)]
    assert chosen
    return chosen[0]


def _contexts(events: list[GameEvent], **cfg: float):
    config = _cfg(**cfg)
    scenarios = build_scenarios(events, config)
    return scenarios, build_scenario_contexts(events, scenarios, config)


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


def test_death_episode_timings() -> None:
    events = [_death(100.0), _respawn(107.5), _active(109.0)]
    ctx = _context(events, ScenarioType.DEATH_EPISODE)
    assert ctx.death_episode is not None
    assert ctx.death_episode.death_to_respawn == pytest.approx(7.5)
    assert ctx.death_episode.death_to_active_again == pytest.approx(9.0)
    assert ctx.death_episode.respawn_to_active_again == pytest.approx(1.5)
    assert ctx.death_episode.awaiting_duration == pytest.approx(7.5)
    assert ctx.death_episode.has_respawn is True
    assert ctx.death_episode.has_active_again is True
    assert ctx.death_episode.complete is True
    assert ctx.combat is None


def test_missing_lifecycle_events_are_none() -> None:
    ctx = _context([_death(100.0)], ScenarioType.DEATH_EPISODE)
    assert ctx.death_episode is not None
    assert ctx.death_episode.death_to_respawn is None
    assert ctx.death_episode.death_to_active_again is None
    assert ctx.death_episode.respawn_to_active_again is None
    assert ctx.death_episode.has_respawn is False
    assert ctx.death_episode.has_active_again is False
    assert ctx.death_episode.complete is False
    assert ctx.death_episode.respawn_reason is None
    assert ctx.combat is None
    assert ctx.timeline is not None
    assert ctx.timeline.time_since_previous_death is None
    assert ctx.timeline.time_to_next_death is None


def test_map_check_before_death() -> None:
    events = [_map(98.0, 99.0), _death(100.0)]
    ctx = _context(events, ScenarioType.DEATH_EPISODE)
    assert ctx.map is not None
    assert ctx.map.map_check_before_death is True
    assert ctx.map.seconds_since_map_check_before_death == pytest.approx(2.0)
    assert ctx.map.last_map_before_death_event_id is not None
    assert ctx.map.last_map_before_death_event_id.startswith("map_overlay:98.000")


def test_map_during_death_episode() -> None:
    events = [_death(100.0), _map(101.0, 102.0), _respawn(107.0), _active(109.0)]
    ctx = _context(events, ScenarioType.DEATH_EPISODE)
    assert ctx.map is not None
    assert ctx.map.map_checks_during_death_episode == 1
    assert ctx.map.map_checked_while_dead is True
    assert ctx.map.map_event_ids_during_episode is not None
    assert len(ctx.map.map_event_ids_during_episode) == 1
    assert _of_type(build_scenarios(events, _cfg()), ScenarioType.MAP_CHECK) == []


def test_map_after_active_again_is_standalone_map_check() -> None:
    events = [_death(100.0), _respawn(107.0), _active(109.0), _map(110.0, 111.0)]
    config = _cfg()
    scenarios = build_scenarios(events, config)
    episode = _of_type(scenarios, ScenarioType.DEATH_EPISODE)[0]
    maps = _of_type(scenarios, ScenarioType.MAP_CHECK)
    ctx = build_scenario_context(events, episode, config)
    assert ctx.map is not None
    assert ctx.map.map_checks_during_death_episode == 0
    assert ctx.map.map_checked_while_dead is False
    assert ctx.map.map_event_ids_during_episode == []
    assert len(maps) == 1
    assert maps[0].context["in_death_episode"] is False


def test_engagement_leads_to_death_episode_relation() -> None:
    events = [_splat(150.0, "aa" * 8), _death(151.0)]
    scenarios, contexts = _contexts(
        events, engagement_include_following_death_seconds=2.0
    )
    by_id = {item.scenario_id: item for item in contexts}
    eng = by_id["engagement:150.000"]
    death = by_id["death_episode:151.000"]
    assert eng.relations.leads_to_death_episode_id == "death_episode:151.000"
    assert death.relations.preceded_by_engagement_id == "engagement:150.000"
    assert scenarios[0].context.get("following_death_id") is not None


def test_following_death_is_not_duplicated_in_engagement_membership() -> None:
    """One-owner model: DEATH lives only on DEATH_EPISODE; link via relations."""
    from collections import Counter

    splat = _splat(150.0, "aa" * 8)
    death = _death(151.5)
    events = [splat, death]
    scenarios, contexts = _contexts(
        events, engagement_include_following_death_seconds=2.0
    )
    engagements = [s for s in scenarios if s.scenario_type is ScenarioType.ENGAGEMENT]
    deaths = [s for s in scenarios if s.scenario_type is ScenarioType.DEATH_EPISODE]
    assert len(engagements) == 1
    assert len(deaths) == 1
    engagement = engagements[0]
    death_episode = deaths[0]
    death_eid = event_id(death)
    splat_eid = event_id(splat)

    assert death_episode.event_ids == [death_eid]
    assert engagement.event_ids == [splat_eid]
    assert death_eid not in engagement.event_ids
    assert engagement.context["following_death_id"] == death_eid

    by_id = {item.scenario_id: item for item in contexts}
    eng_ctx = by_id[engagement.scenario_id]
    death_ctx = by_id[death_episode.scenario_id]
    assert eng_ctx.relations.leads_to_death_episode_id == death_episode.scenario_id
    assert death_ctx.relations.preceded_by_engagement_id == engagement.scenario_id

    owners = Counter(eid for s in scenarios for eid in s.event_ids)
    assert owners[death_eid] == 1
    assert owners[splat_eid] == 1
    owned = set(owners)
    all_ids = {event_id(item) for item in events}
    assert owned == all_ids
    assert max(owners.values()) == 1


def test_death_episode_next_engagement_relation() -> None:
    events = [
        _death(100.0),
        _respawn(107.0),
        _active(109.0),
        _splat(113.0, "aa" * 8),
    ]
    _scenarios, contexts = _contexts(events)
    by_id = {item.scenario_id: item for item in contexts}
    death = by_id["death_episode:100.000"]
    eng = by_id["engagement:113.000"]
    assert death.relations.next_engagement_id == "engagement:113.000"
    assert eng.relations.follows_death_episode_id == "death_episode:100.000"
    assert eng.relations.leads_to_death_episode_id is None
    assert death.combat is None


def test_post_return_splat_lives_on_engagement_not_death_episode() -> None:
    events = [
        _death(100.0),
        _respawn(107.0),
        _active(109.0),
        _splat(113.0, "aa" * 8),
    ]
    death_ctx = _context(events, ScenarioType.DEATH_EPISODE)
    eng_ctx = _context(events, ScenarioType.ENGAGEMENT)
    assert death_ctx.combat is None
    assert eng_ctx.combat is not None
    assert eng_ctx.combat.splat_count == 1
    assert eng_ctx.combat.first_splat_time == pytest.approx(113.0)
    assert eng_ctx.combat.last_splat_time == pytest.approx(113.0)
    assert eng_ctx.combat.duration == pytest.approx(0.0)


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
    episodes = _of_type(build_scenarios(events, config), ScenarioType.DEATH_EPISODE)
    first = build_scenario_context(events, episodes[0], config)
    second = build_scenario_context(events, episodes[1], config)
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
    traded = _context(inside, ScenarioType.ENGAGEMENT, **config.model_dump())
    missed = _context(outside, ScenarioType.ENGAGEMENT, **config.model_dump())
    assert traded.combat is not None
    assert traded.combat.splat_death_gap == pytest.approx(1.0)
    assert traded.combat.trade_candidate is True
    assert traded.combat.first_splat_time == pytest.approx(150.0)
    assert traded.combat.duration == pytest.approx(0.0)
    assert missed.combat is not None
    assert missed.combat.splat_death_gap == pytest.approx(3.0)
    assert missed.combat.trade_candidate is False
    assert traded.combat.time_to_first_splat is None


def test_distinct_splat_fingerprints_stay_distinct() -> None:
    events = [_splat(8.0, "aa" * 8), _splat(8.0, "bb" * 8)]
    ctx = _context(events, ScenarioType.ENGAGEMENT)
    assert ctx.combat is not None
    assert ctx.combat.splat_count == 2
    assert ctx.combat.first_splat_time == pytest.approx(8.0)
    assert ctx.combat.last_splat_time == pytest.approx(8.0)
    assert ctx.combat.duration == pytest.approx(0.0)


def test_insufficient_evidence_is_none_not_invented() -> None:
    events = [_death(50.0), _respawn(55.0)]
    ctx = _context(events, ScenarioType.DEATH_EPISODE)
    assert ctx.death_episode is not None
    assert ctx.death_episode.has_respawn is True
    assert ctx.death_episode.has_active_again is False
    assert ctx.death_episode.death_to_active_again is None
    assert ctx.combat is None
    assert ctx.map is not None
    assert ctx.map.map_check_before_death is False
    assert ctx.map.seconds_since_map_check_before_death is None


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
    ctx = _context(events, ScenarioType.DEATH_EPISODE)
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
    assert payload[0]["death_episode"]["death_to_respawn"] == pytest.approx(2.0)
    assert "relations" in payload[0]


def test_140214_fixture_scenario_contexts() -> None:
    """Assert facts for the water-death slice. Dump JSON on failure."""
    config = load_config(default_config_path())
    events, scenarios = _events_and_scenarios_from_slice(config)
    contexts = build_scenario_contexts(events, scenarios, config.scenarios)
    by_id = {item.scenario_id: item for item in contexts}
    dump = _fixture_dump(events, scenarios, contexts)

    first = by_id.get("death_episode:196.500")
    second = by_id.get("death_episode:216.500")
    engagement = by_id.get("engagement:192.000")
    _assert_with_dump(first is not None, "missing death_episode:196.500", dump)
    _assert_with_dump(second is not None, "missing death_episode:216.500", dump)
    _assert_with_dump(engagement is not None, "missing engagement:192.000", dump)
    assert first is not None and second is not None and engagement is not None

    _assert_with_dump(first.death_episode is not None, "first death_episode nest missing", dump)
    _assert_with_dump(
        first.death_episode.death_to_respawn == pytest.approx(7.5, abs=0.26),
        f"death_to_respawn={first.death_episode.death_to_respawn}",
        dump,
    )
    _assert_with_dump(
        first.death_episode.death_to_active_again == pytest.approx(9.0, abs=0.26),
        f"death_to_active_again={first.death_episode.death_to_active_again}",
        dump,
    )
    _assert_with_dump(
        first.death_episode.respawn_reason == "skip_countdown_control",
        f"respawn_reason={first.death_episode.respawn_reason}",
        dump,
    )
    _assert_with_dump(first.map is not None, "first map nest missing", dump)
    _assert_with_dump(
        first.map.map_checks_during_death_episode == 2,
        f"during_death={first.map.map_checks_during_death_episode}",
        dump,
    )
    _assert_with_dump(
        first.map.map_checked_while_dead is True,
        f"map_checked_while_dead={first.map.map_checked_while_dead}",
        dump,
    )
    _assert_with_dump(first.combat is None, "death episode should omit combat nest", dump)
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

    _assert_with_dump(second.death_episode is not None, "second death_episode nest missing", dump)
    _assert_with_dump(second.timeline is not None, "second timeline missing", dump)
    _assert_with_dump(
        second.timeline.time_since_previous_death == pytest.approx(20.0, abs=0.26),
        f"second since_previous={second.timeline.time_since_previous_death}",
        dump,
    )
    _assert_with_dump(
        second.death_episode.respawn_reason == "countdown_plate_ended",
        f"second respawn_reason={second.death_episode.respawn_reason}",
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
        "death_episode:196.500",
        "death_episode:216.500",
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
