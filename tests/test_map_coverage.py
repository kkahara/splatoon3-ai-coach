"""Rule ``respawn_map_coverage``: when a missing map overlay is a real negative."""

from __future__ import annotations

from public_site.results import _map_line

from splatoon3_ai_coach.analysis.map_coverage import (
    CoverageStatus,
    MapCheckStatus,
    MapOverlaySample,
    MapUnknownReason,
    match_map_coverage,
)
from splatoon3_ai_coach.analysis.scenario_context import build_scenario_contexts
from splatoon3_ai_coach.analysis.scenario_evidence import ScenarioEvidencePack
from splatoon3_ai_coach.analysis.scenario_models import ScenarioType
from splatoon3_ai_coach.analysis.scenarios import build_scenarios
from splatoon3_ai_coach.coach.evidence_limits import collect_evidence_limits
from splatoon3_ai_coach.config.models import MapCoverageConfig, ScenarioBuilderConfig
from splatoon3_ai_coach.media.video_source import Observability
from splatoon3_ai_coach.vision.models import GameEvent, GameEventReason, GameEventType

_STEP = 0.5


def _event(t: float, kind: GameEventType, reason: GameEventReason) -> GameEvent:
    return GameEvent(start_time=t, event_type=kind, reason=reason, confidence=1.0)


def _death(t: float) -> GameEvent:
    return _event(t, GameEventType.DEATH, GameEventReason.ALIVE_TO_DEAD)


def _respawn(t: float) -> GameEvent:
    return _event(t, GameEventType.RESPAWN, GameEventReason.COUNTDOWN_PLATE_ENDED)


def _active(t: float) -> GameEvent:
    reason = GameEventReason.AWAITING_CONTROL_TO_ALIVE
    return _event(t, GameEventType.ACTIVE_AGAIN, reason)


def _map(t: float) -> GameEvent:
    return _event(t, GameEventType.MAP_OVERLAY, GameEventReason.MAP_OVERLAY_PRESENT)


def _cycle(death: float, *, respawn_map: bool) -> list[GameEvent]:
    """Death → respawn at +6 → active at +8, optionally a map on the respawn screen."""
    events = [_death(death), _respawn(death + 6.0), _active(death + 8.0)]
    if respawn_map:
        events.append(_map(death + 3.0))
    return events


def _pack(end: float, *, gap: tuple[float, float] | None = None) -> ScenarioEvidencePack:
    times = [round(i * _STEP, 3) for i in range(int(end / _STEP) + 1)]
    samples = [
        MapOverlaySample(video_time=t, present=False, confidence=0.9, usable=True)
        for t in times
        if gap is None or not (gap[0] <= t < gap[1])
    ]
    return ScenarioEvidencePack(map_overlay_samples=samples, cadence_times=times)


def _death_maps(events, pack, observability=Observability.OBSERVABLE):
    config = ScenarioBuilderConfig()
    scenarios = build_scenarios(events, config)
    contexts = build_scenario_contexts(
        events,
        scenarios,
        config,
        evidence=pack,
        map_overlay_observability=observability,
    )
    by_id = {c.scenario_id: c for c in contexts}
    return {
        s.start_time: by_id[s.scenario_id].map
        for s in scenarios
        if s.scenario_type is ScenarioType.DEATH_EPISODE
    }


def test_four_of_five_respawns_covered_gates_each_episode() -> None:
    deaths = [20.0, 50.0, 80.0, 110.0, 140.0]
    events = []
    for index, death in enumerate(deaths):
        events.extend(_cycle(death, respawn_map=index != 2))
    maps = _death_maps(events, _pack(160.0))
    coverage = maps[20.0].map_coverage
    assert coverage.status is CoverageStatus.SUFFICIENT
    assert (coverage.respawns_with_overlay, coverage.completed_respawns) == (4, 5)
    # The first death has no earlier overlay but a fully covered episode.
    assert maps[20.0].map_check_status is MapCheckStatus.NOT_OBSERVED
    assert maps[20.0].map_check_before_death is False
    # Earlier respawn-screen overlays count as "any overlay before".
    assert maps[50.0].map_check_status is MapCheckStatus.OBSERVED
    # The uncovered respawn episode: its own map is missing → checked-while-dead unknown.
    assert maps[80.0].episode_map_observability.own_respawn_overlay is False
    assert maps[80.0].map_checked_while_dead is None


