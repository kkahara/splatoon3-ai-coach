"""Experiment D runner: dry run never calls a model; run is guarded and run-once."""

from __future__ import annotations

import json
from pathlib import Path

import coaching_units_experiment_d as xd
import pytest
from coaching_units import OBSERVATIONS_HEADER

from splatoon3_ai_coach.coach import llm_client
from splatoon3_ai_coach.coach.llm_client import LLMProvider
from splatoon3_ai_coach.coach.llm_view import LLM_VIEW_SCHEMA_VERSION
from splatoon3_ai_coach.config import default_config_path, load_config

PREAMBLE = "CoachLlmView JSON follows.\n\n"


def _view(t: float) -> dict:
    return {
        "schema_version": LLM_VIEW_SCHEMA_VERSION,
        "death": {"death_time_video": t, "source_path": "primary_context.death"},
    }


def _body(view: dict) -> str:
    return json.dumps(view, sort_keys=True, separators=(",", ":"))


def _deferred(tmp_path: Path, system: str) -> Path:
    root = tmp_path / "deferred"
    lead, other = _view(100.0), _view(110.0)
    files = {
        "ep/individual/call_1.user_prompt.txt": PREAMBLE + _body(lead),
        "ep/individual/call_2.user_prompt.txt": PREAMBLE + _body(other),
        "ep/episode/call_1.user_prompt.txt": (
            PREAMBLE + _body(lead) + OBSERVATIONS_HEADER + _body(other)
        ),
    }
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    (root / "system_prompt.txt").write_text(system, encoding="utf-8")

    def entry(rel: str, lead_t: float, members: list[float]) -> dict:
        return {
            "file": rel,
            "chars": len(files[rel]),
            "lead": f"death_episode:{lead_t:.3f}",
            "members": [f"death_episode:{m:.3f}" for m in members],
        }

    manifest = {
        "episodes": [
            {
                "episode": "ep",
                "match": "m1",
                "arms": {
                    "individual": [
                        entry("ep/individual/call_1.user_prompt.txt", 100.0, [100.0]),
                        entry("ep/individual/call_2.user_prompt.txt", 110.0, [110.0]),
                    ],
                    "episode": [
                        entry("ep/episode/call_1.user_prompt.txt", 100.0, [100.0, 110.0])
                    ],
                },
            }
        ]
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.fixture
def coach():
    return load_config(default_config_path()).coach


@pytest.fixture
def current_system(coach) -> str:
    return xd.load_system_prompt(roster_evidence=coach.roster_evidence)


def test_split_prompt_recovers_lead_and_members(
    tmp_path: Path, current_system: str
) -> None:
    _, calls = xd.load_calls(_deferred(tmp_path, current_system))
    grouped = next(c for c in calls if c.arm == "episode")
    lead, members = xd.split_prompt(grouped.user_prompt)
    assert lead["death"]["death_time_video"] == 100.0
    assert [m["death"]["death_time_video"] for m in members] == [110.0]


def test_plan_constructs_no_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, current_system: str
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an LLM provider was constructed")

    for cls in (
        llm_client.OllamaProvider,
        llm_client.OpenAICompatibleProvider,
        llm_client.CursorProvider,
    ):
        monkeypatch.setattr(cls, "__init__", refuse)
    out = tmp_path / "out"
    deferred = _deferred(tmp_path, current_system)
    assert xd.main(["plan", "--deferred", str(deferred), "--out", str(out)]) == 0
    doc = json.loads((out / "dry_run.json").read_text(encoding="utf-8"))
    assert doc["llm_calls_made"] == 0
    assert doc["stale_inputs"] == []
    assert [r["calls"] for r in doc["arms"]] == [2, 1, 0]
    assert not (out / "calls").exists()


def test_cursor_does_not_honor_temperature_and_nvidia_does() -> None:
    assert xd.temperature_support("cursor") == "unsupported"
    assert xd.temperature_support("ollama") == "supported"
    assert xd.temperature_support("nvidia") == "supported"


def test_stale_system_prompt_is_reported(tmp_path: Path, coach) -> None:
    system, calls = xd.load_calls(_deferred(tmp_path, "OLD SYSTEM"))
    problems = xd.staleness(system, calls, coach)
    assert len(problems) == 1 and "system_prompt.txt differs" in problems[0]


def test_view_missing_current_claim_basis_field_is_stale(tmp_path: Path, coach) -> None:
    root = _deferred(tmp_path, "x")
    stale = _view(100.0) | {"claim_basis": [{"claim_id": "c", "level": "fact"}]}
    rel = "ep/individual/call_1.user_prompt.txt"
    (root / rel).write_text(PREAMBLE + _body(stale), encoding="utf-8")
    system, calls = xd.load_calls(root)
    problems = xd.staleness(system, calls, coach)
    assert any("rec_frames" in p for p in problems)


def test_run_requires_model_and_confirm(tmp_path: Path, current_system: str) -> None:
    deferred = _deferred(tmp_path, current_system)
    with pytest.raises(SystemExit, match="--confirm"):
        xd.main(["run", "--deferred", str(deferred), "--out", str(tmp_path / "o")])


def test_run_refuses_unhonored_temperature(tmp_path: Path, current_system: str) -> None:
    deferred = _deferred(tmp_path, current_system)
    argv = ["run", "--provider", "cursor", "--model", "m", "--confirm"]
    with pytest.raises(SystemExit, match="allow-unhonored-temperature"):
        xd.main([*argv, "--deferred", str(deferred), "--out", str(tmp_path / "o")])


class _Fake(LLMProvider):
    temperature = 0.0
    temperature_support = "supported"

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return json.dumps(
            {
                "assessment": "Two deaths.",
                "evidence_used": [
                    "primary_context.death.death_time_video = 100.0",
                    "related.missing_block",
                ],
                "limitations": [],
                "recommendations": [],
            }
        )


def test_run_records_validates_and_refuses_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, current_system: str
) -> None:
    run = type("Run", (), {"provider": _Fake(), "label": "fake"})
    monkeypatch.setattr(xd, "build_llm_runs", lambda **_kw: [run])
    deferred, out = _deferred(tmp_path, current_system), tmp_path / "o"
    argv = ["run", "--provider", "ollama", "--model", "fake", "--confirm"]
    argv += ["--deferred", str(deferred), "--out", str(out)]
    assert xd.main(argv) == 0
    records = [json.loads(p.read_text()) for p in sorted((out / "calls").glob("*.json"))]
    assert len(records) == 3 and all(r["experiment"] is True for r in records)
    assert all(r["generation_settings"]["temperature"]["sent"] == 0.0 for r in records)
    rows = {r["arm"]: r for r in xd.summarize(records)}
    assert rows["individual"]["calls"] == 2 and rows["episode"]["calls"] == 1
    assert rows["episode"]["unresolved_paths"] == 1
    with pytest.raises(SystemExit, match="run once"):
        xd.main(argv)


