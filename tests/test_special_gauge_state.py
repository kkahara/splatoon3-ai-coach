"""Special gauge evidence keeps ready, not ready, and not observed distinct."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splatoon3_ai_coach.analysis.scenario_context import (
    DeathEpisodeContext,
    ScenarioContext,
    ScenarioRelations,
)
from splatoon3_ai_coach.analysis.scenario_evidence import (
    SpecialEvidence,
    SpecialGaugeState,
    SpecialReading,
    build_special_evidence,
    special_gauge_state,
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
from splatoon3_ai_coach.coach.playback_clock import PlaybackClockInfo, PlaybackClockStatus
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig

_DEATH = 95.0
_RUN = Path(__file__).resolve().parents[1] / (
    "analysis/en-preview-full-fast-2026-10-04 21-32-55/coach_inputs"
)


def _reading(when: float, *, visible: bool, ready: bool, fill: float | None = None):
    return SpecialReading(
        video_time=when,
        visible=visible,
        ready=ready,
        fill_fraction=fill,
        confidence=0.85 if visible else 0.3,
        observation_id=f"obs@{when}",
    )


def _evidence(readings: list[SpecialReading]) -> SpecialEvidence | None:
    return build_special_evidence(
        readings,
        window_start=_DEATH - 8.0,
        window_end=_DEATH + 10.0,
        anchor=_DEATH,
        max_gap_seconds=ScenarioBuilderConfig().context_max_gap_seconds,
    )


_REPLAY = PlaybackClockInfo(
    status=PlaybackClockStatus.RATE_MISMATCH,
    game_seconds_per_video_second=1.5,
    video_seconds_per_game_second=1 / 1.5,
    observation_count=100,
    reason="replay",
)


def _coach_input(
    special: SpecialEvidence | None, playback: PlaybackClockInfo | None = None
) -> CoachInput:
    scenario = Scenario(
        scenario_id=f"death_episode:{_DEATH:.3f}",
        scenario_type=ScenarioType.DEATH_EPISODE,
        start_time=_DEATH,
        end_time=_DEATH + 9.5,
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
    return CoachInput(
        primary_scenario=scenario, primary_context=context, playback_clock=playback
    )


def _special_text(coach_input: CoachInput) -> str | None:
    items = build_supporting_evidence(coach_input)
    return next((item.value for item in items if item.label == "Special"), None)


def test_visible_and_ready_is_ready() -> None:
    evidence = _evidence([_reading(94.5, visible=True, ready=True, fill=1.0)])
    assert evidence is not None
    assert evidence.state_at_anchor is SpecialGaugeState.READY
    assert _special_text(_coach_input(evidence)) == "ready, fill=1.00"


def test_visible_and_not_ready_is_not_ready() -> None:
    evidence = _evidence([_reading(94.5, visible=True, ready=False, fill=0.4)])
    assert evidence is not None
    assert evidence.state_at_anchor is SpecialGaugeState.NOT_READY
    assert _special_text(_coach_input(evidence)) == "not ready, fill=0.40"


def test_hidden_gauge_is_not_observed_even_with_ready_false() -> None:
    reading = _reading(94.5, visible=False, ready=False)
    assert reading.state is SpecialGaugeState.NOT_OBSERVED
    evidence = _evidence([reading])
    assert evidence is not None
    text = _special_text(_coach_input(evidence))
    assert text == "not observed"
    assert "not ready" not in text


def test_hidden_gauge_with_ready_true_is_not_ready_evidence() -> None:
    reading = _reading(94.5, visible=False, ready=True)
    assert special_gauge_state(reading) is SpecialGaugeState.NOT_OBSERVED
    unit = score_death_candidate(_coach_input(_evidence([reading, reading])))
    special = [f for f in unit.factors if f.factor_id == "death_special_ready"]
    assert special and not special[0].active


def test_no_special_reading_is_absent_not_not_ready() -> None:
    assert special_gauge_state(None) is SpecialGaugeState.NOT_OBSERVED
    assert _evidence([]) is None
    coach_input = _coach_input(None)
    assert _special_text(coach_input) is None


def test_hidden_nearest_reading_names_the_last_visible_one() -> None:
    evidence = _evidence(
        [
            _reading(92.0, visible=True, ready=True, fill=0.9),
            _reading(93.0, visible=True, ready=False, fill=0.62),
            _reading(94.5, visible=False, ready=False),
        ]
    )
    assert evidence is not None
    assert evidence.state_at_anchor is SpecialGaugeState.NOT_OBSERVED
    assert evidence.last_visible_before_anchor is not None
    assert evidence.last_visible_before_anchor.video_time == 93.0
    text = _special_text(_coach_input(evidence))
    assert text == (
        "not observed (last observed 2.0s of video (playback speed unverified) "
        "before: not ready)"
    )


def _episode_95_readings() -> list[SpecialReading]:
    """Readings recorded around ``death_episode_95.000`` in the audited run."""
    rows = [
        (87.0, True, True, 0.86),
        (87.5, False, False, None),
        (88.0, True, False, 0.48),
        (89.0, True, False, 0.86),
        (90.0, False, False, None),
        (91.5, True, False, 0.81),
        (92.0, True, True, 0.9),
        (92.5, True, False, 0.57),
        (93.0, True, False, 0.62),
        (93.5, False, False, None),
        (94.0, False, False, None),
        (94.5, False, False, None),
        (95.0, False, False, None),
        (95.5, True, False, 0.71),
    ]
    return [
        _reading(when, visible=visible, ready=ready, fill=fill)
        for when, visible, ready, fill in rows
    ]


def test_death_episode_95_hidden_gauge_is_not_reported_as_not_ready() -> None:
    evidence = _evidence(_episode_95_readings())
    assert evidence is not None
    assert evidence.nearest_before_anchor is not None
    assert evidence.nearest_before_anchor.video_time == _DEATH
    assert evidence.state_at_anchor is SpecialGaugeState.NOT_OBSERVED
    coach_input = _coach_input(evidence, _REPLAY)
    text = _special_text(coach_input)
    assert text == "not observed (last observed 3s (game clock) before: not ready)"
    view = build_coach_llm_view(coach_input, score_death_candidate(coach_input))
    assert view.special is not None
    assert view.special["state_at_anchor"] == "not_observed"
    assert view.special["last_visible_before_anchor"]["video_time"] == 93.0
    support = {item.label: item.value for item in view.supporting_evidence}
    assert support["Special"] == text


@pytest.mark.skipif(
    not (_RUN / "death_episode_95.000.coach_input.json").is_file(),
    reason="audited analysis run is not present",
)
def test_audited_run_episode_95_is_not_observed() -> None:
    payload = json.loads((_RUN / "death_episode_95.000.coach_input.json").read_text())
    saved = CoachInput.model_validate(payload)
    assert saved.primary_context.special is not None
    evidence = _evidence(saved.primary_context.special.observations)
    assert evidence is not None
    assert evidence.state_at_anchor is SpecialGaugeState.NOT_OBSERVED
    assert _special_text(_coach_input(evidence)).startswith("not observed")
