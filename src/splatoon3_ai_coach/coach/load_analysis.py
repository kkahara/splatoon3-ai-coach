"""Load analysis dumps for coaching prototype (read-only)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from splatoon3_ai_coach.analysis.match_bounds import before_match_end, match_end_time
from splatoon3_ai_coach.analysis.pipeline import (
    SCENARIO_CONTEXTS_JSON_FILENAME,
    SCENARIOS_JSON_FILENAME,
)
from splatoon3_ai_coach.analysis.scenario_context import ScenarioContext
from splatoon3_ai_coach.analysis.scenario_models import Scenario, ScenarioType
from splatoon3_ai_coach.coach.game_clock import GameClock, build_game_clock
from splatoon3_ai_coach.coach.playback_clock import (
    PlaybackClockInfo,
    estimate_playback_clock,
)
from splatoon3_ai_coach.coach.player_count_clock import (
    PlayerCountClock,
    build_player_count_clock,
)
from splatoon3_ai_coach.config.models import PlaybackClockConfig
from splatoon3_ai_coach.media.vision_manifest import load_vision_manifest
from splatoon3_ai_coach.vision.score_fusion import resolve_battle_mode


@dataclass(frozen=True)
class CoachAnalysisBundle:
    """Scenarios, contexts, and secondary clocks from an analyze output dir."""

    analysis_dir: Path
    scenarios: list[Scenario]
    contexts: list[ScenarioContext]
    game_clock: GameClock
    player_count_clock: PlayerCountClock
    battle_mode_id: str | None = None
    playback_clock: PlaybackClockInfo | None = None
    analysis_id: str | None = None


def load_coach_analysis_bundle(
    analysis_dir: Path,
    *,
    min_usable_confidence: float,
    playback_config: PlaybackClockConfig | None = None,
) -> CoachAnalysisBundle:
    """Load scenarios, contexts, game clock, player-count clock, battle mode.

    Battle mode uses the same stored-``match_intro`` latch as score fusion.
    The playback rate is estimated once per match from the game clock.
    Clock and roster samples at or after ``MATCH_END`` are dropped.
    """
    root = analysis_dir.resolve()
    scenarios = _load_scenarios(root / SCENARIOS_JSON_FILENAME)
    contexts = _load_contexts(root / SCENARIO_CONTEXTS_JSON_FILENAME)
    manifest = load_vision_manifest(root)
    match_end = match_end_time(manifest.game_events)
    clock = build_game_clock(
        before_match_end(manifest.frame_results, lambda item: item.timestamp, match_end),
        min_usable_confidence=min_usable_confidence,
    )
    player_count_clock = build_player_count_clock(
        before_match_end(manifest.state_snapshots, lambda item: item.timestamp, match_end)
    )
    return CoachAnalysisBundle(
        analysis_dir=root,
        scenarios=scenarios,
        contexts=contexts,
        game_clock=clock,
        player_count_clock=player_count_clock,
        battle_mode_id=resolve_battle_mode(list(manifest.frame_results)),
        playback_clock=estimate_playback_clock(clock, playback_config),
        analysis_id=manifest.analysis.analysis_id,
    )


def select_primary_scenario_ids(
    scenarios: list[Scenario],
    *,
    limit: int,
    prefer: ScenarioType = ScenarioType.DEATH_EPISODE,
    only_preferred: bool = True,
) -> list[str]:
    """Select primary coaching units.

    Default: ``DEATH_EPISODE`` only. ENGAGEMENT / MAP_CHECK stay relation-linked
    supporting context on CoachInput, not primary units, unless
    ``only_preferred=False`` (legacy fill with other types after preferred).
    """
    if limit <= 0:
        return []
    preferred = [s.scenario_id for s in scenarios if s.scenario_type is prefer]
    if only_preferred:
        return preferred[:limit]
    others = [s.scenario_id for s in scenarios if s.scenario_type is not prefer]
    return (preferred + others)[:limit]


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
