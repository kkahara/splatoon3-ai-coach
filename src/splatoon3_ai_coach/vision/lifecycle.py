"""Authoritative player respawn lifecycle fusion."""

from __future__ import annotations

from dataclasses import dataclass, field

from splatoon3_ai_coach.config.models import (
    ActiveGameplayDetectorConfig,
    DeathDetectorConfig,
    LifecycleFusionConfig,
    MapOverlayDetectorConfig,
    RespawnDetectorConfig,
    TimerDetectorConfig,
)
from splatoon3_ai_coach.vision.match_phase import MatchPhaseFuser
from splatoon3_ai_coach.vision.models import (
    ActiveGameplayReading,
    DeathReading,
    DetectorResult,
    MapOverlayReading,
    MatchPhase,
    PlayerLifecycle,
    RespawnReading,
    TimerReading,
    VisionFrameResult,
)


@dataclass
class LifecycleObservation:
    """Per-frame cues consumed by the lifecycle state machine.

    ``map_overlay_present`` is recorded for snapshot/coaching context only.
    It must not drive death-recovery transitions (see MapOverlayReading).
    """

    death_detected: bool
    countdown_present: bool | None
    active_detected: bool | None
    # Softer weapon+center cue for awaiting_control → alive only.
    return_control: bool | None = None
    map_overlay_present: bool | None = None
    match_context: bool | None = None
    timer_seconds: float | None = None
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class LifecycleStepResult:
    """Fused lifecycle outputs for one frame."""

    player_alive: bool | None
    player_lifecycle: PlayerLifecycle
    match_phase: MatchPhase
    countdown_present: bool | None
    active_gameplay: bool | None
    map_overlay_present: bool | None
    evidence_ids: list[str]
    countdown_confirmed_this_death_episode: bool
    awaiting_control_confirmed_this_death_episode: bool


"""Phases that mean a death episode is already in flight.

Continued death evidence while in one of these is supporting evidence for
that episode, never the start of a new one (DEATH is a one-shot event).
"""
_DEATH_EPISODE_PHASES = frozenset(
    {"dead", "countdown", "respawned", "awaiting_control"}
)


@dataclass
class _LifecycleMemory:
    """Mutable episode state across frames."""

    phase: PlayerLifecycle = "unknown"
    latch: bool = False  # countdown present latch
    awaiting_control_latch: bool = False
    present_streak: int = 0
    absent_streak: int = 0
    control_absent_streak: int = 0
    control_present_streak: int = 0
    control_gap_streak: int = 0
    active_streak: int = 0  # unknown → alive only
    skip_streak: int = 0  # dead → respawned without a countdown plate
    death_episode_started_at: float | None = None
    awaiting_control_started_at: float | None = None


