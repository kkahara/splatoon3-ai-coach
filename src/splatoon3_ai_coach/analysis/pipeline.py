"""Orchestration for the cadence analyze command."""

import json
from pathlib import Path

from loguru import logger

from splatoon3_ai_coach.analysis.scenario_context import (
    ScenarioContext,
    build_scenario_contexts,
    serialize_scenario_contexts,
)
from splatoon3_ai_coach.analysis.scenario_models import Scenario
from splatoon3_ai_coach.analysis.scenarios import build_scenarios, format_scenario_timeline
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.vision.models import GameEvent, VisionManifest
from splatoon3_ai_coach.vision.pipeline import run_vision

SCENARIOS_JSON_FILENAME = "scenarios.json"
SCENARIOS_TXT_FILENAME = "scenarios.txt"
SCENARIO_CONTEXTS_JSON_FILENAME = "scenario_contexts.json"


def run_analysis(
    video: Path,
    config: AppConfig,
    output_dir: Path,
    *,
    debug_persist_cadence_frames: bool = False,
) -> VisionManifest:
    """Run cadence vision analysis, then derive scenarios from GameEvents."""
    manifest = run_vision(
        video,
        config,
        output_dir,
        debug_persist_cadence_frames=debug_persist_cadence_frames,
    )
    write_scenarios(manifest.game_events, config, output_dir)
    return manifest


def write_scenarios(
    events: list[GameEvent],
    config: AppConfig,
    output_dir: Path,
) -> list[Scenario]:
    """Persist scenarios.json and a debug timeline beside the vision manifest."""
    scenarios = build_scenarios(events, config.scenarios)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / SCENARIOS_JSON_FILENAME
    json_path.write_text(
        json.dumps(
            [item.model_dump(mode="json") for item in scenarios],
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    text_path = output_dir / SCENARIOS_TXT_FILENAME
    text_path.write_text(
        format_scenario_timeline(events, scenarios),
        encoding="utf-8",
    )
    _write_scenario_contexts(events, scenarios, config, output_dir)
    logger.info(
        "Wrote {} scenarios to {} and {}",
        len(scenarios),
        json_path,
        text_path,
    )
    return scenarios


def _write_scenario_contexts(
    events: list[GameEvent],
    scenarios: list[Scenario],
    config: AppConfig,
    output_dir: Path,
) -> list[ScenarioContext]:
    """Persist scenario_contexts.json. Does not change scenarios.json."""
    contexts = build_scenario_contexts(events, scenarios, config.scenarios)
    path = output_dir / SCENARIO_CONTEXTS_JSON_FILENAME
    path.write_text(
        json.dumps(serialize_scenario_contexts(contexts), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote {} scenario contexts to {}", len(contexts), path)
    return contexts