def test_round2_sheet_is_blind_and_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, current_system: str
) -> None:
    run = type("Run", (), {"provider": _Fake(), "label": "fake"})
    monkeypatch.setattr(xd, "build_llm_runs", lambda **_kw: [run])
    deferred, out = _deferred(tmp_path, current_system), tmp_path / "o"
    common = ["--deferred", str(deferred), "--out", str(out)]
    xd.main(["run", "--provider", "ollama", "--model", "fake", "--confirm", *common])
    assert xd.main(["sheet", *common]) == 0
    sheet = (out / "round2_rating_sheet.csv").read_text(encoding="utf-8")
    key = (out / "round2_rating_key.csv").read_text(encoding="utf-8")
    assert len(sheet.splitlines()) == 4 and "100.0s; 110.0s" in sheet
    assert not any(arm in sheet for arm in xd.ARMS)
    assert all(arm in key for arm in ("individual", "episode"))
    with pytest.raises(SystemExit, match="never overwritten"):
        xd.main(["sheet", *common])


def test_member_only_reference_is_counted_separately() -> None:
    unresolved = [{"text": "primary_context.death.death_time_video = 110.0"}]
    members = [_view(110.0)]
    found = xd.member_only_references(unresolved, members)
    assert len(found) == 1 and found[0]["member_index"] == 0
