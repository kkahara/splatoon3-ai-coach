"""Gameplay metrics computed from vision outputs and timelines."""

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from splatoon3_ai_coach.analysis.session import GameSession


class MetricScore(BaseModel):
    """A single scored metric with optional evidence reference."""

    name: str
    value: float
    good: bool | None = None
    evidence: str | None = None


def compute_metrics(session: "GameSession") -> list[MetricScore]:
    """Derive good-vs-bad gameplay metrics from a session timeline.

    TODO(phase-4): implement positioning, special usage, objective control, etc.
    """
    return []
