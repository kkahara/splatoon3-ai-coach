"""LLM coaching prototype: parse, serialize, annotation-only flags."""

from __future__ import annotations

from pathlib import Path

import pytest

from splatoon3_ai_coach.analysis.scenario_context import build_scenario_contexts
from splatoon3_ai_coach.analysis.scenario_models import ScenarioType
from splatoon3_ai_coach.analysis.scenarios import build_scenarios
from splatoon3_ai_coach.coach.coach import (
    annotate_claim_flags,
    generate_coaching_assessment,
    parse_coaching_assessment,
    serialize_coach_input_user_prompt,
)
from splatoon3_ai_coach.coach.coach_input import build_coach_input_for_scenario
from splatoon3_ai_coach.coach.game_clock import GameClock
from splatoon3_ai_coach.coach.llm_client import CoachingAssessment, LLMProvider
from splatoon3_ai_coach.coach.load_analysis import (
    load_coach_analysis_bundle,
    select_primary_scenario_ids,
)
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig
from splatoon3_ai_coach.vision.models import GameEvent, GameEventReason, GameEventType

_REPO = Path(__file__).resolve().parents[1]
_ANALYSIS_180224 = _REPO / "analysis" / "2026-07-05 18-02-24"


class FakeProvider(LLMProvider):
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.reply


def _death_events() -> list[GameEvent]:
    return [
        GameEvent(
            start_time=87.0,
            event_type=GameEventType.DEATH,
            reason=GameEventReason.ALIVE_TO_DEAD,
            confidence=1.0,
        ),
        GameEvent(
            start_time=94.0,
            event_type=GameEventType.RESPAWN,
            reason=GameEventReason.COUNTDOWN_PLATE_ENDED,
            confidence=1.0,
        ),
        GameEvent(
            start_time=96.0,
            event_type=GameEventType.ACTIVE_AGAIN,
            reason=GameEventReason.AWAITING_CONTROL_TO_ALIVE,
            confidence=1.0,
        ),
    ]


def _coach_input_unit():
    events = _death_events()
    config = ScenarioBuilderConfig()
    scenarios = build_scenarios(events, config)
    contexts = build_scenario_contexts(events, scenarios, config)
    primary = next(s for s in scenarios if s.scenario_type is ScenarioType.DEATH_EPISODE)
    return build_coach_input_for_scenario(
        primary.scenario_id,
        scenarios,
        contexts,
        GameClock(),
        max_gap_seconds=1.0,
    )


def test_parse_coaching_assessment_valid_json() -> None:
    raw = """
    {
      "assessment": "Death cycle took 9.0s.",
      "evidence_used": ["death_episode.death_to_active_again"],
      "limitations": ["No positioning evidence"],
      "recommendations": []
    }
    """
    assessment = parse_coaching_assessment(raw)
    assert assessment.assessment.startswith("Death cycle")
    assert assessment.recommendations == []


def test_parse_coaching_assessment_rejects_invalid() -> None:
    with pytest.raises((ValueError, Exception)):
        parse_coaching_assessment("not json at all")


def test_empty_recommendations_is_valid_success() -> None:
    assessment = CoachingAssessment(
        assessment="Insufficient combat evidence.",
        evidence_used=["evidence_limits"],
        limitations=["ENGAGEMENT is not a complete fight"],
        recommendations=[],
    )
    assert assessment.recommendations == []


def test_annotate_claim_flags_does_not_mutate_assessment() -> None:
    assessment = CoachingAssessment(
        assessment="You overextended into a bad engagement.",
        evidence_used=[],
        limitations=[],
        recommendations=["You should have retreated."],
    )
    before = assessment.model_dump(mode="json")
    flags = annotate_claim_flags(assessment)
    after = assessment.model_dump(mode="json")
    assert before == after
    assert flags["hit_count"] >= 1
    assert "prohibited_pattern_hits" in flags
    assert all("classification" in hit for hit in flags["prohibited_pattern_hits"])
    assert all("counts_as_violation" in hit for hit in flags["prohibited_pattern_hits"])


