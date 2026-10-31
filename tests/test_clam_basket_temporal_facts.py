"""Pure timestamp mathematics for bounded Clam basket temporal facts."""

import pytest

from splatoon3_ai_coach.analysis.basket_open_runs import (
    BasketOpenRun,
    BasketOpenRunsArtifact,
    BasketSample,
)
from splatoon3_ai_coach.analysis.clam_basket_temporal_facts import (
    basket_source_run_id,
    build_basket_action_temporal_relation,
    build_basket_closing_time_bounds,
    build_basket_observed_open_span,
)


def _sample(time: float, label: str) -> BasketSample:
    return BasketSample(
        video_time=time, frame_id=f"frame-{label}", evidence_ids=[f"evidence-{label}"]
    )


def _run(
    *,
    first: float = 12,
    last: float = 18,
    preceding: float | None = 10,
    following: float | None = 20,
    left_reason: str | None = None,
    right_reason: str | None = None,
) -> BasketOpenRun:
    return BasketOpenRun(
        first_observed_open=_sample(first, "first"),
        last_observed_open=_sample(last, "last"),
        preceding_closed=_sample(preceding, "preceding")
        if preceding is not None
        else None,
        following_closed=_sample(following, "following")
        if following is not None
        else None,
        left_censor_reason=left_reason,
        right_censor_reason=right_reason,
    )


def _artifact(
    run: BasketOpenRun,
    *,
    availability: str = "available",
    mode: str | None = "clam_blitz",
) -> BasketOpenRunsArtifact:
    return BasketOpenRunsArtifact(
        analysis_id="analysis-1",
        source_manifest_sha256="abc123",
        battle_mode_id=mode,
        availability=availability,
        limitations=["unrelated_match_gap"] if availability == "partial" else [],
        runs=[run],
    )


def test_observed_span_and_explicit_transition_endpoints() -> None:
    run = _run()
    fact = build_basket_observed_open_span(_artifact(run), run)
    assert fact.first_observed_open_time == 12
    assert fact.last_observed_open_time == 18
    assert fact.observed_span_seconds == 6
    assert fact.opening_transition_bounds.model_dump() == {
        "lower_time_exclusive": 10.0,
        "upper_time_inclusive": 12.0,
        "lower_frame_id": "frame-preceding",
        "upper_frame_id": "frame-first",
        "lower_evidence_ids": ["evidence-preceding"],
        "upper_evidence_ids": ["evidence-first"],
    }
    assert fact.closing_transition_bounds.lower_time_exclusive == 18
    assert fact.closing_transition_bounds.upper_time_inclusive == 20


def test_single_sample_span_is_zero_not_a_duration_claim() -> None:
    run = _run(first=12, last=12)
    fact = build_basket_observed_open_span(_artifact(run), run)
    assert fact.observed_span_seconds == 0
    assert not hasattr(fact, "open_duration")
    assert not hasattr(fact, "remaining_time")


def test_source_partial_does_not_make_usable_facts_partial() -> None:
    run = _run()
    artifact = _artifact(run, availability="partial")
    span = build_basket_observed_open_span(artifact, run)
    closing = build_basket_closing_time_bounds(artifact, run)
    assert span.source_artifact_availability == "partial"
    assert span.source_artifact_limitations == ["unrelated_match_gap"]
    assert span.fact_availability == "available"
    assert closing.fact_availability == "available"


@pytest.mark.parametrize("reason", ["unknown_break", "match_end", "video_end"])
def test_right_censored_closing_is_explicitly_unavailable(reason: str) -> None:
    run = _run(following=None, right_reason=reason)
    artifact = _artifact(run)
    span = build_basket_observed_open_span(artifact, run)
    closing = build_basket_closing_time_bounds(artifact, run)
    assert span.fact_availability == "available"
    assert span.closing_transition_bounds is None
    assert span.right_censor_reason == reason
    assert closing.fact_availability == "unavailable"
    assert closing.bounds is None and closing.right_censor_reason == reason
    assert closing.fact_limitations == ["closing_transition_right_censored"]


def test_left_censored_span_has_no_opening_bounds() -> None:
    run = _run(preceding=None, left_reason="no_prior_closed")
    fact = build_basket_observed_open_span(_artifact(run), run)
    assert fact.opening_transition_bounds is None
    assert fact.left_censor_reason == "no_prior_closed"


@pytest.mark.parametrize(
    ("time", "expected"),
    [
        (9, "at_or_before_preceding_closed_observation"),
        (10, "at_or_before_preceding_closed_observation"),
        (11, "within_opening_transition_bounds"),
        (12, "at_observed_open_boundary"),
        (15, "between_observed_open_boundaries_continuity_unproven"),
        (18, "at_observed_open_boundary"),
        (19, "within_closing_transition_bounds"),
        (20, "at_or_after_following_closed_observation"),
        (21, "at_or_after_following_closed_observation"),
    ],
)
def test_action_relation_endpoint_mathematics(time: float, expected: str) -> None:
    """Exact upper samples use observed-state labels despite inclusive bounds."""
    run = _run()
    fact = build_basket_action_temporal_relation(
        _artifact(run), run, action_reference_id="synthetic", action_video_time=time
    )
    assert fact.relation == expected
    assert fact.fact_limitations == []


@pytest.mark.parametrize(
    ("run", "time", "expected"),
    [
        (
            _run(preceding=None, left_reason="unknown_break"),
            11,
            "indeterminate_left_censored",
        ),
        (
            _run(following=None, right_reason="unknown_break"),
            19,
            "indeterminate_right_censored",
        ),
    ],
)
def test_action_relation_censored_sides(
    run: BasketOpenRun, time: float, expected: str
) -> None:
    fact = build_basket_action_temporal_relation(
        _artifact(run), run, action_reference_id="synthetic", action_video_time=time
    )
    assert fact.relation == expected
    assert fact.fact_limitations == ["relation_limited_by_censored_run_boundary"]


def test_source_run_id_is_deterministic_and_source_sensitive() -> None:
    run = _run()
    artifact = _artifact(run)
    first = basket_source_run_id(artifact, run)
    assert first == basket_source_run_id(artifact.model_copy(deep=True), run)
    changed = artifact.model_copy(update={"source_manifest_sha256": "changed"})
    assert first != basket_source_run_id(changed, run)
    changed_run = _run(first=13)
    changed_artifact = _artifact(changed_run)
    assert first != basket_source_run_id(changed_artifact, changed_run)


@pytest.mark.parametrize("availability", ["unavailable"])
def test_unavailable_source_cannot_produce_facts(availability: str) -> None:
    run = _run()
    artifact = _artifact(run, availability=availability)
    with pytest.raises(ValueError, match="unavailable"):
        build_basket_observed_open_span(artifact, run)


@pytest.mark.parametrize("mode", [None, "splat_zones"])
def test_mode_gate_is_fail_closed(mode: str | None) -> None:
    run = _run()
    with pytest.raises(ValueError, match="Clam Blitz"):
        build_basket_observed_open_span(_artifact(run, mode=mode), run)


def test_foreign_run_is_rejected() -> None:
    source_run = _run()
    foreign_run = _run(first=13)
    with pytest.raises(ValueError, match="not present"):
        build_basket_observed_open_span(_artifact(source_run), foreign_run)
