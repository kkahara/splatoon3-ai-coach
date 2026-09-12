"""ScenarioContext sparse secondary evidence (map ink / players / special)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splatoon3_ai_coach.analysis.pipeline import (
    extract_special_readings,
    write_scenarios,
)
from splatoon3_ai_coach.analysis.scenario_context import (
    ScenarioContext,
    build_scenario_context,
    build_scenario_contexts,
    serialize_scenario_contexts,
)
from splatoon3_ai_coach.analysis.scenario_evidence import (
    ScenarioEvidencePack,
    SpecialReading,
    build_map_ink_evidence,
    build_players_evidence,
    build_special_evidence,
)
from splatoon3_ai_coach.analysis.scenario_models import Scenario, ScenarioOutcome, ScenarioType
from splatoon3_ai_coach.analysis.special_ready_markers import (
    ReadySample,
    derive_special_ready_onsets,
)
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig
from splatoon3_ai_coach.vision.map_ink import MapObservation
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    GameEvent,
    GameEventReason,
    GameEventType,
    GameStateSnapshot,
    SpecialGaugeReading,
    VisionFrameResult,
)
from vision_manifest_viewer.html import render_html
from vision_manifest_viewer.model import (
    ManifestSummary,
    ManifestView,
    ObservationView,
    ScenarioEvidenceView,
)
from vision_manifest_viewer.special_ready import derive_special_ready_markers


def _cfg(**overrides: float) -> ScenarioBuilderConfig:
    base = dict(
        context_lookback_seconds=8.0,
        context_lookforward_seconds=2.0,
        context_max_gap_seconds=1.0,
    )
    base.update(overrides)
    return ScenarioBuilderConfig(**base)


def _death_scenario(start: float = 70.5, end: float = 80.0) -> Scenario:
    return Scenario(
        scenario_id=f"death_episode:{start:.3f}",
        scenario_type=ScenarioType.DEATH_EPISODE,
        start_time=start,
        end_time=end,
        event_ids=[f"death:{start:.3f}:alive_to_dead"],
        confidence=1.0,
        outcome=ScenarioOutcome.RECOVERED,
    )


def _death_event(ts: float = 70.5) -> GameEvent:
    return GameEvent(
        start_time=ts,
        event_type=GameEventType.DEATH,
        reason=GameEventReason.ALIVE_TO_DEAD,
        confidence=1.0,
    )


def _snap(t: float, ally: int, opp: int) -> GameStateSnapshot:
    return GameStateSnapshot(
        timestamp=t,
        match_phase="in_match",
        player_lifecycle="alive",
        ally_alive_count=ally,
        opponent_alive_count=opp,
        player_count_confidence=1.0,
    )


def _ink(t: float, ally: float, opp: float) -> MapObservation:
    return MapObservation(
        video_time=t,
        stage_id="mahi_mahi_resort",
        ally_classified_fraction=ally,
        opponent_classified_fraction=opp,
        classified_fraction=ally + opp,
        confidence=0.9,
    )


def _special(
    t: float,
    *,
    ready: bool = False,
    fill: float | None = 0.5,
    visible: bool = True,
    oid: str | None = None,
) -> SpecialReading:
    return SpecialReading(
        video_time=t,
        visible=visible,
        fill_fraction=fill,
        ready=ready,
        observation_id=oid or f"sg-{t}",
        confidence=0.8,
    )


def test_death_episode_enrichment() -> None:
    scenario = _death_scenario()
    events = [_death_event()]
    pack = ScenarioEvidencePack(
        map_observations=[_ink(68.0, 0.62, 0.38), _ink(70.0, 0.55, 0.45)],
        state_snapshots=[
            _snap(65.0, 4, 3),
            _snap(66.0, 4, 3),
            _snap(69.5, 4, 4),
            _snap(70.5, 4, 4),
        ],
        special_readings=[
            _special(63.0, ready=False, fill=0.5),
            _special(64.0, ready=True, fill=0.7),
            _special(69.0, ready=True, fill=0.81),
            _special(70.0, ready=True, fill=0.8),
        ],
    )
    ctx = build_scenario_context(events, scenario, _cfg(), evidence=pack)
    assert ctx.map is not None and ctx.map.ink is not None
    assert len(ctx.map.ink.observations) == 2
    assert ctx.map.ink.nearest_before_anchor is not None
    assert ctx.map.ink.nearest_before_anchor.video_time == 70.0
    assert ctx.players is not None
    assert [(p.video_time, p.ally_alive_count, p.opponent_alive_count) for p in ctx.players.trajectory] == [
        (65.0, 4, 3),
        (69.5, 4, 4),
    ]
    assert ctx.players.at_death is not None
    assert ctx.special is not None
    assert any(abs(o.video_time - 69.0) < 1e-9 for o in ctx.special.observations)
    assert any(abs(o.video_time - 64.0) < 1e-9 for o in ctx.special.ready_onsets)


def test_gaps_nearest_before_anchor_none() -> None:
    samples = [_ink(50.0, 0.5, 0.5)]
    ink = build_map_ink_evidence(
        samples,
        window_start=62.5,
        window_end=82.0,
        anchor=70.5,
        max_gap_seconds=1.0,
    )
    assert ink is None or ink.nearest_before_anchor is None
    players = build_players_evidence(
        [_snap(50.0, 4, 4)],
        window_start=62.5,
        window_end=82.0,
        anchor=70.5,
        death_time=70.5,
        max_gap_seconds=1.0,
    )
    assert players is None or (
        players.at_anchor is None and players.at_death is None and not players.trajectory
    )


def test_no_interpolation_roster_trajectory() -> None:
    players = build_players_evidence(
        [_snap(65.0, 4, 3), _snap(69.5, 4, 4)],
        window_start=60.0,
        window_end=80.0,
        anchor=70.5,
        death_time=70.5,
        max_gap_seconds=1.0,
    )
    assert players is not None
    assert len(players.trajectory) == 2
    assert players.trajectory[0].ally_alive_count == 4
    assert players.trajectory[0].opponent_alive_count == 3
    assert players.trajectory[1].opponent_alive_count == 4


def test_evidence_outside_window_excluded() -> None:
    """Configured lookback/lookforward bounds drop out-of-window samples."""
    scenario = _death_scenario(start=70.5, end=80.0)
    # window = [62.5, 82.0] with default lookback 8 / lookforward 2
    pack = ScenarioEvidencePack(
        map_observations=[_ink(60.0, 0.5, 0.5), _ink(68.0, 0.6, 0.4), _ink(83.0, 0.4, 0.6)],
        state_snapshots=[_snap(60.0, 4, 4), _snap(69.0, 3, 4), _snap(83.0, 2, 2)],
        special_readings=[
            _special(60.0, ready=False),
            _special(69.0, ready=True),
            _special(83.0, ready=False),
        ],
    )
    ctx = build_scenario_context([_death_event()], scenario, _cfg(), evidence=pack)
    assert ctx.map is not None and ctx.map.ink is not None
    assert [s.video_time for s in ctx.map.ink.observations] == [68.0]
    assert ctx.players is not None
    assert [p.video_time for p in ctx.players.trajectory] == [69.0]
    assert ctx.special is not None
    assert [r.video_time for r in ctx.special.observations] == [69.0]


def test_nearest_before_anchor_respects_max_gap() -> None:
    players = build_players_evidence(
        [_snap(68.0, 4, 3)],
        window_start=60.0,
        window_end=80.0,
        anchor=70.5,
        death_time=70.5,
        max_gap_seconds=1.0,
    )
    assert players is not None
    assert players.at_anchor is None
    assert players.at_death is None
    # Within gap:
    players_ok = build_players_evidence(
        [_snap(70.0, 4, 3)],
        window_start=60.0,
        window_end=80.0,
        anchor=70.5,
        death_time=70.5,
        max_gap_seconds=1.0,
    )
    assert players_ok is not None
    assert players_ok.at_anchor is not None
    assert players_ok.at_anchor.video_time == 70.0


def test_roster_compression_sparse_semantics() -> None:
    from splatoon3_ai_coach.analysis.player_count_series import (
        compress_player_count_observations,
        observations_from_snapshots,
    )

    obs = observations_from_snapshots(
        [
            _snap(1.0, 4, 4),
            _snap(2.0, 4, 4),
            _snap(3.0, 4, 4),
            _snap(4.0, 3, 4),
            _snap(5.0, 3, 4),
            _snap(6.0, 4, 4),
        ]
    )
    compressed = compress_player_count_observations(obs)
    assert [(o.video_time, o.ally_alive_count, o.opponent_alive_count) for o in compressed] == [
        (1.0, 4, 4),
        (4.0, 3, 4),
        (6.0, 4, 4),
    ]
    players = build_players_evidence(
        [
            _snap(1.0, 4, 4),
            _snap(2.0, 4, 4),
            _snap(3.0, 4, 4),
            _snap(4.0, 3, 4),
            _snap(5.0, 3, 4),
            _snap(6.0, 4, 4),
        ],
        window_start=0.0,
        window_end=10.0,
        anchor=6.0,
        death_time=None,
        max_gap_seconds=1.0,
    )
    assert players is not None
    assert [(p.video_time, p.ally_alive_count, p.opponent_alive_count) for p in players.trajectory] == [
        (1.0, 4, 4),
        (4.0, 3, 4),
        (6.0, 4, 4),
    ]


def test_ready_onset_window_boundary_sees_preceding_false() -> None:
    """Window from 50.0 with false@49.0 → true@50.2: one real onset, not a boundary invent.

    Preceding false outside the window still participates in onset derivation.
    Expected marker is the observed transition at 50.2 — not an invented onset
    at the window start (50.0).
    """
    readings = [
        _special(49.0, ready=False, oid="pre"),
        _special(50.2, ready=True, oid="onset"),
    ]
    shared = derive_special_ready_onsets(
        [
            ReadySample(video_time=r.video_time, ready=r.ready, observation_id=r.observation_id)
            for r in readings
        ]
    )
    special = build_special_evidence(
        readings,
        window_start=50.0,
        window_end=60.0,
        anchor=55.0,
        max_gap_seconds=2.0,
    )
    assert [m.video_time for m in shared] == [50.2]
    assert special is not None
    assert [m.video_time for m in special.ready_onsets] == [50.2]
    assert all(abs(m.video_time - 50.0) > 1e-9 for m in special.ready_onsets)


def test_ready_onset_exactly_one_with_continued_true() -> None:
    readings = [
        _special(49.0, ready=False, oid="a"),
        _special(50.2, ready=True, oid="b"),
        _special(51.0, ready=True, oid="c"),
    ]
    shared = derive_special_ready_onsets(
        [
            ReadySample(video_time=r.video_time, ready=r.ready, observation_id=r.observation_id)
            for r in readings
        ]
    )
    vmv = derive_special_ready_markers(
        [
            ObservationView(
                id=r.observation_id or str(i),
                timestamp=r.video_time,
                detector="special_gauge",
                category="other",
                positive=True,
                reading={"ready": r.ready, "visible": True},
            )
            for i, r in enumerate(readings)
        ]
    )
    special = build_special_evidence(
        readings,
        window_start=50.0,
        window_end=60.0,
        anchor=55.0,
        max_gap_seconds=2.0,
    )
    assert [m.video_time for m in shared] == [50.2]
    assert [m.timestamp for m in vmv] == [50.2]
    assert special is not None
    assert [m.video_time for m in special.ready_onsets] == [50.2]


def test_ready_onset_not_invented_when_transition_before_window() -> None:
    """ready=True at window start is not an onset if false→true happened earlier."""
    readings = [
        _special(49.0, ready=False, oid="pre-off"),
        _special(50.2, ready=True, oid="onset"),
        _special(51.0, ready=True, oid="in-window"),
    ]
    special = build_special_evidence(
        readings,
        window_start=50.5,
        window_end=60.0,
        anchor=55.0,
        max_gap_seconds=2.0,
    )
    assert special is not None
    assert any(abs(r.video_time - 51.0) < 1e-9 for r in special.observations)
    assert special.ready_onsets == []


def test_analysis_does_not_import_coach_for_roster() -> None:
    """ScenarioContext builders must not depend on the coach package."""
    src = Path("src/splatoon3_ai_coach/analysis/scenario_evidence.py").read_text(
        encoding="utf-8"
    )
    assert "splatoon3_ai_coach.coach" not in src
    from splatoon3_ai_coach.analysis import player_count_series as pcs

    assert callable(pcs.compress_player_count_observations)
    assert callable(pcs.observations_from_snapshots)


def test_special_video_time_uses_frame_timestamp() -> None:
    """Frame timestamp is canonical even when the reading echo differs."""
    frame = VisionFrameResult(
        frame_id="f1",
        timestamp=10.0,
        source="cadence",
        detections=[
            DetectorResult(
                id="sg-1",
                detector_name="special_gauge",
                detector_version="special_gauge@test",
                confidence=0.9,
                reading=SpecialGaugeReading(
                    visible=True,
                    fill_fraction=0.5,
                    ready=False,
                    timestamp=99.0,  # stale/mismatched echo must not win
                ),
            )
        ],
    )
    readings = extract_special_readings([frame])
    assert readings[0].video_time == 10.0


def test_missing_map_observations_and_empty_secondary_evidence(
    tmp_path: Path,
) -> None:
    """Missing map_observations.json must not fail; empty nests stay None/[]."""
    config = load_config(default_config_path())
    events = [
        _death_event(10.0),
        GameEvent(
            start_time=12.0,
            event_type=GameEventType.RESPAWN,
            reason=GameEventReason.COUNTDOWN_PLATE_ENDED,
            confidence=1.0,
        ),
        GameEvent(
            start_time=13.0,
            event_type=GameEventType.ACTIVE_AGAIN,
            reason=GameEventReason.AWAITING_CONTROL_TO_ALIVE,
            confidence=1.0,
        ),
    ]
    # No map_observations.json, no manifest → empty evidence pack
    scenarios = write_scenarios(events, config, tmp_path)
    assert not (tmp_path / "map_observations.json").exists()
    contexts = json.loads((tmp_path / "scenario_contexts.json").read_text(encoding="utf-8"))
    death_ctx = next(c for c in contexts if "death_episode" in c["scenario_id"])
    # Overlay facts unchanged / absent ink; no fabricated players/special
    if death_ctx.get("map") is not None:
        assert death_ctx["map"].get("map_check_before_death") is False
        assert death_ctx["map"].get("ink") is None
    assert death_ctx.get("players") is None
    assert death_ctx.get("special") is None
    death = next(s for s in scenarios if s.scenario_type is ScenarioType.DEATH_EPISODE)
    assert all(
        not eid.startswith("special_") and "player_count" not in eid and "ink" not in eid
        for eid in death.event_ids
    )


def test_anchor_association_distinction() -> None:
    """Ink/special are at/before-only; roster at_* is nearest within max gap."""
    ink = build_map_ink_evidence(
        [_ink(71.0, 0.5, 0.5)],  # after anchor
        window_start=60.0,
        window_end=80.0,
        anchor=70.5,
        max_gap_seconds=1.0,
    )
    assert ink is not None
    assert ink.nearest_before_anchor is None
    special = build_special_evidence(
        [_special(71.0, ready=False)],
        window_start=60.0,
        window_end=80.0,
        anchor=70.5,
        max_gap_seconds=1.0,
    )
    assert special is not None
    assert special.nearest_before_anchor is None
    players = build_players_evidence(
        [_snap(71.0, 4, 3)],
        window_start=60.0,
        window_end=80.0,
        anchor=70.5,
        death_time=70.5,
        max_gap_seconds=1.0,
    )
    assert players is not None
    assert players.at_anchor is not None
    assert players.at_anchor.video_time == 71.0


def test_ready_onset_parity_shared_vmv_context() -> None:
    readings = [
        _special(1.0, ready=False, oid="a"),
        _special(2.0, ready=False, oid="b"),
        _special(3.0, ready=True, oid="c"),
        _special(4.0, ready=True, oid="d"),
        _special(5.0, ready=False, oid="e"),
        _special(6.0, ready=True, oid="f"),
    ]
    shared = derive_special_ready_onsets(
        [ReadySample(video_time=r.video_time, ready=r.ready, observation_id=r.observation_id) for r in readings]
    )
    vmv = derive_special_ready_markers(
        [
            ObservationView(
                id=r.observation_id or str(i),
                timestamp=r.video_time,
                detector="special_gauge",
                category="other",
                positive=True,
                reading={"ready": r.ready, "visible": True},
            )
            for i, r in enumerate(readings)
        ]
    )
    special = build_special_evidence(
        readings,
        window_start=0.0,
        window_end=10.0,
        anchor=6.0,
        max_gap_seconds=2.0,
    )
    assert special is not None
    assert [m.video_time for m in shared] == [3.0, 6.0]
    assert [m.timestamp for m in vmv] == [3.0, 6.0]
    assert [m.video_time for m in special.ready_onsets] == [3.0, 6.0]


def test_ink_does_not_affect_overlay_map_check() -> None:
    scenario = _death_scenario()
    events = [_death_event()]  # no MAP_OVERLAY
    pack = ScenarioEvidencePack(map_observations=[_ink(68.0, 0.6, 0.4)])
    ctx = build_scenario_context(events, scenario, _cfg(), evidence=pack)
    assert ctx.map is not None
    assert ctx.map.map_check_before_death is False
    assert ctx.map.map_checks_during_scenario == 0
    assert ctx.map.ink is not None
    assert len(ctx.map.ink.observations) == 1


def test_no_special_game_events_and_event_ids_unchanged(tmp_path: Path) -> None:
    config = load_config(default_config_path())
    events = [
        _death_event(10.0),
        GameEvent(
            start_time=12.0,
            event_type=GameEventType.RESPAWN,
            reason=GameEventReason.COUNTDOWN_PLATE_ENDED,
            confidence=1.0,
        ),
        GameEvent(
            start_time=13.0,
            event_type=GameEventType.ACTIVE_AGAIN,
            reason=GameEventReason.AWAITING_CONTROL_TO_ALIVE,
            confidence=1.0,
        ),
    ]
    before_ids = None
    scenarios = write_scenarios(events, config, tmp_path)
    death = next(s for s in scenarios if s.scenario_type is ScenarioType.DEATH_EPISODE)
    before_ids = list(death.event_ids)
    # Re-load contexts; ensure no special events invented
    assert all(
        e.event_type not in {GameEventType.SPECIAL_READY, GameEventType.SPECIAL_USED}
        for e in events
    )
    assert death.event_ids == before_ids
    raw = json.loads((tmp_path / "scenario_contexts.json").read_text(encoding="utf-8"))
    assert isinstance(raw, list)


def test_serialization_round_trip() -> None:
    scenario = _death_scenario()
    events = [_death_event()]
    pack = ScenarioEvidencePack(
        map_observations=[_ink(68.0, 0.62, 0.38)],
        state_snapshots=[_snap(69.5, 4, 4), _snap(70.5, 4, 4)],
        special_readings=[_special(69.0, ready=True, fill=0.81)],
    )
    ctx = build_scenario_context(events, scenario, _cfg(), evidence=pack)
    dumped = serialize_scenario_contexts([ctx])[0]
    restored = ScenarioContext.model_validate(dumped)
    assert restored.map is not None and restored.map.ink is not None
    assert restored.players is not None
    assert restored.special is not None
    assert restored.special.observations[0].fill_fraction == 0.81


def test_vmv_scenario_card_smoke() -> None:
    card = ScenarioEvidenceView(
        scenario_id="death_episode:70.500",
        scenario_type="death_episode",
        start_time=70.5,
        end_time=80.0,
        map={
            "map_check_count": 0,
            "map_check_before_death": False,
            "ink": {
                "observations": [
                    {
                        "video_time": 68.0,
                        "ally_classified_fraction": 0.62,
                        "opponent_classified_fraction": 0.38,
                        "confidence": 0.9,
                    }
                ],
                "nearest_before_anchor": {
                    "video_time": 68.0,
                    "ally_classified_fraction": 0.62,
                    "opponent_classified_fraction": 0.38,
                    "confidence": 0.9,
                },
            },
        },
        players={
            "trajectory": [
                {"video_time": 65.0, "ally_alive_count": 4, "opponent_alive_count": 3},
                {"video_time": 69.5, "ally_alive_count": 4, "opponent_alive_count": 4},
            ],
            "at_anchor": {
                "video_time": 70.5,
                "ally_alive_count": 4,
                "opponent_alive_count": 4,
            },
            "at_death": {
                "video_time": 70.5,
                "ally_alive_count": 4,
                "opponent_alive_count": 4,
            },
        },
        special={
            "observations": [
                {
                    "video_time": 69.0,
                    "visible": True,
                    "fill_fraction": 0.81,
                    "ready": True,
                }
            ],
            "ready_onsets": [{"video_time": 52.0}],
            "nearest_before_anchor": {
                "video_time": 69.0,
                "visible": True,
                "fill_fraction": 0.81,
                "ready": True,
            },
        },
        relations={},
    )
    from vision_manifest_viewer.model import ManifestSummary, ManifestView

    view = ManifestView(
        summary=ManifestSummary(
            video_label="t",
            video_identity="t",
            duration_seconds=100.0,
            frame_count=1,
            detection_count=0,
            death_detections=0,
            countdown_observations=0,
            splat_detections=0,
            active_observations=0,
            spawn_observations=0,
            lifecycle_episodes=0,
        ),
        scenario_evidence=[card],
    )
    html = render_html(view)
    assert "Map ink" in html
    assert "scenarioMapInkRows" in html
    assert "scenarioPlayersRows" in html
    assert "scenarioSpecialRows" in html
    assert "presentation-only; not a GameEvent" in html
    assert "should have" not in html.lower()
    assert "wasted" not in html.lower()


def test_extract_special_readings_from_frames() -> None:
    frame = VisionFrameResult(
        frame_id="f1",
        timestamp=10.0,
        source="cadence",
        detections=[
            DetectorResult(
                id="sg-1",
                detector_name="special_gauge",
                detector_version="special_gauge@test",
                confidence=0.9,
                reading=SpecialGaugeReading(
                    visible=True,
                    fill_fraction=0.5,
                    ready=True,
                    timestamp=10.0,
                    dial_score=0.1,
                    lit_sector_fraction=0.2,
                    charged_score=0.3,
                    press_score=0.4,
                    ready_prompt_score=0.4,
                ),
            )
        ],
    )
    readings = extract_special_readings([frame])
    assert len(readings) == 1
    assert readings[0].ready is True
    assert readings[0].observation_id == "sg-1"
    assert readings[0].fill_fraction == 0.5
    assert readings[0].dial_score == 0.1
    assert readings[0].lit_sector_fraction == 0.2
    assert readings[0].charged_score == 0.3
    assert readings[0].press_score == 0.4
    assert readings[0].ready_prompt_score == 0.4
    assert readings[0].confidence == 0.9


def _splat(ts: float) -> GameEvent:
    return GameEvent(
        start_time=ts,
        event_type=GameEventType.SPLAT,
        reason=GameEventReason.SPLAT_INSTANCE_OPENED,
        confidence=1.0,
    )


def _engagement(start: float, end: float, splat_ids: list[str]) -> Scenario:
    return Scenario(
        scenario_id=f"engagement:{start:.3f}",
        scenario_type=ScenarioType.ENGAGEMENT,
        start_time=start,
        end_time=end,
        event_ids=splat_ids,
        confidence=1.0,
        outcome=ScenarioOutcome.FRAGGED,
    )


def test_death_episode_level2_populated() -> None:
    death = _death_event(70.5)
    splat = _splat(68.0)
    scenario = _death_scenario(70.5, 80.0)
    snaps = [
        _snap(63.0, 4, 4),
        _snap(66.0, 3, 4),
        _snap(70.5, 3, 4),
    ]
    pack = ScenarioEvidencePack(state_snapshots=snaps)
    ctx = build_scenario_context(
        [splat, death], scenario, _cfg(), evidence=pack
    )
    assert ctx.death_episode is not None
    de = ctx.death_episode
    assert de.is_first_death is True
    assert de.time_since_previous_splat == pytest.approx(2.5)
    assert de.match_phase_at_death == "in_match"
    assert de.numbers_state_at_death == "disadvantage"
    assert de.roster_changed_before_death is True
    assert de.seconds_since_roster_change == pytest.approx(4.5)
    assert de.preceded_by_trade_candidate is False


def test_death_episode_level2_absence_match_phase_and_splat() -> None:
    death = _death_event(70.5)
    scenario = _death_scenario(70.5, 80.0)
    # Far snapshot: outside evidence window and max_gap → no players nest.
    pack = ScenarioEvidencePack(state_snapshots=[_snap(50.0, 4, 4)])
    ctx = build_scenario_context([death], scenario, _cfg(), evidence=pack)
    de = ctx.death_episode
    assert de is not None
    assert de.is_first_death is True
    assert de.time_since_previous_splat is None
    assert de.match_phase_at_death is None
    assert de.numbers_state_at_death is None
    assert de.roster_changed_before_death is None
    assert de.seconds_since_roster_change is None
    assert ctx.players is None


def test_death_episode_level2_no_roster_change_in_lookback() -> None:
    death = _death_event(70.5)
    scenario = _death_scenario(70.5, 80.0)
    # Stable AvB inside lookback — change before lookback must not count.
    snaps = [
        _snap(60.0, 4, 4),
        _snap(61.0, 3, 4),  # change before lookback (70.5-8=62.5)
        _snap(63.0, 3, 4),
        _snap(70.5, 3, 4),
    ]
    pack = ScenarioEvidencePack(state_snapshots=snaps)
    ctx = build_scenario_context([death], scenario, _cfg(), evidence=pack)
    de = ctx.death_episode
    assert de is not None
    assert de.numbers_state_at_death == "disadvantage"
    assert de.roster_changed_before_death is False
    assert de.seconds_since_roster_change is None


def test_death_episode_level2_trade_mirror_false_without_engagement() -> None:
    death = _death_event(70.5)
    scenario = _death_scenario(70.5, 80.0)
    contexts = build_scenario_contexts(
        [death], [scenario], _cfg(engagement_include_following_death_seconds=2.0)
    )
    assert contexts[0].death_episode is not None
    assert contexts[0].death_episode.preceded_by_trade_candidate is False
    assert contexts[0].relations.preceded_by_engagement_id is None


def test_death_episode_level2_trade_mirror_from_engagement() -> None:
    from splatoon3_ai_coach.analysis.scenarios import event_id

    splat = _splat(150.0)
    death = _death_event(151.0)
    eng = _engagement(150.0, 150.0, [event_id(splat)])
    death_sc = _death_scenario(151.0, 160.0)
    contexts = build_scenario_contexts(
        [splat, death],
        [eng, death_sc],
        _cfg(engagement_include_following_death_seconds=2.0),
    )
    by_id = {c.scenario_id: c for c in contexts}
    death_ctx = by_id[death_sc.scenario_id]
    eng_ctx = by_id[eng.scenario_id]
    assert eng_ctx.combat is not None
    assert eng_ctx.combat.trade_candidate is True
    assert death_ctx.relations.preceded_by_engagement_id == eng.scenario_id
    assert death_ctx.death_episode is not None
    assert death_ctx.death_episode.preceded_by_trade_candidate is True


def test_death_episode_level2_incomplete_preserves_level1_none() -> None:
    death = _death_event(70.5)
    scenario = Scenario(
        scenario_id="death_episode:70.500",
        scenario_type=ScenarioType.DEATH_EPISODE,
        start_time=70.5,
        end_time=70.5,
        event_ids=["death:70.500:alive_to_dead"],
        confidence=1.0,
        outcome=ScenarioOutcome.DIED,
    )
    ctx = build_scenario_context([death], scenario, _cfg())
    de = ctx.death_episode
    assert de is not None
    assert de.complete is False
    assert de.death_to_respawn is None
    assert de.death_to_active_again is None
    assert de.is_first_death is True