def test_claim_flags_ignore_explicit_negations() -> None:
    from splatoon3_ai_coach.coach.evidence_contract import claim_contains_prohibited_language

    cases_ok = [
        "Cannot infer that the splat caused the death.",
        "The splat did not cause the death.",
        "There is no evidence that the splat caused the death.",
        "This does not establish that the player should have checked the map.",
        "The absence of a map check does not imply the player should have checked the map.",
        "Cannot conclude that you overextended.",
    ]
    for text in cases_ok:
        assert claim_contains_prohibited_language(text) is False, text
        assessment = CoachingAssessment(
            assessment=text, evidence_used=[], limitations=[text], recommendations=[]
        )
        flags = annotate_claim_flags(assessment)
        assert flags["hit_count"] == 0, (text, flags)


def test_claim_flags_count_genuine_assertions() -> None:
    from splatoon3_ai_coach.coach.evidence_contract import claim_contains_prohibited_language

    cases_bad = [
        "The splat caused the death.",
        "You should have checked the map.",
        "You overextended.",
        "You overextended and were outnumbered.",
    ]
    for text in cases_bad:
        assert claim_contains_prohibited_language(text) is True, text
        assessment = CoachingAssessment(
            assessment=text, evidence_used=[], limitations=[], recommendations=[]
        )
        flags = annotate_claim_flags(assessment)
        assert flags["hit_count"] >= 1, text
        assert any(hit["counts_as_violation"] for hit in flags["prohibited_pattern_hits"])


def test_serialize_coach_input_is_byte_identical_across_calls() -> None:
    unit = _coach_input_unit()
    a = serialize_coach_input_user_prompt(unit)
    b = serialize_coach_input_user_prompt(unit)
    assert a == b
    assert a.encode("utf-8") == b.encode("utf-8")


def test_generate_assessment_passes_identical_prompts_to_provider() -> None:
    unit = _coach_input_unit()
    system = load_system_prompt()
    user = serialize_coach_input_user_prompt(unit)
    reply = (
        '{"assessment":"ok","evidence_used":[],"limitations":[],'
        '"recommendations":[]}'
    )
    provider = FakeProvider(reply)
    assessment = generate_coaching_assessment(
        unit, provider, system_prompt=system, user_prompt=user
    )
    assert assessment.assessment == "ok"
    assert len(provider.calls) == 1
    assert provider.calls[0][0] == system
    assert provider.calls[0][1] == user


def test_two_models_receive_same_prompt_bytes() -> None:
    """Comparison invariant: only the provider/model differs."""
    unit = _coach_input_unit()
    system = load_system_prompt()
    user = serialize_coach_input_user_prompt(unit)
    reply = (
        '{"assessment":"a","evidence_used":[],"limitations":[],'
        '"recommendations":[]}'
    )
    a = FakeProvider(reply)
    b = FakeProvider(reply)
    generate_coaching_assessment(unit, a, system_prompt=system, user_prompt=user)
    generate_coaching_assessment(unit, b, system_prompt=system, user_prompt=user)
    assert a.calls[0][0] == b.calls[0][0]
    assert a.calls[0][1] == b.calls[0][1]
    assert a.calls[0][1].encode("utf-8") == b.calls[0][1].encode("utf-8")


@pytest.mark.skipif(
    not (_ANALYSIS_180224 / "vision_manifest.json").is_file(),
    reason="18-02-24 analysis missing",
)
def test_load_analysis_bundle_and_select_primaries() -> None:
    config = load_config(default_config_path())
    bundle = load_coach_analysis_bundle(
        _ANALYSIS_180224,
        min_usable_confidence=config.vision.timer.min_usable_confidence,
    )
    ids = select_primary_scenario_ids(bundle.scenarios, limit=10)
    assert len(ids) >= 2
    assert any(item.startswith("death_episode:") for item in ids)
    assert len(bundle.game_clock.observations) > 0


@pytest.mark.skipif(
    __import__("os").environ.get("S3_COACH_OLLAMA_SMOKE") != "1",
    reason="Set S3_COACH_OLLAMA_SMOKE=1 for live Ollama smoke",
)
def test_ollama_smoke_optional() -> None:
    from splatoon3_ai_coach.coach.llm_client import OllamaProvider

    provider = OllamaProvider("llama3.1:8b")
    raw = provider.complete(
        "Return JSON only.",
        '{"ping":true}\nReturn {"assessment":"pong","evidence_used":[],'
        '"limitations":[],"recommendations":[]}',
    )
    assessment = parse_coaching_assessment(raw)
    assert isinstance(assessment.assessment, str)
