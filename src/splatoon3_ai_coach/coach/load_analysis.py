"""Load analysis dumps for coaching prototype (read-only)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from splatoon3_ai_coach.analysis.pipeline import (
    SCENARIO_CONTEXTS_JSON_FILENAME,
    SCENARIOS_JSON_FILENAME,
)
from splatoon3_ai_coach.analysis.scenario_context import ScenarioContext
from splatoon3_ai_coach.analysis.scenario_models import Scenario, ScenarioType
from splatoon3_ai_coach.coach.game_clock import GameClock, build_game_clock
from splatoon3_ai_coach.coach.player_count_clock import (
    PlayerCountClock,
    build_player_count_clock,
)
from splatoon3_ai_coach.media.vision_manifest import load_vision_manifest


@dataclass(frozen=True)
class CoachAnalysisBundle:
    """Scenarios, contexts, and secondary clocks from an analyze output dir."""

    analysis_dir: Path
    scenarios: list[Scenario]
    contexts: list[ScenarioContext]
    game_clock: GameClock
    player_count_clock: PlayerCountClock


def load_coach_analysis_bundle(
    analysis_dir: Path,
    *,
    min_usable_confidence: float,
) -> CoachAnalysisBundle:
    """Load scenarios, contexts, game clock, and player-count clock."""
    root = analysis_dir.resolve()
    scenarios = _load_scenarios(root / SCENARIOS_JSON_FILENAME)
    contexts = _load_contexts(root / SCENARIO_CONTEXTS_JSON_FILENAME)
    manifest = load_vision_manifest(root)
    clock = build_game_clock(
        list(manifest.frame_results),
        min_usable_confidence=min_usable_confidence,
    )
    player_count_clock = build_player_count_clock(list(manifest.state_snapshots))
    return CoachAnalysisBundle(
        analysis_dir=root,
        scenarios=scenarios,
        contexts=contexts,
        game_clock=clock,
        player_count_clock=player_count_clock,
    )


def select_primary_scenario_ids(
    scenarios: list[Scenario],
    *,
    limit: int,
    prefer: ScenarioType = ScenarioType.DEATH_EPISODE,
) -> list[str]:
    """Prefer DEATH_EPISODE units, then fill with other types up to ``limit``."""
    if limit <= 0:
        return []
    preferred = [s.scenario_id for s in scenarios if s.scenario_type is prefer]
    others = [s.scenario_id for s in scenarios if s.scenario_type is not prefer]
    ordered = preferred + others
    return ordered[:limit]


def _load_scenarios(path: Path) -> list[Scenario]:
    if not path.is_file():
        raise FileNotFoundError(f"scenarios.json not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"scenarios.json must be a list: {path}")
    return [Scenario.model_validate(item) for item in payload]


def _load_contexts(path: Path) -> list[ScenarioContext]:
    if not path.is_file():
        raise FileNotFoundError(f"scenario_contexts.json not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"scenario_contexts.json must be a list: {path}")
    return [ScenarioContext.model_validate(item) for item in payload]