class LifecycleFuser:
    """Death → countdown → respawned → awaiting_control → alive machine.

    A death episode starts once. While one is in flight, continued death
    evidence is supporting evidence and must not restart it.

    The usual path is DEAD → countdown plate → RESPAWNED. Water and wipeout
    deaths skip that plate; after
    ``dead_skip_countdown_min_seconds``, sustained control evidence may
    leave DEAD directly into RESPAWNED. The plate path still wins when
    countdown is observed.

    ``awaiting_control`` is a post-respawn control-return latch only. It is
    not a detector for Super Jump gameplay (which can occur while ALIVE).
    Match phase (intro / opening countdown / in-match) is orthogonal and
    owned by :class:`MatchPhaseFuser`.
    """

    def __init__(self, config: LifecycleFusionConfig) -> None:
        self.config = config
        self._mem = _LifecycleMemory()
        self._match = MatchPhaseFuser(config)

    @property
    def countdown_confirmed_this_death_episode(self) -> bool:
        """Whether countdown present-persistence armed this death episode."""
        return self._mem.latch

    @property
    def awaiting_control_confirmed_this_death_episode(self) -> bool:
        """Whether the post-respawn control-return latch is armed."""
        return self._mem.awaiting_control_latch

    def step(self, timestamp: float, obs: LifecycleObservation) -> LifecycleStepResult:
        """Advance lifecycle from one observation bundle."""
        mem = self._mem
        match_phase = self._match.step(
            timestamp,
            obs.timer_seconds,
            hud_evidence=obs.active_detected is True,
        )

        in_episode = mem.phase in _DEATH_EPISODE_PHASES
        if obs.death_detected and self._death_allowed() and not in_episode:
            self._enter_dead(timestamp)
        elif in_episode:
            self._maybe_stale_to_unknown(timestamp)

        if mem.phase == "dead":
            self._step_dead(obs, timestamp)
        elif mem.phase == "countdown":
            self._step_countdown(obs)
        elif mem.phase == "respawned":
            self._step_respawned(obs, timestamp)
        elif mem.phase == "awaiting_control":
            self._step_awaiting_control(obs, timestamp)
        elif mem.phase == "unknown":
            self._step_unknown(obs, match_phase)
        # alive: hold until death

        return LifecycleStepResult(
            player_alive=_alive_for_phase(mem.phase),
            player_lifecycle=mem.phase,
            match_phase=match_phase,
            countdown_present=obs.countdown_present,
            active_gameplay=_active_for_snapshot(obs, match_phase, mem.phase),
            # Passthrough only — never used to advance phases above.
            map_overlay_present=obs.map_overlay_present,
            evidence_ids=list(obs.evidence_ids),
            countdown_confirmed_this_death_episode=mem.latch,
            awaiting_control_confirmed_this_death_episode=mem.awaiting_control_latch,
        )

    def _death_allowed(self) -> bool:
        """Whether a DeathReading may enter DEAD (in-match gate)."""
        if not self.config.death_requires_match_context:
            return True
        return self._match.phase == "in_match"

    def _enter_dead(self, timestamp: float) -> None:
        """Start a new death episode.

        Only called when no episode is in flight. Persistent death evidence
        must never re-run this and reset the countdown latch mid-episode.
        """
        mem = self._mem
        mem.phase = "dead"
        mem.latch = False
        mem.awaiting_control_latch = False
        mem.present_streak = 0
        mem.absent_streak = 0
        mem.control_absent_streak = 0
        mem.control_present_streak = 0
        mem.control_gap_streak = 0
        mem.active_streak = 0
        mem.skip_streak = 0
        mem.death_episode_started_at = timestamp
        mem.awaiting_control_started_at = None

    def _maybe_stale_to_unknown(self, timestamp: float) -> None:
        """Elapsed-time escape hatch since DEATH; never emits RESPAWN/ACTIVE.

        Runs on every in-episode frame, including ones where death evidence
        is still true, so a stuck detector cannot pin the player in DEAD.
        Re-entering DEAD from UNKNOWN is a new lifecycle episode; whether that
        emits a second DEATH is the event layer's call (it does not, until the
        player has been observed alive again).
        """
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
        mem.awaiting_control_latch = False
        mem.present_streak = 0
        mem.absent_streak = 0
        mem.control_absent_streak = 0
        mem.control_present_streak = 0
        mem.control_gap_streak = 0
        mem.active_streak = 0
        mem.skip_streak = 0
        mem.death_episode_started_at = None
        mem.awaiting_control_started_at = None

    def _step_dead(self, obs: LifecycleObservation, timestamp: float) -> None:
        """DEAD → COUNTDOWN on plate, or DEAD → RESPAWNED if the plate never comes."""
        mem = self._mem
        if obs.countdown_present is True:
            mem.present_streak += 1
            mem.absent_streak = 0
            mem.skip_streak = 0
            if mem.present_streak >= self.config.countdown_present_min_observations:
                mem.phase = "countdown"
                mem.latch = True
            return
        mem.present_streak = 0
        self._maybe_skip_countdown(obs, timestamp)

    def _maybe_skip_countdown(
        self, obs: LifecycleObservation, timestamp: float
    ) -> None:
        """Leave DEAD without a plate after the skip delay + control streak."""
        mem = self._mem
        started = mem.death_episode_started_at
        if started is None:
            return
        if timestamp - started < self.config.dead_skip_countdown_min_seconds:
            mem.skip_streak = 0
            return
        cue = _skip_dead_cue(obs)
        if cue is True:
            mem.skip_streak += 1
            if mem.skip_streak >= self.config.dead_skip_countdown_min_observations:
                mem.phase = "respawned"
                mem.control_absent_streak = 0
                mem.control_present_streak = 0
                mem.control_gap_streak = 0
                mem.active_streak = 0
                mem.skip_streak = 0
            return
        if cue is False:
            mem.skip_streak = 0

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
                mem.control_absent_streak = 0
                mem.control_present_streak = 0
                mem.control_gap_streak = 0
                mem.active_streak = 0
            return
        # None/missing countdown observation: do not advance absence streak.

    def _step_respawned(self, obs: LifecycleObservation, timestamp: float) -> None:
        """RESPAWNED → AWAITING_CONTROL: plate ended, control not established.

        RESPAWNED already means "the countdown plate ended but control has not
        returned", which is exactly the latch condition, so no separate
        control-absence evidence is required to arm it. Leaving the latch is
        still gated on sustained exit cues in the next state.

        Map viewing is independent evidence and neither blocks nor forces this.
        """
        mem = self._mem
        if obs.countdown_present is True:
            mem.phase = "countdown"
            mem.latch = True
            mem.present_streak = 1
            mem.absent_streak = 0
            mem.control_absent_streak = 0
            mem.control_present_streak = 0
            mem.control_gap_streak = 0
            mem.awaiting_control_started_at = None
            return

        mem.phase = "awaiting_control"
        mem.awaiting_control_latch = True
        mem.awaiting_control_started_at = timestamp
        mem.control_absent_streak = 0
        mem.control_present_streak = 0
        mem.control_gap_streak = 0

    def _step_awaiting_control(
        self, obs: LifecycleObservation, timestamp: float
    ) -> None:
        """AWAITING_CONTROL → ALIVE on return-control or delayed mid-tier.

        Strict cue is weapon+center (``return_control``), which may fire
        immediately. Mid-tier is HUD + timer with no death / countdown / map,
        and only after ``active_again_mid_tier_delay_seconds``. HUD ``detected``
        alone never exits. Up to ``active_again_max_gap_observations``
        consecutive misses keep the present streak.
        """
        mem = self._mem
        control = _latch_exit_cue(obs, mid_tier_ready=self._mid_tier_ready(timestamp))
        if control is True:
            mem.control_present_streak += 1
            mem.control_gap_streak = 0
            mem.control_absent_streak = 0
            if (
                mem.awaiting_control_latch
                and mem.control_present_streak
                >= self.config.active_again_min_observations
            ):
                self._leave_awaiting_to_alive()
            return
        if control is False:
            mem.control_absent_streak += 1
            mem.control_gap_streak += 1
            if mem.control_gap_streak > self.config.active_again_max_gap_observations:
                mem.control_present_streak = 0
                mem.control_gap_streak = 0
            return
        # None: hold streaks (do not treat missing as rising edge or gap).

    def _mid_tier_ready(self, timestamp: float) -> bool:
        """Whether the HUD+timer shortcut may count after the latch delay."""
        started = self._mem.awaiting_control_started_at
        if started is None:
            return False
        return timestamp - started >= self.config.active_again_mid_tier_delay_seconds

    def _leave_awaiting_to_alive(self) -> None:
        """Exit the post-respawn latch into ALIVE (ACTIVE_AGAIN edge)."""
        mem = self._mem
        mem.phase = "alive"
        mem.latch = False
        mem.awaiting_control_latch = False
        mem.present_streak = 0
        mem.absent_streak = 0
        mem.control_absent_streak = 0
        mem.control_present_streak = 0
        mem.control_gap_streak = 0
        mem.active_streak = 0
        mem.skip_streak = 0
        mem.death_episode_started_at = None
        mem.awaiting_control_started_at = None

    def _step_unknown(self, obs: LifecycleObservation, match_phase: MatchPhase) -> None:
        """UNKNOWN → ALIVE when in-match control evidence persists."""
        mem = self._mem
        if match_phase != "in_match" or not _positive_active(obs):
            mem.active_streak = 0
            return
        mem.active_streak += 1
        if mem.active_streak >= self.config.active_again_min_observations:
            mem.phase = "alive"
            mem.latch = False
            mem.awaiting_control_latch = False
            mem.active_streak = 0
            mem.awaiting_control_started_at = None


