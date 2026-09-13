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
    for behavioral advice. ``selected_claim_ids`` is optional experiment metadata
    (claim catalog IDs); it never rewrites other fields.
    """

    assessment: str = ""
    evidence_used: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    selected_claim_ids: list[str] = Field(default_factory=list)


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


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI-compatible chat completions (e.g. NVIDIA NIM).

    Same ``complete(system, user)`` contract as :class:`OllamaProvider`.
    Text-only messages — no multimodal content. Never logs the API key.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str,
        base_url: str = "https://integrate.api.nvidia.com/v1",
        timeout_seconds: float = 300.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError(
                "OpenAICompatibleProvider requires a non-empty API key "
                "(set S3_COACH_NVIDIA_API_KEY for NVIDIA)."
            )
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """POST ``/chat/completions`` and return assistant message content."""
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        body = json.dumps(payload).encode("utf-8")
        url = f"{self.base_url}/chat/completions"
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = _safe_http_error_body(exc)
            raise RuntimeError(
                f"OpenAI-compatible request failed for model={self.model!r} "
                f"at {url}: HTTP {exc.code}{detail}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"OpenAI-compatible request failed for model={self.model!r} "
                f"at {url}: {exc}"
            ) from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"OpenAI-compatible response was not JSON for model={self.model!r}"
            ) from exc
        content = _openai_message_content(data)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(
                f"OpenAI-compatible returned empty content for model={self.model!r}"
            )
        return content


def _openai_message_content(data: dict[str, Any]) -> str | None:
    """Extract assistant text from an OpenAI-style chat completion payload."""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    message = first.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content
    return None


def _safe_http_error_body(exc: urllib.error.HTTPError) -> str:
    """Short error detail without leaking Authorization headers or keys."""
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — best-effort diagnostics only
        return ""
    text = body.strip().replace("\n", " ")
    if len(text) > 300:
        text = text[:300] + "…"
    return f" body={text!r}" if text else ""


def normalize_coach_provider(name: str | None) -> str:
    """Normalize provider name; default ``ollama`` when unset/blank."""
    if name is None or not str(name).strip():
        return "ollama"
    value = str(name).strip().lower()
    if value not in {"ollama", "nvidia"}:
        raise ValueError(
            f"Unsupported coach provider {name!r}; expected 'ollama' or 'nvidia'"
        )
    return value
