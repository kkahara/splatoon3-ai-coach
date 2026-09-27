"""Coach-inputs / coach-prototype CLI split."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from splatoon3_ai_coach.cli import app
from splatoon3_ai_coach.cli.coach_common import (
    COACH_INPUTS_META_FILENAME,
    COACHING_INDEX_FILENAME,
    default_coach_inputs_dir,
    resolve_coach_inputs_dir,
    write_json,
)

runner = CliRunner()


def test_resolve_coach_inputs_dir_requires_existing(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    try:
        resolve_coach_inputs_dir(analysis, None)
        assert False, "expected FileNotFoundError"
    except FileNotFoundError as exc:
        assert "coach-inputs" in str(exc)


def test_coach_prototype_errors_without_inputs(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    result = runner.invoke(app, ["coach-prototype", str(analysis)])
    assert result.exit_code != 0
    assert "coach-inputs" in (result.stdout + result.stderr).lower()


def test_coach_prototype_claims_only_loads_saved_inputs(tmp_path: Path) -> None:
    """LLM stage reads coach_inputs artifacts; does not require analysis bundle."""
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    inputs = default_coach_inputs_dir(analysis)
    inputs.mkdir(parents=True)

    scenario_id = "death_episode:10.000"
    safe_id = "death_episode_10.000"
    coach_input = {
        "primary_scenario": {
            "scenario_id": scenario_id,
            "scenario_type": "death_episode",
            "start_time": 10.0,
            "end_time": 20.0,
            "event_ids": [],
            "confidence": 1.0,
            "outcome": "died",
        },
        "primary_context": {
            "scenario_id": scenario_id,
        },
        "related": [],
        "game_clock_samples": [],
        "player_count_samples": [],
        "evidence_limits": [],
    }
    coaching = {
        "candidate_id": scenario_id,
        "candidate_type": "death_episode",
        "video_time": 10.0,
        "importance_score": 0.0,
        "factors": [],
        "rank": 1,
        "selected_for_llm": True,
        "supporting_evidence": [],
        "match_duration_seconds": 300,
    }
    llm_view = {
        "schema_version": 1,
        "unit": {
            "candidate_id": scenario_id,
            "candidate_type": "death_episode",
            "scenario_id": scenario_id,
            "scenario_type": "death_episode",
            "video_time": 10.0,
            "outcome": "died",
        },
        "importance": {
            "importance_score": 0.0,
            "rank": 1,
            "selected_for_llm": True,
            "match_duration_seconds": 300,
            "active_factor_ids": [],
            "active_factors": [],
        },
        "death": None,
        "clock": None,
        "roster": None,
        "special": None,
        "map": None,
        "related": [],
        "evidence_limits": [],
        "supporting_evidence": [],
    }
    write_json(inputs / f"{safe_id}.coach_input.json", coach_input)
    write_json(inputs / f"{safe_id}.coaching.json", coaching)
    write_json(inputs / f"{safe_id}.llm_view.json", llm_view)
    write_json(
        inputs / COACHING_INDEX_FILENAME,
        [
            {
                "scenario_id": scenario_id,
                "candidate_id": scenario_id,
                "candidate_type": "death_episode",
                "safe_id": safe_id,
                "video_time": 10.0,
                "importance_score": 0.0,
                "rank": 1,
                "selected_for_llm": True,
                "active_factors": [],
                "coach_input_json": f"{safe_id}.coach_input.json",
                "coaching_json": f"{safe_id}.coaching.json",
                "llm_view_json": f"{safe_id}.llm_view.json",
                "vmv_player": f"{safe_id}.vmv.player.txt",
                "vmv_dev": f"{safe_id}.vmv.dev.txt",
            }
        ],
    )
    write_json(
        inputs / COACH_INPUTS_META_FILENAME,
        {
            "analysis_dir": str(analysis),
            "match_duration_seconds": 300,
            "unit_count": 1,
        },
    )
    (inputs / f"{safe_id}.vmv.player.txt").write_text("player\n", encoding="utf-8")
    (inputs / f"{safe_id}.vmv.dev.txt").write_text("dev\n", encoding="utf-8")

    out = tmp_path / "proto"
    result = runner.invoke(
        app,
        [
            "coach-prototype",
            str(analysis),
            "--inputs",
            str(inputs),
            "--out",
            str(out),
            "--claims-only",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert (out / f"{safe_id}.coach_input.json").is_file()
    assert (out / f"{safe_id}.coaching.json").is_file()
    assert (out / "summary.md").is_file()
    assert (out / "llm_metrics.jsonl").is_file()


def test_default_coach_prototype_writes_prompts_without_calling(
    tmp_path: Path, monkeypatch
) -> None:
    """No-call mode writes the exact prompt files and does not build a provider."""
    calls: list[object] = []

    def refuse_provider(**_kwargs: object) -> list[object]:
        calls.append(_kwargs)
        raise AssertionError("default coach-prototype must not build a provider")

    monkeypatch.setattr(
        "splatoon3_ai_coach.cli.coach_prototype.build_llm_runs",
        refuse_provider,
    )
    analysis, inputs, selected, unselected = _two_unit_inputs(tmp_path)
    out = tmp_path / "proto"
    result = runner.invoke(
        app,
        ["coach-prototype", str(analysis), "--inputs", str(inputs), "--out", str(out)],
    )
    assert result.exit_code == 0, result.stdout
    assert calls == []
    assert (out / "system_prompt.txt").read_text(encoding="utf-8")
    user_prompt = (out / f"{selected}.user_prompt.txt").read_text(encoding="utf-8")
    assert "death_episode:10.000" in user_prompt
    assert not list(out.glob(f"{selected}.*.output.json"))
    assert not (out / f"{unselected}.user_prompt.txt").exists()
    skipped = list(out.glob(f"{unselected}.*.output.json"))
    assert len(skipped) == 1
    payload = json.loads(skipped[0].read_text(encoding="utf-8"))
    assert "not selected for LLM verbalization" in payload["assessment"]


def test_call_llm_invokes_selected_units_only(tmp_path: Path, monkeypatch) -> None:
    """--call-llm spends complete() on selected units and skips the rest."""
    provider = _RecordingProvider()

    def fake_runs(**_kwargs: object) -> list[_RecordingProvider]:
        return [provider]

    monkeypatch.setattr(
        "splatoon3_ai_coach.cli.coach_prototype.build_llm_runs",
        fake_runs,
    )
    analysis, inputs, selected, unselected = _two_unit_inputs(tmp_path)
    out = tmp_path / "proto"
    result = runner.invoke(
        app,
        [
            "coach-prototype",
            str(analysis),
            "--inputs",
            str(inputs),
            "--out",
            str(out),
            "--call-llm",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert len(provider.calls) == 1
    system_prompt, user_prompt = provider.calls[0]
    assert system_prompt == (out / "system_prompt.txt").read_text(encoding="utf-8")
    saved_prompt = (out / f"{selected}.user_prompt.txt").read_text(encoding="utf-8")
    assert user_prompt == saved_prompt
    called = json.loads(
        (out / f"{selected}.fake-model.output.json").read_text(encoding="utf-8")
    )
    assert called["assessment"] == "called"
    skipped = json.loads(
        (out / f"{unselected}.fake-model.output.json").read_text(encoding="utf-8")
    )
    assert "not selected for LLM verbalization" in skipped["assessment"]
    assert not (out / f"{unselected}.user_prompt.txt").exists()


class _RecordingProvider:
    """Stand-in LlmRun whose complete() records the exact prompt bytes."""

    label = "fake-model"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.provider = self

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return json.dumps(
            {
                "assessment": "called",
                "evidence_used": [],
                "limitations": [],
                "recommendations": [],
                "selected_claim_ids": [],
            }
        )


def _two_unit_inputs(tmp_path: Path) -> tuple[Path, Path, str, str]:
    """Write one selected unit and one unselected unit under coach_inputs/."""
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    inputs = default_coach_inputs_dir(analysis)
    inputs.mkdir(parents=True)
    selected = _write_saved_unit(inputs, "death_episode:10.000", rank=1, selected=True)
    unselected = _write_saved_unit(inputs, "death_episode:40.000", rank=4, selected=False)
    write_json(
        inputs / COACHING_INDEX_FILENAME,
        [_index_entry(selected, 1, True), _index_entry(unselected, 4, False)],
    )
    write_json(
        inputs / COACH_INPUTS_META_FILENAME,
        {"analysis_dir": str(analysis), "match_duration_seconds": 300, "unit_count": 2},
    )
    return analysis, inputs, selected[0], unselected[0]


def _write_saved_unit(
    inputs: Path, scenario_id: str, *, rank: int, selected: bool
) -> tuple[str, str]:
    safe_id = scenario_id.replace(":", "_").replace(".", "_")
    video_time = 10.0 if selected else 40.0
    write_json(
        inputs / f"{safe_id}.coach_input.json",
        {
            "primary_scenario": {
                "scenario_id": scenario_id,
                "scenario_type": "death_episode",
                "start_time": video_time,
                "end_time": video_time + 10,
                "event_ids": [],
                "confidence": 1.0,
                "outcome": "died",
            },
            "primary_context": {"scenario_id": scenario_id},
            "related": [],
            "game_clock_samples": [],
            "player_count_samples": [],
            "evidence_limits": [],
        },
    )
    write_json(
        inputs / f"{safe_id}.coaching.json",
        {
            "candidate_id": scenario_id,
            "candidate_type": "death_episode",
            "video_time": video_time,
            "importance_score": 0.0,
            "factors": [],
            "rank": rank,
            "selected_for_llm": selected,
            "supporting_evidence": [],
            "match_duration_seconds": 300,
        },
    )
    write_json(
        inputs / f"{safe_id}.llm_view.json",
        {
            "schema_version": 1,
            "unit": {
                "candidate_id": scenario_id,
                "candidate_type": "death_episode",
                "scenario_id": scenario_id,
                "scenario_type": "death_episode",
                "video_time": video_time,
                "outcome": "died",
            },
            "importance": {
                "importance_score": 0.0,
                "rank": rank,
                "selected_for_llm": selected,
                "match_duration_seconds": 300,
                "active_factor_ids": [],
                "active_factors": [],
            },
            "death": None,
            "clock": None,
            "roster": None,
            "special": None,
            "map": None,
            "related": [],
            "evidence_limits": [],
            "supporting_evidence": [],
        },
    )
    (inputs / f"{safe_id}.vmv.player.txt").write_text("player\n", encoding="utf-8")
    (inputs / f"{safe_id}.vmv.dev.txt").write_text("dev\n", encoding="utf-8")
    return safe_id, scenario_id


def _index_entry(unit: tuple[str, str], rank: int, selected: bool) -> dict:
    safe_id, scenario_id = unit
    return {
        "scenario_id": scenario_id,
        "candidate_id": scenario_id,
        "candidate_type": "death_episode",
        "safe_id": safe_id,
        "video_time": 10.0 if selected else 40.0,
        "importance_score": 0.0,
        "rank": rank,
        "selected_for_llm": selected,
        "active_factors": [],
        "coach_input_json": f"{safe_id}.coach_input.json",
        "coaching_json": f"{safe_id}.coaching.json",
        "llm_view_json": f"{safe_id}.llm_view.json",
        "vmv_player": f"{safe_id}.vmv.player.txt",
        "vmv_dev": f"{safe_id}.vmv.dev.txt",
    }
