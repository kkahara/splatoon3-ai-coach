"""Japanese localization of finalized English coaching prose (text in, text out)."""

from __future__ import annotations

import json

import pytest

from splatoon3_ai_coach.coach.llm_client import LLMProvider
from splatoon3_ai_coach.coach.prompts import load_translation_prompt
from splatoon3_ai_coach.coach.translation import translate_coaching

_ANALYSIS_KEYS = ("primary_context", "scenario_id", "factors", "event_ids")

_RULES = (
    "1. The input is already-finalized English coaching.",
    "2. Translate into natural Japanese.",
    "3. Preserve meaning and evidence strength.",
    "4. Do not add facts.",
    "5. Do not remove important facts.",
    "6. Do not invent intent or causality.",
    "7. Preserve uncertainty.",
    "8. Preserve positive/neutral/negative tone.",
    "9. Do not turn factual observations into praise or criticism.",
    "10. Keep the output concise.",
    "11. Use natural Japanese Splatoon terminology.",
    "12. Return only the Japanese coaching text",
)


class _FakeProvider(LLMProvider):
    """Records every prompt pair and replies with a fixed string."""

    def __init__(self, reply: str = "日本語のコーチングです。") -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.reply


def test_prompt_file_contains_all_twelve_rules() -> None:
    prompt = load_translation_prompt("ja")
    for rule in _RULES:
        assert rule in prompt


def test_unsupported_locale_has_no_prompt() -> None:
    with pytest.raises(ValueError):
        load_translation_prompt("fr")


def test_user_content_is_exactly_the_english_paragraph() -> None:
    english = "You retreated when your team was down two players. Good call."
    provider = _FakeProvider()
    translate_coaching(english, "ja", provider)
    assert len(provider.calls) == 1
    system_prompt, user_prompt = provider.calls[0]
    assert system_prompt == load_translation_prompt("ja")
    assert user_prompt == english
    for key in _ANALYSIS_KEYS:
        assert key not in system_prompt
        assert key not in user_prompt


@pytest.mark.parametrize("bad", [{"assessment": "Hi"}, ["Hi"], 3, None])
def test_non_text_input_is_refused(bad: object) -> None:
    provider = _FakeProvider()
    with pytest.raises(TypeError):
        translate_coaching(bad, "ja", provider)  # type: ignore[arg-type]
    assert provider.calls == []


def test_empty_input_is_refused() -> None:
    with pytest.raises(ValueError):
        translate_coaching("   ", "ja", _FakeProvider())


@pytest.mark.parametrize("reply", ["", "   ", '{"translation": ""}'])
def test_empty_output_raises(reply: str) -> None:
    with pytest.raises(RuntimeError):
        translate_coaching("Nice splat.", "ja", _FakeProvider(reply))


def test_json_wrapped_reply_is_unwrapped() -> None:
    reply = json.dumps({"japanese": "いい判断です。"}, ensure_ascii=False)
    translated = translate_coaching("Good call.", "ja", _FakeProvider(reply))
    assert translated == "いい判断です。"


def test_json_with_several_strings_is_returned_as_is() -> None:
    reply = json.dumps({"a": "一", "b": "二"}, ensure_ascii=False)
    assert translate_coaching("One. Two.", "ja", _FakeProvider(reply)) == reply


@pytest.mark.parametrize(
    "english",
    [
        "Good job holding the high ground; that kept your team safe.",
        "You were splatted 12 seconds after respawning.",
        "Next time, check the map before pushing into mid.",
        "You may have been able to wait for your team before engaging.",
        "You died while your special was ready.",
        "Retreating here gave your team time to regroup.",
        "You splatted two opponents near the zone.",
        "Your team was down 2v4 when you pushed; with a numbers advantage "
        "the same push could work.",
        "Using your special together with a teammate could have helped your team.",
        "Your team regained control of the zone shortly after.",
    ],
    ids=[
        "positive",
        "neutral_fact",
        "improvement",
        "uncertainty",
        "death",
        "retreat",
        "splat",
        "numbers",
        "special_coordination",
        "zone_control",
    ],
)
def test_contract_holds_for_representative_coaching(english: str) -> None:
    japanese = "（翻訳）" + str(len(english))
    provider = _FakeProvider(japanese)
    assert translate_coaching(english, "ja", provider) == japanese
    assert provider.calls[0][1] == english
