"""Analysis domain bound: no coaching evidence may originate after MATCH_END.

The domain is ``[match_start, MATCH_END)`` when a ``MATCH_END`` GameEvent
exists, else the whole video. Samples at or after MATCH_END are dropped
before ScenarioContext / CoachInput evidence is built.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

from splatoon3_ai_coach.vision.models import GameEvent, GameEventType

T = TypeVar("T")


def match_end_time(events: Iterable[GameEvent]) -> float | None:
    """Start of the first ``MATCH_END`` event, or ``None`` (domain = whole video)."""
    times = [
        event.start_time
        for event in events
        if event.event_type is GameEventType.MATCH_END
    ]
    return min(times) if times else None


def before_match_end(
    items: Iterable[T], time_of: Callable[[T], float], match_end: float | None
) -> list[T]:
    """Items whose video time is strictly before ``match_end`` (all when ``None``)."""
    if match_end is None:
        return list(items)
    return [item for item in items if float(time_of(item)) < match_end]
