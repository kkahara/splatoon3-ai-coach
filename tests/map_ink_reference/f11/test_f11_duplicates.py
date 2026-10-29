"""Synthetic tests for the single F11 duplicate gate."""

from __future__ import annotations

from map_ink_reference.f11.duplicates import evaluate_gate


def test_zero_denominator_fails_and_arms_are_not_gates() -> None:
    result = evaluate_gate([("P", "cant_tell", "cant_tell")])
    assert not result.passed
    assert result.overall.denominator == 0
    assert result.arm_p.excluded == 1
    assert result.arm_r.denominator == 0


def test_disagreement_is_reported_by_arm_under_one_gate() -> None:
    pairs = [("P", "ink_A", "ink_B"), ("R", "ink_A", "ink_A")]
    result = evaluate_gate(pairs)
    assert not result.passed
    assert result.arm_p.numerator == 1
    assert result.arm_r.numerator == 0
    assert result.arm_r.denominator == 1
