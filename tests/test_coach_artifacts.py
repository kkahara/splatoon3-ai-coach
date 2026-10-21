"""The coach artifact boundary: schemas, legacy reads, primary selection, links."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from coach_artifact_helpers import index_entry, write_prototype_unit
from pydantic import ValidationError
from typer.testing import CliRunner

from splatoon3_ai_coach.cli import app
from splatoon3_ai_coach.coach.artifacts import (
    COACH_EVIDENCE_VERSION,
    COACHING_INDEX_FILENAME,
    COACHING_INDEX_SCHEMA_VERSION,
    LEGACY_SCHEMA_VERSION,
    ArtifactError,
    CoachingIndexEntry,
    PrototypeIndex,
    PrototypeIndexEntry,
    PrototypeOutput,
    StoredValidation,
    load_unit_artifacts,
    output_file_names,
    primary_assessment,
    primary_stored_validation,
    read_coaching_index,
    read_prototype_index,
    unit_assessments,
    write_coaching_index,
    write_json,
)
from splatoon3_ai_coach.coach.claim_validation import (
    VALIDATION_CONTRACT_VERSION,
    ValidationStatus,
)
from splatoon3_ai_coach.coach.llm_runs import empty_coaching_assessment

SCENARIO = "death_episode:10.000"
SAFE = "death_episode_10.000"


def _write_unit(inputs: Path, *, coaching_candidate: str = SCENARIO) -> None:
    inputs.mkdir(parents=True, exist_ok=True)
    scenario = {
        "scenario_id": SCENARIO,
        "scenario_type": "death_episode",
        "start_time": 10.0,
        "end_time": 20.0,
        "event_ids": [],
        "confidence": 1.0,
        "outcome": "died",
    }
    write_json(
        inputs / f"{SAFE}.coach_input.json",
        {
            "primary_scenario": scenario,
            "primary_context": {"scenario_id": SCENARIO},
            "related": [],
            "game_clock_samples": [],
            "player_count_samples": [],
            "evidence_limits": [],
        },
    )
    write_json(
        inputs / f"{SAFE}.coaching.json",
        {
            "candidate_id": coaching_candidate,
            "candidate_type": "death_episode",
            "video_time": 10.0,
            "importance_score": 0.0,
            "factors": [],
            "match_duration_seconds": 300,
        },
    )


def _entry(**overrides: object) -> CoachingIndexEntry:
    row = index_entry(SAFE, scenario_id=SCENARIO, candidate_id=SCENARIO, video_time=10.0)
    return CoachingIndexEntry.model_validate({**row, **overrides})


def test_coaching_index_round_trips_with_schema_version(tmp_path: Path) -> None:
    write_coaching_index(tmp_path, [_entry()], analysis_id="a1")
    raw = json.loads((tmp_path / COACHING_INDEX_FILENAME).read_text(encoding="utf-8"))
    assert raw["schema_version"] == COACHING_INDEX_SCHEMA_VERSION
    assert raw["evidence_version"] == COACH_EVIDENCE_VERSION
    assert raw["analysis_id"] == "a1"
    index = read_coaching_index(tmp_path)
    assert index.entries == [_entry()]
    assert index.by_scenario_id(SCENARIO).safe_id == SAFE


def test_legacy_list_index_is_read_as_schema_one(tmp_path: Path) -> None:
    write_json(tmp_path / COACHING_INDEX_FILENAME, [_entry().model_dump(mode="json")])
    index = read_coaching_index(tmp_path)
    assert index.schema_version == LEGACY_SCHEMA_VERSION
    assert index.entries[0].candidate_id == SCENARIO


def test_newer_or_malformed_index_is_rejected(tmp_path: Path) -> None:
    write_json(
        tmp_path / COACHING_INDEX_FILENAME,
        {"schema_version": COACHING_INDEX_SCHEMA_VERSION + 1, "entries": []},
    )
    with pytest.raises(ArtifactError, match="schema_version"):
        read_coaching_index(tmp_path)
    write_json(tmp_path / COACHING_INDEX_FILENAME, [{"safe_id": SAFE}])
    with pytest.raises(ArtifactError, match="index schema"):
        read_coaching_index(tmp_path)


def test_index_refuses_paths_outside_the_artifact_directory() -> None:
    with pytest.raises(ValidationError, match="bare filename"):
        _entry(coaching_json="../secrets.json")


def test_unit_documents_load_typed_and_cross_checked(tmp_path: Path) -> None:
    _write_unit(tmp_path)
    loaded = load_unit_artifacts(tmp_path, _entry())
    assert loaded.coach_input.primary_scenario.scenario_id == SCENARIO
    assert loaded.unit.candidate_id == SCENARIO
    assert loaded.llm_view is None


def test_mismatched_unit_documents_raise(tmp_path: Path) -> None:
    _write_unit(tmp_path, coaching_candidate="death_episode:99.000")
    with pytest.raises(ArtifactError, match="coaching candidate"):
        load_unit_artifacts(tmp_path, _entry())


def test_output_names_keep_exact_model_label() -> None:
    names = output_file_names(SAFE, "llama3.1:8b")
    assert names.output == f"{SAFE}.llama3.1_8b.output.json"
    assert names.flags == f"{SAFE}.llama3.1_8b.flags.json"


def test_primary_model_is_explicit_not_last_non_empty(tmp_path: Path) -> None:
    write_prototype_unit(
        tmp_path,
        SAFE,
        {"primary": empty_coaching_assessment().assessment, "zz-baseline": "Prose."},
        primary_model="primary",
    )
    chosen = primary_assessment(tmp_path, SAFE)
    assert chosen.assessment == empty_coaching_assessment().assessment
    listed = unit_assessments(tmp_path, SAFE)
    assert [(a.model, a.primary) for a in listed] == [
        ("primary", True),
        ("zz-baseline", False),
    ]


def test_public_result_never_falls_back_to_a_secondary_model(tmp_path: Path) -> None:
    from public_site.results import _review

    write_prototype_unit(
        tmp_path,
        SAFE,
        {"primary": empty_coaching_assessment().assessment, "zz-baseline": "Prose."},
        primary_model="primary",
    )
    entry = CoachingIndexEntry.model_validate(index_entry(SAFE))
    review = _review(tmp_path, tmp_path, entry)
    assert review.state == "not_reviewed"
    assert (review.assessment, review.status) == (None, None)


@pytest.mark.parametrize(
    ("assessment", "recommendations", "state"),
    [
        ("No map overlay was observed before the death.", ["Open the map."],
         "recommendation"),
        ("No map overlay was observed before the death.", [], "no_recommendation"),
        ("Skipping the map check caused the death.", ["Open the map."], "blocked"),
    ],
)
def test_public_review_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    assessment: str,
    recommendations: list[str],
    state: str,
) -> None:
    from public_site import results

    from splatoon3_ai_coach.coach.llm_client import CoachingAssessment

    output = CoachingAssessment(assessment=assessment, recommendations=recommendations)
    monkeypatch.setattr(results, "primary_assessment", lambda *_: output)
    entry = CoachingIndexEntry.model_validate(index_entry(SAFE))
    review = results._review(tmp_path, tmp_path, entry)
    assert review.state == state
    if state == "blocked":
        assert review.assessment is None and review.recommendations == []
    else:
        assert review.assessment == assessment
        assert review.recommendations == recommendations


def _set_flags(folder: Path, model_tag: str, flags: dict[str, object]) -> None:
    write_json(folder / f"{SAFE}.{model_tag}.flags.json", flags)


def test_stored_validation_is_read_from_the_primary_output_only(tmp_path: Path) -> None:
    write_prototype_unit(
        tmp_path,
        SAFE,
        {"primary": "One.", "zz-baseline": "Two."},
        primary_model="primary",
    )
    _set_flags(tmp_path, "zz-baseline", {"validation_status": "validated"})
    assert primary_stored_validation(tmp_path, SAFE) is None
    _set_flags(
        tmp_path,
        "primary",
        {"validation_status": "contains_unverified_wording",
         "validation_contract_version": 1},
    )
    assert primary_stored_validation(tmp_path, SAFE) == StoredValidation(
        status=ValidationStatus.CONTAINS_UNVERIFIED_WORDING, contract_version=1
    )


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ({"validation_status": "validated"}, StoredValidation(status="validated")),
        ({"validation_status": "validated", "validation_contract_version": True},
         StoredValidation(status="validated")),
        ({"hit_count": 0}, None),
        ({"validation_status": "maybe"}, None),
        ({"validation_status": None}, None),
    ],
    ids=["unversioned", "bad_version", "legacy", "unknown", "null"],
)
def test_stored_validation_is_never_guessed(
    tmp_path: Path, flags: dict[str, object], expected: StoredValidation | None
) -> None:
    write_prototype_unit(tmp_path, SAFE, {"m": "Text."}, primary_model="m")
    _set_flags(tmp_path, "m", flags)
    assert primary_stored_validation(tmp_path, SAFE) == expected


def test_stored_validation_absent_without_an_unambiguous_primary(tmp_path: Path) -> None:
    assert primary_stored_validation(tmp_path, SAFE) is None
    _legacy_output(tmp_path, "gpt-oss_20b", "One.")
    _set_flags(tmp_path, "gpt-oss_20b", {"validation_status": "validated"})
    assert primary_stored_validation(tmp_path, SAFE) == StoredValidation(
        status="validated"
    )
    _legacy_output(tmp_path, "llama3.1_8b", "Two.")
    assert primary_stored_validation(tmp_path, SAFE) is None


def test_new_flags_record_the_validation_contract_version() -> None:
    from splatoon3_ai_coach.coach.coach import annotate_claim_flags
    from splatoon3_ai_coach.coach.llm_client import CoachingAssessment

    flags = annotate_claim_flags(CoachingAssessment(assessment="Text."))
    assert flags["validation_contract_version"] == VALIDATION_CONTRACT_VERSION
    assert flags["validation_status"] == "not_validated"


def test_prototype_index_validates_primary_links() -> None:
    output = PrototypeOutput(
        model="a", output_json="x.a.output.json", raw_txt="x.a.raw.txt",
        flags_json="x.a.flags.json", skipped_llm=False,
    )
    fields = PrototypeIndexEntry.model_fields
    base = {k: v for k, v in index_entry("x").items() if k in fields}
    with pytest.raises(ValidationError, match="not listed"):
        PrototypeIndexEntry(**base, primary_output_json="x.b.output.json")
    entry = PrototypeIndexEntry(
        **base, outputs=[output], primary_output_json=output.output_json
    )
    with pytest.raises(ValidationError, match="primary_model"):
        PrototypeIndex(primary_model="b", entries=[entry])


def _legacy_output(folder: Path, model: str, text: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / f"{SAFE}.{model}.output.json", {"assessment": text})


def test_legacy_single_output_is_unambiguous(tmp_path: Path) -> None:
    _legacy_output(tmp_path, "gpt-oss_20b", "Only one.")
    assert primary_assessment(tmp_path, SAFE).assessment == "Only one."


def test_legacy_multiple_outputs_require_regeneration(tmp_path: Path) -> None:
    _legacy_output(tmp_path, "gpt-oss_20b", "One.")
    _legacy_output(tmp_path, "llama3.1_8b", "Two.")
    with pytest.raises(ArtifactError, match="Re-run `s3-coach coach-prototype`"):
        primary_assessment(tmp_path, SAFE)
    assert {a.model for a in unit_assessments(tmp_path, SAFE)} == {
        "gpt-oss_20b",
        "llama3.1_8b",
    }


def test_legacy_list_prototype_index_is_read(tmp_path: Path) -> None:
    _legacy_output(tmp_path, "gpt-oss_20b", "One.")
    row = {k: v for k, v in index_entry(SAFE).items() if k != "safe_id"}
    write_json(tmp_path / COACHING_INDEX_FILENAME, [row])
    index = read_prototype_index(tmp_path)
    assert index.schema_version == LEGACY_SCHEMA_VERSION
    assert index.primary_model is None
    entry = index.by_safe_id(SAFE)
    assert entry.primary_output_json == f"{SAFE}.gpt-oss_20b.output.json"
    assert entry.outputs[0].skipped_llm is None


def test_reannotate_uses_recorded_outputs(tmp_path: Path) -> None:
    write_prototype_unit(
        tmp_path, SAFE, {"llama3.1:8b": "Text."}, primary_model="llama3.1:8b"
    )
    flags = tmp_path / f"{SAFE}.llama3.1_8b.flags.json"
    flags.unlink()
    result = CliRunner().invoke(app, ["coach-reannotate-flags", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert not flags.exists()
    assert (tmp_path / f"{SAFE}.llama3.1_8b.revalidation.json").is_file()
    assert f"{SAFE}.llama3.1_8b.output.json" in result.output


def test_reannotate_requires_a_prototype_index(tmp_path: Path) -> None:
    _legacy_output(tmp_path, "gpt-oss_20b", "One.")
    result = CliRunner().invoke(app, ["coach-reannotate-flags", str(tmp_path)])
    assert result.exit_code == 1
    assert "coach-prototype" in result.output
