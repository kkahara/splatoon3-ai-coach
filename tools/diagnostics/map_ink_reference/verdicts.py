"""Per-look precedence, staging and the 4b / criterion 4 verdicts."""

from __future__ import annotations

from collections.abc import Mapping, Set

from map_ink_reference.constants import (
    ALPHA_IMPROVEMENT_PER_LOOK,
    ALPHA_REGRESSION_PER_LOOK,
    MIN_EVALUABLE_RECORDINGS,
)
from map_ink_reference.errors import MalformedInput, ProcedureOrderError
from map_ink_reference.statistics import SignFlipResult

NOT_EVALUABLE = "NOT_EVALUABLE"
PRESERVED = "PRESERVED"
PRESERVED_IMPROVED = "PRESERVED_IMPROVED"
REGRESSED = "REGRESSED"
INCONCLUSIVE = "INCONCLUSIVE"
PASS = "PASS"
FAIL = "FAIL"

_PRESERVED = (PRESERVED, PRESERVED_IMPROVED)


def look_verdict(judgeable: bool, identity: bool, test: SignFlipResult | None) -> str:
    """Precedence at one look: judgeability, identity, regression, improvement."""
    if not judgeable:
        return NOT_EVALUABLE
    if identity:
        return PRESERVED
    if test is None:
        raise MalformedInput("a sign-flip result is required when identity fails")
    if test.p_reg <= ALPHA_REGRESSION_PER_LOOK:
        return REGRESSED
    if test.p_imp <= ALPHA_IMPROVEMENT_PER_LOOK:
        return PRESERVED_IMPROVED
    return INCONCLUSIVE


def verdict_4b(
    stage1: Mapping[str, str],
    *,
    gate1_passed: bool,
    stage2: Mapping[str, str] | None = None,
    gate2_passed: bool | None = None,
    n_applicable: int,
    capacity_exhausted: Set[str] = frozenset(),
) -> str:
    """Overall 4b verdict from final per-recording results."""
    if not gate1_passed:
        return NOT_EVALUABLE
    inconclusive = {rid for rid, value in stage1.items() if value == INCONCLUSIVE}
    exhausted = set(capacity_exhausted)
    if not exhausted <= inconclusive:
        raise MalformedInput("capacity-exhausted recordings must be Stage 1 INCONCLUSIVE")
    triggered = inconclusive - exhausted
    finals = dict(stage1)
    if triggered:
        if gate2_passed is None:
            raise ProcedureOrderError("Stage 1 INCONCLUSIVE results require Stage 2")
        if not gate2_passed:
            return NOT_EVALUABLE
        if stage2 is None:
            raise ProcedureOrderError(
                "Stage 2 gate passed but Stage 2 results are missing"
            )
        if set(stage2) != triggered:
            raise MalformedInput(
                "Stage 2 must cover exactly the triggered INCONCLUSIVE set"
            )
        finals.update(stage2)
    elif gate2_passed is not None or stage2 is not None:
        raise MalformedInput("Stage 2 data exists without a triggered recording")
    if len(finals) != n_applicable:
        raise MalformedInput("every correctness-applicable recording needs a result")
    if any(v == REGRESSED for v in finals.values()):
        return FAIL
    evaluable = [
        value
        for rid, value in finals.items()
        if rid not in exhausted and value != NOT_EVALUABLE
    ]
    if len(evaluable) >= MIN_EVALUABLE_RECORDINGS and all(
        v in _PRESERVED for v in evaluable
    ):
        return PASS
    return NOT_EVALUABLE


def criterion4(verdict_4a: str, verdict_4b_value: str) -> str:
    """PASS iff both PASS; FAIL if either FAIL; otherwise NOT EVALUABLE."""
    for value in (verdict_4a, verdict_4b_value):
        if value not in (PASS, FAIL, NOT_EVALUABLE):
            raise MalformedInput(f"invalid verdict {value!r}")
    if FAIL in (verdict_4a, verdict_4b_value):
        return FAIL
    if verdict_4a == PASS and verdict_4b_value == PASS:
        return PASS
    return NOT_EVALUABLE
