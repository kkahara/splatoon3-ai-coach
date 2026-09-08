"""Gameplay analysis: metrics, session aggregation, and scenarios."""

from splatoon3_ai_coach.analysis.metrics import MetricScore, compute_metrics
from splatoon3_ai_coach.analysis.scenario_context import (
    CombatContext,
    MapContext,
    RecoveryContext,
    ScenarioContext,
    TimelineContext,
    build_scenario_context,
    build_scenario_contexts,
    serialize_scenario_contexts,
)
from splatoon3_ai_coach.analysis.scenario_models import (
    Scenario,
    ScenarioOutcome,
    ScenarioType,
)
from splatoon3_ai_coach.analysis.scenarios import (
    build_scenarios,
    event_id,
    format_scenario_timeline,
)
from splatoon3_ai_coach.analysis.session import GameSession, TimelinePoint

__all__ = [
    "GameSession",
    "MetricScore",
    "CombatContext",
    "MapContext",
    "RecoveryContext",
    "Scenario",
    "ScenarioContext",
    "ScenarioOutcome",
    "ScenarioType",
    "TimelineContext",
    "TimelinePoint",
    "build_scenario_context",
    "build_scenario_contexts",
    "build_scenarios",
    "compute_metrics",
    "event_id",
    "format_scenario_timeline",
    "serialize_scenario_contexts",
]
