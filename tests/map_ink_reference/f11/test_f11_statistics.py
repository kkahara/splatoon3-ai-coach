"""Synthetic tests for the F11 bound, verdicts and the rejected sign-flip."""

from __future__ import annotations

from fractions import Fraction

from map_ink_reference.f11.statistics import (
    IMPROVED,
    INCONCLUSIVE,
    NONINFERIOR,
    NOT_EVALUABLE,
    REGRESSED,
    cluster_statistic,
    lower_bound,
    point_value,
    recording_verdict,
    stratum,
)
from map_ink_reference.statistics import sign_flip


def test_strata_use_the_sealed_margin_and_tau() -> None:
    assert stratum(0) == "identity"
    assert stratum(0.0045) == "F-PRESERVED_BY_BOUND"
    assert stratum(0.01) == "low"
    assert stratum(0.249) == "low"
    assert stratum(0.25) == "high"
    assert stratum(0.9157) == "high"


def test_unjudgeable_points_and_excluded_pixels_are_conservative() -> None:
    assert point_value(judgeable=False, a1_correct=True, baseline_correct=False) == -1
    judged = lower_bound([cluster_statistic(1, 1, [1])], [1.0], 0, 10)
    withheld = lower_bound([cluster_statistic(1, 1, [-1])], [1.0], 1, 10)
    assert withheld < judged


def test_rejected_shift_false_claims_on_the_length_imbalance() -> None:
    """G2: the r0 shifted sign-flip declares non-inferiority while H0 holds."""
    result = sign_flip([140] * 7 + [-60])
    assert result.p_imp == Fraction(2, 256)
    assert float(result.p_imp) <= 0.025
    theta = (7 * 100 - 20 * 100) / (7 * 100 + 20 * 100)
    assert theta == (700 - 2000) / 2700
    assert theta + 0.4 < 0


def test_verdict_precedence() -> None:
    improved = recording_verdict(
        [1] * 6,
        swatches_valid=True,
        team_consistent=True,
        gate_passed=True,
        informative_clusters=6,
        lower=1.0,
    )
    assert improved.outcome == IMPROVED
    regressed = recording_verdict(
        [-1] * 8,
        swatches_valid=True,
        team_consistent=True,
        gate_passed=True,
        informative_clusters=8,
        lower=1.0,
    )
    assert regressed.outcome == REGRESSED
    held = recording_verdict(
        [0] * 6,
        swatches_valid=True,
        team_consistent=True,
        gate_passed=True,
        informative_clusters=6,
        lower=0.0,
    )
    assert held.outcome == NONINFERIOR
    inconclusive = recording_verdict(
        [0] * 6,
        swatches_valid=True,
        team_consistent=True,
        gate_passed=True,
        informative_clusters=6,
        lower=-1.0,
    )
    assert inconclusive.outcome == INCONCLUSIVE
    blocked = recording_verdict(
        [1] * 6,
        swatches_valid=True,
        team_consistent=True,
        gate_passed=True,
        informative_clusters=5,
        lower=1.0,
    )
    assert blocked.outcome == NOT_EVALUABLE
