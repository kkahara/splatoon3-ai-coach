"""Authoritative gameplay events from fused snapshots.

Detector readings stay on ``VisionFrameResult``. This module only turns
``GameStateSnapshot`` edges into ``GameEvent`` records.
"""

from splatoon3_ai_coach.config.models import EventFusionConfig
from splatoon3_ai_coach.vision.models import (
    GameEvent,
    GameEventReason,
    GameEventSource,
    GameEventType,
    GameStateSnapshot,
    PlayerLifecycle,
    SplatBannerInstance,
)
from splatoon3_ai_coach.vision.splat_episodes import SplatEpisodeFuser


def infer_events(
    snapshots: list[GameStateSnapshot],
    config: EventFusionConfig,
) -> list[GameEvent]:
    """Build the match event timeline from fused domain state.

    DEATH: first transition into ``player_alive=False`` from True/None.
    RESPAWN: first rising edge into lifecycle ``respawned`` per death episode.
    ACTIVE_AGAIN: ``awaiting_control → alive`` (control return / gameplay).
    SPLAT: one event per newly opened splat episode (banner-instance identity
    via fingerprint and occupied stack slot — see ``SplatEpisodeFuser``).
    MAP_OVERLAY: interval while fused ``map_overlay_present`` is true.

    ``EventFusionConfig.debounce_ms`` only guards lifecycle edges. It must
    not define splat episodes.
    """
    debounce_s = config.debounce_ms / 1000.0
    events: list[GameEvent] = []
    previous_alive: bool | None = None
    previous_lifecycle: PlayerLifecycle | None = None
    previous_map: bool | None = None
    last_death_at = float("-inf")
    last_respawn_at = float("-inf")
    last_active_again_at = float("-inf")
    respawn_emitted_this_death_episode = False
    splat_fuser = SplatEpisodeFuser(config)
    open_map: GameEvent | None = None

    for snapshot in snapshots:
        death = _death_event(
            snapshot, previous_alive, previous_lifecycle, last_death_at, debounce_s
        )
        if death is not None:
            events.append(death)
            last_death_at = snapshot.timestamp
            respawn_emitted_this_death_episode = False
        if snapshot.player_alive is not None:
            previous_alive = snapshot.player_alive

        respawn = _respawn_event(
            snapshot,
            previous_lifecycle,
            respawn_emitted_this_death_episode,
            last_respawn_at,
            debounce_s,
        )
        if respawn is not None:
            events.append(respawn)
            last_respawn_at = snapshot.timestamp
            respawn_emitted_this_death_episode = True

        active = _active_again_event(
            snapshot, previous_lifecycle, last_active_again_at, debounce_s
        )
        if active is not None:
            events.append(active)
            last_active_again_at = snapshot.timestamp

        previous_lifecycle = snapshot.player_lifecycle
        for opened in splat_fuser.step(snapshot.splat_instances).opened:
            events.append(_splat_event(snapshot, opened))

        open_map, started = _step_map_overlay(snapshot, previous_map, open_map)
        if started is not None:
            events.append(started)
        previous_map = snapshot.map_overlay_present

    if open_map is not None and snapshots:
        open_map.end_time = snapshots[-1].timestamp
    return events


def _death_event(
    snapshot: GameStateSnapshot,
    previous_alive: bool | None,
    previous_lifecycle: PlayerLifecycle | None,
    last_death_at: float,
    debounce_s: float,
) -> GameEvent | None:
    """DEATH on the first falling edge of ``player_alive``."""
    if snapshot.player_alive is not False:
        return None
    if previous_alive is False:
        return None
    if snapshot.timestamp - last_death_at < debounce_s:
        return None
    reason = (
        GameEventReason.ALIVE_TO_DEAD
        if previous_alive is True
        else GameEventReason.UNKNOWN_TO_DEAD
    )
    from_life = previous_lifecycle or ("alive" if previous_alive is True else "unknown")
    return _event(
        snapshot,
        GameEventType.DEATH,
        source=GameEventSource.LIFECYCLE,
        reason=reason,
        from_lifecycle=from_life,
        to_lifecycle="dead",
    )


