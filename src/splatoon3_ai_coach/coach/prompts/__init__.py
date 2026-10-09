"""Bundled coaching prompts."""

from importlib.resources import files


def _read(name: str) -> str:
    package = files("splatoon3_ai_coach.coach.prompts")
    return package.joinpath(name).read_text(encoding="utf-8")


def load_system_prompt(*, roster_evidence: bool = False) -> str:
    """Return the coaching system prompt.

    Roster guidance is appended only when the roster evidence capability
    (``coach.roster_evidence``) is on.
    """
    base = _read("coach_system.txt")
    if not roster_evidence:
        return base
    return f"{base.rstrip()}\n\n{_read('coach_roster.txt')}"


def load_translation_prompt(locale: str) -> str:
    """Return the localization system prompt for ``locale`` (only ``ja`` exists)."""
    if locale != "ja":
        raise ValueError(f"No translation prompt for locale {locale!r}")
    return _read("coach_translate_ja.txt")