def test_no_respawn_overlays_means_unknown_not_false() -> None:
    events = []
    for death in (20.0, 50.0, 80.0, 110.0, 140.0):
        events.extend(_cycle(death, respawn_map=False))
    maps = _death_maps(events, _pack(160.0))
    for nest in maps.values():
        assert nest.map_coverage.status is CoverageStatus.INSUFFICIENT
        assert nest.map_coverage.respawns_with_overlay == 0
        assert nest.map_check_status is MapCheckStatus.UNKNOWN
        reason = nest.map_check_unknown_reason
        assert reason is MapUnknownReason.MATCH_COVERAGE_INSUFFICIENT
        assert nest.map_check_before_death is None


def test_pre_death_reading_gap_makes_episode_unknown() -> None:
    events = _cycle(20.0, respawn_map=True) + _cycle(60.0, respawn_map=True)
    maps = _death_maps(events, _pack(80.0, gap=(5.0, 15.0)))
    first = maps[20.0]
    assert first.map_coverage.status is CoverageStatus.SUFFICIENT
    assert first.map_check_status is MapCheckStatus.UNKNOWN
    assert first.map_check_unknown_reason is (
        MapUnknownReason.EPISODE_PRE_DEATH_COVERAGE_GAP
    )
    assert first.episode_map_observability.max_pre_death_gap_seconds > 2.0


def test_missing_readings_are_unknown() -> None:
    events = _cycle(20.0, respawn_map=True)
    maps = _death_maps(events, ScenarioEvidencePack())
    assert maps[20.0].map_check_status is MapCheckStatus.UNKNOWN
    assert maps[20.0].map_check_unknown_reason is (
        MapUnknownReason.MAP_READINGS_UNAVAILABLE
    )


def test_unobservable_source_never_asserts_absence() -> None:
    events = _cycle(20.0, respawn_map=True)
    maps = _death_maps(events, _pack(40.0), Observability.UNOBSERVABLE)
    assert maps[20.0].map_check_status is MapCheckStatus.UNKNOWN
    assert maps[20.0].map_check_unknown_reason is MapUnknownReason.SOURCE_UNOBSERVABLE


def test_positive_overlay_is_observed_regardless_of_coverage() -> None:
    events = [_map(10.0), *_cycle(20.0, respawn_map=False)]
    maps = _death_maps(events, ScenarioEvidencePack())
    assert maps[20.0].map_check_status is MapCheckStatus.OBSERVED
    assert maps[20.0].map_check_before_death is True


def test_match_coverage_requires_completed_respawns() -> None:
    events = [_death(20.0), _respawn(26.0)]
    coverage = match_map_coverage(
        events,
        source_observability=Observability.OBSERVABLE,
        config=MapCoverageConfig(),
    )
    assert coverage.status is CoverageStatus.INSUFFICIENT
    assert coverage.completed_respawns == 0


def test_unknown_map_reaches_limit_and_public_line() -> None:
    events = _cycle(20.0, respawn_map=False)
    config = ScenarioBuilderConfig()
    scenarios = build_scenarios(events, config)
    contexts = build_scenario_contexts(
        events, scenarios, config, evidence=_pack(40.0)
    )
    death = next(
        s for s in scenarios if s.scenario_type is ScenarioType.DEATH_EPISODE
    )
    context = next(c for c in contexts if c.scenario_id == death.scenario_id)
    limits = collect_evidence_limits(death, context, [])
    limit = next(item for item in limits if item.code == "map_check_unobservable")
    assert "match_coverage_insufficient" in limit.statement
    payload = {"primary_context": context.model_dump(mode="json")}
    assert _map_line(payload) == "Map-check evidence was unavailable for this video."
