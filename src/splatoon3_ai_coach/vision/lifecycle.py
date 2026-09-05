"""Authoritative player respawn lifecycle fusion."""

from __future__ import annotations

from dataclasses import dataclass, field

from splatoon3_ai_coach.config.models import (
    ActiveGameplayDetectorConfig,
    DeathDetectorConfig,
    LifecycleFusionConfig,
    RespawnDetectorConfig,
    TimerDetectorConfig,
)
from splatoon3_ai_coach.vision.models import (
    ActiveGameplayReading,
    DeathReading,
    DetectorResult,
    PlayerLifecycle,
    RespawnReading,
    TimerReading,
    VisionFrameResult,
)


@dataclass
class LifecycleObservation:
    """Per-frame cues consumed by the lifecycle state machine."""

    death_detected: bool
    countdown_present: bool | None
    active_detected: bool | None
    match_context: bool | None = None
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class LifecycleStepResult:
    """Fused lifecycle outputs for one frame."""

    player_alive: bool | None
    player_lifecycle: PlayerLifecycle
    countdown_present: bool | None
    active_gameplay: bool | None
    evidence_ids: list[str]
    countdown_confirmed_this_death_episode: bool


@dataclass
class _LifecycleMemory:
    """Mutable episode state across frames."""

    phase: PlayerLifecycle = "unknown"
    latch: bool = False
    present_streak: int = 0
    absent_streak: int = 0
    active_streak: int = 0
    death_episode_started_at: float | None = None
    last_match_context_at: float | None = None
    saw_match_context: bool = False


class LifecycleFuser:
    """Observation-based death → countdown → respawned → alive machine."""

    def __init__(self, config: LifecycleFusionConfig) -> None:
        self.config = config
        self._mem = _LifecycleMemory()

    @property
    def countdown_confirmed_this_death_episode(self) -> bool:
        """Whether countdown present-persistence armed this death episode."""
        return self._mem.latch

    def step(self, timestamp: float, obs: LifecycleObservation) -> LifecycleStepResult:
        """Advance lifecycle from one observation bundle."""
        mem = self._mem

        if obs.match_context is True:
            mem.last_match_context_at = timestamp
            mem.saw_match_context = True

        if obs.death_detected and self._death_allowed(timestamp):
            self._enter_dead(timestamp)
        elif mem.phase in {"dead", "countdown", "respawned"}:
            self._maybe_stale_to_unknown(timestamp)

        if mem.phase == "dead":
            self._step_dead(obs)
        elif mem.phase == "countdown":
            self._step_countdown(obs)
        elif mem.phase == "respawned":
            self._step_respawned(obs)
        elif mem.phase == "unknown":
            self._step_unknown(obs)
        # alive: hold until death

        return LifecycleStepResult(
            player_alive=_alive_for_phase(mem.phase),
            player_lifecycle=mem.phase,
            countdown_present=obs.countdown_present,
            active_gameplay=_active_for_snapshot(obs),
            evidence_ids=list(obs.evidence_ids),
            countdown_confirmed_this_death_episode=mem.latch,
        )

    def _death_allowed(self, timestamp: float) -> bool:
        """Whether a DeathReading may enter DEAD (in-match gate)."""
        if not self.config.death_requires_match_context:
            return True
        mem = self._mem
        # Unit tests / timer-disabled pipelines: never saw a timer → allow.
        if not mem.saw_match_context:
            return True
        last = mem.last_match_context_at
        if last is None:
            return False
        return timestamp - last <= self.config.match_context_hold_seconds

    def _enter_dead(self, timestamp: float) -> None:
        """Start or reassert a death episode."""
        mem = self._mem
        mem.phase = "dead"
        mem.latch = False
        mem.present_streak = 0
        mem.absent_streak = 0
        mem.active_streak = 0
        mem.death_episode_started_at = timestamp

    def _maybe_stale_to_unknown(self, timestamp: float) -> None:
        """Elapsed-time escape hatch since DEATH; never emits RESPAWN/ACTIVE."""
        mem = self._mem
        if not self.config.stale_to_unknown:
            return
        started = mem.death_episode_started_at
        if started is None:
            return
        if timestamp - started <= self.config.max_respawn_observation_seconds:
            return
        mem.phase = "unknown"
        mem.latch = False
        mem.present_streak = 0
        mem.absent_streak = 0
        mem.active_streak = 0
        mem.death_episode_started_at = None

    def _step_dead(self, obs: LifecycleObservation) -> None:
        """DEAD → COUNTDOWN only after present persistence arms the latch."""
        mem = self._mem
        if obs.countdown_present is True:
            mem.present_streak += 1
            mem.absent_streak = 0
            if mem.present_streak >= self.config.countdown_present_min_observations:
                mem.phase = "countdown"
                mem.latch = True
        else:
            mem.present_streak = 0
            # Sub-threshold / absent: do not arm latch; cannot go RESPAWNED.

    def _step_countdown(self, obs: LifecycleObservation) -> None:
        """COUNTDOWN → RESPAWNED on sustained absent with latch armed."""
        mem = self._mem
        if obs.countdown_present is True:
            mem.present_streak += 1
            mem.absent_streak = 0
            mem.latch = True
            return
        if obs.countdown_present is False:
            mem.absent_streak += 1
            mem.present_streak = 0
            if (
                mem.latch
                and mem.absent_streak >= self.config.countdown_absent_min_observations
            ):
                mem.phase = "respawned"
                mem.active_streak = 0
            return
        # No countdown observation this frame: do not advance absence streak.

    def _step_respawned(self, obs: LifecycleObservation) -> None:
        """RESPAWNED → ALIVE on positive active composite only."""
        mem = self._mem
        if _positive_active(obs):
            mem.active_streak += 1
            if mem.active_streak >= self.config.active_again_min_observations:
                mem.phase = "alive"
                mem.latch = False
                mem.present_streak = 0
                mem.absent_streak = 0
                mem.active_streak = 0
                mem.death_episode_started_at = None
            return
        mem.active_streak = 0

    def _step_unknown(self, obs: LifecycleObservation) -> None:
        """UNKNOWN → ALIVE when positive active evidence persists."""
        mem = self._mem
        if _positive_active(obs):
            mem.active_streak += 1
            if mem.active_streak >= self.config.active_again_min_observations:
                mem.phase = "alive"
                mem.latch = False
                mem.active_streak = 0
            return
        mem.active_streak = 0


