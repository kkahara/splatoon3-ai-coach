"""Experiment I stop point 4: sheet checks, join, frozen rule (synthetic data)."""

from __future__ import annotations

import coaching_units_experiment_i as xi
import coaching_units_experiment_i_analyze as xa
import coaching_units_experiment_i_results as xres
import pytest

HEADER = "output_id,match,death,assessment,recommendations," + ",".join(
    xres.SHEET_COLUMNS
)
MATCH = "2026-10-11 22-57-30"


def _sheet(rows: list[str]) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


def _manifest() -> dict:
    calls = [
        {
            "match": xi.MATCH_PREFIX + MATCH,
            "cid": "death_episode:308.000",
            "eligible": True,
            "arm": arm,
            "file": f"{arm}/x",
            "system": "s",
        }
        for arm in xi.ARMS
    ]
    return {"repeats": 1, "calls": calls}


def _key(rows: list[tuple[str, str]]) -> str:
    lines = ["output_id,call_id,match,cid,eligible,arm,repeat"]
    for oid, arm in rows:
        lines.append(
            f"{oid},{arm}__x__r1,{xi.MATCH_PREFIX}{MATCH},death_episode:308.000,True,{arm},1"
        )
    return "\n".join(lines) + "\n"


def test_sheet_problems_flag_values_and_changed_text(monkeypatch) -> None:
    monkeypatch.setattr(xa.xr, "EXPECTED_CALLS", 1)
    blank = _sheet([f"output_001,{MATCH},308.0s,text,rec,,,,,,,"])
    good = _sheet([f"output_001,{MATCH},308.0s,text,rec,4,2,3,2,yes,correct,n"])
    assert xa.sheet_problems(good, blank) == []
    bad = _sheet([f"output_001,{MATCH},308.0s,TEXT,rec,6,2,3,2,maybe,right,n"])
    joined = " ".join(xa.sheet_problems(bad, blank))
    assert "fixed column changed" in joined
    assert "usefulness_1to5" in joined and "supported_yes_no" in joined
    assert xa.MAP_USE in joined


def test_join_validates_ids_deaths_and_coverage() -> None:
    sheet = _sheet(
        [
            f"output_001,{MATCH},308.0s,t,r,4,2,3,2,yes,correct,n",
            f"output_002,{MATCH},308.0s,t,r,3,2,3,2,no,not_mentioned,n",
        ]
    )
    rows, problems = xa.join(
        sheet, _key([("output_001", "A"), ("output_002", "M")]), _manifest()
    )
    assert problems == [] and [r["arm"] for r in rows] == ["A", "M"]
    assert rows[1]["supported"] is False and rows[0]["usefulness"] == 4
    _, problems = xa.join(
        sheet, _key([("output_001", "A"), ("output_003", "M")]), _manifest()
    )
    assert "sheet and key output_ids differ" in problems
    _, problems = xa.join(
        sheet, _key([("output_001", "A"), ("output_002", "A")]), _manifest()
    )
    assert any("duplicate" in p or "missing" in p for p in problems)


def _group(calls: int, adjusted: int, production: int, **extra) -> dict:
    return {
        "calls": calls,
        "failed": 0,
        "blocked": 0,
        "map_rule_violations": 0,
        "value_fidelity_errors": 0,
        "validator_hits_adjusted": adjusted,
        "validator_hits_production": production,
        "map_mentions": 0,
        **extra,
    }


def test_safety_gate_uses_adjusted_count_and_margin() -> None:
    summary = {"arms": {"A": {"all": _group(44, 1, 1)}, "M": {"all": _group(44, 12, 60)}}}
    gate = xa.safety_gate(summary)
    assert gate["checks"]["adjusted_validator_hits_within_margin"]  # 12/44 <= 1/44+.25
    assert not gate["production_count_view_not_the_gate"]["within_margin"]
    summary["arms"]["M"]["all"]["validator_hits_adjusted"] = 13
    assert not xa.safety_gate(summary)["passed"]


def test_uptake_threshold_is_14_of_28() -> None:
    def doc(n: int) -> dict:
        return {"arms": {"M": {"eligible": _group(28, 0, 0, map_mentions=n)}}}

    assert xa.uptake(doc(14))["passed"] and not xa.uptake(doc(13))["passed"]


def _ratings(m_eligible: int, m_other: int, supported_m: bool = True) -> list[dict]:
    rows = []
    for d, eligible in (("d1", True), ("d2", False)):
        for arm in xi.ARMS:
            for rep in (1, 2):
                use = 3 if arm == "A" else (m_eligible if eligible else m_other)
                rows.append(
                    {
                        "match": "m",
                        "cid": d,
                        "eligible": eligible,
                        "arm": arm,
                        "repeat": rep,
                        "usefulness": use,
                        "supported": supported_m if arm == "M" else True,
                    }
                )
    return rows


def test_value_rule_and_outcome() -> None:
    v = xa.value(_ratings(4, 3))
    assert v["passed"] and v["eligible_usefulness_m_minus_a"] == 1.0
    assert not xa.value(_ratings(3, 3))["checks"][
        "eligible_usefulness_gain_exceeds_floor"
    ]
    assert not xa.value(_ratings(4, 1))["checks"]["all_deaths_no_harm"]
    assert not xa.value(_ratings(4, 3, supported_m=False))["passed"]
    ok, bad = {"passed": True}, {"passed": False}
    assert xa.outcome(ok, ok, ok) == "passes_1_2_3"
    assert xa.outcome(ok, ok, bad) == "passes_1_2_fails_3"
    assert xa.outcome(bad, ok, ok) == "fails_1_or_2"
    assert xa.outcome(ok, bad, ok) == "fails_1_or_2"


def test_repeat_consistency() -> None:
    rows = _ratings(4, 3)
    rows[2]["usefulness"] = 5  # M d1 r1
    rc = xa.repeat_consistency(rows)
    assert rc["A"]["mean_abs_r1_minus_r2"] == 0
    assert rc["M"]["mean_abs_r1_minus_r2"] == pytest.approx(0.5)
