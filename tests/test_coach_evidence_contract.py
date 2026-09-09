"""Coaching evidence contract: permitted claims over ScenarioContext.

Does not change scenario grouping. Uses frozen ScenarioContext fields only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splatoon3_ai_coach.analysis.scenario_context import build_scenario_contexts
from splatoon3_ai_coach.analysis.scenario_models import ScenarioType
from splatoon3_ai_coach.analysis.scenarios import build_scenarios, event_id
from splatoon3_ai_coach.coach.evidence_contract import (
    ClaimSupport,
    claim_contains_prohibited_language,
    death_lifecycle_statements,
    describe_leads_to_association,
    describe_trade_candidate,
    engagement_observation_statements,
    engagement_proves_complete_fight,
    evidence_contract_summary,
    map_observation_statements,
)
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.vision.models import (
    GameEvent,
    GameEventReason,
    GameEventType,
    VisionManifest,
)

_REPO = Path(__file__).resolve().parents[1]
_ANALYSIS_180224 = _REPO / "analysis" / "2026-07-05 18-02-24"
_MANIFEST_180224 = _ANALYSIS_180224 / "vision_manifest.json"


def _event(
    timestamp: float,
    event_type: GameEventType,
    *,
    reason: GameEventReason | None = None,
    fingerprint: str | None = None,
    end_time: float | None = None,
) -> GameEvent:
    return GameEvent(
        start_time=timestamp,
        end_time=end_time,
        event_type=event_type,
        reason=reason,
        splat_fingerprint=fingerprint,
        confidence=1.0,
    )


def _death(t: float) -> GameEvent:
    return _event(t, GameEventType.DEATH, reason=GameEventReason.ALIVE_TO_DEAD)


def _respawn(t: float) -> GameEvent:
    return _event(
        t, GameEventType.RESPAWN, reason=GameEventReason.COUNTDOWN_PLATE_ENDED
    )


def _active(t: float) -> GameEvent:
    return _event(
        t,
        GameEventType.ACTIVE_AGAIN,
        reason=GameEventReason.AWAITING_CONTROL_TO_ALIVE,
    )


def _splat(t: float, fp: str) -> GameEvent:
    return _event(
        t,
        GameEventType.SPLAT,
        reason=GameEventReason.SPLAT_INSTANCE_OPENED,
        fingerprint=fp,
    )


def _map(start: float, end: float) -> GameEvent:
    return _event(
        start,
        GameEventType.MAP_OVERLAY,
        reason=GameEventReason.MAP_OVERLAY_PRESENT,
        end_time=end,
    )


def _contexts(events: list[GameEvent]):
    config = load_config(default_config_path())
    scenarios = build_scenarios(events, config.scenarios)
    return scenarios, build_scenario_contexts(events, scenarios, config.scenarios)


def test_death_lifecycle_timing_statements_are_permitted() -> None:
    events = [_death(63.0), _respawn(70.5), _active(72.0)]
    scenarios, contexts = _contexts(events)
    death = next(c for c in contexts if c.scenario_id.startswith("death_episode:"))
    lines = death_lifecycle_statements(death)
    assert any("9.0 seconds until active" in line for line in lines)
    assert any("7.5 seconds after death" in line for line in lines)
    assert any("1.5 seconds from respawn" in line for line in lines)
    joined = " ".join(lines)
    assert not claim_contains_prohibited_language(joined)


def test_map_observations_have_no_quality_judgments() -> None:
    events = [
        _death(63.0),
        _map(65.5, 66.0),
        _map(67.5, 68.0),
        _respawn(70.5),
        _active(72.0),
    ]
    _scenarios, contexts = _contexts(events)
    death = next(c for c in contexts if c.scenario_id.startswith("death_episode:"))
    lines = map_observation_statements(death)
    assert any("map overlay" in line.lower() for line in lines)
    joined = " ".join(lines).lower()
    assert "good" not in joined
    assert "bad" not in joined
    assert "should have" not in joined
    assert not claim_contains_prohibited_language(" ".join(lines))


def test_engagement_is_not_proof_of_complete_fight() -> None:
    events = [_splat(153.0, "aa" * 8)]
    scenarios, contexts = _contexts(events)
    eng = scenarios[0]
    ctx = contexts[0]
    assert eng.scenario_type is ScenarioType.ENGAGEMENT
    assert ctx.combat is not None
    assert ctx.combat.splat_count == 1
    assert ctx.combat.duration == pytest.approx(0.0)
    assert engagement_proves_complete_fight(ctx.combat) is False
    lines = engagement_observation_statements(eng, ctx)
    joined = " ".join(lines).lower()
    assert "observed splat" in joined
    assert "won" not in joined
    assert "lost" not in joined
    assert "fight boundary" in joined or "not a proven fight" in joined


def test_leads_to_described_as_association_never_causation() -> None:
    events = [_splat(150.0, "aa" * 8), _death(151.0)]
    scenarios, contexts = _contexts(events)
    eng = next(c for c in contexts if c.scenario_id.startswith("engagement:"))
    assert eng.relations.leads_to_death_episode_id is not None
    text = describe_leads_to_association()
    assert "associated" in text.lower() or "configured temporal rule" in text.lower()
    assert "caused" not in text.lower()
    window = describe_leads_to_association(use_window_phrasing=True)
    assert "configured pre-death window" in window.lower()
    assert "caused" not in window.lower()
    bad = "This splat caused your death."
    assert claim_contains_prohibited_language(bad)
    scenario = next(s for s in scenarios if s.scenario_type is ScenarioType.ENGAGEMENT)
    safe = " ".join(engagement_observation_statements(scenario, eng))
    assert "caused" not in safe.lower()


def test_trade_candidate_is_not_proof_of_trade() -> None:
    text = describe_trade_candidate()
    assert "configured temporal window" in text.lower() or "trade_candidate" in text
    assert "does not prove" in text.lower()
    assert claim_contains_prohibited_language("You traded poorly.")
    assert claim_contains_prohibited_language("you traded")


def test_missing_combat_does_not_invent_fight_narrative() -> None:
    events = [_death(63.0), _respawn(70.5), _active(72.0)]
    _scenarios, contexts = _contexts(events)
    death = contexts[0]
    assert death.combat is None
    lines = death_lifecycle_statements(death) + map_observation_statements(death)
    joined = " ".join(lines).lower()
    for banned in ("won", "lost", "fight", "duel", "outnumbered", "overextend"):
        assert banned not in joined


def test_prohibited_patterns_cover_contract_examples() -> None:
    prohibited = [
        "You won the fight.",
        "You lost a fight.",
        "That was a bad engagement.",
        "You overextended.",
        "You were outnumbered.",
        "You should have retreated.",
        "You should have checked the map.",
        "Your map usage was good.",
        "Your gear made your respawn slow.",
        "You rushed.",
        "You hesitated.",
        "You spawn-camped.",
        "This splat caused your death.",
        "These splats were the same fight.",
    ]
    for claim in prohibited:
        assert claim_contains_prohibited_language(claim), claim
    permitted = [
        "This death lasted 9.0 seconds until active again.",
        "Four map overlays were observed during the death episode.",
        "No map overlay was observed before this death.",
        "At least one map overlay occurred before this death (unbounded lookback).",
        describe_leads_to_association(),
    ]
    for claim in permitted:
        assert not claim_contains_prohibited_language(claim), claim


def test_system_prompt_encodes_evidence_boundary() -> None:
    prompt = load_system_prompt().lower()
    assert "associated" in prompt or "temporal rule" in prompt
    assert "caused" in prompt  # instructed never to say caused — must mention rule
    assert "engagement" in prompt
    assert "scenario context" in prompt or "scenariocontext" in prompt.replace(" ", "")
    assert "do not invent" in prompt or "do not" in prompt
    assert "unbounded" in prompt or "any map overlay" in prompt
    assert "fight quality" in prompt or "fragged" in prompt


def test_unsupported_desire_classification() -> None:
    from splatoon3_ai_coach.coach.evidence_contract import classify_unsupported_desire

    assert classify_unsupported_desire("map_advice") is ClaimSupport.REQUIRES_INTERPRETATION
    assert (
        classify_unsupported_desire("fight_boundaries")
        is ClaimSupport.REQUIRES_NEW_EVIDENCE
    )
    assert classify_unsupported_desire("splat_caused_death") is ClaimSupport.PROHIBITED


def test_evidence_contract_summary_has_required_categories() -> None:
    summary = evidence_contract_summary()
    assert "permitted" in summary
    assert "prohibited_as_facts" in summary
    assert "requires_new_evidence" in summary
    assert "requires_interpretation" in summary


@pytest.mark.skipif(
    not _MANIFEST_180224.is_file(), reason="18-02-24 analysis manifest missing"
)
def test_180224_fixture_membership_and_contract_regression() -> None:
    """Grouping unchanged; coaching statements stay within the evidence contract."""
    config = load_config(default_config_path())
    manifest = VisionManifest.model_validate_json(_MANIFEST_180224.read_text())
    events = list(manifest.game_events)
    scenarios = build_scenarios(events, config.scenarios)
    contexts = build_scenario_contexts(events, scenarios, config.scenarios)

    by_type = {ScenarioType.DEATH_EPISODE: 0, ScenarioType.ENGAGEMENT: 0, ScenarioType.MAP_CHECK: 0}
    for item in scenarios:
        by_type[item.scenario_type] = by_type.get(item.scenario_type, 0) + 1
    assert by_type[ScenarioType.DEATH_EPISODE] == 2
    assert by_type[ScenarioType.ENGAGEMENT] == 3
    assert by_type[ScenarioType.MAP_CHECK] == 0

    owned = {eid for s in scenarios for eid in s.event_ids}
    all_ids = {event_id(e) for e in events}
    assert owned == all_ids

    for ctx in contexts:
        if ctx.scenario_id.startswith("death_episode:"):
            assert ctx.combat is None
            for line in death_lifecycle_statements(ctx) + map_observation_statements(ctx):
                assert not claim_contains_prohibited_language(line), line
        if ctx.scenario_id.startswith("engagement:"):
            scenario = next(s for s in scenarios if s.scenario_id == ctx.scenario_id)
            assert engagement_proves_complete_fight(ctx.combat) is False
            for line in engagement_observation_statements(scenario, ctx):
                assert not claim_contains_prohibited_language(line), line
                assert "caused" not in line.lower()

    # Golden dump still present and aligned on scenario count.
    scenarios_json = _ANALYSIS_180224 / "scenarios.json"
    if scenarios_json.is_file():
        dumped = json.loads(scenarios_json.read_text(encoding="utf-8"))
        assert len(dumped) == len(scenarios)
