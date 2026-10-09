"""MATCH_END: clock-zero boundary, overtime / knockout fallback, and its effects.

The golden case is ja-2026-09-20 20-02-59 (Clam Blitz): the player dies with
1 s on the clock, the clock reads 0:00 at 317.5 s, and the recording runs
another 33 s. Expected: one death episode ending at MATCH_END, no gameplay
events or coaching evidence after it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from splatoon3_ai_coach.analysis.match_bounds import before_match_end, match_end_time
from splatoon3_ai_coach.analysis.pipeline import write_scenarios
from splatoon3_ai_coach.analysis.scenario_context import ScenarioContext
from splatoon3_ai_coach.analysis.scenarios import build_scenarios
from splatoon3_ai_coach.cli import app
from splatoon3_ai_coach.coach.artifacts import read_coaching_index
from splatoon3_ai_coach.coach.evidence_contract import death_lifecycle_statements
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import AppConfig, VisionLanguage
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.media.vision_manifest import (
    load_vision_manifest,
    save_vision_manifest,
)
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.match_end import detect_match_end, match_end_event
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    GameEvent,
    GameEventReason,
    GameEventType,
    GameStateSnapshot,
    TimerReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.pipeline import refuse_vision_manifest

FIXTURE = Path(__file__).parent / "fixtures" / "traces" / "match_end_2059.json"
MATCH_END_AT = 317.5
DEATH_AT = 317.0


@pytest.fixture(scope="module")
def config() -> AppConfig:
    return load_config(default_config_path())


def _frame(
    t: float, seconds: float | None = None, conf: float = 0.9
) -> VisionFrameResult:
    detections = []
    if seconds is not None:
        reading = TimerReading(display=f"{int(seconds)}", seconds_remaining=seconds)
        detections.append(
            DetectorResult(
                id=f"timer@{t}",
                detector_name="timer",
                detector_version="test",
                confidence=conf,
                reading=reading,
            )
        )
    return VisionFrameResult(
        frame_id=f"f{t}", timestamp=t, source="cadence", detections=detections
    )


def _scan(timer: dict[float, float], end: float) -> list[VisionFrameResult]:
    """Cadence frames every 0.5 s to ``end``; ``timer`` maps time → reading."""
    times = [i * 0.5 for i in range(int(end * 2) + 1)]
    return [_frame(t, timer.get(t)) for t in times]


def _ticking(start: float, stop: float, remaining_at_start: float) -> dict[float, float]:
    """A clock counting down one second per video second, half-second cadence."""
    out: dict[float, float] = {}
    t = start
    while t <= stop:
        out[t] = max(0.0, remaining_at_start - (t - start))
        t += 0.5
    return out


def _decide(config: AppConfig, frames: list[VisionFrameResult]):
    return detect_match_end(
        frames, config.vision.timer, config.vision.lifecycle, config.vision.match_end
    )


def test_clock_zero_then_clock_gone_is_match_end(config: AppConfig) -> None:
    timer = {0.0: 300.0, 1.0: 300.0, **_ticking(2.0, 12.0, 10.0)}
    decision = _decide(config, _scan(timer, 40.0))
    assert decision is not None
    assert decision.reason is GameEventReason.CLOCK_ZERO
    assert decision.time == 12.0


def test_recording_cut_right_after_zero_is_clock_zero(config: AppConfig) -> None:
    timer = _ticking(0.0, 5.0, 5.0)
    decision = _decide(config, _scan(timer, 5.5))
    assert decision is not None
    assert decision.reason is GameEventReason.CLOCK_ZERO
    assert decision.time == 5.0


def _with_hud_loss(config: AppConfig) -> AppConfig:
    enabled = config.model_copy(deep=True)
    enabled.vision.match_end.hud_loss_fallback = True
    return enabled


def test_overtime_banner_has_no_match_end_by_default(config: AppConfig) -> None:
    """Real overtime: 0:01 then the OVERTIME banner (no reading) while play goes on."""
    timer = {t: s for t, s in _ticking(0.0, 9.5, 10.0).items() if s >= 1.0}
    assert _decide(config, _scan(timer, 60.0)) is None


def test_overtime_zero_keeps_showing_is_not_clock_zero(config: AppConfig) -> None:
    timer = _ticking(0.0, 5.0, 5.0)
    timer.update({t / 2: 0.0 for t in range(10, 61)})  # 0:00 held 5..30 s
    assert _decide(config, _scan(timer, 60.0)) is None
    decision = _decide(_with_hud_loss(config), _scan(timer, 60.0))
    assert decision is not None
    assert decision.reason is GameEventReason.HUD_LOSS
    assert decision.time == 30.0


def test_knockout_is_hud_loss_only_when_opted_in(config: AppConfig) -> None:
    timer = _ticking(0.0, 20.0, 120.0)
    assert _decide(config, _scan(timer, 50.0)) is None
    decision = _decide(_with_hud_loss(config), _scan(timer, 50.0))
    assert decision is not None
    assert decision.reason is GameEventReason.HUD_LOSS
    assert decision.time == 20.0


def test_mid_match_zero_misread_is_ignored(config: AppConfig) -> None:
    timer = _ticking(0.0, 30.0, 120.0)
    timer[10.0] = 0.0
    assert _decide(config, _scan(timer, 31.0)) is None


def test_video_ending_before_hold_has_no_match_end(config: AppConfig) -> None:
    timer = _ticking(0.0, 20.0, 120.0)
    assert _decide(_with_hud_loss(config), _scan(timer, 25.0)) is None


def test_disabled_config_has_no_match_end(config: AppConfig) -> None:
    disabled = config.model_copy(deep=True)
    disabled.vision.match_end.enabled = False
    timer = _ticking(0.0, 5.0, 5.0)
    assert _decide(disabled, _scan(timer, 30.0)) is None


def _snap(t: float, lifecycle: str, *, map_open: bool = False) -> GameStateSnapshot:
    return GameStateSnapshot(
        timestamp=t,
        player_alive=lifecycle == "alive",
        player_lifecycle=lifecycle,
        map_overlay_present=map_open,
        match_phase="in_match",
    )


def _match_end_at(config: AppConfig, t: float) -> GameEvent:
    decision = _decide(config, _scan(_ticking(0.0, t, t), t + 0.5))
    assert decision is not None and decision.time == t
    return match_end_event(decision)


def test_events_stop_at_match_end_and_intervals_close(config: AppConfig) -> None:
    end = _match_end_at(config, 10.0)
    snaps = [_snap(t / 2, "alive", map_open=t == 18) for t in range(0, 19)]
    snaps += [_snap(9.5, "dead", map_open=True), _snap(10.0, "dead", map_open=True)]
    snaps += [_snap(10.5 + i / 2, "respawned") for i in range(4)]
    snaps += [_snap(13.0, "alive")]
    events = infer_events(snaps, config.vision.events, match_end=end)
    assert events[-1].event_type is GameEventType.MATCH_END
    assert all(e.start_time < 10.0 for e in events[:-1])
    (overlay,) = [e for e in events if e.event_type is GameEventType.MAP_OVERLAY]
    assert overlay.end_time == 10.0
    assert [e.event_type for e in events].count(GameEventType.RESPAWN) == 0


def test_death_before_match_end_closes_episode_at_match_end(config: AppConfig) -> None:
    end = _match_end_at(config, 10.0)
    snaps = [_snap(t / 2, "alive") for t in range(0, 19)]
    snaps += [_snap(9.5, "dead"), _snap(10.0, "dead"), _snap(30.0, "alive")]
    events = infer_events(snaps, config.vision.events, match_end=end)
    (episode,) = [
        s
        for s in build_scenarios(events, config.scenarios)
        if s.scenario_type.value == "death_episode"
    ]
    assert episode.start_time == 9.5
    assert episode.end_time == 10.0
    assert episode.context["end_reason"] == "match_end"
    assert episode.context["match_end_time"] == 10.0
    assert episode.context["has_respawn"] is False
    assert episode.context["has_active_again"] is False
    assert episode.outcome.value == "incomplete"


def test_without_match_end_episode_keeps_max_duration(config: AppConfig) -> None:
    snaps = [_snap(0.0, "alive"), _snap(9.5, "dead")]
    events = infer_events(snaps, config.vision.events)
    (episode,) = build_scenarios(events, config.scenarios)
    assert episode.context["end_reason"] == "max_duration"
    assert episode.end_time == 9.5 + config.scenarios.post_death_max_seconds


def test_before_match_end_helper() -> None:
    assert match_end_time([]) is None
    assert before_match_end([1.0, 2.0], float, None) == [1.0, 2.0]
    assert before_match_end([1.0, 2.0, 3.0], float, 2.0) == [1.0]


# --- golden: ja-2026-09-20 20-02-59 ---------------------------------------


def _video_times(node: Any, *, observed: bool = False) -> list[tuple[float, bool]]:
    """Every numeric ``video_time``; flag = inside an ``observation`` record.

    Lookup labels (e.g. ``primary_end``) may sit exactly on the boundary; the
    observations they resolve to must be strictly before it.
    """
    out: list[tuple[float, bool]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "video_time" and isinstance(value, (int, float)):
                out.append((float(value), observed))
            else:
                out.extend(_video_times(value, observed=observed or key == "observation"))
    elif isinstance(node, list):
        for item in node:
            out.extend(_video_times(item, observed=observed))
    return out


@pytest.fixture(scope="module")
def golden(tmp_path_factory: pytest.TempPathFactory, config: AppConfig) -> Path:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    analysis = tmp_path_factory.mktemp("match_end") / "ja-2026-09-20 20-02-59"
    analysis.mkdir()
    (analysis / "vision_manifest.json").write_text(
        json.dumps(fixture["manifest"]), encoding="utf-8"
    )
    manifest = load_vision_manifest(analysis)
    cfg = config.model_copy(deep=True)
    cfg.vision.language = VisionLanguage(manifest.analysis.language)
    refused = refuse_vision_manifest(manifest, cfg)
    save_vision_manifest(refused, analysis)
    write_scenarios(refused.game_events, cfg, analysis, manifest=refused)
    result = CliRunner().invoke(app, ["coach-inputs", str(analysis)])
    assert result.exit_code == 0, result.stdout
    return analysis


def test_golden_match_end_is_clock_zero(golden: Path) -> None:
    manifest = load_vision_manifest(golden)
    ends = [e for e in manifest.game_events if e.event_type is GameEventType.MATCH_END]
    assert len(ends) == 1
    assert ends[0].start_time == MATCH_END_AT
    assert ends[0].reason is GameEventReason.CLOCK_ZERO
    assert ends[0].confirmation is None


def test_golden_no_gameplay_events_after_match_end(golden: Path) -> None:
    manifest = load_vision_manifest(golden)
    late = [
        e
        for e in manifest.game_events
        if e.event_type is not GameEventType.MATCH_END and e.start_time >= MATCH_END_AT
    ]
    assert late == []
    assert all((e.end_time or e.start_time) <= MATCH_END_AT for e in manifest.game_events)
    tail = [s for s in manifest.state_snapshots if s.timestamp >= MATCH_END_AT]
    assert tail and {s.match_phase for s in tail} == {"post_match"}


def test_golden_last_death_episode_ends_at_match_end(golden: Path) -> None:
    scenarios = json.loads((golden / "scenarios.json").read_text(encoding="utf-8"))
    deaths = [s for s in scenarios if s["scenario_type"] == "death_episode"]
    last = deaths[-1]
    assert last["start_time"] == DEATH_AT
    assert last["end_time"] == MATCH_END_AT
    assert last["context"]["end_reason"] == "match_end"
    assert last["context"]["respawn_time"] is None
    assert all(s["end_time"] <= MATCH_END_AT for s in scenarios)


def test_golden_no_evidence_after_match_end(golden: Path) -> None:
    contexts = json.loads((golden / "scenario_contexts.json").read_text(encoding="utf-8"))
    (ctx,) = [c for c in contexts if c["scenario_id"] == f"death_episode:{DEATH_AT:.3f}"]
    assert ctx["death_episode"]["end_reason"] == "match_end"
    assert ctx["death_episode"]["death_to_match_end"] == pytest.approx(0.5)
    times = _video_times(contexts)
    inputs = golden / "coach_inputs"
    for path in sorted(inputs.glob("*.json")):
        times += _video_times(json.loads(path.read_text(encoding="utf-8")))
    assert times
    assert max(t for t, _ in times) <= MATCH_END_AT
    observations = [t for t, observed in times if observed]
    assert observations and max(observations) < MATCH_END_AT
    contexts_times = [t for t, _ in _video_times(contexts)]
    assert max(contexts_times) < MATCH_END_AT


def test_golden_death_statement_says_match_ended(golden: Path) -> None:
    contexts = json.loads((golden / "scenario_contexts.json").read_text(encoding="utf-8"))
    (raw,) = [c for c in contexts if c["scenario_id"] == f"death_episode:{DEATH_AT:.3f}"]
    lines = death_lifecycle_statements(ScenarioContext.model_validate(raw))
    assert (
        "The match ended 0.5 video seconds after this death, before a respawn "
        "was observed." in lines
    )
    assert not any("incomplete" in line for line in lines)


def test_golden_coaching_view_marks_terminal_end(golden: Path) -> None:
    inputs = golden / "coach_inputs"
    entry = read_coaching_index(inputs).by_scenario_id(f"death_episode:{DEATH_AT:.3f}")
    assert entry is not None and entry.llm_view_json is not None
    view = json.loads((inputs / entry.llm_view_json).read_text(encoding="utf-8"))
    text = json.dumps(view)
    assert '"ended_by_match_end": true' in text
    assert "incomplete (death only)" not in text
