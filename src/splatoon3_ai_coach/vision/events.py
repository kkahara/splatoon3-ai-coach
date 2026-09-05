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

    DEATH: first transition into ``player_alive=False`` from True/None.
    RESPAWN: rising edge into lifecycle ``respawned`` (countdown anchor crossed;
    player is still not alive).
    ACTIVE_AGAIN: rising edge into lifecycle ``alive`` / ``player_alive=True``
    after a death-side episode.
    SPLAT: rising edge into ``player_splatted=True``.
    """
    debounce_s = config.debounce_ms / 1000.0
    events: list[GameEvent] = []
    previous_alive: bool | None = None
    previous_splatted: bool | None = None
    previous_lifecycle: str | None = None
    last_death_at = float("-inf")
    last_splat_at = float("-inf")
    last_respawn_at = float("-inf")
    last_active_again_at = float("-inf")

    for snapshot in snapshots:
        if (
            snapshot.player_alive is False
            and previous_alive is not False
            and snapshot.timestamp - last_death_at >= debounce_s
        ):
            events.append(_typed_event(snapshot, GameEventType.DEATH))
            last_death_at = snapshot.timestamp
        if snapshot.player_alive is not None:
            previous_alive = snapshot.player_alive

        if (
            snapshot.player_lifecycle == "respawned"
            and previous_lifecycle != "respawned"
            and snapshot.timestamp - last_respawn_at >= debounce_s
        ):
            events.append(_typed_event(snapshot, GameEventType.RESPAWN))
            last_respawn_at = snapshot.timestamp

        if (
            snapshot.player_lifecycle == "alive"
            and previous_lifecycle in {"respawned", "dead", "countdown"}
            and snapshot.player_alive is True
            and snapshot.timestamp - last_active_again_at >= debounce_s
        ):
            events.append(_typed_event(snapshot, GameEventType.ACTIVE_AGAIN))
            last_active_again_at = snapshot.timestamp

        previous_lifecycle = snapshot.player_lifecycle

        if (
            snapshot.player_splatted is True
            and previous_splatted is not True
            and snapshot.timestamp - last_splat_at >= debounce_s
        ):
            events.append(_typed_event(snapshot, GameEventType.SPLAT))
            last_splat_at = snapshot.timestamp
        previous_splatted = snapshot.player_splatted

    return events


def _typed_event(snapshot: GameStateSnapshot, event_type: GameEventType) -> GameEvent:
    """Build a semantic event from the snapshot that first asserts the edge."""
    source_frames = [snapshot.source_frame] if snapshot.source_frame is not None else []
    return GameEvent(
        start_time=snapshot.timestamp,
        event_type=event_type,
        confidence=1.0 if snapshot.quality != "unknown" else 0.7,
        evidence_ids=list(snapshot.evidence_ids),
        source_frames=source_frames,
    )
