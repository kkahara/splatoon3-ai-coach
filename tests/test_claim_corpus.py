"""Permanent saved-output corpus: rule labels, missed opportunities, ranking.

Each entry in ``tests/fixtures/claim_corpus`` is a frozen CoachInput plus the
model output it produced. Views are rebuilt with current code, so a rule or
prompt-contract change shows up here as a label diff. Expected labels were
set deliberately from the claim-validity audit (L1→R2, L2→R1, L3→R5, L4→R9,
L6→R8); change them only with a reason recorded in the entry's ``note``.
"""

from __future__ import annotations

from typing import Any

import pytest
from claim_corpus import corpus_unit, corpus_view, evaluate, load_corpus, metrics

from splatoon3_ai_coach.coach.coaching_candidates import (
    CoachingCandidate,
    rank_candidates,
)

CORPUS = {entry["id"]: entry for entry in load_corpus()}
MISSED = {
    "5d42_145.000": "death_own_splat_before",
    "6030_188.500": "death_final_30s",
    "1828_206.500": "death_final_30s",
}


def _with_output(entry: dict[str, Any], **output: Any) -> dict[str, Any]:
    base = {"assessment": "", "recommendations": [], "limitations": []}
    return {**entry, "model_output": {**base, **output}}


def test_corpus_has_the_audited_outputs() -> None:
    assert len(CORPUS) == 23


@pytest.mark.parametrize("entry_id", sorted(CORPUS))
def test_entry_rules_match_expected_labels(entry_id: str) -> None:
    entry = CORPUS[entry_id]
    result = evaluate(entry)
    assert sorted({hit["rule"] for hit in result["hits"]}) == entry["expected"]["rules"]


@pytest.mark.parametrize("entry_id", sorted(CORPUS))
def test_entry_coachable_claims_match_expected(entry_id: str) -> None:
    entry = CORPUS[entry_id]
    view = corpus_view(entry)
    coachable = [b["claim_id"] for b in view["claim_basis"] if b["coachable"]]
    assert coachable == entry["expected"]["coachable_claims"]


def test_causal_map_output_is_blocked_by_r1() -> None:
    result = evaluate(CORPUS["9d47_72.500"])
    assert result["status"] == "blocked_unsupported_claim"
    assert any(hit["rule"] == "R1" for hit in result["hits"])


def test_roster_claims_block_while_capability_is_off() -> None:
    for entry_id in ("f46b_65.000", "3f7b_208.500", "162a_130.500", "1ea3_72.000"):
        assert evaluate(CORPUS[entry_id])["status"] == "blocked_unsupported_claim"


def test_roster_abstention_is_not_blocked() -> None:
    entry = _with_output(
        CORPUS["1828_206.500"],
        assessment="No roster information is available for this death.",
    )
    assert not [h for h in evaluate(entry)["hits"] if h["rule"] == "R2"]


@pytest.mark.parametrize(("entry_id", "claim_id"), sorted(MISSED.items()))
def test_missed_opportunity_units_carry_the_claim(entry_id: str, claim_id: str) -> None:
    view = corpus_view(CORPUS[entry_id])
    basis = {b["claim_id"]: b for b in view["claim_basis"]}
    assert claim_id in basis
    assert basis[claim_id]["coachable"] is True


@pytest.mark.parametrize("entry_id", sorted(MISSED))
def test_canned_abstention_fails_r11_on_missed_units(entry_id: str) -> None:
    entry = _with_output(
        CORPUS[entry_id],
        assessment="No coaching claim is supported by the available evidence.",
    )
    assert any(hit["rule"] == "R11" for hit in evaluate(entry)["hits"])


def test_canned_abstention_passes_r11_without_coachable_claims() -> None:
    entry = _with_output(
        CORPUS["9d47_102.500"],
        assessment="No coaching claim is supported by the available evidence.",
    )
    assert not [h for h in evaluate(entry)["hits"] if h["rule"] == "R11"]


def test_missed_units_rank_with_roster_off() -> None:
    candidates = []
    for entry in CORPUS.values():
        _, unit = corpus_unit(entry)
        candidates.append(
            CoachingCandidate(
                candidate_id=entry["id"],
                candidate_type=unit.candidate_type,
                video_time=unit.video_time,
                importance_score=unit.importance_score,
                factors=unit.factors,
            )
        )
    ranked = rank_candidates(
        candidates, max_llm_units=len(candidates), require_positive_score=True
    )
    by_id = {c.candidate_id: c for c in ranked}
    for entry_id, claim_id in MISSED.items():
        candidate = by_id[entry_id]
        assert candidate.selected_for_llm
        contributing = {f.factor_id for f in candidate.factors if f.contribution > 0}
        assert claim_id in contributing
        assert not contributing & {"death_while_outnumbered", "death_last_ally_alive"}


def test_metrics_report_per_category_counts() -> None:
    report = metrics([evaluate(entry) for entry in CORPUS.values()])
    assert report["entries"] == 23
    assert report["hits"]["causal_R1"] == 1
    assert report["hits"]["roster_R2"] > 0
    assert report["hits"]["missed_claim_R11"] == 0
    assert set(report["statuses"]) <= {
        "validated",
        "blocked_unsupported_claim",
        "contains_unverified_wording",
    }
    used = report["coachable_used_per_unit"]
    assert 0 <= used <= report["coachable_available_per_unit"]
