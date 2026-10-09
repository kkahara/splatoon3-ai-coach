"""Provider sampling settings, exact-input hashes, and prototype-index compat."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from splatoon3_ai_coach.coach.artifacts import (
    PROTOTYPE_INDEX_SCHEMA_VERSION,
    read_prototype_index,
)
from splatoon3_ai_coach.coach.llm_client import (
    CursorProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    sha256_text,
)
from splatoon3_ai_coach.coach.llm_runs import LlmRun, build_llm_runs, run_provenance
from splatoon3_ai_coach.config.models import CoachConfig
from splatoon3_ai_coach.config.settings import CoachSettings


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _capture(monkeypatch: pytest.MonkeyPatch, reply: dict[str, Any]) -> list[dict]:
    sent: list[dict] = []

    def fake_urlopen(request, timeout):  # noqa: ANN001, ARG001
        sent.append(json.loads(request.data.decode("utf-8")))
        return _Response(reply)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return sent


def test_ollama_sends_temperature_and_seed_as_options(monkeypatch) -> None:
    sent = _capture(monkeypatch, {"message": {"content": "{}"}})
    provider = OllamaProvider("m", temperature=0.2, seed=7)
    provider.complete("sys", "user")
    assert sent[0]["options"] == {"temperature": 0.2, "seed": 7}
    settings = provider.generation_settings()
    assert settings["temperature"].sent == 0.2
    assert settings["seed"].support == "supported"


def test_ollama_omits_options_when_unset(monkeypatch) -> None:
    sent = _capture(monkeypatch, {"message": {"content": "{}"}})
    OllamaProvider("m").complete("sys", "user")
    assert "options" not in sent[0]


def test_openai_compatible_sends_fields_and_marks_seed_unverified(monkeypatch) -> None:
    sent = _capture(monkeypatch, {"choices": [{"message": {"content": "{}"}}]})
    provider = OpenAICompatibleProvider("m", api_key="k", temperature=0.0, seed=3)
    provider.complete("sys", "user")
    assert sent[0]["temperature"] == 0.0
    assert sent[0]["seed"] == 3
    settings = provider.generation_settings()
    assert settings["temperature"].support == "supported"
    assert settings["seed"].support == "unverified"
    assert settings["seed"].sent == 3


def test_cursor_records_requested_but_sends_nothing() -> None:
    provider = CursorProvider("m", api_key="k", temperature=0.3, seed=1)
    settings = provider.generation_settings()
    for name, requested in (("temperature", 0.3), ("seed", 1)):
        assert settings[name].requested == requested
        assert settings[name].sent is None
        assert settings[name].support == "unsupported"
    message = CursorProvider.joined_message("sys", "user")
    assert provider.input_hashes("sys", "user") == {
        "message_sha256": sha256_text(message)
    }


def test_build_llm_runs_threads_config_sampling() -> None:
    cfg = CoachConfig(llm_temperature=0.1, llm_seed=42)
    runs = build_llm_runs(
        provider_name="ollama",
        coach_cfg=cfg,
        settings=CoachSettings(),
        ollama_model="m",
        nvidia_model_override=None,
        also_baseline=False,
        baseline_model_override=None,
    )
    assert runs[0].provider.temperature == 0.1
    assert runs[0].provider.seed == 42


def test_run_provenance_records_exact_inputs() -> None:
    run = LlmRun(label="m", provider=OllamaProvider("m", temperature=0.0, seed=5))
    provenance = run_provenance(
        run,
        provider_name="ollama",
        system_prompt="sys",
        user_prompt="user",
        llm_view_bytes=b"{}",
        importance_config_hash="abc",
        executed_at="2026-10-06T00:00:00+00:00",
    )
    assert provenance.input_hashes["user_prompt_sha256"] == sha256_text("user")
    assert provenance.llm_view_sha256 == (
        "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
    )
    assert provenance.seed.sent == 5
    assert provenance.determinism == "best_effort"


def _index(schema: int, output: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": schema,
        "provider": "ollama",
        "primary_model": "m",
        "entries": [
            {
                "scenario_id": "death_episode:1.000",
                "candidate_id": "death_episode:1.000",
                "candidate_type": "death_episode",
                "safe_id": "u",
                "video_time": 1.0,
                "importance_score": 1.0,
                "rank": 1,
                "selected_for_llm": True,
                "active_factors": [],
                "coach_input_json": "u.coach_input.json",
                "coaching_json": "u.coaching.json",
                "outputs": [output],
                "primary_output_json": output["output_json"],
            }
        ],
    }


_OUTPUT = {
    "model": "m",
    "output_json": "u.m.output.json",
    "raw_txt": "u.m.raw.txt",
    "flags_json": "u.m.flags.json",
    "skipped_llm": False,
}


def test_schema_2_index_reads_with_provenance_not_recorded(tmp_path: Path) -> None:
    (tmp_path / "coaching_index.json").write_text(json.dumps(_index(2, _OUTPUT)))
    index = read_prototype_index(tmp_path)
    assert index is not None
    assert index.entries[0].outputs[0].provenance is None
    assert PROTOTYPE_INDEX_SCHEMA_VERSION == 3


def test_schema_4_index_is_rejected(tmp_path: Path) -> None:
    from splatoon3_ai_coach.coach.artifacts import ArtifactError

    (tmp_path / "coaching_index.json").write_text(json.dumps(_index(4, _OUTPUT)))
    with pytest.raises(ArtifactError):
        read_prototype_index(tmp_path)
