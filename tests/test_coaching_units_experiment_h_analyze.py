"""Experiment H stop point 4: the pre-registered decision rule."""

from __future__ import annotations

import coaching_units_experiment_h as xh
import coaching_units_experiment_h_analyze as xa
import pytest


def _group(**kw) -> dict:
    base = {
        "roster_violations": 0,
        "count_fidelity_errors": 0,
        "blocked": 0,
        "validator_hits_per_call": 0.1,
        "roster_mentions": 0,
    }
    return {**base, **kw}


def _summary(c_all: dict | None = None, treat: float = 0.2, ctrl: float = 0.1) -> dict:
    subsets = ("all", "treatment", "control", xh.AT_OR_BEFORE_DEATH)
    arms = {arm: {s: _group() for s in subsets} for arm in xh.ARMS}
    if c_all:
        arms["C"]["all"] = _group(**c_all)
    return {
        "arms": arms,
        "divergence_b_c": {"treatment_mean": treat, "control_mean": ctrl},
    }


@pytest.mark.parametrize(
    ("c_all", "passed"),
    [
        ({}, True),
        ({"validator_hits_per_call": 0.35}, True),
        ({"validator_hits_per_call": 0.36}, False),
        ({"roster_violations": 1}, False),
        ({"count_fidelity_errors": 1}, False),
        ({"blocked": 1}, False),
    ],
)
def test_safety_gate(c_all: dict, passed: bool) -> None:
    assert xa.safety_gate(_summary(c_all))["passed"] is passed


def _check(mentions: bool, fidelity: bool = True) -> dict:
    return {
        "arm": "C",
        "change_type": xh.AT_OR_BEFORE_DEATH,
        "ok": True,
        "mentions_roster": mentions,
        "count_fidelity": fidelity,
    }


def _rating(arm: str, death: str, use: int, **kw) -> dict:
    return {
        "arm": arm,
        "match": "m",
        "cid": death,
        "set": kw.pop("set", "treatment"),
        "usefulness": use,
        "supported": kw.pop("supported", True),
        "missing_context": 5,
        "roster_use": kw.pop("roster_use", "not_mentioned"),
    }


def test_q1_zero_mentions_fails_and_fidelity_threshold() -> None:
    ratings = [_rating("C", "d", 3, roster_use="correct")]
    assert not xa.q1(_summary(), [_check(False)] * 10, ratings)["passed"]
    nine = [_check(True)] * 9 + [_check(True, fidelity=False)]
    assert xa.q1(_summary(), nine, ratings)["passed"]
    eight = [_check(True)] * 8 + [_check(True, fidelity=False)] * 2
    assert not xa.q1(_summary(), eight, ratings)["passed"]


def test_q1_divergence_and_roster_correct() -> None:
    checks = [_check(True)]
    assert not xa.q1(_summary(treat=0.1, ctrl=0.1), checks, [])["passed"]
    b_more = [_rating("B", "d", 3, roster_use="correct")]
    assert not xa.q1(_summary(), checks, b_more)["passed"]


def test_q2_noise_floor_and_supported() -> None:
    rows = [
        _rating("A", "t", 3),
        _rating("B", "t", 3),
        _rating("C", "t", 4),
        _rating("A", "c", 3, set="control"),
        _rating("B", "c", 3, set="control"),
        _rating("C", "c", 3, set="control"),
    ]
    doc = xa.q2(rows)
    assert doc["usefulness_c_minus_a"] == 0.5 and doc["noise_floor"] == 0.0
    assert doc["passed"]
    rows[2] = _rating("C", "t", 4, supported=False)
    assert not xa.q2(rows)["passed"]


def test_outcome() -> None:
    ok, bad = {"passed": True}, {"passed": False}
    assert xa.outcome(ok, ok, ok) == "recommend_enable"
    assert xa.outcome(ok, ok, bad) == "keep_off_no_value"
    assert xa.outcome(ok, bad, ok) == "keep_off_investigate"
    assert xa.outcome(bad, ok, ok) == "keep_off_investigate"
