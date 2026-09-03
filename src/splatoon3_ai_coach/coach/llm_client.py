"""LLM provider interface for evidence-constrained coaching."""

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class CoachingOutput(BaseModel):
    """Structured coaching response grounded in session metrics."""

    things_done_well: list[str] = Field(default_factory=list, min_length=0, max_length=5)
    things_to_fix: list[str] = Field(default_factory=list, min_length=0, max_length=5)
    drill_to_practice: str = ""


class LLMProvider(ABC):
    """Abstract interface for LLM backends (OpenAI, Anthropic, local, etc.)."""

    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return a raw completion string from the provider."""
