"""Evidence-constrained LLM coaching layer."""

from splatoon3_ai_coach.coach.coach import generate_coaching
from splatoon3_ai_coach.coach.llm_client import CoachingOutput, LLMProvider
from splatoon3_ai_coach.coach.prompts import load_system_prompt

__all__ = [
    "CoachingOutput",
    "LLMProvider",
    "generate_coaching",
    "load_system_prompt",
]
