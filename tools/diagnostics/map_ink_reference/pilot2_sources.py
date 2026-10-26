"""Prospective, outcome-independent source selection for Amendment 9 pilot 2."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from map_ink_reference.constants import (
    BLIND_INPUT_SCHEMA,
    INTRO_LATCH_RULE,
    PILOT2_SELECTION,
    PILOT_AUTHORIZATION,
    RECORDS_DIR,
    SCOPE_PILOT2,
    SUPPLEMENTARY_BLIND_INPUT_MANIFEST,
    SUPPLEMENTARY_COUNT,
    SUPPLEMENTARY_DIR,
    SUPPLEMENTARY_POOL,
    SUPPLEMENTARY_POOL_SCHEMA,
    SUPPLEMENTARY_PREPARATION,
    SUPPLEMENTARY_PREPARATION_SCHEMA,
    SUPPLEMENTARY_SOURCE_MANIFEST,
)
from map_ink_reference.errors import MalformedInput, ProcedureOrderError
from map_ink_reference.io_utils import read_json, sha256_file
from map_ink_reference.pilot import (
    PilotSource,
    _bind,
    _eligibility,
    check_declared_source,
    corpus_exclusions,
    load_pilot_source_manifest,
    verify_source_video,
)
from map_ink_reference.pilot_pipeline import PilotScope, read_pilot_record, verify_groups
from map_ink_reference.seal import (
    chain_entry,
    changed_paths,
    protected_commits,
    require_sealed,
    strictly_after,
)
from map_ink_reference.stage_identity import (
    PILOT_TOP_KEYS,
    load_pilot_blind_input,
    require_pinned_provenance,
    step2a_paths,
    validate_entry,
)

_SUPPLEMENTARY_NAMES = (
    SUPPLEMENTARY_POOL,
    SUPPLEMENTARY_PREPARATION,
    SUPPLEMENTARY_SOURCE_MANIFEST,
    SUPPLEMENTARY_BLIND_INPUT_MANIFEST,
)
_POOL_KEYS = frozenset({"schema", "amendment5_commit", "declared_by", "candidates"})
_POOL_ENTRY_KEYS = frozenset(
    {
        "candidate_id",
        "video_path",
        "analysis_dir",
        "recorded_stage",
        "recorded_on",
        "authorization",
    }
)
_PREPARATION_KEYS = frozenset(
    {
        "schema",
        "pool_sha256",
        "code_commit",
        "config_sha256",
        "analyze_command",
        "python_version",
        "candidates",
    }
)
_PREPARATION_ENTRY_KEYS = frozenset(
    {
        "candidate_id",
        "status",
        "video_sha256",
        "video_identity",
        "vision_manifest_sha256",
        "manifest_video_identity",
        "attempts",
        "exclusion",
    }
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_TECHNICAL_EXCLUSIONS = frozenset({"T1", "T2", "T3"})
_ORIGINAL_POOL = "pilot_candidate_pool.json"


def _supplementary_paths(scope: PilotScope) -> tuple[Path, ...]:
    return tuple(
        scope.records / SUPPLEMENTARY_DIR / name for name in _SUPPLEMENTARY_NAMES
    )


def _require_exact_string_dict(
    value: Any, keys: frozenset[str], where: str
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise MalformedInput(f"{where} must have exactly the sealed keys")
    return value


def _validate_pool(path: Path) -> dict[str, Any]:
    payload = _require_exact_string_dict(read_json(path), _POOL_KEYS, "candidate pool")
    if payload["schema"] != SUPPLEMENTARY_POOL_SCHEMA:
        raise MalformedInput("supplementary candidate pool has the wrong schema")
    if payload["amendment5_commit"] != chain_entry("amendment5")[0]:
        raise MalformedInput("candidate pool names another Amendment 5 commit")
    if not isinstance(payload["declared_by"], str) or not payload["declared_by"]:
        raise MalformedInput("candidate pool declared_by must be non-empty")
    candidates = payload["candidates"]
    if not isinstance(candidates, list) or len(candidates) != SUPPLEMENTARY_COUNT:
        raise MalformedInput("supplementary pool must contain exactly five candidates")
    for entry in candidates:
        _require_exact_string_dict(entry, _POOL_ENTRY_KEYS, "candidate-pool entry")
        if any(not isinstance(entry[key], str) or not entry[key] for key in entry):
            raise MalformedInput("candidate-pool values must be non-empty strings")
        if entry["authorization"] != PILOT_AUTHORIZATION:
            raise MalformedInput("candidate-pool authorization is not the sealed literal")
        analysis = PurePosixPath(entry["analysis_dir"])
        video = PurePosixPath(entry["video_path"])
        if (
            analysis.is_absolute()
            or video.is_absolute()
            or len(analysis.parts) != 2
            or analysis.parts[0] != "analysis"
            or ".." in analysis.parts
            or ".." in video.parts
        ):
            raise MalformedInput("candidate paths violate the sealed path rules")
    ids = [entry["candidate_id"] for entry in candidates]
    if len(set(ids)) != SUPPLEMENTARY_COUNT:
        raise MalformedInput("supplementary candidate ids must be unique")
    for key in ("video_path", "analysis_dir"):
        values = [entry[key] for entry in candidates]
        if len(set(values)) != SUPPLEMENTARY_COUNT:
            raise MalformedInput(f"supplementary candidate {key} values must be unique")
    return payload


def _validate_preparation(
    path: Path, pool_path: Path, pool: Mapping[str, Any]
) -> dict[str, Any]:
    payload = _require_exact_string_dict(
        read_json(path), _PREPARATION_KEYS, "preparation record"
    )
    if payload["schema"] != SUPPLEMENTARY_PREPARATION_SCHEMA or payload[
        "pool_sha256"
    ] != sha256_file(pool_path):
        raise MalformedInput("supplementary preparation provenance differs")
    if (
        not isinstance(payload["code_commit"], str)
        or not _COMMIT.fullmatch(payload["code_commit"])
        or not isinstance(payload["config_sha256"], str)
        or not _HEX64.fullmatch(payload["config_sha256"])
        or payload["analyze_command"]
        != "s3-coach analyze <video_path> --out <analysis_dir>"
        or not isinstance(payload["python_version"], str)
        or not payload["python_version"]
    ):
        raise MalformedInput("supplementary preparation has invalid fixed provenance")
    entries = payload["candidates"]
    if not isinstance(entries, list) or len(entries) != SUPPLEMENTARY_COUNT:
        raise MalformedInput("preparation must cover exactly five candidates")
    for entry in entries:
        _require_exact_string_dict(entry, _PREPARATION_ENTRY_KEYS, "preparation entry")
        if entry["status"] not in {"prepared", "excluded"}:
            raise MalformedInput("preparation status must be prepared or excluded")
        if (entry["status"] == "prepared") != (entry["exclusion"] is None):
            raise MalformedInput("preparation exclusion is inconsistent with status")
        attempts = entry["attempts"]
        if not isinstance(attempts, list) or any(
            not isinstance(attempt, dict) for attempt in attempts
        ):
            raise MalformedInput("candidate attempts must be a list of records")
        if entry["status"] == "prepared":
            successful = [
                attempt for attempt in attempts if attempt.get("exit_status") == 0
            ]
            if (
                len(successful) != 1
                or not attempts
                or attempts[-1].get("exit_status") != 0
            ):
                raise MalformedInput("prepared candidate must have one final success")
            for key in (
                "video_sha256",
                "video_identity",
                "vision_manifest_sha256",
                "manifest_video_identity",
            ):
                if not isinstance(entry[key], str) or not _HEX64.fullmatch(entry[key]):
                    raise MalformedInput(f"prepared candidate has invalid {key}")
            if entry["video_identity"] != entry["manifest_video_identity"]:
                raise MalformedInput("prepared candidate has conflicting video identity")
        else:
            exclusion = entry["exclusion"]
            if (
                not isinstance(exclusion, dict)
                or exclusion.get("condition") not in _TECHNICAL_EXCLUSIONS
            ):
                raise MalformedInput("only T1-T3 may exclude a supplementary candidate")
    pool_ids = [entry["candidate_id"] for entry in pool["candidates"]]
    if [entry["candidate_id"] for entry in entries] != pool_ids:
        raise MalformedInput("preparation must preserve the complete pool order")
    return payload


def _load_blind_input(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    if not isinstance(payload, dict) or set(payload) != PILOT_TOP_KEYS:
        raise MalformedInput("supplementary blind input has the wrong keys")
    if (
        payload.get("schema") != BLIND_INPUT_SCHEMA
        or payload.get("scope") != SCOPE_PILOT2
        or payload.get("rule") != INTRO_LATCH_RULE
    ):
        raise MalformedInput("supplementary blind input has wrong fixed literals")
    entries = payload.get("recordings")
    if not isinstance(entries, list) or not entries:
        raise MalformedInput("supplementary blind input has no recordings")
    validated = [validate_entry(entry) for entry in entries]
    if len({entry["recording"] for entry in validated}) != len(validated):
        raise MalformedInput("duplicate supplementary blind-input recording")
    require_pinned_provenance(payload)
    return validated


def _sealed_supplementary(
    scope: PilotScope,
) -> tuple[list[PilotSource], list[dict[str, Any]], dict[str, str]] | None:
    paths = _supplementary_paths(scope)
    if not any(path.exists() for path in paths):
        return None
    if not all(path.exists() for path in paths):
        raise ProcedureOrderError("supplementary records are incomplete")
    commits = [require_sealed(scope.repo, path) for path in paths]
    repairs = protected_commits(scope.repo, chain_entry("amendment7")[0])
    if len(repairs) != 3 or not strictly_after(scope.repo, repairs[2], commits[0]):
        raise ProcedureOrderError("supplementary records must follow repair 9")
    for previous, commit in zip(commits, commits[1:], strict=True):
        if not strictly_after(scope.repo, previous, commit):
            raise ProcedureOrderError("supplementary records are out of order")
    for path, commit in zip(paths, commits, strict=True):
        if set(changed_paths(scope.repo, commit)) != {scope.rel(path)}:
            raise ProcedureOrderError("each supplementary record must be sealed alone")
    pool = _validate_pool(paths[0])
    preparation = _validate_preparation(paths[1], paths[0], pool)
    sources = load_pilot_source_manifest(paths[2])
    entries = _load_blind_input(paths[3])
    blind_payload = read_json(paths[3])
    if blind_payload["pilot_source_manifest_sha256"] != sha256_file(paths[2]):
        raise MalformedInput("supplementary blind input names another source manifest")
    prepared = [row for row in preparation["candidates"] if row["status"] == "prepared"]
    by_candidate = {row["candidate_id"]: row for row in prepared}
    pool_by_path = {row["video_path"]: row for row in pool["candidates"]}
    if len(sources) != len(prepared):
        raise MalformedInput("source manifest must declare every prepared candidate")
    original_pool_path = scope.repo / RECORDS_DIR / _ORIGINAL_POOL
    require_sealed(scope.repo, original_pool_path)
    original_pool = _validate_pool(original_pool_path)
    original_paths = {
        value
        for entry in original_pool["candidates"]
        for value in (entry["video_path"], entry["analysis_dir"])
    }
    for candidate in pool["candidates"]:
        if (
            candidate["video_path"] in original_paths
            or candidate["analysis_dir"] in original_paths
        ):
            raise MalformedInput("supplementary pool repeats an original candidate")
        prohibited = ("22-20-42", "18-25-16", "2026-10-24")
        if any(item in candidate["video_path"] for item in prohibited):
            raise MalformedInput("supplementary pool contains expressly prohibited media")
    original_sources = load_pilot_source_manifest(
        step2a_paths(scope.repo / RECORDS_DIR)[0]
    )
    original_ids = {source.video_identity for source in original_sources}
    original_shas = {source.video_sha256 for source in original_sources}
    for source in sources:
        candidate = pool_by_path.get(source.video_path)
        row = by_candidate.get(candidate["candidate_id"] if candidate else "")
        if (
            candidate is None
            or row is None
            or source.analysis_dir != candidate["analysis_dir"]
            or source.video_sha256 != row["video_sha256"]
            or source.video_identity != row["video_identity"]
        ):
            raise MalformedInput("supplementary source differs from preparation")
        if source.video_identity in original_ids or source.video_sha256 in original_shas:
            raise MalformedInput("supplementary source repeats original pilot material")
    blind_by_recording = _bind(sources, entries)
    for source in sources:
        candidate = pool_by_path[source.video_path]
        row = by_candidate[candidate["candidate_id"]]
        if (
            blind_by_recording[source.recording]["vision_manifest_sha256"]
            != row["vision_manifest_sha256"]
        ):
            raise MalformedInput("supplementary blind input differs from preparation")
    digests = {
        path.relative_to(scope.records).as_posix(): sha256_file(path) for path in paths
    }
    return sources, entries, digests


def _pilot1_material(scope: PilotScope) -> tuple[list[PilotSource], list[dict], dict]:
    pilot1 = PilotScope.from_repo(scope.repo)
    verify_groups(pilot1, 1)
    selection = read_pilot_record(pilot1.records / PILOT2_SELECTION)
    if set(selection).issuperset({"chosen", "declared"}) is False:
        raise MalformedInput("pilot-1 G1 lacks chosen or declared")
    source_path = step2a_paths(scope.repo / RECORDS_DIR)[0]
    sources = load_pilot_source_manifest(source_path)
    blind = load_pilot_blind_input(scope.repo, scope.repo / RECORDS_DIR)["recordings"]
    pilot1 = {
        "chosen": selection["chosen"],
        "declared": selection["declared"],
    }
    return sources, blind, pilot1


def source_bindings(
    scope: PilotScope, chosen: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Bind selected pilot-2 recordings to sealed source and blind records."""
    sources, entries, _ = _pilot1_material(scope)
    supplementary = _sealed_supplementary(scope)
    if supplementary is not None:
        extra_sources, extra_entries, _ = supplementary
        sources += extra_sources
        entries += extra_entries
    by_source = {source.recording: source for source in sources}
    by_entry = {entry["recording"]: entry for entry in entries}
    if any(rid not in by_source or rid not in by_entry for rid in chosen):
        raise MalformedInput("pilot-2 selection is not bound to declared material")
    return {rid: {**by_entry[rid], "source": by_source[rid]} for rid in chosen}


