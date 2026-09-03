"""Bundled coaching prompts."""

from importlib.resources import files


def load_system_prompt() -> str:
    """Return the default system prompt for the coaching LLM."""
    return (
        files("splatoon3_ai_coach.coach.prompts")
        .joinpath("coach_system.txt")
        .read_text(encoding="utf-8")
    )
