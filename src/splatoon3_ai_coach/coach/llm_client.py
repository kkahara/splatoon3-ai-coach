"""LLM provider interface and coaching assessment schema."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field


class CoachingAssessment(BaseModel):
    """Evidence-grounded coaching reply for one CoachInput unit.

    Empty ``recommendations`` is a valid success when evidence is insufficient
    for behavioral advice.
    """

    assessment: str = ""
    evidence_used: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


# Backwards-compatible name used by the earlier Phase-5 stub.
CoachingOutput = CoachingAssessment


class LLMProvider(ABC):
    """Abstract interface for LLM backends (OpenAI, Anthropic, local, etc.)."""

    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return a raw completion string from the provider."""


class OllamaProvider(LLMProvider):
    """Ollama chat API via stdlib urllib (no extra package dependency)."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 300.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """POST ``/api/chat`` and return the assistant message content."""
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Ollama request failed for model={self.model!r} at {self.base_url}: {exc}"
            ) from exc
        data = json.loads(raw)
        message = data.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"Ollama returned empty content for model={self.model!r}")
        return content