def select_pilot2(scope: PilotScope) -> dict[str, Any]:
    """Apply the frozen fresh-recording and deterministic selection rule."""
    sources, entries, pilot1 = _pilot1_material(scope)
    by_entry = _bind(sources, entries)
    by_row = {row["recording"]: row for row in pilot1["declared"]}
    by_source = {source.recording: source for source in sources}
    chosen_sources = [by_source[rid] for rid in pilot1["chosen"]]
    excluded_ids = {source.video_identity for source in chosen_sources}
    excluded_shas = {source.video_sha256 for source in chosen_sources}
    fresh = [
        source
        for source in sources
        if source.video_identity not in excluded_ids
        and source.video_sha256 not in excluded_shas
    ]
    exclusions = corpus_exclusions(scope.repo)
    rows: list[dict[str, Any]] = []
    for source in fresh:
        check_declared_source(scope.repo, source, exclusions)
        verify_source_video(scope.repo, source)
        row = _eligibility(scope.repo, by_entry[source.recording])
        if row != by_row.get(source.recording):
            raise MalformedInput("recomputed eligibility differs from sealed pilot-1 G1")
        rows.append(row)
    eligible = [row for row in rows if row["eligible"]]
    supplementary = _sealed_supplementary(scope)
    supplementary_digests: dict[str, str] = {}
    if supplementary is not None and len(eligible) >= 2:
        raise ProcedureOrderError(
            "supplementary fallback exists without a base shortfall"
        )
    if len(eligible) < 2:
        if supplementary is None:
            raise ProcedureOrderError("fewer than two base candidates are eligible")
        extra_sources, extra_entries, supplementary_digests = supplementary
        extra_by_entry = _bind(extra_sources, extra_entries)
        for source in extra_sources:
            if (
                source.video_identity in excluded_ids
                or source.video_sha256 in excluded_shas
            ):
                raise MalformedInput("supplementary source is not fresh")
            check_declared_source(scope.repo, source, exclusions)
            verify_source_video(scope.repo, source)
            rows.append(_eligibility(scope.repo, extra_by_entry[source.recording]))
        eligible = [row for row in rows if row["eligible"]]
        if len(eligible) < 2:
            raise ProcedureOrderError("supplementary union has fewer than two candidates")
    selected = sorted(eligible, key=lambda row: row["video_identity"])[:2]
    return {
        "scope": SCOPE_PILOT2,
        "declared": rows,
        "chosen": [row["recording"] for row in selected],
        "supplementary_records": supplementary_digests,
    }
