"""Static UI strings: English and Japanese resources stay in step."""

from __future__ import annotations

import json
from pathlib import Path

_I18N = Path(__file__).resolve().parents[1] / "web" / "public" / "src" / "i18n"


def _load(name: str) -> dict[str, str]:
    return json.loads((_I18N / name).read_text(encoding="utf-8"))


def test_english_and_japanese_have_the_same_keys() -> None:
    english = _load("en.json")
    japanese = _load("ja.json")
    assert set(english) == set(japanese)
    assert all(isinstance(value, str) and value for value in english.values())
    assert all(isinstance(value, str) and value for value in japanese.values())


def test_japanese_keeps_every_placeholder() -> None:
    english = _load("en.json")
    japanese = _load("ja.json")
    for key, text in english.items():
        names = {part.split("}")[0] for part in text.split("{")[1:]}
        for name in names:
            assert f"{{{name}}}" in japanese[key], key
