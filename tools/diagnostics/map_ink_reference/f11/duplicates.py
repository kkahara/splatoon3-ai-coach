"""The single F11 duplicate gate, plus descriptive per-arm counts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from map_ink_reference.constants import GATE_CAP
from map_ink_reference.errors import MalformedInput
from map_ink_reference.gate import (
    DISAGREE,
    EXCLUDED,
    classify_pair,
    clopper_pearson_upper,
)


@dataclass(frozen=True)
class ArmDuplicateReport:
    """Descriptive duplicate counts. This report is not a gate."""

    numerator: int
    denominator: int
    excluded: int
    upper_bound: float | None


@dataclass(frozen=True)
class DuplicateGate:
    """The one overall gate and the two descriptive arm reports."""

    passed: bool
    overall: ArmDuplicateReport
    arm_p: ArmDuplicateReport
    arm_r: ArmDuplicateReport


def evaluate_gate(
    pairs: Sequence[tuple[str, str | None, str | None]],
) -> DuplicateGate:
    """``pairs`` are ``(arm, first_label, second_label)`` with arm ``P`` or ``R``."""
    classified = [_classify(arm, first, second) for arm, first, second in pairs]
    overall = _report(classified)
    return DuplicateGate(
        passed=_passes(overall),
        overall=overall,
        arm_p=_report([row for row in classified if row[0] == "P"]),
        arm_r=_report([row for row in classified if row[0] == "R"]),
    )


def _classify(arm: str, first: str | None, second: str | None) -> tuple[str, str]:
    if arm not in {"P", "R"}:
        raise MalformedInput(f"duplicate arm must be P or R, not {arm!r}")
    return arm, classify_pair(first, second)


def _report(rows: Sequence[tuple[str, str]]) -> ArmDuplicateReport:
    numerator = sum(kind == DISAGREE for _, kind in rows)
    denominator = sum(kind != EXCLUDED for _, kind in rows)
    excluded = sum(kind == EXCLUDED for _, kind in rows)
    upper = None if denominator == 0 else clopper_pearson_upper(numerator, denominator)
    return ArmDuplicateReport(numerator, denominator, excluded, upper)


def _passes(report: ArmDuplicateReport) -> bool:
    if report.denominator == 0 or report.upper_bound is None:
        return False
    return report.upper_bound <= GATE_CAP
