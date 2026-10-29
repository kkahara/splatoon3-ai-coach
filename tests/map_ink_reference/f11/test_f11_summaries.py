"""Synthetic tests for the sealed study-level rules."""

from __future__ import annotations

from map_ink_reference.f11.summaries import (
    SummaryRecording,
    preservation_supported,
    regressions,
    rescue_consistently_supported,
    rescue_not_evaluable,
    rescue_replicated,
)


def _row(
    arm: str,
    stratum: str,
    outcome: str,
    session: str = "s",
) -> SummaryRecording:
    return SummaryRecording(arm, stratum, outcome, session)


def test_rescue_ignores_arm_p_even_when_high_stratum() -> None:
    rows = (
        _row("P", "high", "F-IMPROVED", "corpus"),
        _row("R", "high", "F-IMPROVED", "one"),
    )
    assert not rescue_replicated(rows)
    assert not rescue_consistently_supported(rows)


def test_rescue_requires_two_sessions_and_ignores_non_successes() -> None:
    same_session = (
        _row("R", "high", "F-IMPROVED", "one"),
        _row("R", "high", "F-IMPROVED", "one"),
    )
    assert not rescue_replicated(same_session)
    mixed = (
        _row("R", "high", "F-IMPROVED", "one"),
        _row("R", "high", "F-INCONCLUSIVE", "two"),
        _row("R", "high", "F-NOT_EVALUABLE", "three"),
    )
    assert not rescue_replicated(mixed)
    assert not rescue_consistently_supported(mixed)
    assert not rescue_not_evaluable(mixed)
    one_session = (
        _row("R", "high", "F-IMPROVED", "one"),
        _row("R", "high", "F-NOT_EVALUABLE", "two"),
    )
    assert rescue_not_evaluable(one_session)
    replicated = (
        _row("R", "high", "F-IMPROVED", "one"),
        _row("R", "high", "F-IMPROVED", "two"),
        _row("R", "high", "F-INCONCLUSIVE", "three"),
    )
    assert rescue_replicated(replicated)
    assert not rescue_consistently_supported(replicated)


def test_any_high_stratum_regression_blocks_rescue_and_stays_visible() -> None:
    rows = (
        _row("R", "high", "F-IMPROVED", "one"),
        _row("R", "high", "F-IMPROVED", "two"),
        _row("R", "high", "F-REGRESSED", "three"),
    )
    assert not rescue_replicated(rows)
    assert [row.outcome for row in regressions(rows)] == ["F-REGRESSED"]


def test_preservation_counts_improvement_but_not_inconclusive() -> None:
    passing = (
        _row("P", "high", "F-IMPROVED"),
        _row("P", "low", "identity"),
        _row("R", "low", "F-PRESERVED_BY_BOUND", "arm-r"),
    )
    assert preservation_supported(passing)
    blocked = (*passing, _row("P", "low", "F-INCONCLUSIVE"))
    assert not preservation_supported(blocked)
    regressed = (*passing, _row("R", "high", "F-REGRESSED", "arm-r"))
    assert not preservation_supported(regressed)
