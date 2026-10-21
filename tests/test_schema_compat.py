"""Older artifacts keep loading, and are never presented with current semantics."""

from __future__ import annotations

import json
from pathlib import Path

from coach_artifact_helpers import index_entry
from public_site.results import load_result

from splatoon3_ai_coach.analysis.scenario_context import MapContext
from splatoon3_ai_coach.coach.artifacts import (
    LEGACY_SCHEMA_VERSION,
    output_file_names,
    read_prototype_index,
    write_assessment_artifacts,
)
from splatoon3_ai_coach.coach.claim_validation import validate_assessment
from splatoon3_ai_coach.coach.llm_client import CoachingAssessment
from splatoon3_ai_coach.coach.llm_view import CoachLlmView

FIXTURES = Path(__file__).parent / "fixtures" / "traces"
SAFE = "death_episode_72.500"
MODEL = "gpt-oss:20b"


def _v1_view() -> dict:
    return json.loads((FIXTURES / "9d47_b_view_v1.json").read_text(encoding="utf-8"))


def _gpt_oss() -> CoachingAssessment:
    fixture = json.loads((FIXTURES / "9d47_b.json").read_text(encoding="utf-8"))
    return CoachingAssessment.model_validate(fixture["model_output"])


def test_v1_view_still_loads() -> None:
    view = CoachLlmView.model_validate(_v1_view())
    assert view.schema_version == 1
    assert view.map["map_check_before_death"] is False


def test_validator_resolves_against_v1_structure() -> None:
    report = validate_assessment(_gpt_oss().model_dump(mode="json"), _v1_view())
    assert report["validation_status"] == "blocked_unsupported_claim"
    assert report["causal_claim_hits"]
    kinds = {item["kind"] for item in report["evidence_path_resolution"]}
    assert kinds <= {"view_native", "unresolved"}


def _legacy_analysis(tmp_path: Path) -> Path:
    """Pre-versioning layout: bare-list indexes, v1 view, legacy coach input."""
    analysis = tmp_path / "legacy"
    inputs = analysis / "coach_inputs"
    prototype = analysis / "coach_prototype"
    inputs.mkdir(parents=True)
    prototype.mkdir()
    entry = index_entry(SAFE, scenario_id="death_episode:72.500", video_time=72.5)
    (inputs / "coaching_index.json").write_text(json.dumps([entry]))
    (inputs / f"{SAFE}.llm_view.json").write_text(json.dumps(_v1_view()))
    (inputs / f"{SAFE}.coaching.json").write_text(json.dumps({"factors": []}))
    legacy_input = {
        "primary_context": {
            "death_episode": {"death_time": 72.5},
            "map": {"map_check_before_death": False},
        }
    }
    (inputs / f"{SAFE}.coach_input.json").write_text(json.dumps(legacy_input))
    gpt = _gpt_oss()
    write_assessment_artifacts(
        out_dir=prototype,
        safe_id=SAFE,
        model_label=MODEL,
        assessment=gpt,
        raw_text=gpt.model_dump_json(),
        flags={"hit_count": 0},
        skipped_llm=False,
    )
    (prototype / "coaching_index.json").write_text(json.dumps([entry]))
    return analysis


def test_v1_prototype_index_reads_without_provenance(tmp_path: Path) -> None:
    analysis = _legacy_analysis(tmp_path)
    index = read_prototype_index(analysis / "coach_prototype")
    assert index is not None
    assert index.schema_version == LEGACY_SCHEMA_VERSION
    (output,) = index.entries[0].outputs
    assert output.output_json == output_file_names(SAFE, MODEL).output
    assert output.provenance is None


def test_v1_artifacts_get_current_status_but_are_marked_stale(tmp_path: Path) -> None:
    result = load_result(_legacy_analysis(tmp_path))
    assert result.stale is True
    assert result.stale_reason == "evidence_version_missing"
    (moment,) = result.moments
    assert moment.assessment_status == "blocked_unsupported_claim"
    assert "Map-check evidence was unavailable for this video." in moment.context
    assert "No map check observed before death" not in moment.context


def test_missing_new_context_fields_load_as_unknown() -> None:
    ctx = MapContext.model_validate({"map_check_before_death": False})
    assert ctx.map_check_status is None
    assert ctx.map_check_unknown_reason is None
    assert ctx.map_coverage is None
