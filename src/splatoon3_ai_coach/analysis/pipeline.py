"""Orchestration for the cadence analyze command."""

import json
from pathlib import Path
from typing import Any

from loguru import logger

from splatoon3_ai_coach.analysis.scenario_context import (
    ScenarioContext,
    build_scenario_contexts,
    serialize_scenario_contexts,
)
from splatoon3_ai_coach.analysis.scenario_evidence import (
    ScenarioEvidencePack,
    SpecialReading,
    special_reading_from_persisted,
)
from splatoon3_ai_coach.analysis.scenario_models import Scenario
from splatoon3_ai_coach.analysis.scenarios import build_scenarios, format_scenario_timeline
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.vision.map_ink import MAP_OBSERVATIONS_FILENAME, MapObservation
from splatoon3_ai_coach.vision.models import (
    GameEvent,
    GameStateSnapshot,
    SpecialGaugeReading,
    VisionFrameResult,
    VisionManifest,
)
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
    write_scenarios(
        manifest.game_events,
        config,
        output_dir,
        manifest=manifest,
    )
    return manifest


def write_scenarios(
    events: list[GameEvent],
    config: AppConfig,
    output_dir: Path,
    *,
    manifest: VisionManifest | None = None,
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
    _write_scenario_contexts(
        events, scenarios, config, output_dir, manifest=manifest
    )
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
    *,
    manifest: VisionManifest | None = None,
) -> list[ScenarioContext]:
    """Persist scenario_contexts.json. Does not change scenarios.json."""
    evidence = load_scenario_evidence_pack(output_dir, manifest=manifest)
    contexts = build_scenario_contexts(
        events, scenarios, config.scenarios, evidence=evidence
    )
    path = output_dir / SCENARIO_CONTEXTS_JSON_FILENAME
    path.write_text(
        json.dumps(serialize_scenario_contexts(contexts), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote {} scenario contexts to {}", len(contexts), path)
    return contexts


def load_scenario_evidence_pack(
    analysis_dir: Path,
    *,
    manifest: VisionManifest | None = None,
) -> ScenarioEvidencePack:
    """Load persisted secondary evidence for ScenarioContext enrichment."""
    map_obs = _load_map_observations(analysis_dir / MAP_OBSERVATIONS_FILENAME)
    snapshots: list[GameStateSnapshot] = []
    special: list[SpecialReading] = []
    if manifest is not None:
        snapshots = list(manifest.state_snapshots)
        special = extract_special_readings(manifest.frame_results)
    else:
        # Fallback: read vision_manifest.json beside scenarios when re-run.
        raw = _read_json_object(analysis_dir / "vision_manifest.json")
        if raw:
            snapshots = [
                GameStateSnapshot.model_validate(item)
                for item in (raw.get("state_snapshots") or [])
                if isinstance(item, dict)
            ]
            frames = [
                VisionFrameResult.model_validate(item)
                for item in (raw.get("frame_results") or [])
                if isinstance(item, dict)
            ]
            special = extract_special_readings(frames)
    return ScenarioEvidencePack(
        map_observations=map_obs,
        state_snapshots=snapshots,
        special_readings=special,
    )


def extract_special_readings(
    frame_results: list[VisionFrameResult],
) -> list[SpecialReading]:
    """Pull persisted special_gauge readings from vision frame results."""
    out: list[SpecialReading] = []
    for frame in frame_results:
        ts = float(frame.timestamp)
        for det in frame.detections:
            reading = det.reading
            if not isinstance(reading, SpecialGaugeReading):
                continue
            out.append(
                special_reading_from_persisted(
                    reading,
                    video_time=ts,
                    observation_id=str(det.id),
                    confidence=float(det.confidence),
                )
            )
    out.sort(key=lambda r: r.video_time)
    return out


def _load_map_observations(path: Path) -> list[MapObservation]:
    """Load map_observations.json; missing file → []."""
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    out: list[MapObservation] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            out.append(MapObservation.model_validate(item))
        except Exception:
            continue
    return out


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
