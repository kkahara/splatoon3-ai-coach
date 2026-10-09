"""Lifecycle-aware special readings and the last ready reading before a death."""

from __future__ import annotations

import json

from splatoon3_ai_coach.analysis.scenario_context import (
    DeathEpisodeContext,
    ScenarioContext,
    ScenarioRelations,
)
from splatoon3_ai_coach.analysis.scenario_evidence import (
    SpecialEvidence,
    SpecialGaugeState,
    SpecialReading,
    _life_starts,
    build_special_evidence,
    contextualize_special_readings,
)
from splatoon3_ai_coach.analysis.scenario_models import (
    Scenario,
    ScenarioOutcome,
    ScenarioType,
)
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.death_importance import (
    build_supporting_evidence,
    score_death_candidate,
)
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.vision.models import GameStateSnapshot

_DEATH = 72.5
_LOOKBACK = 8.0


def _reading(
    when: float, *, ready: bool, fill: float | None = None, prompt: float = 0.0
) -> SpecialReading:
    """Ready readings default to a full ring; pass ``fill`` for prompt-only."""
    if fill is None:
        fill = 1.0 if ready else 0.5
    return SpecialReading(
        video_time=when,
        visible=True,
        ready=ready,
        fill_fraction=fill,
        ready_prompt_score=prompt,
        confidence=0.9,
    )


def _snapshots(spec: list[tuple[float, float, str]]) -> list[GameStateSnapshot]:
    """Snapshots every 0.5 s; ``spec`` rows are [start, end) lifecycle spans."""
    out: list[GameStateSnapshot] = []
    for start, end, lifecycle in spec:
        t = start
        while t < end - 1e-9:
            out.append(
                GameStateSnapshot(timestamp=round(t, 3), player_lifecycle=lifecycle)
            )
            t += 0.5
    return out


_ALIVE_THEN_DEAD = _snapshots(
    [(60.0, _DEATH, "alive"), (_DEATH, 79.0, "dead"), (79.0, 82.0, "countdown"),
     (82.0, 84.0, "awaiting_control"), (84.0, 90.0, "alive")]
)


def _evidence(
    readings: list[SpecialReading],
    snapshots: list[GameStateSnapshot] | None = _ALIVE_THEN_DEAD,
) -> SpecialEvidence | None:
    return build_special_evidence(
        readings,
        window_start=_DEATH - _LOOKBACK,
        window_end=_DEATH + 15.0,
        anchor=_DEATH,
        max_gap_seconds=2.0,
        snapshots=snapshots,
    )


def _coach_input(special: SpecialEvidence | None) -> CoachInput:
    scenario = Scenario(
        scenario_id=f"death_episode:{_DEATH:.3f}",
        scenario_type=ScenarioType.DEATH_EPISODE,
        start_time=_DEATH,
        end_time=_DEATH + 11.5,
        event_ids=[f"DEATH@{_DEATH:.3f}"],
        confidence=1.0,
        outcome=ScenarioOutcome.DIED,
    )
    context = ScenarioContext(
        scenario_id=scenario.scenario_id,
        death_episode=DeathEpisodeContext(death_time=_DEATH, complete=True),
        special=special,
        relations=ScenarioRelations(),
    )
    return CoachInput(primary_scenario=scenario, primary_context=context)


def _view_and_support(special: SpecialEvidence) -> tuple[dict, str]:
    coach_input = _coach_input(special)
    view = build_coach_llm_view(coach_input, score_death_candidate(coach_input))
    support = next(
        item.value
        for item in build_supporting_evidence(coach_input)
        if item.label == "Special"
    )
    return view.model_dump(mode="json"), support


def test_last_ready_is_named_when_gauge_dropped_before_death() -> None:
    readings = [
        _reading(70.0, ready=False, fill=0.8),
        _reading(71.0, ready=True, fill=0.9),
        _reading(72.0, ready=False, fill=0.62),
        _reading(80.5, ready=True, fill=1.0),
        _reading(81.5, ready=True, fill=1.0),
    ]
    special = _evidence(readings)
    assert special is not None
    assert special.state_at_anchor is SpecialGaugeState.NOT_READY
    assert special.last_ready_before_anchor is not None
    assert special.last_ready_before_anchor.video_time == 71.0
    # Ready readings during the countdown are recorded but not coaching-valid.
    late = [r for r in special.observations if r.video_time >= 80.5]
    assert [r.coaching_valid for r in late] == [False, False]
    assert [o.video_time for o in special.ready_onsets] == [71.0]
    view, support = _view_and_support(special)
    assert view["special"]["last_ready"]["video_time"] == 71.0
    assert "last ready 1.5s of video (playback speed unverified) before" in support
    text = json.dumps(view).lower() + support.lower()
    assert "special_used" not in text
    assert "used" not in text


