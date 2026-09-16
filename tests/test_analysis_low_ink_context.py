"""LOW_INK → ScenarioContext association rule.

The rule under test is deterministic and structural: a LOW_INK interval
attaches when it overlaps the scenario interval, and never otherwise.
LOW_INK stays out of scenario membership.
"""

from __future__ import annotations

from splatoon3_ai_coach.analysis.low_ink_context import (
    overlaps_scenario,
)
from splatoon3_ai_coach.analysis.scenario_context import build_scenario_context
from splatoon3_ai_coach.analysis.scenario_models import ScenarioType
from splatoon3_ai_coach.analysis.scenarios import build_scenarios, event_id
from splatoon3_ai_coach.config.models import EventFusionConfig, ScenarioBuilderConfig
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.models import (
    GameEvent,
    GameEventReason,
    GameEventSource,
    GameEventType,
    GameStateSnapshot,
)


def _cfg() -> ScenarioBuilderConfig:
    return ScenarioBuilderConfig()


def _event(
    timestamp: float,
    event_type: GameEventType,
    *,
    reason: GameEventReason | None = None,
    end_time: float | None = None,
) -> GameEvent:
    return GameEvent(
        start_time=timestamp,
        end_time=end_time,
        event_type=event_type,
        reason=reason,
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


def _low_ink(start: float, end: float | None) -> GameEvent:
    return GameEvent(
        start_time=start,
        end_time=end,
        event_type=GameEventType.LOW_INK,
        source=GameEventSource.STATE,
        reason=GameEventReason.LOW_INK_PRESENT,
        confidence=1.0,
    )


def _death_episode(events: list[GameEvent]):
    """Build the single DEATH_EPISODE scenario from ``events``."""
    scenarios = build_scenarios(events, _cfg())
    episodes = [
        item for item in scenarios if item.scenario_type is ScenarioType.DEATH_EPISODE
    ]
    assert len(episodes) == 1
    return episodes[0]


def _lifecycle() -> list[GameEvent]:
    """DEATH_EPISODE spanning 100.0 → 109.0."""
    return [_death(100.0), _respawn(107.5), _active(109.0)]


def test_low_ink_inside_episode_is_attached() -> None:
    low = _low_ink(102.0, 103.0)
    events = [*_lifecycle(), low]
    episode = _death_episode(events)
    ctx = build_scenario_context(events, episode, _cfg())
    assert ctx.low_ink is not None
    assert len(ctx.low_ink.intervals) == 1
    interval = ctx.low_ink.intervals[0]
    assert interval.start_time == 102.0
    assert interval.end_time == 103.0
    assert interval.end_time_observed is True
    assert interval.event_id == event_id(low)


def test_low_ink_overlapping_episode_start_is_attached() -> None:
    """Interval opens before the death and is still open when it happens."""
    events = [*_lifecycle(), _low_ink(98.0, 101.0)]
    episode = _death_episode(events)
    ctx = build_scenario_context(events, episode, _cfg())
    assert ctx.low_ink is not None
    assert [i.start_time for i in ctx.low_ink.intervals] == [98.0]


def test_low_ink_overlapping_episode_end_is_attached() -> None:
    """Interval opens inside the episode and closes after it ends."""
    events = [*_lifecycle(), _low_ink(108.5, 112.0)]
    episode = _death_episode(events)
    ctx = build_scenario_context(events, episode, _cfg())
    assert ctx.low_ink is not None
    assert [i.start_time for i in ctx.low_ink.intervals] == [108.5]


def test_low_ink_touching_episode_boundary_is_attached() -> None:
    """Closed-interval rule: sharing exactly one endpoint counts as overlap."""
    events = [*_lifecycle(), _low_ink(96.0, 100.0), _low_ink(109.0, 111.0)]
    episode = _death_episode(events)
    ctx = build_scenario_context(events, episode, _cfg())
    assert ctx.low_ink is not None
    assert [i.start_time for i in ctx.low_ink.intervals] == [96.0, 109.0]


def test_low_ink_outside_episode_is_not_attached() -> None:
    """Before and after the interval, with no shared endpoint."""
    events = [*_lifecycle(), _low_ink(90.0, 95.0), _low_ink(115.0, 117.0)]
    episode = _death_episode(events)
    ctx = build_scenario_context(events, episode, _cfg())
    assert ctx.low_ink is None


def test_multiple_overlapping_low_ink_intervals_all_attach_in_order() -> None:
    events = [
        *_lifecycle(),
        _low_ink(106.0, 106.5),
        _low_ink(101.0, 102.0),
        _low_ink(120.0, 121.0),  # outside
        _low_ink(103.5, 104.0),
    ]
    episode = _death_episode(events)
    ctx = build_scenario_context(events, episode, _cfg())
    assert ctx.low_ink is not None
    assert [i.start_time for i in ctx.low_ink.intervals] == [101.0, 103.5, 106.0]


def test_unclosed_low_ink_interval_is_an_instant_not_a_duration() -> None:
    events = [*_lifecycle(), _low_ink(104.0, None)]
    episode = _death_episode(events)
    ctx = build_scenario_context(events, episode, _cfg())
    assert ctx.low_ink is not None
    interval = ctx.low_ink.intervals[0]
    assert interval.start_time == 104.0
    assert interval.end_time == 104.0
    assert interval.end_time_observed is False


def test_association_uses_no_proximity_window() -> None:
    """A near-miss stays unattached however small the gap."""
    events = _lifecycle()
    episode = _death_episode(events)
    assert overlaps_scenario(_low_ink(99.0, 99.9), episode) is False
    assert overlaps_scenario(_low_ink(109.1, 110.0), episode) is False
    assert overlaps_scenario(_low_ink(99.0, 100.0), episode) is True


def test_engagement_scenario_also_receives_overlapping_low_ink() -> None:
    splat = GameEvent(
        start_time=50.0,
        event_type=GameEventType.SPLAT,
        splat_fingerprint="fp-1",
        confidence=1.0,
    )
    events = [splat, _low_ink(49.0, 51.0)]
    scenarios = build_scenarios(events, _cfg())
    engagement = next(
        item for item in scenarios if item.scenario_type is ScenarioType.ENGAGEMENT
    )
    ctx = build_scenario_context(events, engagement, _cfg())
    assert ctx.low_ink is not None
    assert len(ctx.low_ink.intervals) == 1


def test_low_ink_never_becomes_a_scenario_member() -> None:
    events = [
        *_lifecycle(),
        _low_ink(102.0, 103.0),
        _low_ink(90.0, 95.0),
        GameEvent(
            start_time=105.0,
            event_type=GameEventType.MAP_OVERLAY,
            end_time=105.5,
            confidence=1.0,
        ),
    ]
    scenarios = build_scenarios(events, _cfg())
    assert scenarios
    for scenario in scenarios:
        for eid in scenario.event_ids:
            assert not eid.startswith(f"{GameEventType.LOW_INK.value}:")


def test_no_low_ink_event_when_fused_state_is_none() -> None:
    """Unusable readings leave ``low_ink_present`` None — no interval opens."""
    snaps = [
        GameStateSnapshot(
            timestamp=float(index),
            player_lifecycle="unknown",
            low_ink_present=None,
            quality="observed",
        )
        for index in range(1, 5)
    ]
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    assert [e for e in events if e.event_type is GameEventType.LOW_INK] == []

    scenarios = build_scenarios([_death(2.0)], _cfg())
    ctx = build_scenario_context([_death(2.0), *events], scenarios[0], _cfg())
    assert ctx.low_ink is None
