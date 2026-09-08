"""Match-phase state machine (orthogonal to player death/respawn lifecycle)."""

from __future__ import annotations

from dataclasses import dataclass

from splatoon3_ai_coach.config.models import LifecycleFusionConfig
from splatoon3_ai_coach.vision.models import MatchPhase


@dataclass
class _MatchPhaseMemory:
    """Mutable match-phase state across frames."""

    phase: MatchPhase = "out_of_match"
    saw_in_match: bool = False
    last_timer_at: float | None = None


class MatchPhaseFuser:
    """INTRO → opening countdown → in-match → post-match.

    Uses the timer reading as evidence. Frozen 5:00 / 3:00 before the clock
    has ever ticked is ``opening_countdown``, not in-match play. Player
    death/respawn does not change match phase while the clock is held.
    """

    def __init__(self, config: LifecycleFusionConfig) -> None:
        self.config = config
        self._mem = _MatchPhaseMemory()

    @property
    def phase(self) -> MatchPhase:
        """Current mutually exclusive match phase."""
        return self._mem.phase

    def step(
        self,
        timestamp: float,
        timer_seconds: float | None,
        hud_evidence: bool = False,
    ) -> MatchPhase:
        """Advance match phase from one timer observation."""
        if self._mem.saw_in_match:
            return self._step_after_match(timestamp, timer_seconds)
        return self._step_pre_match(timestamp, timer_seconds, hud_evidence)

    def _opening_values(self) -> set[float]:
        """Frozen spawn-clock remaining-second values."""
        return {float(value) for value in self.config.opening_clock_seconds}

    def _step_pre_match(
        self,
        timestamp: float,
        timer_seconds: float | None,
        hud_evidence: bool,
    ) -> MatchPhase:
        """Lobby / intro / frozen 5:00 before the clock has ticked."""
        mem = self._mem
        opening = self._opening_values()
        if timer_seconds is not None and timer_seconds in opening:
            mem.phase = "opening_countdown"
            mem.last_timer_at = timestamp
            return mem.phase
        if timer_seconds is not None:
            return self._enter_in_match(timestamp)
        if mem.phase == "opening_countdown":
            return mem.phase
        mem.phase = "intro" if hud_evidence else "out_of_match"
        return mem.phase

    def _step_after_match(
        self,
        timestamp: float,
        timer_seconds: float | None,
    ) -> MatchPhase:
        """Stay in-match while the clock is held; then post-match."""
        mem = self._mem
        if timer_seconds is not None:
            return self._enter_in_match(timestamp)
        last = mem.last_timer_at
        hold = self.config.match_context_hold_seconds
        if last is not None and timestamp - last <= hold:
            mem.phase = "in_match"
            return mem.phase
        mem.phase = "post_match"
        return mem.phase

    def _enter_in_match(self, timestamp: float) -> MatchPhase:
        """Mark that a ticking (non-opening) clock has been seen."""
        mem = self._mem
        mem.phase = "in_match"
        mem.saw_in_match = True
        mem.last_timer_at = timestamp
        return mem.phase