def test_ready_at_anchor_omits_last_ready() -> None:
    special = _evidence([_reading(72.0, ready=True, fill=1.0)])
    assert special is not None
    assert special.state_at_anchor is SpecialGaugeState.READY
    view, support = _view_and_support(special)
    assert "last_ready" not in view["special"]
    assert "last ready" not in support


def test_no_ready_reading_has_no_last_ready() -> None:
    special = _evidence([_reading(71.0, ready=False), _reading(72.0, ready=False)])
    assert special is not None
    assert special.last_ready_before_anchor is None


def test_ready_only_before_lookback_window_is_absent() -> None:
    special = _evidence([_reading(60.0, ready=True), _reading(72.0, ready=False)])
    assert special is not None
    assert special.last_ready_before_anchor is None


def test_ready_only_while_dead_or_counting_down_is_absent() -> None:
    snapshots = _snapshots(
        [(60.0, 66.0, "alive"), (66.0, 68.0, "dead"), (68.0, 70.0, "countdown"),
         (70.0, 76.0, "alive")]
    )
    readings = [
        _reading(67.0, ready=True),
        _reading(69.0, ready=True),
        _reading(72.0, ready=False),
    ]
    special = _evidence(readings, snapshots)
    assert special is not None
    assert special.last_ready_before_anchor is None
    assert special.ready_onsets == []


def test_multiple_ready_runs_use_latest_valid() -> None:
    readings = [
        _reading(66.0, ready=True),
        _reading(67.0, ready=False),
        _reading(69.5, ready=True),
        _reading(70.0, ready=True),
        _reading(72.0, ready=False),
    ]
    special = _evidence(readings)
    assert special is not None
    assert special.last_ready_before_anchor is not None
    assert special.last_ready_before_anchor.video_time == 70.0


def test_lifecycle_comes_from_each_reading_timestamp() -> None:
    times = [71.0, 72.5, 74.0, 80.0, 83.0, 86.0]
    tagged = contextualize_special_readings(
        [_reading(t, ready=False) for t in times], _ALIVE_THEN_DEAD
    )
    assert [r.lifecycle for r in tagged] == [
        "alive", "dead", "dead", "countdown", "awaiting_control", "alive",
    ]
    assert [r.coaching_valid for r in tagged] == [True, True, False, False, False, True]


def test_lifecycle_uses_nearest_snapshot_within_half_cadence_only() -> None:
    tagged = contextualize_special_readings(
        [_reading(71.2, ready=False), _reading(95.0, ready=False)], _ALIVE_THEN_DEAD
    )
    assert tagged[0].lifecycle == "alive"
    assert tagged[1].lifecycle == "unknown"
    assert tagged[1].coaching_valid is True


def test_raw_readings_are_not_mutated() -> None:
    raw = [_reading(71.0, ready=True), _reading(80.5, ready=True)]
    before = [r.model_dump() for r in raw]
    special = _evidence(raw)
    assert special is not None
    assert [r.model_dump() for r in raw] == before
    assert all(r.lifecycle is None and r.coaching_valid is None for r in raw)
    assert [r.ready for r in special.observations] == [True, True]


def test_ready_run_ignores_invalid_readings() -> None:
    snapshots = _snapshots(
        [(60.0, 71.0, "alive"), (71.0, 72.0, "dead"), (72.0, 76.0, "alive")]
    )
    readings = [_reading(71.5, ready=True), _reading(72.0, ready=True)]

    def active(snaps: list[GameStateSnapshot] | None) -> bool:
        special = _evidence(readings, snaps)
        assert special is not None
        unit = score_death_candidate(_coach_input(special))
        return next(
            f.active for f in unit.factors if f.factor_id == "death_special_ready"
        )

    # Without lifecycle both readings count; 71.5 is a non-first dead frame.
    assert active(None) is True
    assert active(snapshots) is False


