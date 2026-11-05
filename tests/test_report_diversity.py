"""Experimental report-level advice-family cap (``coach.report_advice_family_cap``)."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_claim_basis import _input

from splatoon3_ai_coach.coach import report_diversity as rd
from splatoon3_ai_coach.coach.claim_basis import build_claim_basis
from splatoon3_ai_coach.coach.claim_catalog import CoachingUnitResult
from splatoon3_ai_coach.coach.coach_inputs_build import write_coach_inputs
from splatoon3_ai_coach.coach.death_importance import score_death_candidate
from splatoon3_ai_coach.coach.importance_config import (
    importance_config_hash,
    importance_config_payload,
)
from splatoon3_ai_coach.coach.load_analysis import load_coach_analysis_bundle
from splatoon3_ai_coach.coach.match_scoring import score_match
from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import AppConfig, CoachConfig

_REPO = Path(__file__).resolve().parents[1]
_SAVED = _REPO / "analysis" / "en-live-playerview-2026-10-11 22-57-30"
R, M, INK = "rec_redeath_reset", "rec_map_after_respawn", "rec_refill_ink"


def _unit(cid: str, rank: int, score: float, selected: bool) -> CoachingUnitResult:
    return CoachingUnitResult(
        candidate_id=cid,
        candidate_type="death_episode",
        video_time=float(rank),
        importance_score=score,
        rank=rank,
        selected_for_llm=selected,
    )


def _cap(monkeypatch, units, families, top_n=3):
    monkeypatch.setattr(rd, "advice_families", lambda _ci, u: families[u.candidate_id])
    monkeypatch.setattr(
        rd, "evidence_summary", lambda _ci, _u: dict.fromkeys(rd.EVIDENCE_CATEGORIES)
    )
    monkeypatch.setattr(rd, "_coachable_claims", lambda _ci, _u: [])
    inputs = {u.candidate_id: None for u in units}
    return rd.apply_advice_family_cap(units, inputs, max_llm_units=top_n)


def _by_id(diagnostics):
    return {m.candidate_id: m for m in diagnostics.moments}


def test_repeated_family_is_suppressed_and_backfilled_only_with_a_new_family(monkeypatch):
    units = [
        _unit("a", 1, 6.0, True),
        _unit("b", 2, 5.0, True),
        _unit("c", 3, 4.0, True),
        _unit("d", 4, 3.0, False),
        _unit("e", 5, 2.0, False),
    ]
    families = {"a": [R], "b": [R], "c": [], "d": [R], "e": [M]}
    remarked, diag = _cap(monkeypatch, units, families)
    by_id = _by_id(diag)
    assert by_id["a"].reason == "kept_unused_advice_family"
    assert by_id["b"].reason == "suppressed_repeated_advice_family"
    assert by_id["b"].repeats_family_of == ["a"]
    assert by_id["c"].reason == "kept_fact_only"
    assert by_id["d"].reason == "not_promoted_repeated_advice_family"
    assert by_id["e"].reason == "promoted_unused_advice_family"
    assert diag.selected == ["a", "c", "e"]
    assert [u.selected_for_llm for u in remarked] == [True, False, True, False, True]


def test_scores_ranks_and_order_are_unchanged(monkeypatch):
    units = [
        _unit("a", 1, 6.0, True),
        _unit("b", 2, 5.0, True),
        _unit("e", 3, 2.0, False),
    ]
    remarked, _ = _cap(monkeypatch, units, {"a": [R], "b": [R], "e": [M]})
    strip = {"selected_for_llm"}
    assert [u.model_dump(exclude=strip) for u in remarked] == [
        u.model_dump(exclude=strip) for u in units
    ]


def test_fact_only_and_zero_score_units_are_never_promoted(monkeypatch):
    units = [
        _unit("a", 1, 6.0, True),
        _unit("b", 2, 5.0, True),
        _unit("f", 3, 3.0, False),
        _unit("z", 4, 0.0, False),
    ]
    _, diag = _cap(monkeypatch, units, {"a": [R], "b": [R], "f": [], "z": [M]})
    by_id = _by_id(diag)
    assert by_id["f"].reason == "not_promoted_fact_only"
    assert by_id["z"].reason == "not_promoted_zero_score"
    assert diag.selected == ["a"]
    assert diag.changed


def test_a_unit_adding_any_new_family_is_kept(monkeypatch):
    units = [_unit("a", 1, 6.0, True), _unit("b", 2, 5.0, True)]
    _, diag = _cap(monkeypatch, units, {"a": [R], "b": [M, R]})
    assert _by_id(diag)["b"].reason == "kept_unused_advice_family"
    assert not diag.changed


def test_promotion_stops_when_slots_are_full(monkeypatch):
    units = [
        _unit("a", 1, 6.0, True),
        _unit("b", 2, 5.0, False),
        _unit("c", 3, 4.0, False),
    ]
    _, diag = _cap(monkeypatch, units, {"a": [R], "b": [M], "c": [INK]}, top_n=2)
    assert diag.selected == ["a", "b"]
    assert _by_id(diag)["c"].reason == "not_promoted_slots_full"


def test_suppressed_evidence_is_compared_not_judged(monkeypatch):
    units = [_unit("a", 1, 6.0, True), _unit("b", 2, 5.0, True)]
    summaries = {
        "a": {
            "objective": None,
            "roster": {"ally_alive_count": 4, "opponent_alive_count": 4},
            "map_ink": None,
            "special": {"state_at_anchor": "ready"},
            "combat": None,
        },
        "b": {
            "objective": {"ally_remaining": 40},
            "roster": {"ally_alive_count": 4, "opponent_alive_count": 4},
            "map_ink": None,
            "special": {"state_at_anchor": "not_ready"},
            "combat": None,
        },
    }
    monkeypatch.setattr(rd, "advice_families", lambda _ci, u: [R])
    monkeypatch.setattr(rd, "evidence_summary", lambda _ci, u: summaries[u.candidate_id])
    monkeypatch.setattr(
        rd,
        "_coachable_claims",
        lambda _ci, u: ["x", "y"] if u.candidate_id == "b" else ["x"],
    )
    _, diag = rd.apply_advice_family_cap(units, {"a": None, "b": None}, max_llm_units=3)
    suppressed = _by_id(diag)["b"]
    status = {c.category: c.status for c in suppressed.distinct_evidence}
    assert status == {
        "objective": "different_from_retained",
        "roster": "same_value_in_retained",
        "map_ink": "not_observed",
        "special": "different_from_retained",
        "combat": "not_observed",
    }
    assert suppressed.coachable_claims_not_in_retained == ["y"]


def test_advice_families_come_from_eligible_claim_basis():
    coach_input = _input(prev_gap=12.0)
    unit = score_death_candidate(coach_input)
    expected = sorted(
        {t for e in build_claim_basis(coach_input, unit) for t in e.rec_template_ids}
    )
    assert rd.advice_families(coach_input, unit) == expected == [M, R]


def test_flag_defaults_off_and_leaves_the_config_hash_unchanged():
    off = CoachConfig()
    assert off.report_advice_family_cap is False
    payload = importance_config_payload(off, max_llm_units=3)
    assert "report_advice_family_cap" not in payload
    on = off.model_copy(update={"report_advice_family_cap": True})
    on_payload = importance_config_payload(on, max_llm_units=3)
    assert on_payload == {**payload, "report_advice_family_cap": True}
    assert importance_config_hash(on_payload) != importance_config_hash(payload)


def test_default_yaml_keeps_the_cap_off():
    assert (
        load_config(_REPO / "configs/default.yaml").coach.report_advice_family_cap
        is False
    )


def _saved_configs() -> tuple[AppConfig, AppConfig]:
    base = load_config(_REPO / "configs/default.yaml")
    coach = base.coach.model_copy(update={"report_advice_family_cap": True})
    return base, base.model_copy(update={"coach": coach})


@pytest.fixture
def saved_bundle():
    if not (_SAVED / "scenario_contexts.json").is_file():
        pytest.skip("Local saved analysis is unavailable")
    return load_coach_analysis_bundle(_SAVED, min_usable_confidence=0.5)


def test_saved_match_cap_changes_only_selection(saved_bundle):
    off_cfg, on_cfg = _saved_configs()
    off = score_match(saved_bundle, off_cfg, limit=50)
    on = score_match(saved_bundle, on_cfg, limit=50)
    assert off.report_diversity is None
    strip = {"selected_for_llm"}
    assert [u.model_dump(exclude=strip) for u in on.units] == [
        u.model_dump(exclude=strip) for u in off.units
    ]
    for cid, coach_input in off.coach_inputs.items():
        assert on.coach_inputs[cid].model_dump() == coach_input.model_dump()
    diag = on.report_diversity
    assert diag is not None and diag.changed
    suppressed = [m for m in diag.moments if m.action == "suppressed"]
    assert [m.candidate_id for m in suppressed] == ["death_episode:119.000"]
    assert {c.category for c in suppressed[0].distinct_evidence} == set(
        rd.EVIDENCE_CATEGORIES
    )
    families = [f for m in diag.moments if m.selected for f in m.advice_families]
    assert len(families) == len(set(families))


def test_written_inputs_add_diagnostics_only_when_enabled(saved_bundle, tmp_path):
    off_cfg, on_cfg = _saved_configs()
    off = write_coach_inputs(_SAVED, off_cfg, out_dir=tmp_path / "off", limit=50)
    on = write_coach_inputs(_SAVED, on_cfg, out_dir=tmp_path / "on", limit=50)
    assert off is not None and on is not None
    assert off.report_diversity_path is None
    assert not (tmp_path / "off" / rd.REPORT_DIVERSITY_FILENAME).exists()
    assert on.report_diversity_path == tmp_path / "on" / rd.REPORT_DIVERSITY_FILENAME
    names = sorted(p.name for p in (tmp_path / "off").glob("*.coach_input.json"))
    assert names
    for name in names:
        assert (tmp_path / "off" / name).read_bytes() == (
            tmp_path / "on" / name
        ).read_bytes()
