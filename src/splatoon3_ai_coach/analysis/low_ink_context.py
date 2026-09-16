"""LOW_INK interval evidence for ScenarioContext (facts only).

Association is structural, not proximity-based: a ``LOW_INK`` GameEvent
attaches to a scenario when the two closed intervals intersect. There is no
lookback/lookforward window, no tolerance, and no nearest-neighbour search
over arbitrary timestamps.

``LOW_INK`` never becomes a ``Scenario`` member — ownership stays in
``scenarios.py``. An attached interval states only that the low-ink plate was
observed while the scenario interval was open. It is not ink management, a
resource judgment, or a cause of anything that happened in the scenario.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.scenario_models import Scenario
from splatoon3_ai_coach.analysis.scenarios import event_id
from splatoon3_ai_coach.vision.models import GameEvent, GameEventType


class LowInkInterval(BaseModel):
    """One observed LOW_INK interval overlapping a scenario."""

    event_id: str
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    # False when the source event never closed; the interval is then the
    # single observed instant, not a measured duration.
    end_time_observed: bool = True


class LowInkEvidence(BaseModel):
    """Sparse LOW_INK intervals overlapping a scenario. Not scenario members."""

    intervals: list[LowInkInterval] = Field(default_factory=list)


def interval_bounds(event: GameEvent) -> tuple[float, float]:
    """Closed bounds of a LOW_INK event; an unclosed interval is an instant."""
    start = float(event.start_time)
    if event.end_time is None:
        return start, start
    return start, max(start, float(event.end_time))


def overlaps_scenario(event: GameEvent, scenario: Scenario) -> bool:
    """Whether a LOW_INK interval intersects a scenario interval.

    Closed-interval intersection: touching endpoints count as overlap.
    """
    start, end = interval_bounds(event)
    return start <= float(scenario.end_time) and end >= float(scenario.start_time)


def build_low_ink_evidence(
    events: list[GameEvent],
    scenario: Scenario,
) -> LowInkEvidence | None:
    """Every LOW_INK interval overlapping ``scenario``; None when there are none."""
    intervals = [
        _interval(event)
        for event in events
        if event.event_type is GameEventType.LOW_INK
        and overlaps_scenario(event, scenario)
    ]
    if not intervals:
        return None
    intervals.sort(key=lambda item: (item.start_time, item.event_id))
    return LowInkEvidence(intervals=intervals)


def _interval(event: GameEvent) -> LowInkInterval:
    """Factual pass-through view of one LOW_INK event."""
    start, end = interval_bounds(event)
    return LowInkInterval(
        event_id=event_id(event),
        start_time=start,
        end_time=end,
        end_time_observed=event.end_time is not None,
    )