_TWO_LIVES = _snapshots(
    [(60.0, 70.0, "alive"), (70.0, 72.0, "dead"), (72.0, 74.0, "countdown"),
     (74.0, 75.0, "awaiting_control"), (75.0, 90.0, "alive")]
)


def _by_time(
    readings: list[SpecialReading], **kwargs: float
) -> dict[float, SpecialReading]:
    tagged = contextualize_special_readings(readings, _TWO_LIVES, **kwargs)
    return {r.video_time: r for r in tagged}


def test_life_starts_at_first_snapshot_and_each_return_to_alive() -> None:
    assert _life_starts(_TWO_LIVES) == [60.0, 75.0]
    assert _life_starts([]) == []


def test_unknown_neither_starts_nor_ends_a_life() -> None:
    snapshots = _snapshots(
        [(60.0, 62.0, "alive"), (62.0, 63.0, "unknown"), (63.0, 65.0, "alive"),
         (65.0, 66.0, "dead"), (66.0, 67.0, "unknown"), (67.0, 69.0, "alive")]
    )
    assert _life_starts(snapshots) == [60.0, 67.0]


def test_prompt_only_ready_after_respawn_without_ring_is_uncorroborated() -> None:
    tagged = _by_time([_reading(80.0, ready=True, fill=0.38, prompt=0.83)])
    assert tagged[80.0].coaching_valid is False
    assert tagged[80.0].coaching_invalid_reason == "uncorroborated_prompt_ready"


def test_prompt_only_ready_after_ring_in_same_life_stays_valid() -> None:
    tagged = _by_time(
        [_reading(78.0, ready=True, fill=0.95), _reading(80.0, ready=True, fill=0.6)]
    )
    assert tagged[78.0].coaching_valid is True
    assert tagged[80.0].coaching_valid is True
    assert tagged[80.0].coaching_invalid_reason is None


def test_ring_before_death_does_not_carry_into_next_life() -> None:
    tagged = _by_time(
        [
            _reading(65.0, ready=True, fill=0.95),
            _reading(71.0, ready=False),
            _reading(80.0, ready=True, fill=0.5, prompt=0.9),
        ]
    )
    assert tagged[65.0].coaching_valid is True
    assert tagged[80.0].coaching_invalid_reason == "uncorroborated_prompt_ready"


def test_life_resets_even_without_readings_during_death() -> None:
    tagged = _by_time(
        [_reading(65.0, ready=True, fill=0.95), _reading(80.0, ready=True, fill=0.5)]
    )
    assert tagged[80.0].coaching_valid is False
    assert tagged[80.0].coaching_invalid_reason == "uncorroborated_prompt_ready"


def test_lifecycle_invalid_ring_never_corroborates() -> None:
    tagged = _by_time(
        [_reading(74.5, ready=True, fill=1.0), _reading(76.0, ready=True, fill=0.5)]
    )
    assert tagged[74.5].coaching_invalid_reason == "lifecycle"
    assert tagged[76.0].coaching_invalid_reason == "uncorroborated_prompt_ready"


def test_ring_threshold_comes_from_config_value() -> None:
    readings = [
        _reading(78.0, ready=True, fill=0.90),
        _reading(80.0, ready=True, fill=0.6),
    ]
    default = _by_time(readings)
    strict = _by_time(readings, ring_fill_threshold=0.95)
    assert default[80.0].coaching_valid is True
    assert strict[78.0].coaching_invalid_reason == "uncorroborated_prompt_ready"
    assert strict[80.0].coaching_invalid_reason == "uncorroborated_prompt_ready"


def test_not_ready_readings_are_untouched_by_corroboration() -> None:
    tagged = _by_time([_reading(80.0, ready=False, fill=0.3)])
    assert tagged[80.0].coaching_valid is True
    assert tagged[80.0].coaching_invalid_reason is None


def test_without_snapshots_readings_are_not_contextualized() -> None:
    reading = _reading(80.0, ready=True, fill=0.38)
    special = _evidence([reading], snapshots=None)
    assert special is not None
    (observed,) = [r for r in special.observations if r.video_time == 80.0]
    assert observed.coaching_valid is None
    assert observed.coaching_invalid_reason is None
    assert reading.coaching_valid is None
