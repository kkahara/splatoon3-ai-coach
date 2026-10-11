"""Coaching snapshot counts stay independent of prose and scenario ids."""

from __future__ import annotations

import json
from pathlib import Path

from public_site.coaching_metrics import read_coaching_metrics
from public_site.results import load_result

from splatoon3_ai_coach.coach.artifacts import COACH_EVIDENCE_VERSION

_SCENARIO = "scenario-secret-9f3a"
_PROSE = "Hold the zone before you push."
_HASH = "abc123def456"


def test_missing_index_has_no_snapshot(tmp_path: Path) -> None:
    assert read_coaching_metrics(tmp_path) is None


def test_selected_disposition_ignores_unselected_calls_and_prose(tmp_path: Path) -> None:
    _write_match(tmp_path)
    snapshot = read_coaching_metrics(tmp_path)
    assert snapshot is not None
    assert snapshot.candidate == 3
    assert snapshot.positive_score == 2
    assert snapshot.selected == 2
    assert snapshot.not_requested == 1
    assert snapshot.requested_failed == 1
    assert snapshot.requested_succeeded == 0
    assert snapshot.unselected_requested == 1
    assert snapshot.importance_config_hash == _HASH
    assert snapshot.evidence_version == COACH_EVIDENCE_VERSION
    assert snapshot.stale is False

    result = load_result(tmp_path)
    states = [moment.coaching_state for moment in result.moments]
    assert states.count("recommendation") == snapshot.recommendation
    assert states.count("not_reviewed") == snapshot.not_reviewed
    assert snapshot.recommendation == 1
    assert snapshot.requested_succeeded == 0
    document = snapshot.model_dump(mode="json")
    encoded = json.dumps(document)
    assert _SCENARIO not in encoded
    assert _PROSE not in encoded
    assert "Reset on the rim." not in encoded
    assert _closed(document)


def test_factor_map_collapses_past_thirty_two_labels(tmp_path: Path) -> None:
    factors = [f"factor{index:02d}" for index in range(33)]
    _write_index(tmp_path, [_entry("only", factors=factors, selected=False, score=1)])
    snapshot = read_coaching_metrics(tmp_path)
    assert snapshot is not None
    assert len(snapshot.by_factor) == 32
    assert snapshot.by_factor["other"].not_reviewed == 2


def _write_match(analysis: Path) -> None:
    selected = _entry("kept", selected=True, score=1.0, video_time=1)
    ignored = _entry("other", selected=False, score=0, video_time=2)
    shown = _entry(
        "shown", selected=True, score=2, video_time=3, factors=["special_ready"]
    )
    _write_index(analysis, [selected, ignored, shown])
    prototype = analysis / "coach_prototype"
    prototype.mkdir(parents=True)
    (prototype / "coaching_index.json").write_text(
        json.dumps(_prototype("shown")), encoding="utf-8"
    )
    (prototype / "shown.test-model.output.json").write_text(
        json.dumps(
            {
                "assessment": _PROSE,
                "recommendations": ["Reset on the rim."],
            }
        ),
        encoding="utf-8",
    )
    (prototype / "llm_metrics.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "scenario_id": _SCENARIO,
                        "selected_for_llm": True,
                        "skipped_llm": True,
                        "success": False,
                    }
                ),
                json.dumps(
                    {
                        "scenario_id": "scenario-unselected",
                        "selected_for_llm": False,
                        "skipped_llm": False,
                        "success": True,
                    }
                ),
                json.dumps(
                    {
                        "scenario_id": "scenario-shown",
                        "selected_for_llm": True,
                        "skipped_llm": False,
                        "success": False,
                    }
                ),
                json.dumps(
                    {
                        "scenario_id": "scenario-shown",
                        "selected_for_llm": True,
                        "skipped_llm": False,
                        "success": True,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )


def _write_index(analysis: Path, entries: list[dict]) -> None:
    inputs = analysis / "coach_inputs"
    inputs.mkdir(parents=True)
    (inputs / "coaching_index.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "evidence_version": COACH_EVIDENCE_VERSION,
                "analysis_id": "analysis-current",
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )
    (analysis / "vision_manifest.json").write_text(
        json.dumps({"analysis": {"analysis_id": "analysis-current"}}),
        encoding="utf-8",
    )


def _entry(
    safe_id: str,
    *,
    selected: bool,
    score: float,
    video_time: float = 1,
    factors: list[str] | None = None,
) -> dict:
    scenario = _SCENARIO if safe_id == "kept" else f"scenario-{safe_id}"
    return {
        "scenario_id": scenario,
        "candidate_id": safe_id,
        "candidate_type": "death_episode",
        "safe_id": safe_id,
        "video_time": video_time,
        "importance_score": score,
        "rank": 1,
        "selected_for_llm": selected,
        "importance_config_hash": _HASH,
        "active_factors": ["special_ready"] if factors is None else factors,
        "coach_input_json": f"{safe_id}.coach_input.json",
        "coaching_json": f"{safe_id}.coaching.json",
    }


def _prototype(safe_id: str) -> dict:
    output = f"{safe_id}.test-model.output.json"
    entry = _entry(safe_id, selected=True, score=2, video_time=3)
    entry.pop("importance_config_hash")
    return {
        "schema_version": 3,
        "provider": "nvidia",
        "primary_model": "test-model",
        "entries": [
            {
                **entry,
                "outputs": [
                    {
                        "model": "test-model",
                        "output_json": output,
                        "raw_txt": f"{safe_id}.test-model.raw.txt",
                        "flags_json": f"{safe_id}.test-model.flags.json",
                        "skipped_llm": False,
                    }
                ],
                "primary_output_json": output,
            }
        ],
    }


def _closed(value: object) -> bool:
    if isinstance(value, dict):
        return all(_token(key) and _closed(item) for key, item in value.items())
    if isinstance(value, list):
        return all(_closed(item) for item in value)
    if isinstance(value, str):
        return _token(value)
    return isinstance(value, (int, float, bool)) or value is None


def _token(value: str) -> bool:
    return bool(value) and " " not in value and "/" not in value and len(value) <= 64
