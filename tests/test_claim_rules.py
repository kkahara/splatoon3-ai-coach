"""Claim-family rules R1-R4, R6 and splitter/negation scope (R13).

Each rule has at least one positive (counts) and one negative (does not count)
example. Hedging never exempts; negation and the limitations field do.
"""

from __future__ import annotations

from typing import Any

import pytest

from splatoon3_ai_coach.coach.claim_rules import (
    blocking_hits,
    claim_rule_hits,
    ja_claim_hits,
    translation_introduced_hits,
)
from splatoon3_ai_coach.coach.evidence_contract import split_sentences


def _rules(text: str, view: dict[str, Any] | None = None, **fields: Any) -> set[str]:
    assessment = {"assessment": text, "recommendations": [], "limitations": [], **fields}
    return {hit["rule"] for hit in blocking_hits(claim_rule_hits(assessment, view))}


@pytest.mark.parametrize(
    "text",
    [
        "Not checking the map caused the death.",
        "This lack of map awareness may have contributed to the splat.",
        "You died because you pushed alone.",
        "If you had checked the map, you would have survived.",
        "The quick re-death possibly resulted in a lost trade.",
    ],
)
def test_r1_causal_and_counterfactual_claims_count(text: str) -> None:
    assert "R1" in _rules(text)


@pytest.mark.parametrize(
    "text",
    [
        "No map overlay was observed before the death.",
        "You died again 6.0s after respawning.",
        "The death occurred during the final 30 seconds.",
        "The evidence cannot show whether the map check caused the death.",
    ],
)
def test_r1_associations_and_negations_do_not_count(text: str) -> None:
    assert "R1" not in _rules(text)


def test_r1_needs_a_death_subject() -> None:
    assert "R1" not in _rules("Inking turf leads to more special gauge.")


def test_r1_limitation_field_is_exempt() -> None:
    assert _rules("", limitations=["We cannot say what caused the death."]) == set()


def test_r2_roster_claims_count_only_while_capability_is_off() -> None:
    text = "You were in a 2v4 and outnumbered when you died."
    assert "R2" in _rules(text, {"roster_evidence": False})
    assert "R2" in _rules(text, None)
    assert "R2" not in _rules(text, {"roster_evidence": True})


def test_r2_ordinary_numbers_do_not_count() -> None:
    assert "R2" not in _rules("You died 2.5s after a splat at 1:04.")


def test_r2_count_with_advantage_wording_counts() -> None:
    assert "R2" in _rules("A single player died at 4\u20113 advantage.")


@pytest.mark.parametrize(
    "text",
    [
        "No roster information is available, so it is impossible to tell.",
        "Ally alive counts at the time of death are unknown.",
        "We cannot determine whether teammates were splatted.",
    ],
)
def test_state_family_abstentions_do_not_count(text: str) -> None:
    assert _rules(text) & {"R2", "R3"} == set()


@pytest.mark.parametrize(
    ("japanese", "rule"),
    [
        ("マップを見なかったことがデスの原因です。", "R1"),
        ("マップを確認していれば防げたデスです。", "R1"),
        ("人数不利の状況でやられました。", "R2"),
        ("味方が全滅していました。", "R3"),
        ("スペシャルを使うべきでした。", "R4"),
        ("前に出すぎたミスです。", "R6"),
    ],
)
def test_japanese_family_cues(japanese: str, rule: str) -> None:
    assert rule in {hit["rule"] for hit in ja_claim_hits(japanese)}


def test_neutral_japanese_has_no_family_cues() -> None:
    neutral = "デスの 2.0 秒前にキルを取りました。インク残量が少ない状態でした。"
    assert ja_claim_hits(neutral) == []


def test_translation_introduced_hits_ignore_families_in_the_source() -> None:
    english = "The evidence does not show what caused the death."
    assert translation_introduced_hits(english, "デスの原因は分かりません。") == []
    added = translation_introduced_hits("You had low ink.", "インク不足が原因です。")
    assert [hit["rule"] for hit in added] == ["R1"]


@pytest.mark.parametrize(
    "text",
    [
        "Your teammates were all splatted at that moment.",
        "You were the last player alive.",
        "You pushed with no team support.",
        "The enemy was flanking from the left.",
    ],
)
def test_r3_teammate_and_opponent_state_claims_count(text: str) -> None:
    assert "R3" in _rules(text)


def test_r3_neutral_teammate_mentions_do_not_count() -> None:
    assert "R3" not in _rules(
        "Check where your teammates are on the map after respawning."
    )


@pytest.mark.parametrize(
    "text",
    [
        "You should have used your special before the push.",
        "You did not use your special.",
        "Your special was wasted.",
    ],
)
def test_r4_special_usage_claims_count(text: str) -> None:
    assert "R4" in _rules(text)


def test_r4_observed_gauge_state_does_not_count() -> None:
    assert "R4" not in _rules("Special was ready for 4.0s before the death.")


@pytest.mark.parametrize(
    ("text", "subcategory"),
    [
        ("You overextended into their side.", "positioning"),
        ("You chased too far after the splat.", "pursuit"),
        ("That push was too aggressive.", "aggression"),
        ("You should have retreated after the splat.", "counterfactual_action"),
        ("That was an avoidable death.", "outcome_judgment"),
    ],
)
def test_r6_judgments_count_unless_authorized(text: str, subcategory: str) -> None:
    assert "R6" in _rules(text)
    authorized = {"claim_basis": [{"claim_id": "x", "authorizes": [subcategory]}]}
    assert "R6" not in _rules(text, authorized)


def test_r6_neutral_recommendation_does_not_count() -> None:
    assert _rules("", recommendations=["Open the map once after respawning."]) == set()


def test_r13_decimals_do_not_split_sentences() -> None:
    pieces = split_sentences("Died 3.5s later. Map shown.")
    sentences = [text.strip() for _, text in pieces]
    assert sentences == ["Died 3.5s later.", "Map shown."]


def test_r13_negation_is_clause_scoped() -> None:
    assert "R6" in _rules("You did not wait, so you overextended.")
    assert "R6" not in _rules("You did not overextend.")


def test_r13_epistemic_negation_spans_the_sentence() -> None:
    text = "The evidence cannot establish, from these samples, that you overextended."
    assert "R6" not in _rules(text)


def test_hedging_does_not_exempt() -> None:
    hits = claim_rule_hits(
        {"assessment": "You might have overextended."}, None
    )
    assert any(hit["counts_as_violation"] for hit in hits)