def _respawn_event(
    snapshot: GameStateSnapshot,
    previous_lifecycle: PlayerLifecycle | None,
    already_emitted: bool,
    last_respawn_at: float,
    debounce_s: float,
) -> GameEvent | None:
    """RESPAWN on the first enter-``respawned`` per death episode."""
    if snapshot.player_lifecycle != "respawned":
        return None
    if previous_lifecycle == "respawned" or already_emitted:
        return None
    if snapshot.timestamp - last_respawn_at < debounce_s:
        return None
    plate = snapshot.countdown_confirmed_this_death_episode is True
    reason = (
        GameEventReason.COUNTDOWN_PLATE_ENDED
        if plate
        else GameEventReason.SKIP_COUNTDOWN_CONTROL
    )
    from_life = previous_lifecycle or "dead"
    return _event(
        snapshot,
        GameEventType.RESPAWN,
        source=GameEventSource.LIFECYCLE,
        reason=reason,
        from_lifecycle=from_life,
        to_lifecycle="respawned",
    )


def _active_again_event(
    snapshot: GameStateSnapshot,
    previous_lifecycle: PlayerLifecycle | None,
    last_active_again_at: float,
    debounce_s: float,
) -> GameEvent | None:
    """ACTIVE_AGAIN: control returned and in-match play resumed."""
    if snapshot.player_lifecycle != "alive" or snapshot.player_alive is not True:
        return None
    if previous_lifecycle != "awaiting_control":
        return None
    if snapshot.timestamp - last_active_again_at < debounce_s:
        return None
    return _event(
        snapshot,
        GameEventType.ACTIVE_AGAIN,
        source=GameEventSource.LIFECYCLE,
        reason=GameEventReason.AWAITING_CONTROL_TO_ALIVE,
        from_lifecycle="awaiting_control",
        to_lifecycle="alive",
    )


def _splat_event(
    snapshot: GameStateSnapshot, instance: SplatBannerInstance
) -> GameEvent:
    """SPLAT for one newly opened banner instance (not a time window)."""
    return _event(
        snapshot,
        GameEventType.SPLAT,
        source=GameEventSource.SPLAT_EPISODE,
        reason=GameEventReason.SPLAT_INSTANCE_OPENED,
        splat_fingerprint=instance.fingerprint,
    )


def _step_map_overlay(
    snapshot: GameStateSnapshot,
    previous_map: bool | None,
    open_event: GameEvent | None,
) -> tuple[GameEvent | None, GameEvent | None]:
    """Open or close a fused map-overlay interval. Does not drive lifecycle."""
    present = snapshot.map_overlay_present is True
    was = previous_map is True
    if present and not was:
        started = _event(
            snapshot,
            GameEventType.MAP_OVERLAY,
            source=GameEventSource.STATE,
            reason=GameEventReason.MAP_OVERLAY_PRESENT,
        )
        return started, started
    if was and not present and open_event is not None:
        open_event.end_time = snapshot.timestamp
        return None, None
    return open_event, None


def _event(
    snapshot: GameStateSnapshot,
    event_type: GameEventType,
    *,
    source: GameEventSource,
    reason: GameEventReason,
    from_lifecycle: PlayerLifecycle | None = None,
    to_lifecycle: PlayerLifecycle | None = None,
    splat_fingerprint: str | None = None,
) -> GameEvent:
    """Attach provenance from the snapshot that first asserts the edge."""
    source_frames = [snapshot.source_frame] if snapshot.source_frame is not None else []
    return GameEvent(
        start_time=snapshot.timestamp,
        event_type=event_type,
        source=source,
        reason=reason,
        from_lifecycle=from_lifecycle,
        to_lifecycle=to_lifecycle,
        splat_fingerprint=splat_fingerprint,
        confidence=1.0 if snapshot.quality != "unknown" else 0.7,
        evidence_ids=list(snapshot.evidence_ids),
        source_frames=source_frames,
    )
