"""Per-look precedence, staging and the 4b / criterion 4 verdicts."""

from __future__ import annotations

from collections.abc import Mapping

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
) -> str:
    """Overall 4b verdict from final per-recording results."""
    if not gate1_passed:
        return NOT_EVALUABLE
    inconclusive = [rid for rid, v in stage1.items() if v == INCONCLUSIVE]
    finals = dict(stage1)
    if inconclusive:
        if gate2_passed is None:
            raise ProcedureOrderError("Stage 1 INCONCLUSIVE results require Stage 2")
        if not gate2_passed:
            return NOT_EVALUABLE
        if stage2 is None:
            raise ProcedureOrderError(
                "Stage 2 gate passed but Stage 2 results are missing"
            )
        if set(stage2) != set(inconclusive):
            raise MalformedInput(
                "Stage 2 must cover exactly the Stage 1 INCONCLUSIVE set"
            )
        finals.update(stage2)
    if len(finals) != n_applicable:
        raise MalformedInput("every correctness-applicable recording needs a result")
    if any(v == REGRESSED for v in finals.values()):
        return FAIL
    evaluable = [v for v in finals.values() if v != NOT_EVALUABLE]
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
