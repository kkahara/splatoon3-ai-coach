"""Pure state-to-event transition rules."""

from splatoon3_ai_coach.config.models import EventFusionConfig
from splatoon3_ai_coach.vision.models import GameEvent, GameStateSnapshot


def infer_events(
    snapshots: list[GameStateSnapshot],
    config: EventFusionConfig,
) -> list[GameEvent]:
    """Infer semantic game events from state transitions.

    Phase 3 intentionally emits no timer-derived events. Future detectors will
    add transition rules here without importing detector implementations.
    """
    _ = config
    _ = snapshots
    return []