def extract_lifecycle_observation(
    frame: VisionFrameResult,
    *,
    death_config: DeathDetectorConfig,
    respawn_config: RespawnDetectorConfig,
    active_config: ActiveGameplayDetectorConfig,
    timer_config: TimerDetectorConfig | None = None,
) -> LifecycleObservation:
    """Pull usable death/respawn/active/match cues from one vision frame.

    Respawn readings map onto ``countdown_present`` so lifecycle edge
    semantics stay unchanged (DEAD → COUNTDOWN → RESPAWNED → ALIVE).
    """
    evidence: list[str] = []
    death_detected = False
    best_death = _best_named(frame, "death", DeathReading)
    death_usable = (
        best_death is not None
        and best_death.confidence >= death_config.min_usable_confidence
    )
    if death_usable:
        reading = best_death.reading
        assert isinstance(reading, DeathReading)
        if reading.detected:
            death_detected = True
            evidence.append(best_death.id)

    countdown_present: bool | None = None
    best_rs = _best_named(frame, "respawn", RespawnReading)
    rs_usable = (
        best_rs is not None
        and best_rs.confidence >= respawn_config.min_usable_confidence
    )
    if rs_usable:
        reading = best_rs.reading
        assert isinstance(reading, RespawnReading)
        countdown_present = reading.detected
        evidence.append(best_rs.id)

    active_detected: bool | None = None
    best_ag = _best_named(frame, "active_gameplay", ActiveGameplayReading)
    ag_usable = (
        best_ag is not None
        and best_ag.confidence >= active_config.min_usable_confidence
    )
    if ag_usable:
        reading = best_ag.reading
        assert isinstance(reading, ActiveGameplayReading)
        active_detected = reading.detected
        evidence.append(best_ag.id)

    match_context: bool | None = None
    best_timer = _best_named(frame, "timer", TimerReading)
    timer_min = (
        timer_config.min_usable_confidence if timer_config is not None else 0.50
    )
    if best_timer is not None and best_timer.confidence >= timer_min:
        reading = best_timer.reading
        assert isinstance(reading, TimerReading)
        if reading.seconds_remaining is not None:
            match_context = True
            evidence.append(best_timer.id)

    return LifecycleObservation(
        death_detected=death_detected,
        countdown_present=countdown_present,
        active_detected=active_detected,
        match_context=match_context,
        evidence_ids=evidence,
    )


def _best_named(
    frame: VisionFrameResult,
    detector_name: str,
    reading_type: type,
) -> DetectorResult | None:
    """Highest-confidence result for a detector name and reading type."""
    results = [
        detection
        for detection in frame.detections
        if detection.detector_name == detector_name
        and isinstance(detection.reading, reading_type)
    ]
    return max(results, key=lambda item: item.confidence) if results else None


def _positive_active(obs: LifecycleObservation) -> bool:
    """Active composite: positive active evidence without death/countdown conflict."""
    if obs.death_detected:
        return False
    if obs.countdown_present is True:
        return False
    return obs.active_detected is True


def _active_for_snapshot(obs: LifecycleObservation) -> bool | None:
    """Snapshot active_gameplay after conflict filter."""
    if obs.active_detected is None:
        return None
    return _positive_active(obs)


def _alive_for_phase(phase: PlayerLifecycle) -> bool | None:
    """Map lifecycle phase to the externally consumed alive bit."""
    if phase == "unknown":
        return None
    if phase == "alive":
        return True
    return False
