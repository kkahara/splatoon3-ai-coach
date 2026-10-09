"""Authoritative elapsed-clock helpers for Review screenshots."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field

_CLOCK_RE = re.compile(r"^(?P<sign>[-+])?(?P<minutes>\d+):(?P<seconds>[0-5]\d)$")


class ClockSource(StrEnum):
    """Where a clock value came from."""

    manifest = "manifest"
    filename_hint = "filename_hint"
    image = "image"


class ReviewClock(BaseModel):
    """A displayed elapsed clock and its sole numeric representation."""

    display: str
    elapsed_seconds: int
    source: ClockSource
    confidence: float = Field(ge=0, le=1)


def parse_elapsed_clock(display: str) -> int | None:
    """Parse a signed Review label without clamping.

    The label counts down to 0:00. ``-M:SS`` is the pre-GO countdown and
    ``+M:SS`` is overtime past 0:00; both map to negative values. Overtime
    values continue the match-history axis to the right of 0:00.
    """
    match = _CLOCK_RE.fullmatch(display.strip())
    if match is None:
        return None
    seconds = int(match.group("minutes")) * 60 + int(match.group("seconds"))
    return -seconds if match.group("sign") else seconds


def is_overtime_display(display: str | None) -> bool:
    """Whether a displayed label is an overtime (``+M:SS``) clock."""
    return display is not None and display.strip().startswith("+")


def is_countdown_display(display: str | None) -> bool:
    """Whether a displayed label is the pre-GO (``-M:SS``) countdown."""
    return display is not None and display.strip().startswith("-")


class ClockPhase(StrEnum):
    """Which part of the Review clock a plausible label belongs to."""

    countdown = "countdown"
    regulation = "regulation"
    overtime = "overtime"


def clock_phase(
    display: str | None,
    value: int | None,
    *,
    regulation_seconds: int,
    max_overtime_seconds: int,
    max_countdown_seconds: int,
) -> ClockPhase | None:
    """Classify a read label, or ``None`` when it is implausible (e.g. ``95:00``).

    Only plausible labels may become per-second evidence; the decision uses
    the label's own sign and magnitude, never neighbouring readings.
    """
    if display is None or value is None:
        return None
    magnitude = abs(value)
    if is_overtime_display(display):
        return ClockPhase.overtime if magnitude <= max_overtime_seconds else None
    if is_countdown_display(display):
        return ClockPhase.countdown if magnitude <= max_countdown_seconds else None
    return ClockPhase.regulation if 0 <= value <= regulation_seconds else None


def format_elapsed_clock(elapsed_seconds: int, *, overtime: bool = False) -> str:
    """Format a signed label value as ``M:SS``, ``-M:SS`` or overtime ``+M:SS``."""
    total = abs(elapsed_seconds)
    sign = "+" if overtime and elapsed_seconds < 0 else ""
    if not sign and elapsed_seconds < 0:
        sign = "-"
    return f"{sign}{total // 60}:{total % 60:02d}"


def remaining_seconds(elapsed_seconds: int, regulation_seconds: int) -> int | None:
    """Convert elapsed time to remaining time only inside regulation."""
    if regulation_seconds < 0 or elapsed_seconds < 0:
        return None
    remaining = regulation_seconds - elapsed_seconds
    return remaining if remaining >= 0 else None


def elapsed_from_remaining(remaining: int, regulation_seconds: int) -> int | None:
    """Convert a non-negative remaining value to the authoritative elapsed value."""
    if remaining < 0 or regulation_seconds < 0:
        return None
    return regulation_seconds - remaining


def filename_clock_hint(filename: str) -> ReviewClock | None:
    """Extract a signed ``M-SS`` or ``M:SS`` filename hint when present."""
    match = re.search(r"(?<!\d)(-?\d{1,3})[-:](\d{2})(?!\d)", filename)
    if match is None:
        return None
    display = f"{match.group(1)}:{match.group(2)}"
    elapsed = parse_elapsed_clock(display)
    if elapsed is None:
        return None
    return ReviewClock(
        display=display,
        elapsed_seconds=elapsed,
        source=ClockSource.filename_hint,
        confidence=0.35,
    )


def resolve_clock(
    *,
    manifest: ReviewClock | None,
    filename: ReviewClock | None,
    observed: ReviewClock | None,
) -> tuple[ReviewClock | None, str, str | None]:
    """Resolve clock evidence without inventing a value.

    Manifest metadata is preferred as supplied metadata; an observed image
    value is retained by callers for validation. A conflict is never hidden.
    """
    supplied = manifest or filename
    if supplied is not None and observed is not None:
        if supplied.elapsed_seconds != observed.elapsed_seconds:
            return (
                supplied,
                "conflict",
                (f"supplied {supplied.display} != observed {observed.display}"),
            )
        return (
            supplied.model_copy(
                update={"confidence": max(supplied.confidence, observed.confidence)}
            ),
            "confirmed",
            None,
        )
    if observed is not None:
        return observed, "observed_only", None
    if supplied is not None:
        return supplied, "supplied_only", None
    return None, "unreadable", "no trustworthy clock evidence"
