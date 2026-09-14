"""OpenAI-compatible provider + coach-prototype provider selection (no live API)."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from splatoon3_ai_coach.coach.llm_runs import _build_llm_runs
from splatoon3_ai_coach.coach.llm_client import (
    OllamaProvider,
    OpenAICompatibleProvider,
    normalize_coach_provider,
)
from splatoon3_ai_coach.config.models import CoachConfig
from splatoon3_ai_coach.config.settings import CoachSettings
from splatoon3_ai_coach.exceptions import ConfigError


def test_normalize_coach_provider_defaults_to_ollama() -> None:
    assert normalize_coach_provider(None) == "ollama"
    assert normalize_coach_provider("") == "ollama"
    assert normalize_coach_provider("Ollama") == "ollama"
    assert normalize_coach_provider("nvidia") == "nvidia"
    with pytest.raises(ValueError):
        normalize_coach_provider("openai")


def test_openai_compatible_rejects_empty_key() -> None:
    with pytest.raises(ValueError, match="S3_COACH_NVIDIA_API_KEY"):
        OpenAICompatibleProvider("nvidia/test-model", api_key="  ")


def test_openai_compatible_request_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _FakeResponse:
        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": '{"ok":true}'}}
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> _FakeResponse:
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = OpenAICompatibleProvider(
        "nvidia/nemotron-test",
        api_key="nvapi-test-key-not-real",
        base_url="https://integrate.api.nvidia.com/v1",
    )
    content = provider.complete("system text", "user text")
    assert content == '{"ok":true}'
    assert captured["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert captured["method"] == "POST"
    assert captured["headers"]["authorization"] == "Bearer nvapi-test-key-not-real"
    assert captured["headers"]["accept"] == "application/json"
    body = captured["body"]
    assert body["model"] == "nvidia/nemotron-test"
    assert body["stream"] is False
    assert body["messages"] == [
        {"role": "system", "content": "system text"},
        {"role": "user", "content": "user text"},
    ]
    # Text-only: no multimodal content blocks.
    assert isinstance(body["messages"][0]["content"], str)
    assert isinstance(body["messages"][1]["content"], str)


def test_openai_compatible_http_error_is_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> object:
        raise urllib.error.HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b'{"error":"bad auth"}'),
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    provider = OpenAICompatibleProvider(
        "nvidia/test",
        api_key="nvapi-test-key-not-real",
    )
    with pytest.raises(RuntimeError, match="HTTP 401") as exc_info:
        provider.complete("s", "u")
    message = str(exc_info.value)
    assert "nvapi-test-key-not-real" not in message
    assert "Authorization" not in message


def test_build_llm_runs_ollama_default_unchanged() -> None:
    runs = _build_llm_runs(
        provider_name="ollama",
        coach_cfg=CoachConfig(),
        settings=CoachSettings(),
        ollama_model=None,
        nvidia_model_override=None,
        also_baseline=True,
        baseline_model_override=None,
    )
    assert [run.label for run in runs] == ["gpt-oss:20b", "llama3.1:8b"]
    assert all(isinstance(run.provider, OllamaProvider) for run in runs)


def test_build_llm_runs_nvidia_requires_env_key() -> None:
    with pytest.raises(ConfigError, match="S3_COACH_NVIDIA_API_KEY"):
        _build_llm_runs(
            provider_name="nvidia",
            coach_cfg=CoachConfig(),
            settings=CoachSettings(nvidia_api_key=None),
            ollama_model=None,
            nvidia_model_override=None,
            also_baseline=False,
            baseline_model_override=None,
        )


def test_build_llm_runs_nvidia_ignores_also_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("S3_COACH_NVIDIA_API_KEY", "nvapi-test-key-not-real")
    settings = CoachSettings()
    assert settings.nvidia_api_key == "nvapi-test-key-not-real"
    runs = _build_llm_runs(
        provider_name="nvidia",
        coach_cfg=CoachConfig(),
        settings=settings,
        ollama_model=None,
        nvidia_model_override=None,
        also_baseline=True,
        baseline_model_override=None,
    )
    assert len(runs) == 1
    assert isinstance(runs[0].provider, OpenAICompatibleProvider)
    assert runs[0].label == CoachConfig().nvidia_model


def test_default_yaml_provider_remains_ollama() -> None:
    from pathlib import Path

    from splatoon3_ai_coach.config import default_config_path, load_config

    cfg = load_config(default_config_path())
    assert cfg.coach.provider == "ollama"
    assert cfg.coach.model == "gpt-oss:20b"
    assert cfg.coach.baseline_model == "llama3.1:8b"
    assert "integrate.api.nvidia.com" in cfg.coach.openai_compatible_base_url
    assert cfg.coach.nvidia_model.startswith("nvidia/")
    # No secrets in YAML text.
    text = Path(default_config_path()).read_text(encoding="utf-8")
    assert "nvapi-" not in text
    assert "api_key" not in text.lower()
