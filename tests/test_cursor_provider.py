"""Cursor SDK provider + run selection, against a fake ``cursor_sdk`` (no live API)."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from splatoon3_ai_coach.coach.llm_client import CursorProvider, normalize_coach_provider
from splatoon3_ai_coach.coach.llm_runs import build_llm_runs
from splatoon3_ai_coach.config.models import CoachConfig
from splatoon3_ai_coach.config.settings import CoachSettings
from splatoon3_ai_coach.exceptions import ConfigError


class _FakeCursorAgentError(Exception):
    def __init__(self, message: str, *, is_retryable: bool) -> None:
        super().__init__(message)
        self.message = message
        self.is_retryable = is_retryable


@dataclass
class _FakeRunResult:
    id: str
    status: str
    result: str


def _install_fake_sdk(
    monkeypatch: pytest.MonkeyPatch, outcome: _FakeRunResult | Exception
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _options(**kwargs: Any) -> dict[str, Any]:
        return kwargs

    def _prompt(message: str, options: dict[str, Any]) -> _FakeRunResult:
        captured["message"] = message
        captured["options"] = options
        captured["cwd_existed"] = Path(options["local"]["cwd"]).is_dir()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    module = types.ModuleType("cursor_sdk")
    module.Agent = types.SimpleNamespace(prompt=_prompt)  # type: ignore[attr-defined]
    module.AgentOptions = _options  # type: ignore[attr-defined]
    module.LocalAgentOptions = _options  # type: ignore[attr-defined]
    module.CursorAgentError = _FakeCursorAgentError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "cursor_sdk", module)
    return captured


def test_normalize_accepts_cursor() -> None:
    assert normalize_coach_provider("Cursor") == "cursor"


def test_cursor_rejects_empty_key() -> None:
    with pytest.raises(ValueError, match="S3_COACH_CURSOR_API_KEY"):
        CursorProvider("composer-2.5", api_key=" ")


def test_cursor_complete_runs_toolless_agent_in_scratch_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_fake_sdk(
        monkeypatch, _FakeRunResult(id="run-1", status="finished", result='{"a": 1}')
    )
    provider = CursorProvider("composer-2.5", api_key="secret-key")

    assert provider.complete("SYSTEM", "USER") == '{"a": 1}'
    options = captured["options"]
    assert options["model"] == "composer-2.5"
    assert options["api_key"] == "secret-key"
    assert options["tools"] == []
    assert captured["cwd_existed"]
    assert not Path(options["local"]["cwd"]).exists()
    assert captured["message"].startswith("SYSTEM")
    assert captured["message"].endswith("USER")


def test_cursor_non_finished_status_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(monkeypatch, _FakeRunResult(id="run-2", status="error", result=""))
    with pytest.raises(RuntimeError, match="status='error'"):
        CursorProvider("composer-2.5", api_key="k").complete("s", "u")


def test_cursor_startup_error_is_wrapped_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sdk(monkeypatch, _FakeCursorAgentError("bad auth", is_retryable=False))
    with pytest.raises(RuntimeError, match="bad auth") as excinfo:
        CursorProvider("composer-2.5", api_key="secret-key").complete("s", "u")
    assert "secret-key" not in str(excinfo.value)


def test_cursor_missing_sdk_explains_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "cursor_sdk", None)
    with pytest.raises(RuntimeError, match=r"\.\[cursor\]"):
        CursorProvider("composer-2.5", api_key="k").complete("s", "u")


def _runs(settings: CoachSettings, **overrides: Any) -> list[Any]:
    return build_llm_runs(
        provider_name="cursor",
        coach_cfg=CoachConfig(),
        settings=settings,
        ollama_model=None,
        nvidia_model_override=None,
        also_baseline=True,
        baseline_model_override=None,
        **overrides,
    )


def test_build_runs_cursor_requires_key() -> None:
    settings = CoachSettings(_env_file=None, cursor_api_key="")
    with pytest.raises(ConfigError, match="S3_COACH_CURSOR_API_KEY"):
        _runs(settings)


def test_build_runs_cursor_uses_config_model_and_override() -> None:
    settings = CoachSettings(_env_file=None, cursor_api_key="k")
    runs = _runs(settings)
    assert [r.label for r in runs] == ["gpt-5.6-luna"]
    assert isinstance(runs[0].provider, CursorProvider)
    assert _runs(settings, cursor_model_override="gpt-5")[0].label == "gpt-5"
