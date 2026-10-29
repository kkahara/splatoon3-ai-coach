"""Design-based F11 statistics. The shifted sign-flip test is not implemented."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from map_ink_reference.statistics import SignFlipResult, sign_flip

M = 0.01
TAU = 0.25
ALPHA = 0.025
P_REG_MAX = 0.005
P_IMP_MAX = 0.025

IDENTITY = "identity"
PRESERVED_BY_BOUND = "F-PRESERVED_BY_BOUND"
LOW = "low"
HIGH = "high"
COVERAGE = "coverage-only"
NOT_EVALUABLE = "F-NOT_EVALUABLE"
REGRESSED = "F-REGRESSED"
IMPROVED = "F-IMPROVED"
NONINFERIOR = "F-NONINFERIOR"
INCONCLUSIVE = "F-INCONCLUSIVE"


def stratum(q_all: float) -> str:
    """Label-free outcome or annotation stratum for one recording."""
    if q_all < 0:
        raise ValueError("q_all is negative")
    if q_all == 0:
        return IDENTITY
    if q_all < M:
        return PRESERVED_BY_BOUND
    if q_all < TAU:
        return LOW
    return HIGH


def point_value(*, judgeable: bool, a1_correct: bool, baseline_correct: bool) -> int:
    """Conservative target-point value. Unjudgeable points count as -1."""
    if not judgeable:
        return -1
    if a1_correct and not baseline_correct:
        return 1
    if baseline_correct and not a1_correct:
        return -1
    return 0


def cluster_statistic(length: int, sample_count: int, values: Sequence[int]) -> float:
    """``Y_k``. An empty target population contributes 0."""
    if length < 0 or sample_count < 0:
        raise ValueError("cluster sizes must be non-negative")
    if sample_count == 0:
        return 0.0
    if len(values) != min(6, sample_count):
        raise ValueError("target values do not match the drawn count")
    return length * sample_count * (sum(values) / len(values))


def cluster_bound(length: int, max_sample_count: int) -> float:
    """Label-free ``B_k``."""
    if length < 0 or max_sample_count < 0:
        raise ValueError("bound sizes must be non-negative")
    return float(length * max_sample_count)


def hoeffding_threshold(bounds: Sequence[float], alpha: float = ALPHA) -> float:
    """One-sided Hoeffding threshold for ranges ``[-B_k, B_k]``."""
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between 0 and 1")
    return math.sqrt(2 * sum(bound * bound for bound in bounds) * math.log(1 / alpha))


def lower_bound(
    statistics: Sequence[float],
    bounds: Sequence[float],
    excluded_count: int,
    domain_count: int,
    alpha: float = ALPHA,
) -> float:
    """``LB_Delta``. ``excluded_count`` is ``|X ∩ D|`` and counts at -1 each."""
    if len(statistics) != len(bounds):
        raise ValueError("one bound is required per cluster")
    if excluded_count < 0 or domain_count <= 0:
        raise ValueError("domain counts are invalid")
    threshold = hoeffding_threshold(bounds, alpha)
    return (sum(statistics) - threshold - excluded_count) / domain_count


def attainability(
    lengths: Sequence[int],
    drawn_counts: Sequence[int],
    bounds: Sequence[float],
    excluded_count: int,
    domain_count: int,
) -> float:
    """``LB_max`` when every drawn target point has value +1."""
    if len(lengths) != len(drawn_counts):
        raise ValueError("each interval needs its drawn count")
    pairs = zip(lengths, drawn_counts, strict=True)
    best = [float(length * count) for length, count in pairs]
    return lower_bound(best, bounds, excluded_count, domain_count)


def noninferior(bound: float, margin: float = M) -> bool:
    """Reject ``H0: Delta <= -m`` at one-sided alpha 0.025 under R1–R3."""
    return bound > -margin


@dataclass(frozen=True)
class RecordingVerdict:
    """One annotated recording's precedence outcome and its sign-flip result."""

    outcome: str
    sign_flip: SignFlipResult
    lower: float


def recording_verdict(
    differences: Sequence[int],
    *,
    swatches_valid: bool,
    team_consistent: bool,
    gate_passed: bool,
    informative_clusters: int,
    lower: float,
) -> RecordingVerdict:
    """Apply the sealed per-recording precedence. ``lower`` is ``LB_Delta``."""
    flipped = sign_flip(differences)
    if (
        not swatches_valid
        or not team_consistent
        or not gate_passed
        or informative_clusters < 6
    ):
        outcome = NOT_EVALUABLE
    elif flipped.p_reg <= P_REG_MAX:
        outcome = REGRESSED
    elif flipped.p_imp <= P_IMP_MAX:
        outcome = IMPROVED
    elif noninferior(lower):
        outcome = NONINFERIOR
    else:
        outcome = INCONCLUSIVE
    return RecordingVerdict(outcome, flipped, lower)
