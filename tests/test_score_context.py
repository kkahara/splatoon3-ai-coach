"""ScenarioContext.score: sparse observed Splat Zones samples (facts only)."""

from __future__ import annotations

from splatoon3_ai_coach.analysis.scenario_context import build_scenario_contexts
from splatoon3_ai_coach.analysis.scenario_evidence import ScenarioEvidencePack
from splatoon3_ai_coach.analysis.scenarios import build_scenarios
from splatoon3_ai_coach.analysis.score_context import (
    build_score_evidence,
    score_sample_before,
)
from splatoon3_ai_coach.config.models import (
    DeathFactorThresholds,
    ScenarioBuilderConfig,
)
from splatoon3_ai_coach.vision.models import (
    GameEvent,
    GameEventReason,
    GameEventType,
    GameStateSnapshot,
)

SCORE_ID = "vid:result:1:score:score@0.2.0:abc"
TIMER_ID = "vid:result:1:timer:timer@0.2.0:def"


def _snap(t: float, **fields: object) -> GameStateSnapshot:
    return GameStateSnapshot(
        timestamp=t,
        match_phase="in_match",
        evidence_ids=[TIMER_ID, SCORE_ID],
        **fields,
    )


def _both(t: float, ally: int, opp: int, **fields: object) -> GameStateSnapshot:
    return _snap(
        t,
        ally_remaining=ally,
        ally_score_quality="observed",
        opponent_remaining=opp,
        opponent_score_quality="observed",
        **fields,
    )


def test_sample_exposes_only_observed_values_with_quality() -> None:
    snap = _snap(
        10.0,
        ally_remaining=40,
        ally_score_quality="observed",
        opponent_remaining=55,
        opponent_score_quality="held",
        ally_penalty_quality="not_shown",
        opponent_penalty=7,
        opponent_penalty_quality="observed",
    )
    sample = score_sample_before([snap], 10.0, 1.0)
    assert sample is not None
    assert (sample.ally_remaining, sample.ally_score_quality) == (40, "observed")
    assert (sample.opponent_remaining, sample.opponent_score_quality) == (None, "held")
    assert (sample.ally_penalty, sample.ally_penalty_quality) == (None, "not_shown")
    assert (sample.opponent_penalty, sample.opponent_penalty_quality) == (7, "observed")
    assert sample.evidence_ids == [SCORE_ID]


def test_sample_prefers_both_main_observed_and_never_looks_ahead() -> None:
    snaps = [
        _both(9.2, 40, 55),
        _snap(9.8, ally_remaining=39, ally_score_quality="observed"),
        _both(10.5, 38, 54),
    ]
    sample = score_sample_before(snaps, 10.0, 1.0)
    assert sample is not None and sample.video_time == 9.2


def test_no_sample_when_nothing_observed_in_gap() -> None:
    snaps = [
        _both(5.0, 40, 55),
        _snap(9.5, ally_remaining=40, ally_score_quality="held"),
        _snap(9.8, ally_score_quality="rejected_implausible"),
    ]
    assert score_sample_before(snaps, 10.0, 1.0) is None
    assert (
        build_score_evidence(
            snaps,
            anchor=10.0,
            death_time=None,
            pre_death_offset_seconds=0.5,
            max_gap_seconds=1.0,
        )
        is None
    )


def test_pre_death_sample_uses_offset() -> None:
    snaps = [_both(19.0, 30, 20), _both(19.5, 29, 20), _both(20.0, 28, 20)]
    evidence = build_score_evidence(
        snaps,
        anchor=20.0,
        death_time=20.0,
        pre_death_offset_seconds=0.5,
        max_gap_seconds=1.0,
    )
    assert evidence is not None
    assert evidence.at_anchor is not None and evidence.at_anchor.video_time == 20.0
    assert evidence.pre_death is not None and evidence.pre_death.video_time == 19.5


def test_offset_default_matches_roster_offset() -> None:
    assert (
        ScenarioBuilderConfig().score_pre_death_offset_seconds
        == DeathFactorThresholds().roster_pre_death_offset_seconds
    )


def test_scenario_context_carries_score_for_death_episode() -> None:
    events = [
        GameEvent(
            start_time=20.0,
            event_type=GameEventType.DEATH,
            reason=GameEventReason.ALIVE_TO_DEAD,
            confidence=1.0,
        )
    ]
    config = ScenarioBuilderConfig()
    scenarios = build_scenarios(events, config)
    pack = ScenarioEvidencePack(
        state_snapshots=[_both(19.5, 29, 20), _both(20.0, 28, 20)]
    )
    contexts = build_scenario_contexts(events, scenarios, config, evidence=pack)
    score = contexts[0].score
    assert score is not None and score.pre_death is not None
    assert (score.pre_death.ally_remaining, score.pre_death.opponent_remaining) == (
        29,
        20,
    )


def test_scenario_context_score_absent_without_score_fusion() -> None:
    """Non-Splat-Zones snapshots carry no score → no score nest."""
    events = [
        GameEvent(
            start_time=20.0,
            event_type=GameEventType.DEATH,
            reason=GameEventReason.ALIVE_TO_DEAD,
            confidence=1.0,
        )
    ]
    config = ScenarioBuilderConfig()
    scenarios = build_scenarios(events, config)
    pack = ScenarioEvidencePack(state_snapshots=[_snap(19.5), _snap(20.0)])
    contexts = build_scenario_contexts(events, scenarios, config, evidence=pack)
    assert contexts[0].score is None