def extract_lifecycle_observation(
    frame: VisionFrameResult,
    *,
    death_config: DeathDetectorConfig,
    respawn_config: RespawnDetectorConfig,
    active_config: ActiveGameplayDetectorConfig,
    map_overlay_config: MapOverlayDetectorConfig | None = None,
    timer_config: TimerDetectorConfig | None = None,
) -> LifecycleObservation:
    """Pull usable death/respawn/active/map/match cues from one vision frame."""
    map_cfg = map_overlay_config or MapOverlayDetectorConfig()
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
        # Hold covers landing frames for the viewer; countdown is plate-only.
        countdown_present = reading.detected and reading.evidence_type != "hold"
        evidence.append(best_rs.id)

    active_detected: bool | None = None
    return_control: bool | None = None
    best_ag = _best_named(frame, "active_gameplay", ActiveGameplayReading)
    ag_usable = (
        best_ag is not None
        and best_ag.confidence >= active_config.min_usable_confidence
    )
    if ag_usable:
        reading = best_ag.reading
        assert isinstance(reading, ActiveGameplayReading)
        active_detected = reading.detected
        return_control = reading.return_control
        evidence.append(best_ag.id)

    map_overlay_present: bool | None = None
    best_map = _best_named(frame, "map_overlay", MapOverlayReading)
    map_usable = (
        best_map is not None
        and best_map.confidence >= map_cfg.min_usable_confidence
    )
    if map_usable:
        reading = best_map.reading
        assert isinstance(reading, MapOverlayReading)
        map_overlay_present = reading.present
        evidence.append(best_map.id)

    match_context: bool | None = None
    best_timer = _best_named(frame, "timer", TimerReading)
    timer_min = (
        timer_config.min_usable_confidence if timer_config is not None else 0.50
    )
    timer_seconds: float | None = None
    if best_timer is not None and best_timer.confidence >= timer_min:
        reading = best_timer.reading
        assert isinstance(reading, TimerReading)
        if reading.seconds_remaining is not None:
            match_context = True
            timer_seconds = reading.seconds_remaining
            evidence.append(best_timer.id)

    return LifecycleObservation(
        death_detected=death_detected,
        countdown_present=countdown_present,
        active_detected=active_detected,
        return_control=return_control,
        map_overlay_present=map_overlay_present,
        match_context=match_context,
        timer_seconds=timer_seconds,
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


def _control_present(obs: LifecycleObservation) -> bool | None:
    """Strict gameplay-control composite (weapon + HUD + center).

    Used for UNKNOWN → alive recovery. Map viewing is ignored.
    """
    if obs.death_detected:
        return False
    if obs.countdown_present is True:
        return False
    if obs.active_detected is None:
        return None
    return obs.active_detected is True


def _return_control(obs: LifecycleObservation) -> bool | None:
    """Softer latch-exit cue: weapon + center (HUD optional).

    Falls back to strict ``active_detected`` when ``return_control`` was not
    populated (older fixtures / partial readings).
    """
    if obs.death_detected:
        return False
    if obs.countdown_present is True:
        return False
    if obs.return_control is not None:
        return obs.return_control is True
    if obs.active_detected is None:
        return None
    return obs.active_detected is True


def _mid_tier_control(obs: LifecycleObservation) -> bool | None:
    """HUD + running timer, with death / countdown / map vetoes.

    Used only after the post-latch delay. HUD ``detected`` alone is not
    enough: timer must be present and map-view must be absent.
    """
    if obs.death_detected:
        return False
    if obs.countdown_present is True:
        return False
    if obs.map_overlay_present is True:
        return False
    if obs.active_detected is None:
        return None
    if obs.active_detected is True and obs.timer_seconds is not None:
        return True
    return False


def _skip_dead_cue(obs: LifecycleObservation) -> bool | None:
    """Control-return cue that may leave DEAD without a countdown plate.

    Map viewing is a veto only (same as mid-tier latch exit). A running
    timer is required so wipeout / fade frames cannot count.
    """
    if obs.death_detected or obs.countdown_present is True:
        return False
    if obs.map_overlay_present is True:
        return False
    if obs.timer_seconds is None:
        return False
    if _return_control(obs) is True:
        return True
    if obs.active_detected is True:
        return True
    return False


def _latch_exit_cue(
    obs: LifecycleObservation, *, mid_tier_ready: bool
) -> bool | None:
    """Present / absent / missing cue for awaiting_control → alive.

    Weapon+center wins immediately. Mid-tier HUD+timer counts only when
    ``mid_tier_ready`` is true. Missing observations hold the streak.
    """
    if _return_control(obs) is True:
        return True
    if mid_tier_ready and _mid_tier_control(obs) is True:
        return True
    if _return_control(obs) is None and _mid_tier_control(obs) is None:
        return None
    return False


def _positive_active(obs: LifecycleObservation) -> bool:
    """Active composite for UNKNOWN recovery (not ACTIVE_AGAIN after death)."""
    control = _control_present(obs)
    return control is True


def _active_for_snapshot(
    obs: LifecycleObservation,
    match_phase: MatchPhase,
    player_phase: PlayerLifecycle,
) -> bool | None:
    """ACTIVE_GAMEPLAY state: in-match and player alive.

    Detector HUD evidence may still be true during intro, death-cam, or
    respawn; those frames are not the active-gameplay state.
    """
    if match_phase != "in_match":
        return False
    if player_phase == "alive":
        return True
    if player_phase in {"dead", "countdown", "respawned", "awaiting_control"}:
        return False
    if obs.active_detected is None:
        return None
    return False


def _alive_for_phase(phase: PlayerLifecycle) -> bool | None:
    """Map lifecycle phase to the externally consumed alive bit."""
    if phase == "unknown":
        return None
    if phase == "alive":
        return True
    return False
