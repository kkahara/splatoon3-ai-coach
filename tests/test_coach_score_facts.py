"""Coaching-layer Splat Zones count facts, limits and LLM projection."""

from __future__ import annotations

from splatoon3_ai_coach.analysis.scenario_context import ScenarioContext
from splatoon3_ai_coach.analysis.score_context import ScoreEvidence, ScoreSample
from splatoon3_ai_coach.coach.evidence_contract import claim_contains_prohibited_language
from splatoon3_ai_coach.coach.evidence_limits import score_evidence_limits
from splatoon3_ai_coach.coach.llm_view import _project_score
from splatoon3_ai_coach.coach.score_facts import derive_score_facts


def _sample(t: float, ally: int | None, opp: int | None, **fields: object) -> ScoreSample:
    return ScoreSample(
        video_time=t,
        ally_remaining=ally,
        ally_score_quality="observed" if ally is not None else "unknown",
        opponent_remaining=opp,
        opponent_score_quality="observed" if opp is not None else "unknown",
        **fields,
    )


def _ctx(score: ScoreEvidence | None) -> ScenarioContext:
    return ScenarioContext(scenario_id="death_episode:20.000", score=score)


def test_remaining_diff_and_counts_before_progress() -> None:
    pre = _sample(
        19.5,
        42,
        30,
        ally_penalty=7,
        ally_penalty_quality="observed",
        opponent_penalty_quality="not_shown",
    )
    back = _sample(14.5, 42, 35)
    facts = derive_score_facts(
        _ctx(ScoreEvidence(pre_death=pre, lookback=back)), battle_mode_id="splat_zones"
    )
    assert facts.sample_label == "pre_death"
    assert facts.remaining_diff == 30 - 42
    assert facts.ally_count_before_progress == 7
    assert facts.opponent_count_before_progress == 0
    assert facts.penalty_not_shown_treated_as_zero is True
    assert facts.ally_work_remaining == 49
    assert facts.opponent_work_remaining == 30
    assert facts.ally_counter_decreased_before_death is False
    assert facts.opponent_counter_decreased_before_death is True
    assert facts.source_path == "primary_context.score.pre_death"


def test_unknown_penalty_is_not_zero() -> None:
    facts = derive_score_facts(
        _ctx(ScoreEvidence(pre_death=_sample(19.5, 42, 30))), battle_mode_id="splat_zones"
    )
    assert facts.ally_count_before_progress is None
    assert facts.ally_work_remaining is None
    assert facts.penalty_not_shown_treated_as_zero is False
    assert facts.ally_counter_decreased_before_death is None


def test_partial_count_has_no_diff_and_falls_back_to_anchor() -> None:
    facts = derive_score_facts(
        _ctx(ScoreEvidence(at_anchor=_sample(20.0, 42, None))),
        battle_mode_id="splat_zones",
    )
    assert facts.sample_label == "at_anchor"
    assert facts.remaining_diff is None
    codes = [limit.code for limit in score_evidence_limits(facts)]
    assert codes == ["score_count_unavailable", "score_not_zone_holder"]


def test_non_splat_zones_records_mode_only() -> None:
    evidence = ScoreEvidence(pre_death=_sample(19.5, 42, 30))
    facts = derive_score_facts(_ctx(evidence), battle_mode_id="rainmaker")
    assert facts.video_time is None and facts.remaining_diff is None
    limits = score_evidence_limits(facts)
    assert [limit.code for limit in limits] == ["score_not_splat_zones"]
    assert "rainmaker" in limits[0].statement
    assert _project_score(facts) is None


def test_unresolved_mode_is_not_splat_zones() -> None:
    facts = derive_score_facts(_ctx(None), battle_mode_id=None)
    assert "unresolved" in score_evidence_limits(facts)[0].statement


def test_limits_include_penalty_zero_statement_and_stay_contract_safe() -> None:
    pre = _sample(19.5, 42, 30, opponent_penalty_quality="not_shown")
    facts = derive_score_facts(
        _ctx(ScoreEvidence(pre_death=pre)), battle_mode_id="splat_zones"
    )
    limits = score_evidence_limits(facts)
    assert [limit.code for limit in limits] == [
        "score_not_zone_holder",
        "penalty_not_shown_as_zero",
    ]
    for limit in limits:
        assert not claim_contains_prohibited_language(limit.statement), limit.statement


def test_llm_projection_copies_facts() -> None:
    facts = derive_score_facts(
        _ctx(ScoreEvidence(pre_death=_sample(19.5, 42, 30))), battle_mode_id="splat_zones"
    )
    view = _project_score(facts)
    assert view is not None
    assert view["remaining_diff"] == -12
    assert view["source_path"] == "primary_context.score.pre_death"
