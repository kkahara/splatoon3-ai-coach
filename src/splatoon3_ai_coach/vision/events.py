"""Pure state-to-event transition rules."""

from splatoon3_ai_coach.config.models import EventFusionConfig
from splatoon3_ai_coach.vision.models import GameEvent, GameEventType, GameStateSnapshot


def infer_events(
    snapshots: list[GameStateSnapshot],
    config: EventFusionConfig,
) -> list[GameEvent]:
    """Infer semantic game events from domain state transitions.

    Detectors debounce noisy HUD animations. This layer only turns fused
    ``GameStateSnapshot`` edges into ``GameEvent`` records.

    A DEATH is emitted on the first transition into ``player_alive=False``
    from ``True`` or ``None``. Staying dead does not emit again. Returning
    to alive requires explicit alive evidence in state fusion (not yet
    implemented), so later deaths in the same life cycle will not fire until
    that cue exists.

    A SPLAT is emitted on the rising edge into ``player_splatted=True`` from
    ``None`` or ``False``. Holding ``True`` does not emit again; returning to
    ``None`` resets the edge for a later kill.
    """
    debounce_s = config.debounce_ms / 1000.0
    events: list[GameEvent] = []
    previous_alive: bool | None = None
    previous_splatted: bool | None = None
    last_death_at = float("-inf")
    last_splat_at = float("-inf")

    for snapshot in snapshots:
        if (
            snapshot.player_alive is False
            and previous_alive is not False
            and snapshot.timestamp - last_death_at >= debounce_s
        ):
            events.append(_death_event(snapshot))
            last_death_at = snapshot.timestamp
        if snapshot.player_alive is not None:
            previous_alive = snapshot.player_alive

        if (
            snapshot.player_splatted is True
            and previous_splatted is not True
            and snapshot.timestamp - last_splat_at >= debounce_s
        ):
            events.append(_splat_event(snapshot))
            last_splat_at = snapshot.timestamp
        previous_splatted = snapshot.player_splatted

    return events


def _death_event(snapshot: GameStateSnapshot) -> GameEvent:
    """Build a DEATH event from the snapshot that first asserts not-alive."""
    source_frames = [snapshot.source_frame] if snapshot.source_frame is not None else []
    return GameEvent(
        start_time=snapshot.timestamp,
        event_type=GameEventType.DEATH,
        confidence=1.0 if snapshot.quality != "unknown" else 0.7,
        evidence_ids=list(snapshot.evidence_ids),
        source_frames=source_frames,
    )


def _splat_event(snapshot: GameStateSnapshot) -> GameEvent:
    """Build a SPLAT event from the snapshot that first asserts a local kill."""
    source_frames = [snapshot.source_frame] if snapshot.source_frame is not None else []
    return GameEvent(
        start_time=snapshot.timestamp,
        event_type=GameEventType.SPLAT,
        confidence=1.0 if snapshot.quality != "unknown" else 0.7,
        evidence_ids=list(snapshot.evidence_ids),
        source_frames=source_frames,
    )
