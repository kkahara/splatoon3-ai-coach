"""Study-level F11 summaries. They are combination rules, not extra tests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from map_ink_reference.f11.statistics import (
    IDENTITY,
    IMPROVED,
    NONINFERIOR,
    NOT_EVALUABLE,
    PRESERVED_BY_BOUND,
    REGRESSED,
)

PRESERVATION_PASS = frozenset({IDENTITY, PRESERVED_BY_BOUND, NONINFERIOR, IMPROVED})


@dataclass(frozen=True)
class SummaryRecording:
    """One recording's arm, stratum, session and outcome."""

    arm: str
    stratum: str
    outcome: str
    session: str
    correctness_applicable: bool = True


def preservation_supported(recordings: Sequence[SummaryRecording]) -> bool:
    """Arm P and Arm R low stratum pass, and no F11 recording is F-REGRESSED."""
    if any(row.outcome == REGRESSED for row in recordings):
        return False
    relevant = [row for row in recordings if row.arm == "P" or row.stratum == "low"]
    return all(row.outcome in PRESERVATION_PASS for row in relevant)


def rescue_replicated(recordings: Sequence[SummaryRecording]) -> bool:
    """At least two improved Arm R high-stratum recordings from two sessions."""
    high = _rescue_rows(recordings)
    improved = [row for row in high if row.outcome == IMPROVED]
    sessions = {row.session for row in improved}
    return len(improved) >= 2 and len(sessions) >= 2 and not _regressed(high)


def rescue_consistently_supported(recordings: Sequence[SummaryRecording]) -> bool:
    """Every evaluable Arm R high-stratum recording is improved, across sessions."""
    high = _rescue_rows(recordings)
    evaluable = [row for row in high if row.outcome != NOT_EVALUABLE]
    sessions = {row.session for row in evaluable}
    all_improved = bool(evaluable) and all(row.outcome == IMPROVED for row in evaluable)
    enough = len(evaluable) >= 2 and len(sessions) >= 2 and all_improved
    return enough and not _regressed(high)


def rescue_not_evaluable(recordings: Sequence[SummaryRecording]) -> bool:
    """Fewer than two sessions contribute an evaluable Arm R high-stratum recording."""
    evaluable = [row for row in _rescue_rows(recordings) if row.outcome != NOT_EVALUABLE]
    return len({row.session for row in evaluable}) < 2


def regressions(recordings: Sequence[SummaryRecording]) -> list[SummaryRecording]:
    """Every F-REGRESSED recording, whatever the summaries say."""
    return [row for row in recordings if row.outcome == REGRESSED]


def _rescue_rows(recordings: Sequence[SummaryRecording]) -> list[SummaryRecording]:
    return [
        row
        for row in recordings
        if row.arm == "R" and row.stratum == "high" and row.correctness_applicable
    ]


def _regressed(rows: Sequence[SummaryRecording]) -> bool:
    return any(row.outcome == REGRESSED for row in rows)
