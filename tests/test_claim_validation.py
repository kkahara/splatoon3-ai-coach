"""Validator rules: causal map claims, map recommendations, evidence paths."""

from __future__ import annotations

import pytest

from splatoon3_ai_coach.coach.claim_catalog import (
    FACTOR_ANNOTATIONS,
    DeathImportanceFactorId,
)
from splatoon3_ai_coach.coach.claim_validation import (
    causal_map_claim_hits,
    resolve_evidence_reference,
    unsupported_recommendation_hits,
    validate_assessment,
)
from splatoon3_ai_coach.coach.coach import annotate_claim_flags
from splatoon3_ai_coach.coach.llm_client import CoachingAssessment

_MAP_FACTOR = DeathImportanceFactorId.DEATH_MAP_OVERLAY_BEFORE_FALSE

VIEW = {
    "schema_version": 2,
    "unit": {"scenario_id": "death_episode:72.500"},
    "importance": {"active_factor_ids": [_MAP_FACTOR.value]},
    "death": {
        "death_time_video": 72.5,
        "death_to_respawn": {"video_seconds": 6.0, "value": 6.0, "clock": "video"},
        "source_path": "primary_context.death_episode",
    },
    "map": {
        "map_check_before_death": {"status": "not_observed"},
        "map_checked_while_dead": True,
        "source_path": "primary_context.map",
    },
    "supporting_evidence": [
        {
            "label": "Since previous death",
            "value": "30.0s",
            "source_path": "durations.time_since_previous_death",
        }
    ],
}


@pytest.mark.parametrize(
    "sentence",
    [
        "The lack of a map check could have contributed to the death.",
        "No map check may have led to this splat.",
        "Missing map checks possibly caused the death.",
        "You died because there was no map check.",
        "The death happened due to the lack of map awareness.",
        "This death resulted from not checking the map.",
    ],
)
def test_flags_hedged_causal_map_claim(sentence: str) -> None:
    hits = causal_map_claim_hits(sentence, field="assessment")
    assert len(hits) == 1
    assert hits[0]["counts_as_violation"] is True
    assert hits[0]["classification"] == "assertion"


@pytest.mark.parametrize(
    ("sentence", "field", "classification"),
    [
        (
            "The evidence does not establish that lack of map awareness caused "
            "the death.",
            "assessment",
            "negated",
        ),
        (
            "Lack of map awareness may have contributed to the death.",
            "limitations",
            "limitation",
        ),
    ],
)
def test_negated_and_limitation_causal_claims_are_recorded_not_counted(
    sentence: str, field: str, classification: str
) -> None:
    hits = causal_map_claim_hits(sentence, field=field)
    assert len(hits) == 1
    assert hits[0]["counts_as_violation"] is False
    assert hits[0]["classification"] == classification


@pytest.mark.parametrize(
    "sentence",
    [
        "There is insufficient map evidence to determine whether the player "
        "checked the map.",
        "No map overlay was observed before the death.",
    ],
)
def test_neutral_map_sentences_are_not_causal(sentence: str) -> None:
    assert causal_map_claim_hits(sentence, field="assessment") == []


def test_map_recommendation_flagged_without_catalog_recommendation() -> None:
    assessment = {
        "assessment": "You should check the map before pushing.",
        "recommendations": ["Review map usage before engaging."],
    }
    hits = unsupported_recommendation_hits(
        assessment, active_factor_ids=[_MAP_FACTOR.value]
    )
    assert {hit["field"] for hit in hits} == {"assessment", "recommendations"}
    assert all(hit["counts_as_violation"] for hit in hits)


def test_limitation_wording_is_not_a_recommendation() -> None:
    assessment = {
        "assessment": "It is unclear whether the player checked the map.",
        "limitations": ["Whether the player checked the map is unknown."],
    }
    assert unsupported_recommendation_hits(assessment, active_factor_ids=[]) == []


def test_catalog_recommendation_supports_map_advice() -> None:
    annotations = dict(FACTOR_ANNOTATIONS)
    annotations[_MAP_FACTOR] = annotations[_MAP_FACTOR].model_copy(
        update={"recommendation": "Check the map before re-entering."}
    )
    assessment = {"recommendations": ["Check the map before re-entering."]}
    assert (
        unsupported_recommendation_hits(
            assessment, active_factor_ids=[_MAP_FACTOR.value], annotations=annotations
        )
        == []
    )
    # Same text without the factor active stays unsupported.
    assert unsupported_recommendation_hits(
        assessment, active_factor_ids=[], annotations=annotations
    )


def test_non_map_recommendations_are_not_judged() -> None:
    assessment = {"recommendations": ["Stay near your teammates after respawning."]}
    assert unsupported_recommendation_hits(assessment, active_factor_ids=[]) == []


@pytest.mark.parametrize(
    ("text", "kind", "extra"),
    [
        ("death.death_to_respawn = 6.0", "view_native", None),
        ("map.map_check_before_death.status", "view_native", None),
        (
            "primary_context.death_episode.death_time_video",
            "source_path_ref",
            "primary_context.death_episode",
        ),
        (
            "durations.time_since_previous_death",
            "source_path_ref",
            "durations.time_since_previous_death",
        ),
    ],
)
def test_resolves_evidence_reference(text: str, kind: str, extra: str | None) -> None:
    result = resolve_evidence_reference(VIEW, text)
    assert result["kind"] == kind
    if extra is not None:
        assert result["source_path"] == extra


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("the death timing", "malformed"),
        ("primary_context.map_check_before_death", "unknown_block"),
        ("primary_context.death_episode.time", "unknown_key"),
        ("death.time_of_death", "unknown_key"),
    ],
)
def test_flags_unresolved_evidence_path(text: str, reason: str) -> None:
    result = resolve_evidence_reference(VIEW, text)
    assert result["kind"] == "unresolved"
    assert result["reason"] == reason


def test_validation_status_reflects_each_rule() -> None:
    clean = {
        "assessment": "No map overlay was observed before the death.",
        "evidence_used": ["map.map_check_before_death.status"],
    }
    assert validate_assessment(clean, VIEW)["validation_status"] == "validated"
    assert validate_assessment(clean, None)["validation_status"] == "not_validated"
    bad_path = {**clean, "evidence_used": ["primary_context.death_episode.time"]}
    result = validate_assessment(bad_path, VIEW)
    assert result["validation_status"] == "contains_unverified_wording"
    assert result["unresolved_evidence_paths"][0]["reason"] == "unknown_key"


def test_annotate_claim_flags_never_rewrites_text() -> None:
    text = "The lack of a map check could have contributed to the death."
    assessment = CoachingAssessment(
        assessment=text, evidence_used=["map.map_check_before_death"]
    )
    before = assessment.model_dump_json()
    flags = annotate_claim_flags(assessment, VIEW)
    assert assessment.model_dump_json() == before
    assert flags["hit_count"] == 0
    assert flags["validation_status"] == "blocked_unsupported_claim"
    assert flags["causal_claim_hits"][0]["sentence"] == text
    assert flags["unresolved_evidence_paths"] == []
