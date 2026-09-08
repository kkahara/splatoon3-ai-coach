"""Scenario types and the coaching-opportunity model.

Scenarios describe what happened. They do not contain recommendations.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ScenarioType(StrEnum):
    """Named gameplay situations. Only some constructors exist in v1."""

    POST_DEATH_RECOVERY = "post_death_recovery"
    MAP_CHECK = "map_check"
    ENGAGEMENT = "engagement"
    SPECIAL_OPPORTUNITY = "special_opportunity"
    OBJECTIVE_SITUATION = "objective_situation"
    SUPER_JUMP_CONTEXT = "super_jump_context"


class ScenarioOutcome(StrEnum):
    """Closed-vocabulary result of a constructed scenario."""

    RECOVERED = "recovered"
    INCOMPLETE = "incomplete"
    OBSERVED = "observed"
    FRAGGED = "fragged"
    DIED = "died"


class Scenario(BaseModel):
    """A meaningful gameplay situation derived from GameEvents."""

    scenario_id: str
    scenario_type: ScenarioType
    start_time: float = Field(ge=0)
    end_time: float = Field(ge=0)
    event_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    context: dict[str, Any] = Field(default_factory=dict)
    outcome: ScenarioOutcome
