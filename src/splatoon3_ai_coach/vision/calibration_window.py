"""Bounded post-``in_match`` team-color calibration window (A1).

The window opens once, at the scan/video time ``t0`` of the frame whose
qualifying raw timer observation first moves the scan ``MatchPhaseFuser``
into ``in_match``. It admits frames in the closed interval
``[t0, t0 + POST_IN_MATCH_WINDOW_SECONDS]``, closes on a latch or on the first
processed frame after the bound, and never reopens for the match.

Holds no phase semantics: callers report the fused phase they observed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from splatoon3_ai_coach.vision.models import MatchPhase

# Fixed by alignment v4 development preregistration Amendment 2; not a knob.
POST_IN_MATCH_WINDOW_SECONDS = 3.0

WindowCloseReason = Literal["latched", "expired"]


@dataclass
class _RejectionTally:
    """Count and first/last video time for one rejection reason."""

    count: int = 0
    first_at: float | None = None
    last_at: float | None = None

    def add(self, video_time: float) -> None:
        """Record one rejected frame."""
        self.count += 1
        if self.first_at is None:
            self.first_at = video_time
        self.last_at = video_time


@dataclass
class CalibrationWindow:
    """One-shot ``[t0, t0 + duration]`` window; closed on latch or expiry."""

    duration_seconds: float = POST_IN_MATCH_WINDOW_SECONDS
    t0: float | None = None
    close_reason: WindowCloseReason | None = None
    closed_at: float | None = None
    rejections: dict[str, _RejectionTally] = field(default_factory=dict)

    @property
    def end(self) -> float | None:
        """Inclusive upper bound, or ``None`` before the window opens."""
        if self.t0 is None:
            return None
        return self.t0 + self.duration_seconds

    @property
    def is_open(self) -> bool:
        """True between the legitimate ``in_match`` entry and closing."""
        return self.t0 is not None and self.close_reason is None

    def advance(self, video_time: float, match_phase: MatchPhase) -> None:
        """Open on the first ``in_match`` frame; expire after the bound."""
        if self.t0 is None:
            if match_phase == "in_match":
                self.t0 = video_time
            return
        end = self.end
        if self.close_reason is None and end is not None and video_time > end:
            self._close("expired", video_time)

    def admit(self, video_time: float, match_phase: MatchPhase) -> str | None:
        """Return ``None`` if the frame is in the window, else a rejection reason."""
        if self.t0 is None:
            return f"phase_ineligible:{match_phase}"
        if self.close_reason is not None:
            return f"outside_window:{self.close_reason}"
        end = self.end
        if video_time < self.t0 or (end is not None and video_time > end):
            return "outside_window:out_of_bounds"
        return None

    def record_rejection(self, reason: str, video_time: float) -> None:
        """Tally one rejected candidate frame."""
        self.rejections.setdefault(reason, _RejectionTally()).add(video_time)

    def close_latched(self, video_time: float) -> None:
        """Close immediately after a successful calibration latch."""
        if self.close_reason is None:
            self._close("latched", video_time)

    def _close(self, reason: WindowCloseReason, video_time: float) -> None:
        self.close_reason = reason
        self.closed_at = video_time

    def to_dict(self) -> dict[str, object]:
        """Factual window provenance for ``match_identity.json``."""
        return {
            "t0": self.t0,
            "start": self.t0,
            "end": self.end,
            "duration_seconds": self.duration_seconds,
            "close_reason": self.close_reason,
            "closed_at": self.closed_at,
            "rejections": {
                reason: {
                    "count": tally.count,
                    "first_at": tally.first_at,
                    "last_at": tally.last_at,
                }
                for reason, tally in sorted(self.rejections.items())
            },
        }
