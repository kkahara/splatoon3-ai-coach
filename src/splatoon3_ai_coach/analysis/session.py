"""GameSession: timeline + scores for a single gameplay recording."""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from splatoon3_ai_coach.vision.models import GameEvent


class TimelinePoint(BaseModel):
    """HUD and metric readings at a fixed point in time."""

    timestamp: float = Field(ge=0)
    readings: dict[str, float | int | str] = Field(default_factory=dict)


class GameSession(BaseModel):
    """Aggregated view of one gameplay video: timeline, events, and scores."""

    video: Path
    duration_seconds: float = Field(ge=0)
    timeline: list[TimelinePoint] = Field(default_factory=list)
    events: list[GameEvent] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
