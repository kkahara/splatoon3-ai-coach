"""Generate structured coaching from a GameSession."""

from loguru import logger

from splatoon3_ai_coach.analysis.session import GameSession
from splatoon3_ai_coach.coach.llm_client import CoachingOutput, LLMProvider
from splatoon3_ai_coach.coach.prompts import load_system_prompt


def generate_coaching(
    session: GameSession,
    provider: LLMProvider,
) -> CoachingOutput:
    """Turn a GameSession into structured coaching via the LLM provider.

    TODO(phase-5): serialize session metrics into the user prompt and parse
    the LLM response into CoachingOutput. No hallucinations — only extracted
    metrics may appear in the output.
    """
    system_prompt = load_system_prompt()
    user_prompt = _build_user_prompt(session)
    logger.info(
        "Generating coaching for {} ({:.1f}s, {} timeline points)",
        session.video,
        session.duration_seconds,
        len(session.timeline),
    )
    raw = provider.complete(system_prompt, user_prompt)
    logger.debug("LLM raw response length: {} chars", len(raw))
    return CoachingOutput()


def _build_user_prompt(session: GameSession) -> str:
    """Serialize session evidence for the LLM user turn."""
    return (
        f"Video: {session.video}\n"
        f"Duration: {session.duration_seconds:.1f}s\n"
        f"Timeline points: {len(session.timeline)}\n"
        f"Events: {len(session.events)}\n"
        f"Scores: {session.scores}\n"
    )
