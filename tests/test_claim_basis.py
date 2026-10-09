"""Claim definitions, per-unit claim_basis and undetermined topics."""

from __future__ import annotations

from coach_artifact_helpers import map_ctx, video_durations

from splatoon3_ai_coach.analysis.scenario_context import (
    DeathEpisodeContext,
    ScenarioContext,
    ScenarioRelations,
    TimelineContext,
)
from splatoon3_ai_coach.analysis.scenario_models import (
    Scenario,
    ScenarioOutcome,
    ScenarioType,
)
from splatoon3_ai_coach.coach.claim_basis import (
    C_LEVEL_WHITELIST,
    CLAIM_DEFINITIONS,
    RECOMMENDATION_TEMPLATES,
    ClaimLevel,
    UndeterminedTopic,
    build_claim_basis,
    build_undetermined,
)
from splatoon3_ai_coach.coach.claim_integrity import recommendation_grounding_hits
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.death_importance import score_death_candidate
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view

_DEATH = 100.0


def _input(*, prev_gap: float | None = None, roster: bool = False) -> CoachInput:
    scenario = Scenario(
        scenario_id=f"death_episode:{_DEATH:.3f}",
        scenario_type=ScenarioType.DEATH_EPISODE,
        start_time=_DEATH,
        end_time=_DEATH + 8.0,
        event_ids=[f"DEATH@{_DEATH:.3f}"],
        confidence=1.0,
        outcome=ScenarioOutcome.DIED,
    )
    context = ScenarioContext(
        scenario_id=scenario.scenario_id,
        timeline=TimelineContext(duration=8.0, time_since_previous_death=prev_gap),
        death_episode=DeathEpisodeContext(death_time=_DEATH, complete=True),
        map=map_ctx(False),
        relations=ScenarioRelations(),
    )
    return CoachInput(
        primary_scenario=scenario,
        primary_context=context,
        durations=video_durations(
            time_since_previous_death=prev_gap, death_to_active_again=9.0
        ),
        roster_evidence=roster,
    )


def _basis(coach_input: CoachInput) -> dict[str, ClaimLevel]:
    unit = score_death_candidate(coach_input)
    return {e.claim_id: e.level for e in build_claim_basis(coach_input, unit)}


def test_every_definition_has_a_frame_and_a_path() -> None:
    ids = [c.claim_id for c in CLAIM_DEFINITIONS]
    assert len(ids) == len(set(ids))
    for claim in CLAIM_DEFINITIONS:
        assert claim.frame and claim.evidence_paths
        assert claim.level is not ClaimLevel.D


def test_no_level_c_claim_is_whitelisted_today() -> None:
    assert C_LEVEL_WHITELIST == frozenset()


def test_basis_lists_eligible_a_and_b_claims() -> None:
    basis = _basis(_input(prev_gap=12.0))
    assert basis["death_quick_redeath"] is ClaimLevel.A
    assert basis["death_out_of_play"] is ClaimLevel.A
    assert basis["death_map_overlay_before_false"] is ClaimLevel.A
    assert basis["principle_redeath_out_of_play"] is ClaimLevel.B
    assert basis["process_map_after_respawn"] is ClaimLevel.B


def test_b_claims_need_their_a_claim() -> None:
    basis = _basis(_input(prev_gap=None))
    assert "death_quick_redeath" not in basis
    assert "principle_redeath_out_of_play" not in basis


def test_roster_is_undetermined_only_while_the_capability_is_off() -> None:
    off = _input()
    topics = {e.topic for e in build_undetermined(off, [])}
    assert UndeterminedTopic.ROSTER in topics
    assert UndeterminedTopic.CAUSE_OF_DEATH in topics
    on = _input(roster=True)
    assert UndeterminedTopic.ROSTER not in {e.topic for e in build_undetermined(on, [])}


def test_view_exposes_basis_and_undetermined_with_eligible_alternatives() -> None:
    coach_input = _input(prev_gap=12.0)
    view = build_coach_llm_view(coach_input, score_death_candidate(coach_input))
    ids = {entry["claim_id"] for entry in view.claim_basis}
    assert "death_quick_redeath" in ids
    assert not any(claim_id.startswith("death_while_") for claim_id in ids)
    cause = next(u for u in view.undetermined if u["topic"] == "cause_of_death")
    assert set(cause["alternative_claim_ids"]) <= ids
    assert "death_quick_redeath" in cause["alternative_claim_ids"]


def test_view_carries_rec_frames_that_r10_accepts() -> None:
    coach_input = _input(prev_gap=12.0)
    view = build_coach_llm_view(coach_input, score_death_candidate(coach_input))
    basis = {entry["claim_id"]: entry for entry in view.claim_basis}
    redeath = basis["principle_redeath_out_of_play"]
    assert redeath["rec_frames"] == [RECOMMENDATION_TEMPLATES["rec_redeath_reset"].frame]
    assert basis["death_quick_redeath"]["rec_frames"] == []
    for entry in view.claim_basis:
        assert len(entry["rec_frames"]) == len(entry["rec_template_ids"])
        for frame in entry["rec_frames"]:
            assessment = {"recommendations": [frame]}
            assert recommendation_grounding_hits(assessment, view.model_dump()) == []


def test_own_splat_and_low_ink_claims() -> None:
    from splatoon3_ai_coach.analysis.low_ink_context import (
        LowInkEvidence,
        LowInkInterval,
    )

    base = _input()
    death = base.primary_context.death_episode.model_copy(
        update={"time_since_previous_splat": 1.0}
    )
    low_ink = LowInkEvidence(
        intervals=[
            LowInkInterval(event_id="LOW_INK@97", start_time=97.0, end_time=100.0)
        ]
    )
    context = base.primary_context.model_copy(
        update={"death_episode": death, "low_ink": low_ink}
    )
    coach_input = base.model_copy(update={"primary_context": context})
    basis = _basis(coach_input)
    assert basis["death_own_splat_before"] is ClaimLevel.A
    assert basis["death_low_ink"] is ClaimLevel.A
    assert basis["process_ink_before_engaging"] is ClaimLevel.B
    view = build_coach_llm_view(coach_input, score_death_candidate(coach_input))
    assert view.low_ink is not None and view.low_ink["at_death"] is True


def test_low_ink_after_the_death_is_not_at_death() -> None:
    from splatoon3_ai_coach.analysis.low_ink_context import (
        LowInkEvidence,
        LowInkInterval,
    )

    base = _input()
    low_ink = LowInkEvidence(
        intervals=[
            LowInkInterval(event_id="LOW_INK@104", start_time=104.0, end_time=106.0)
        ]
    )
    context = base.primary_context.model_copy(update={"low_ink": low_ink})
    coach_input = base.model_copy(update={"primary_context": context})
    assert "death_low_ink" not in _basis(coach_input)
