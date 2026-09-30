"""Localize finalized English coaching prose. Not a second coaching analyst.

The translator receives one already-finalized English coaching paragraph as
its only user content. It never sees ``CoachInput``, ScenarioContext,
evidence objects, or scenario data, so it cannot make a new coaching decision.
English stays the canonical coaching; the output is presentation only.
"""

from __future__ import annotations

import json
from typing import Literal

from splatoon3_ai_coach.coach.llm_client import LLMProvider
from splatoon3_ai_coach.coach.prompts import load_translation_prompt

TranslationLocale = Literal["ja"]


def translate_coaching(
    text: str, locale: TranslationLocale, provider: LLMProvider
) -> str:
    """Return ``text`` (English coaching prose) translated into ``locale``.

    Raises ``TypeError`` for non-string input, ``ValueError`` for empty input,
    and ``RuntimeError`` when the provider returns no usable text.
    """
    if not isinstance(text, str):
        raise TypeError("translate_coaching accepts only finalized English coaching text")
    english = text.strip()
    if not english:
        raise ValueError("Nothing to translate")
    reply = provider.complete(load_translation_prompt(locale), english)
    translated = _unwrap(reply).strip()
    if not translated:
        raise RuntimeError("Translation returned empty text")
    return translated


def _unwrap(reply: str) -> str:
    """Accept plain text, or a one-string JSON object from JSON-only providers."""
    stripped = reply.strip()
    if not stripped.startswith("{"):
        return stripped
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    if isinstance(payload, dict):
        values = [value for value in payload.values() if isinstance(value, str)]
        if len(values) == 1:
            return values[0]
    return stripped
