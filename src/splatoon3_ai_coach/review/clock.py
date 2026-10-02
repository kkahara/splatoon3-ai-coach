"""Authoritative elapsed-clock helpers for Review screenshots."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field

_CLOCK_RE = re.compile(r"^(?P<sign>-)?(?P<minutes>\d+):(?P<seconds>[0-5]\d)$")


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
    """Parse signed ``M:SS`` elapsed time without clamping."""
    match = _CLOCK_RE.fullmatch(display.strip())
    if match is None:
        return None
    seconds = int(match.group("minutes")) * 60 + int(match.group("seconds"))
    return -seconds if match.group("sign") else seconds


def format_elapsed_clock(elapsed_seconds: int) -> str:
    """Format signed elapsed seconds as ``M:SS``."""
    sign = "-" if elapsed_seconds < 0 else ""
    total = abs(elapsed_seconds)
    return f"{sign}{total // 60}:{total % 60:02d}"


def remaining_seconds(elapsed_seconds: int, regulation_seconds: int) -> int | None:
    """Convert elapsed time to remaining time only inside regulation."""
    if regulation_seconds < 0 or elapsed_seconds < 0:
        return None
    remaining = regulation_seconds - elapsed_seconds
    return remaining if remaining >= 0 else None


def elapsed_from_remaining(
    remaining: int, regulation_seconds: int
) -> int | None:
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
            return supplied, "conflict", (
                f"supplied {supplied.display} != observed {observed.display}"
            )
        return supplied.model_copy(update={"confidence": max(
            supplied.confidence, observed.confidence
        )}), "confirmed", None
    if observed is not None:
        return observed, "observed_only", None
    if supplied is not None:
        return supplied, "supplied_only", None
    return None, "unreadable", "no trustworthy clock evidence"

