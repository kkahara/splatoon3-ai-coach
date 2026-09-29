"""Temporal fusion for Splat Zones ownership observations."""

from __future__ import annotations

from dataclasses import dataclass, field

from splatoon3_ai_coach.config.models import ZoneControlDetectorConfig
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    MatchPhase,
    VisionFrameResult,
    ZoneControlQuality,
    ZoneControlReading,
    ZoneControlState,
)


@dataclass
class _ControlMemory:
    state: ZoneControlState = "unknown"
    last_at: float | None = None
    evidence_ids: list[str] = field(default_factory=list)
    pending: ZoneControlState | None = None
    pending_count: int = 0
    pending_ids: list[str] = field(default_factory=list)

    def reset(self) -> None:
        """Clear accepted and pending state."""
        self.state = "unknown"
        self.last_at = None
        self.evidence_ids = []
        self.pending = None
        self.pending_count = 0
        self.pending_ids = []


@dataclass(frozen=True)
class ZoneControlFrameFusion:
    """Fused ownership state for one frame."""

    state: ZoneControlState = "unknown"
    quality: ZoneControlQuality = "unknown"
    evidence_ids: list[str] = field(default_factory=list)


def _best_reading(frame: VisionFrameResult) -> DetectorResult | None:
    """Select the highest-confidence zone-control detector result."""
    matches = [
        item
        for item in frame.detections
        if isinstance(item.reading, ZoneControlReading)
    ]
    return max(matches, key=lambda item: item.confidence, default=None)


class ZoneControlFuser:
    """Mode-gated, confirmation-based fusion of ownership readings.

    Temporal contract (shared with ``vision.events`` and
    ``analysis.zone_control_context``):

    - a changed state needs ``confirm_readings`` consecutive usable readings;
      any gap (missing, low-confidence or ``unknown`` reading) clears pending
      confirmation;
    - during a gap the accepted state is exported as ``held`` for at most
      ``hold_seconds`` after its last observed reading;
    - when the hold expires the export is ``unknown`` and the accepted state
      is forgotten, so recovery needs full confirmation again;
    - the confirming snapshot carries every candidate reading's evidence ID.
    """

    def __init__(
        self,
        config: ZoneControlDetectorConfig,
        battle_mode_id: str | None,
    ) -> None:
        self.config = config
        self.enabled = battle_mode_id == "splat_zones"
        self._memory = _ControlMemory()

    def reset(self) -> None:
        """Forget ownership outside the in-match interval."""
        self._memory.reset()

    def step(
        self,
        frame: VisionFrameResult,
        match_phase: MatchPhase,
    ) -> ZoneControlFrameFusion:
        """Fuse one frame without emitting a GameEvent."""
        if not self.enabled or match_phase != "in_match":
            self.reset()
            return ZoneControlFrameFusion()
        result = _best_reading(frame)
        state: ZoneControlState = "unknown"
        if result is not None and result.confidence >= self.config.min_usable_confidence:
            reading = result.reading
            assert isinstance(reading, ZoneControlReading)
            state = reading.observed_state
        if result is None or state == "unknown":
            self._clear_pending()
            return self._hold_or_unknown(frame.timestamp)
        if state == self._memory.state:
            self._clear_pending()
            self._memory.last_at = frame.timestamp
            self._memory.evidence_ids = [result.id]
            return ZoneControlFrameFusion(state, "observed", [result.id])
        if self._confirm_change(state, frame.timestamp, result.id):
            self._memory.state = state
            self._memory.last_at = frame.timestamp
            self._memory.evidence_ids = list(self._memory.pending_ids)
            ids = list(self._memory.pending_ids)
            self._clear_pending()
            return ZoneControlFrameFusion(state, "observed", ids)
        return self._hold_or_unknown(frame.timestamp)

    def _confirm_change(
        self,
        state: ZoneControlState,
        timestamp: float,
        evidence_id: str,
    ) -> bool:
        """Track consecutive candidate states and report confirmation."""
        if self._memory.pending == state:
            self._memory.pending_count += 1
            self._memory.pending_ids.append(evidence_id)
        else:
            self._memory.pending = state
            self._memory.pending_count = 1
            self._memory.pending_ids = [evidence_id]
        return self._memory.pending_count >= self.config.confirm_readings

    def _hold_or_unknown(self, timestamp: float) -> ZoneControlFrameFusion:
        """Retain accepted ownership briefly; forget it once the hold expires."""
        memory = self._memory
        if (
            memory.state != "unknown"
            and memory.last_at is not None
            and timestamp - memory.last_at <= self.config.hold_seconds
        ):
            return ZoneControlFrameFusion(
                memory.state, "held", list(memory.evidence_ids)
            )
        memory.state = "unknown"
        memory.last_at = None
        memory.evidence_ids = []
        return ZoneControlFrameFusion()

    def _clear_pending(self) -> None:
        """Discard an incomplete transition confirmation."""
        self._memory.pending = None
        self._memory.pending_count = 0
        self._memory.pending_ids = []
