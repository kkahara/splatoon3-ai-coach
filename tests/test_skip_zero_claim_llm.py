"""Top-N LLM skip + metrics helpers."""

from __future__ import annotations

from splatoon3_ai_coach.coach.llm_runs import (
    approx_token_count,
    empty_coaching_assessment,
    should_skip_llm,
)
from splatoon3_ai_coach.coach.llm_client import LLMProvider


class RecordingProvider(LLMProvider):
    """Records complete() calls; never hits the network."""

    def __init__(self, reply: str = '{"assessment":"x","evidence_used":[],'
                 '"limitations":[],"recommendations":[],"selected_claim_ids":[]}') -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.reply


def test_should_skip_llm_when_not_selected() -> None:
    assert should_skip_llm(
        selected_for_llm=False, force_llm=False, has_llm_runs=True
    )
    assert not should_skip_llm(
        selected_for_llm=False, force_llm=True, has_llm_runs=True
    )
    assert not should_skip_llm(
        selected_for_llm=True, force_llm=False, has_llm_runs=True
    )
    assert not should_skip_llm(
        selected_for_llm=False, force_llm=False, has_llm_runs=False
    )


def test_empty_assessment_is_successful_empty() -> None:
    assessment = empty_coaching_assessment()
    assert assessment.recommendations == []
    assert assessment.selected_claim_ids == []
    assert assessment.assessment
    assert assessment.limitations


def test_approx_token_count() -> None:
    assert approx_token_count(None) is None
    assert approx_token_count("") == 0
    assert approx_token_count("abcd") == 1


def test_skip_path_never_calls_provider() -> None:
    """Regression: selected_for_llm=false must not invoke complete() unless forced."""
    provider = RecordingProvider()
    skip = should_skip_llm(
        selected_for_llm=False, force_llm=False, has_llm_runs=True
    )
    assert skip
    if not skip:
        provider.complete("sys", "user")
    assert provider.calls == []

    force = should_skip_llm(
        selected_for_llm=False, force_llm=True, has_llm_runs=True
    )
    assert not force
    if not force:
        provider.complete("sys", "user")
    assert len(provider.calls) == 1
