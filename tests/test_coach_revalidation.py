"""`coach-reannotate-flags` writes revalidations and never touches original flags."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest
from coach_artifact_helpers import index_entry
from typer.testing import CliRunner

from splatoon3_ai_coach.cli import app
from splatoon3_ai_coach.coach.artifacts import (
    ArtifactError,
    PrototypeIndex,
    PrototypeIndexEntry,
    Revalidation,
    primary_stored_validation,
    read_revalidation,
    revalidation_file_name,
    unit_file_names,
    write_assessment_artifacts,
    write_json,
    write_prototype_index,
)
from splatoon3_ai_coach.coach.claim_validation import VALIDATION_CONTRACT_VERSION
from splatoon3_ai_coach.coach.llm_client import CoachingAssessment
from splatoon3_ai_coach.coach.revalidation import (
    RevalidationOutcome,
    apply_revalidations,
    plan_revalidations,
)

SAFE = "unit"
CAUSAL = (
    "The death at 72.5 s occurred without a prior map check. This lack of map "
    "awareness may have contributed to the splat."
)
CLEAN = "No map overlay was observed before the death."
VIEW = {
    "schema_version": 2,
    "unit": {"scenario_id": "death_episode:72.500"},
    "importance": {"active_factor_ids": []},
    "map": {
        "map_check_before_death": {"status": "not_observed"},
        "source_path": "primary_context.map",
    },
}
ORIGINAL = {
    "hit_count": 0,
    "validation_status": "validated",
    "validation_contract_version": 1,
}


def _unit(
    prototype: Path,
    texts: dict[str, str],
    flags: dict[str, object] | None = ORIGINAL,
    *,
    view: bool = True,
) -> None:
    """One unit with one output per model, its flags and (optionally) its view."""
    prototype.mkdir(parents=True, exist_ok=True)
    outputs = [
        write_assessment_artifacts(
            out_dir=prototype,
            safe_id=SAFE,
            model_label=model,
            assessment=CoachingAssessment(assessment=text),
            raw_text=text,
            flags=flags or {},
            skipped_llm=False,
        )
        for model, text in texts.items()
    ]
    if flags is None:
        for output in outputs:
            (prototype / output.flags_json).unlink()
    names = unit_file_names(SAFE)
    if view:
        write_json(prototype / names.llm_view, VIEW)
    fields = {
        k: v
        for k, v in index_entry(SAFE).items()
        if k in PrototypeIndexEntry.model_fields
    }
    entry = PrototypeIndexEntry(
        **fields,
        llm_view_json=names.llm_view if view else None,
        outputs=outputs,
        primary_output_json=outputs[0].output_json,
    )
    index = PrototypeIndex(primary_model=next(iter(texts)), entries=[entry])
    write_prototype_index(prototype, index)


def _run(prototype: Path, *extra: str):
    return CliRunner().invoke(app, ["coach-reannotate-flags", str(prototype), *extra])


def _files(prototype: Path, *, revalidations: bool = False) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(prototype.iterdir())
        if path.name.endswith(".revalidation.json") is revalidations
    }


def _revalidation_path(prototype: Path, model: str = "m") -> Path:
    return prototype / f"{SAFE}.{model}.revalidation.json"


@pytest.mark.parametrize(
    "flags",
    [ORIGINAL, {"hit_count": 0}, {"validation_status": "contains_unverified_wording"}],
    ids=["versioned", "legacy", "unversioned"],
)
def test_generation_flags_and_outputs_stay_byte_identical(
    tmp_path: Path, flags: dict[str, object]
) -> None:
    _unit(tmp_path, {"m": CAUSAL}, flags)
    before = _files(tmp_path)
    result = _run(tmp_path)
    assert result.exit_code == 0, result.output
    assert _files(tmp_path) == before
    assert _revalidation_path(tmp_path).is_file()


def test_original_stored_status_cannot_change(tmp_path: Path) -> None:
    _unit(tmp_path, {"m": CAUSAL})
    assert _run(tmp_path).exit_code == 0
    assert _run(tmp_path, "--replace-revalidation").exit_code == 0
    stored = primary_stored_validation(tmp_path, SAFE)
    assert stored is not None
    assert (stored.status.value, stored.contract_version) == ("validated", 1)
    revalidation = read_revalidation(tmp_path, f"{SAFE}.m.output.json")
    assert revalidation.validation_status.value == "blocked_unsupported_claim"


def test_revalidation_records_contract_hashes_and_details(tmp_path: Path) -> None:
    _unit(tmp_path, {"m": CAUSAL})
    assert _run(tmp_path).exit_code == 0
    raw = json.loads(_revalidation_path(tmp_path).read_text(encoding="utf-8"))
    assert set(raw) == set(Revalidation.model_fields)
    revalidation = Revalidation.model_validate(raw)
    assert revalidation.validation_contract_version == VALIDATION_CONTRACT_VERSION
    output = tmp_path / f"{SAFE}.m.output.json"
    view = tmp_path / unit_file_names(SAFE).llm_view
    assert revalidation.output_json == output.name
    assert revalidation.output_sha256 == hashlib.sha256(output.read_bytes()).hexdigest()
    assert revalidation.llm_view_json == view.name
    assert revalidation.llm_view_sha256 == hashlib.sha256(view.read_bytes()).hexdigest()
    assert datetime.fromisoformat(revalidation.revalidated_at).tzinfo is not None
    assert {"hit_count", "claim_rule_hits", "integrity_hits"} <= set(
        revalidation.details
    )
    assert not set(revalidation.details) & {
        "validation_status",
        "validation_contract_version",
    }


def test_revalidation_without_a_saved_view_has_no_view_identity(tmp_path: Path) -> None:
    _unit(tmp_path, {"m": CLEAN}, view=False)
    assert _run(tmp_path).exit_code == 0
    revalidation = read_revalidation(tmp_path, f"{SAFE}.m.output.json")
    assert (revalidation.llm_view_json, revalidation.llm_view_sha256) == (None, None)
    assert revalidation.validation_status.value == "not_validated"


def test_repeated_identical_revalidation_is_idempotent(tmp_path: Path) -> None:
    _unit(tmp_path, {"m": CAUSAL})
    assert _run(tmp_path).exit_code == 0
    first = _files(tmp_path, revalidations=True)
    plan = plan_revalidations(tmp_path, revalidated_at="2099-01-01T00:00:00+00:00")
    assert [i.outcome for i in plan.items] == [RevalidationOutcome.UNCHANGED]
    result = _run(tmp_path)
    assert result.exit_code == 0, result.output
    assert "unchanged" in result.output
    assert _files(tmp_path, revalidations=True) == first


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"validation_contract_version": 0}, "validation_contract_version"),
        ({"output_sha256": "0" * 64}, "output_sha256"),
        ({"llm_view_sha256": "0" * 64}, "llm_view_sha256"),
        ({"validation_status": "validated"}, "validation_status"),
        ({"details": {}}, "details"),
    ],
)
def test_differing_revalidation_is_never_silently_replaced(
    tmp_path: Path, change: dict[str, object], field: str
) -> None:
    _unit(tmp_path, {"m": CAUSAL})
    assert _run(tmp_path).exit_code == 0
    path = _revalidation_path(tmp_path)
    stale = {**json.loads(path.read_text(encoding="utf-8")), **change}
    path.write_text(json.dumps(stale), encoding="utf-8")
    stale_bytes, flags_before = path.read_bytes(), _files(tmp_path)
    result = _run(tmp_path)
    assert result.exit_code == 1
    assert f"differs: {field}" in result.output
    assert "Nothing written" in result.output
    assert path.read_bytes() == stale_bytes
    replaced = _run(tmp_path, "--replace-revalidation")
    assert replaced.exit_code == 0, replaced.output
    fresh = read_revalidation(tmp_path, f"{SAFE}.m.output.json")
    assert fresh.validation_contract_version == VALIDATION_CONTRACT_VERSION
    assert fresh.validation_status.value == "blocked_unsupported_claim"
    assert _files(tmp_path) == flags_before


def test_unreadable_revalidation_is_a_conflict(tmp_path: Path) -> None:
    _unit(tmp_path, {"m": CAUSAL})
    _revalidation_path(tmp_path).write_text("{not json", encoding="utf-8")
    result = _run(tmp_path)
    assert result.exit_code == 1
    assert "differs: unreadable" in result.output
    assert _revalidation_path(tmp_path).read_text(encoding="utf-8") == "{not json"
    with pytest.raises(ArtifactError, match="not a valid revalidation"):
        read_revalidation(tmp_path, f"{SAFE}.m.output.json")


def test_one_conflict_blocks_every_write(tmp_path: Path) -> None:
    _unit(tmp_path, {"a": CAUSAL, "b": CLEAN})
    _revalidation_path(tmp_path, "a").write_text("{}", encoding="utf-8")
    plan = plan_revalidations(tmp_path)
    assert {i.outcome for i in plan.items} == {
        RevalidationOutcome.CONFLICT,
        RevalidationOutcome.NEW,
    }
    with pytest.raises(ArtifactError, match="1 conflict"):
        apply_revalidations(tmp_path, plan)
    assert _run(tmp_path).exit_code == 1
    assert not _revalidation_path(tmp_path, "b").exists()


@pytest.mark.parametrize(
    "flags", [None, {"hit_count": 0}], ids=["no_flags_file", "legacy_flags"]
)
def test_legacy_output_gets_no_invented_original(
    tmp_path: Path, flags: dict[str, object] | None
) -> None:
    _unit(tmp_path, {"m": CLEAN}, flags)
    flags_path = tmp_path / f"{SAFE}.m.flags.json"
    assert _run(tmp_path).exit_code == 0
    assert flags_path.exists() is (flags is not None)
    assert primary_stored_validation(tmp_path, SAFE) is None
    revalidation = read_revalidation(tmp_path, f"{SAFE}.m.output.json")
    assert revalidation.validation_status.value == "validated"


def test_revalidation_name_is_a_sibling_of_the_output() -> None:
    assert revalidation_file_name("u.gpt-oss_20b.output.json") == (
        "u.gpt-oss_20b.revalidation.json"
    )
    for bad in ("u.gpt-oss_20b.flags.json", "../u.m.output.json"):
        with pytest.raises(ValueError):
            revalidation_file_name(bad)
