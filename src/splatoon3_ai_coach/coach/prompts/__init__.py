"""Bundled coaching prompts."""

from importlib.resources import files


def _read(name: str) -> str:
    package = files("splatoon3_ai_coach.coach.prompts")
    return package.joinpath(name).read_text(encoding="utf-8")


def load_system_prompt() -> str:
    """Return the default system prompt for the coaching LLM."""
    return _read("coach_system.txt")


def load_translation_prompt(locale: str) -> str:
    """Return the localization system prompt for ``locale`` (only ``ja`` exists)."""
    if locale != "ja":
        raise ValueError(f"No translation prompt for locale {locale!r}")
    return _read("coach_translate_ja.txt")
