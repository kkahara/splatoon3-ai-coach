"""Coach-inputs / coach-prototype CLI split."""

from __future__ import annotations

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
