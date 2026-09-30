"""LLM provider run factory and skip helpers for coaching verbalization."""

from __future__ import annotations

from dataclasses import dataclass

from splatoon3_ai_coach.coach.llm_client import (
    CoachingAssessment,
    CursorProvider,
    LLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)
from splatoon3_ai_coach.config.models import CoachConfig
from splatoon3_ai_coach.config.settings import CoachSettings
from splatoon3_ai_coach.exceptions import ConfigError

_EMPTY_ASSESSMENT_TEXT = (
    "This coaching candidate was not selected for LLM verbalization "
    "(below top-N importance rank)."
)


@dataclass(frozen=True)
class LlmRun:
    """One LLM invocation label + provider (same system/user bytes)."""

    label: str
    provider: LLMProvider


def should_skip_llm(
    *,
    selected_for_llm: bool,
    force_llm: bool,
    has_llm_runs: bool,
) -> bool:
    """True when top-N ranking excluded this candidate (unless forced)."""
    return bool(has_llm_runs) and (not force_llm) and (not selected_for_llm)


def empty_coaching_assessment() -> CoachingAssessment:
    """Successful empty result when candidate was not selected for LLM."""
    return CoachingAssessment(
        assessment=_EMPTY_ASSESSMENT_TEXT,
        evidence_used=[],
        limitations=[
            "Candidate not in top-N by importance_score; skipped LLM "
            "verbalization for this unit."
        ],
        recommendations=[],
        selected_claim_ids=[],
    )


def approx_token_count(text: str | None) -> int | None:
    """Rough token estimate (``len // 4``). ``None`` when text was not built."""
    if text is None:
        return None
    return max(0, len(text) // 4)


def build_llm_runs(
    *,
    provider_name: str,
    coach_cfg: CoachConfig,
    settings: CoachSettings,
    ollama_model: str | None,
    nvidia_model_override: str | None,
    also_baseline: bool,
    baseline_model_override: str | None,
    cursor_model_override: str | None = None,
) -> list[LlmRun]:
    """Build primary (+ optional Ollama baseline) runs. Never logs API keys."""
    ollama_base = settings.ollama_base_url or coach_cfg.ollama_base_url
    baseline = baseline_model_override or coach_cfg.baseline_model
    runs: list[LlmRun] = []

    if provider_name == "ollama":
        primary = ollama_model or coach_cfg.model
        runs.append(
            LlmRun(
                label=primary,
                provider=OllamaProvider(primary, base_url=ollama_base),
            )
        )
    elif provider_name == "nvidia":
        runs.append(_nvidia_run(coach_cfg, settings, nvidia_model_override))
    elif provider_name == "cursor":
        runs.append(_cursor_run(coach_cfg, settings, cursor_model_override))
    else:  # pragma: no cover — normalize_coach_provider already validates
        raise ConfigError(f"Unsupported provider: {provider_name}")

    if (
        provider_name == "ollama"
        and also_baseline
        and baseline not in {run.label for run in runs}
    ):
        runs.append(
            LlmRun(
                label=baseline,
                provider=OllamaProvider(baseline, base_url=ollama_base),
            )
        )
    return runs


def _require_key(value: str | None, provider: str, env_name: str) -> str:
    """Return a non-blank API key or raise a ConfigError naming the variable."""
    if value and value.strip():
        return value
    raise ConfigError(
        f"provider={provider} requires {env_name}. "
        "Put it in a gitignored .env in the repo root "
        f"({env_name}=...) or export it in the same "
        "process that runs s3-coach. Do not put the key in YAML. "
        "Note: exporting in one terminal does not apply to Cursor "
        "Debug launches."
    )


def _nvidia_run(
    coach_cfg: CoachConfig, settings: CoachSettings, model_override: str | None
) -> LlmRun:
    """NVIDIA NIM through the OpenAI-compatible chat completions API."""
    api_key = _require_key(settings.nvidia_api_key, "nvidia", "S3_COACH_NVIDIA_API_KEY")
    model = model_override or coach_cfg.nvidia_model
    return LlmRun(
        label=model,
        provider=OpenAICompatibleProvider(
            model, api_key=api_key, base_url=coach_cfg.openai_compatible_base_url
        ),
    )


def _cursor_run(
    coach_cfg: CoachConfig, settings: CoachSettings, model_override: str | None
) -> LlmRun:
    """Cursor-hosted model through the Cursor SDK."""
    api_key = _require_key(settings.cursor_api_key, "cursor", "S3_COACH_CURSOR_API_KEY")
    model = model_override or coach_cfg.cursor_model
    return LlmRun(label=model, provider=CursorProvider(model, api_key=api_key))


# Backward-compatible aliases.
_LlmRun = LlmRun
_build_llm_runs = build_llm_runs
