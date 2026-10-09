"""LLM provider interface and coaching assessment schema."""

from __future__ import annotations

import hashlib
import json
import tempfile
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from splatoon3_ai_coach.config.models import canonical_factor_id

SettingSupport = Literal["supported", "unsupported", "unverified"]


class GenerationSetting(BaseModel):
    """What was asked for, what was actually sent, and whether it is honored."""

    requested: float | int | None = None
    sent: float | int | None = None
    support: SettingSupport


def sha256_text(text: str) -> str:
    """Hex sha256 of the UTF-8 bytes of ``text``."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ClaimStatement(BaseModel):
    """One authorized ``claim_basis`` claim as the model worded it."""

    claim_id: str
    level: str = ""
    sentence: str = ""

    @field_validator("claim_id")
    @classmethod
    def _current_id(cls, value: str) -> str:
        return canonical_factor_id(value)


class CoachingAssessment(BaseModel):
    """Evidence-grounded coaching reply for one CoachInput unit.

    Empty ``recommendations`` is a valid success when evidence is insufficient
    for behavioral advice. ``selected_claim_ids`` and ``claims`` name
    ``claim_basis`` entries; the validator checks them, never rewrites them.
    Renamed factor IDs are read under their current name (identifiers only).
    """

    assessment: str = ""
    evidence_used: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    selected_claim_ids: list[str] = Field(default_factory=list)
    claims: list[ClaimStatement] = Field(default_factory=list)

    @field_validator("claims", mode="before")
    @classmethod
    def _bare_claim_ids(cls, value: Any) -> Any:
        """Accept ``["claim_id", ...]`` as claims with no sentence."""
        if not isinstance(value, list):
            return value
        return [{"claim_id": v} if isinstance(v, str) else v for v in value]

    @field_validator("selected_claim_ids")
    @classmethod
    def _current_ids(cls, value: list[str]) -> list[str]:
        return [canonical_factor_id(item) for item in value]


# Backwards-compatible name used by the earlier Phase-5 stub.
CoachingOutput = CoachingAssessment


class LLMProvider(ABC):
    """Abstract interface for LLM backends (OpenAI, Anthropic, local, etc.)."""

    temperature: float | None = None
    seed: int | None = None
    temperature_support: SettingSupport = "unsupported"
    seed_support: SettingSupport = "unsupported"

    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Return a raw completion string from the provider."""

    def generation_settings(self) -> dict[str, GenerationSetting]:
        """Requested vs sent temperature / seed; unsupported values are not sent."""
        return {
            "temperature": _setting(self.temperature, self.temperature_support),
            "seed": _setting(self.seed, self.seed_support),
        }

    def input_hashes(self, system_prompt: str, user_prompt: str) -> dict[str, str]:
        """sha256 of the exact strings this provider passes to its API call."""
        return {
            "system_prompt_sha256": sha256_text(system_prompt),
            "user_prompt_sha256": sha256_text(user_prompt),
        }


def _setting(value: float | int | None, support: SettingSupport) -> GenerationSetting:
    sent = value if support != "unsupported" else None
    return GenerationSetting(requested=value, sent=sent, support=support)


def _sampling_fields(provider: LLMProvider) -> dict[str, Any]:
    """Temperature / seed fields actually placed in the request body."""
    settings = provider.generation_settings()
    return {
        name: setting.sent
        for name, setting in settings.items()
        if setting.sent is not None
    }


class OllamaProvider(LLMProvider):
    """Ollama chat API via stdlib urllib (no extra package dependency)."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 300.0,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.seed = seed

    temperature_support: SettingSupport = "supported"
    seed_support: SettingSupport = "supported"

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
        options = _sampling_fields(self)
        if options:
            payload["options"] = options
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Ollama request failed for model={self.model!r} at {self.base_url}: "
                f"{exc}"
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
        temperature: float | None = None,
        seed: int | None = None,
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
        self.temperature = temperature
        self.seed = seed

    # NVIDIA documents temperature; whether a seed is honored is not documented.
    temperature_support: SettingSupport = "supported"
    seed_support: SettingSupport = "unverified"

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """POST ``/chat/completions`` and return assistant message content."""
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            **_sampling_fields(self),
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
            with urllib.request.urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
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


class CursorProvider(LLMProvider):
    """Cursor-hosted models through the Cursor SDK (optional ``cursor`` extra).

    Each call is a one-shot local agent with **no built-in tools**, run in an
    empty scratch directory, so the model can only answer in text and never
    sees or edits the repo. The SDK has no separate system-prompt field, so
    both prompts are sent as one message. Never logs the API key.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str,
        temperature: float | None = None,
        seed: int | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError(
                "CursorProvider requires a non-empty API key "
                "(set S3_COACH_CURSOR_API_KEY)."
            )
        self.model = model
        self.api_key = api_key
        # Recorded as requested only: the SDK exposes no sampling controls.
        self.temperature = temperature
        self.seed = seed

    @staticmethod
    def joined_message(system_prompt: str, user_prompt: str) -> str:
        """The single message the SDK receives (no separate system field)."""
        return f"{system_prompt}\n\n---\n\n{user_prompt}"

    def input_hashes(self, system_prompt: str, user_prompt: str) -> dict[str, str]:
        """sha256 of the one joined message actually sent."""
        message = self.joined_message(system_prompt, user_prompt)
        return {"message_sha256": sha256_text(message)}

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """Run one tool-less Cursor agent turn and return its final text."""
        try:
            from cursor_sdk import (
                Agent,
                AgentOptions,
                CursorAgentError,
                LocalAgentOptions,
            )
        except ImportError as exc:
            raise RuntimeError(
                "provider=cursor needs the cursor-sdk package: pip install -e '.[cursor]'"
            ) from exc
        message = self.joined_message(system_prompt, user_prompt)
        with tempfile.TemporaryDirectory(prefix="s3-coach-cursor-") as scratch:
            options = AgentOptions(
                api_key=self.api_key,
                model=self.model,
                local=LocalAgentOptions(cwd=scratch),
                tools=[],
            )
            try:
                result = Agent.prompt(message, options)
            except CursorAgentError as exc:
                raise RuntimeError(
                    f"Cursor request failed for model={self.model!r}: "
                    f"{exc.message} (retryable={exc.is_retryable})"
                ) from exc
        if result.status != "finished":
            raise RuntimeError(
                f"Cursor run {result.id} ended with status={result.status!r} "
                f"for model={self.model!r}"
            )
        if not result.result.strip():
            raise RuntimeError(f"Cursor returned empty content for model={self.model!r}")
        return result.result


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


COACH_PROVIDERS = ("ollama", "nvidia", "cursor")


def normalize_coach_provider(name: str | None) -> str:
    """Normalize provider name; default ``ollama`` when unset/blank."""
    if name is None or not str(name).strip():
        return "ollama"
    value = str(name).strip().lower()
    if value not in COACH_PROVIDERS:
        raise ValueError(
            f"Unsupported coach provider {name!r}; "
            f"expected one of {', '.join(COACH_PROVIDERS)}"
        )
    return value
