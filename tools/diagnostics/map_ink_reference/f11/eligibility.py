"""Arm R source checks and the Amendment 12 Step 4 record producer.

Prohibited-source enforcement, each category to its mechanism:

- seven corpus recordings: ``corpus_exclusions`` video identities and the pinned
  corpus video SHA-256 values;
- Amendment 5 candidate-pool entries: ``analysis_dir`` and ``video_path`` from
  the sealed pool record;
- Amendment 9 pilot sources, prepared or excluded: identity and path fields
  from the sealed preparation, source-manifest, blind-input and selection
  records, including supplementary records when those files exist;
- pair-009: path token ``pair-009`` and the preregistration-published manifest
  digest, without opening the held-out manifest;
- ``22-20-42``, ``18-25-16`` and the 2026-10-24 recording: those path tokens,
  with the two coverage recordings also covered by the corpus identities;
- capture packages and platform-linked jobs: ``capture_package.json``. The
  capture contract treats platform acquisitions as capture packages; there is
  no separate identity list;
- ``_``-prefixed analysis directories, ``alignment-v3-heldout`` and
  ``review_validation``: the sealed directory rule;
- v3 held-out and fresh v4 held-out status with no mechanical marker: the
  sealed Step 3 authorization literal. Held-out records are not opened.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from map_ink_reference.constants import (
    AMENDMENT4_COMMIT,
    BLIND_INPUT_SCHEMA,
    INTRO_LATCH_RULE,
)
from map_ink_reference.errors import MalformedInput, PinMismatch, SealViolation
from map_ink_reference.f11.guard import tooling_commit
from map_ink_reference.intro_latch import (
    build_payload,
    derive_latch,
    intro_deadline_seconds,
)
from map_ink_reference.io_utils import canonical_json, read_json, sha256_file, write_json
from map_ink_reference.pilot import _in_capture_package, corpus_exclusions
from map_ink_reference.pins import (
    GEOMETRY_DIR,
    STAGE_GEOMETRY_PINS,
    require_pinned_geometry,
    verify_pin,
)
from map_ink_reference.population import eligible_intervals, reduce_manifest
from map_ink_reference.stage_identity import (
    require_pinned_provenance,
    validate_blind_input,
)
from splatoon3_ai_coach.media.video_identity import video_identity

AUTHORIZATION = (
    "development-independent media for Amendment 11 Arm R; "
    "not corpus, pilot or held-out media"
)
INDEPENDENCE = (
    "This recording was not used to develop, debug, tune, select, visually assess, "
    "or make design decisions about team-colour calibration, A1, or map ink."
)
SOURCE_SCHEMA = "amendment11-arm-r-sources/v1"
ELIGIBILITY_SCHEMA = "amendment11-arm-r-eligibility/v1"
SCOPE_ARM_R = "f11_arm_r"
SOURCE_MANIFEST = "analysis/review_validation/alignment/v4/amendment11/arm_r_sources.json"
BLIND_INPUT = "analysis/review_validation/alignment/v4/amendment11/blind_input_arm_r.json"
ELIGIBILITY_RECORD = (
    "analysis/review_validation/alignment/v4/amendment11/eligibility.json"
)
_FIELDS = (
    "analysis_dir",
    "video_path",
    "video_sha256",
    "video_identity",
    "vision_manifest_sha256",
    "language",
    "acquisition_session",
    "authorization",
    "development_independence",
    "declared_by",
)
_PROHIBITED_DIRS = frozenset({"alignment-v3-heldout", "review_validation"})
_PROHIBITION_RECORDS = (
    "analysis/review_validation/alignment/v4/amendment3/pilot_candidate_pool.json",
    "analysis/review_validation/alignment/v4/amendment3/pilot_source_preparation.json",
    "analysis/review_validation/alignment/v4/amendment3/pilot_source_manifest.json",
    "analysis/review_validation/alignment/v4/amendment3/pilot_blind_input_manifest.json",
    "analysis/review_validation/alignment/v4/amendment3/pilot/pilot_selection.json",
    "analysis/review_validation/alignment/v4/amendment3/pilot2/pilot_selection.json",
)
_SUPPLEMENTARY_DIR = (
    "analysis/review_validation/alignment/v4/amendment3/pilot2/supplementary"
)
_PATH_TOKENS = (
    "22-20-42",
    "18-25-16",
    "2026-10-24",
    "pair-009",
    "alignment-v3-heldout",
    "review_validation",
)
PAIR009_MANIFEST_SHA256 = (
    "7261d2c97b862772ca7a66337926f733d314dc9bd2c547077639c27904c0d82e"
)
_IDENTITY_KEYS = {
    "video_sha256": "video_sha256",
    "video_identity": "video_identity",
    "manifest_video_identity": "video_identity",
    "vision_manifest_sha256": "vision_manifest_sha256",
    "analysis_dir": "analysis_dir",
    "video_path": "video_path",
}
_SOURCE_KEYS = frozenset({"schema", "entries"})
_TOP_KEYS = frozenset({"schema", "arm_r_source_manifest", "blind_input", "entries"})
_SOURCE_REF_KEYS = frozenset({"path", "sha256", "schema"})
_BLIND_REF_KEYS = frozenset({"path", "sha256", "schema", "rule", "scope"})
_ENTRY_KEYS = frozenset(
    {
        "analysis_dir",
        "video_sha256",
        "video_identity",
        "vision_manifest_sha256",
        "integrity",
        "source_pts",
        "intro_latch",
        "geometry",
        "eligible_interval_count",
        "eligible",
        "ineligibility_reasons",
    }
)
_INTEGRITY_KEYS = (
    "declaration",
    "source_file",
    "vision_manifest",
    "language",
    "prohibited_source",
)
_SOURCE_PTS_KEYS = frozenset({"frame_count", "usable_source_pts_count", "complete"})
_INTRO_KEYS = frozenset(
    {
        "blind_input_recording",
        "stage_id",
        "battle_mode_id",
        "stage_latched_at",
        "battle_mode_latched_at",
        "intro_reading_count",
    }
)
_GEOMETRY_KEYS = frozenset({"status", "stage_id", "battle_mode_id", "files"})
_GEOMETRY_STATUS = frozenset({"resolved", "missing", "not_applicable_no_stage"})
_FILE_KEYS = frozenset({"role", "path", "sha256"})
_ROLE_ORDER = ("default", "battle_mode", "stage_mask")
_REASONS = (
    "missing_source_pts",
    "stage_identity_not_latched",
    "required_geometry_missing",
    "fewer_than_8_eligible_intervals",
)
_TIMING_FAILURE = "lacks source_pts/time base"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def require_declaration(entry: Mapping[str, str]) -> None:
    """Accept one operator entry, or stop. Missing independence excludes it."""
    missing = [field for field in _FIELDS if not entry.get(field)]
    if missing:
        raise MalformedInput(f"Arm R entry is missing {', '.join(missing)}")
    if entry["authorization"] != AUTHORIZATION:
        raise MalformedInput("Arm R authorization literal does not match")
    if entry["development_independence"] != INDEPENDENCE:
        raise MalformedInput("development independence was not declared")
    if entry["language"] not in {"en", "ja"}:
        raise MalformedInput("analysis language must be en or ja")
    _require_analysis_dir(entry["analysis_dir"])


def require_hash_match(
    *,
    expected_sha256: str,
    actual_sha256: str,
    expected_identity: str,
    actual_identity: str,
    manifest_identity: str,
    expected_manifest_sha256: str,
    actual_manifest_sha256: str,
) -> None:
    """Stop unless the declared video and manifest hashes match."""
    if actual_sha256 != expected_sha256 or actual_identity != expected_identity:
        raise MalformedInput("declared video hash or identity does not match")
    if manifest_identity != expected_identity:
        raise MalformedInput("manifest video_identity does not match the declaration")
    if actual_manifest_sha256 != expected_manifest_sha256:
        raise MalformedInput("vision manifest hash does not match the declaration")


def load_prohibited_sources(repo: Path) -> dict[str, frozenset[str]]:
    """Mechanically sealed identities and paths. Held-out records stay closed."""
    found = {bucket: set() for bucket in set(_IDENTITY_KEYS.values())}
    for rel in _PROHIBITION_RECORDS:
        _collect_identities(read_json(repo / rel), found)
    supplementary = repo / _SUPPLEMENTARY_DIR
    if supplementary.is_dir():
        for path in sorted(supplementary.glob("*.json")):
            _collect_identities(read_json(path), found)
    identities, shas = corpus_exclusions(repo)
    found["video_identity"].update(identities)
    found["video_sha256"].update(shas)
    return {key: frozenset(values) for key, values in found.items()}


def require_permitted_source(
    material_root: Path,
    entry: Mapping[str, str],
    prohibited: Mapping[str, frozenset[str]],
) -> None:
    """Stop when an entry matches a mechanically sealed prohibited source."""
    analysis_dir = entry["analysis_dir"]
    video = Path(entry["video_path"])
    if not video.is_absolute():
        video = material_root / video
    if _in_capture_package(material_root / analysis_dir, video):
        raise MalformedInput("prohibited source")
    if _matches_prohibited(entry, prohibited) or _matches_token(entry):
        raise MalformedInput("prohibited source")


def produce_step4(
    repo: Path,
    *,
    source_manifest: Path | None = None,
    material_root: Path | None = None,
    geometry_root: Path | None = None,
    blind_output: Path | None = None,
    eligibility_output: Path | None = None,
) -> Path:
    """Derive and validate both Step 4 artifacts, then write them.

    A failure before both payloads validate writes neither file. An existing
    artifact is refused. An I/O failure after writing begins is not rolled back.
    """
    source_path = repo / SOURCE_MANIFEST if source_manifest is None else source_manifest
    material = repo if material_root is None else material_root
    geometry = repo if geometry_root is None else geometry_root
    blind_path = repo / BLIND_INPUT if blind_output is None else blind_output
    eligibility_path = (
        repo / ELIGIBILITY_RECORD if eligibility_output is None else eligibility_output
    )
    _refuse_existing(blind_path, eligibility_path)
    blind, eligibility = _derive_step4(repo, source_path, material, geometry, blind_path)
    _refuse_existing(blind_path, eligibility_path)
    write_json(blind_path, blind)
    write_json(eligibility_path, eligibility)
    return eligibility_path


def validate_step4_records(
    eligibility: Mapping[str, Any],
    blind: Mapping[str, Any],
    declarations: Sequence[Mapping[str, str]],
    source_sha256: str,
) -> None:
    """Require the sealed two-artifact representation and its cross-checks."""
    recordings = validate_blind_input(dict(blind), SCOPE_ARM_R)
    _validate_envelope(eligibility, blind, source_sha256)
    entries = eligibility["entries"]
    if not isinstance(entries, list) or len(entries) != len(declarations):
        raise MalformedInput("eligibility entries do not match the source manifest")
    expected = [PurePosixPath(item["analysis_dir"]).name for item in declarations]
    if [item["recording"] for item in recordings] != expected:
        raise MalformedInput("blind-input recordings are not in declaration order")
    for declaration, recording, entry in zip(
        declarations, recordings, entries, strict=True
    ):
        _validate_determination(declaration, recording, entry)


def _require_analysis_dir(analysis_dir: str) -> None:
    parts = analysis_dir.split("/")
    if len(parts) != 2 or parts[0] != "analysis" or not parts[1]:
        raise MalformedInput("analysis_dir must be directly under analysis/")
    name = parts[1]
    if name.startswith("_") or name in _PROHIBITED_DIRS:
        raise MalformedInput(f"{analysis_dir} is not an Arm R source directory")


def _collect_identities(payload: Any, found: dict[str, set[str]]) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            bucket = _IDENTITY_KEYS.get(key)
            if bucket is not None and isinstance(value, str) and value:
                found[bucket].add(value)
            else:
                _collect_identities(value, found)
        return
    if isinstance(payload, list):
        for item in payload:
            _collect_identities(item, found)


def _matches_prohibited(
    entry: Mapping[str, str], prohibited: Mapping[str, frozenset[str]]
) -> bool:
    checks = (
        ("video_sha256", entry["video_sha256"]),
        ("video_identity", entry["video_identity"]),
        ("vision_manifest_sha256", entry["vision_manifest_sha256"]),
        ("analysis_dir", entry["analysis_dir"]),
        ("video_path", entry["video_path"]),
    )
    if any(value in prohibited[key] for key, value in checks):
        return True
    return entry["vision_manifest_sha256"] == PAIR009_MANIFEST_SHA256


def _matches_token(entry: Mapping[str, str]) -> bool:
    haystack = f"{entry['analysis_dir']}\n{entry['video_path']}"
    if any(token in haystack for token in _PATH_TOKENS):
        return True
    name = entry["analysis_dir"].split("/")[1]
    return name.startswith("_") or name in _PROHIBITED_DIRS


def _derive_step4(
    repo: Path,
    source_path: Path,
    material: Path,
    geometry: Path,
    blind_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    declarations, source_sha = _load_declarations(source_path)
    prohibited = load_prohibited_sources(repo)
    deadline = intro_deadline_seconds(repo)
    blind_entries: list[dict[str, Any]] = []
    determinations: list[dict[str, Any]] = []
    for entry in declarations:
        blind_entry, determination = _determine_entry(
            material, geometry, entry, prohibited, deadline
        )
        blind_entries.append(blind_entry)
        determinations.append(determination)
    tooling = tooling_commit(repo)
    if tooling is None:
        raise SealViolation("F11 tooling commit is not sealed")
    blind = build_payload(
        repo,
        SCOPE_ARM_R,
        blind_entries,
        tool_commit=tooling,
        amendment4_commit=AMENDMENT4_COMMIT,
    )
    require_pinned_provenance(blind)
    eligibility = _eligibility_payload(
        repo, source_path, source_sha, blind, blind_path, determinations
    )
    validate_step4_records(eligibility, blind, declarations, source_sha)
    return blind, eligibility


def _load_declarations(path: Path) -> tuple[list[dict[str, str]], str]:
    payload = read_json(path)
    if not isinstance(payload, dict) or set(payload) != _SOURCE_KEYS:
        raise MalformedInput("Arm R source manifest must have exactly schema and entries")
    if payload["schema"] != SOURCE_SCHEMA:
        raise MalformedInput("Arm R source manifest schema does not match")
    entries = payload["entries"]
    if not isinstance(entries, list) or not entries:
        raise MalformedInput("Arm R source manifest has no entries")
    declared: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != set(_FIELDS):
            raise MalformedInput("Arm R entry must have exactly the sealed fields")
        require_declaration(entry)
        declared.append(entry)
    return declared, sha256_file(path)


def _determine_entry(
    material: Path,
    geometry_root: Path,
    entry: Mapping[str, str],
    prohibited: Mapping[str, frozenset[str]],
    deadline: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest, manifest_sha = _checked_manifest(material, entry, prohibited)
    source_pts = _source_pts(manifest)
    latch = derive_latch(manifest, deadline)
    geometry = resolve_geometry(geometry_root, latch["stage_id"], latch["battle_mode_id"])
    count = _interval_count(manifest)
    reasons = _reasons(source_pts, latch, geometry, count)
    recording = PurePosixPath(entry["analysis_dir"]).name
    blind_entry = {
        "recording": recording,
        "video_identity": entry["video_identity"],
        "video_sha256": entry["video_sha256"],
        "vision_manifest_path": f"{entry['analysis_dir']}/vision_manifest.json",
        "vision_manifest_sha256": manifest_sha,
        **latch,
    }
    determination = {
        "analysis_dir": entry["analysis_dir"],
        "video_sha256": entry["video_sha256"],
        "video_identity": entry["video_identity"],
        "vision_manifest_sha256": entry["vision_manifest_sha256"],
        "integrity": {key: "pass" for key in _INTEGRITY_KEYS},
        "source_pts": source_pts,
        "intro_latch": _intro_latch(recording, latch),
        "geometry": geometry,
        "eligible_interval_count": count,
        "eligible": _is_eligible(source_pts, latch, geometry, count, reasons),
        "ineligibility_reasons": reasons,
    }
    return blind_entry, determination


def _checked_manifest(
    material: Path,
    entry: Mapping[str, str],
    prohibited: Mapping[str, frozenset[str]],
) -> tuple[dict[str, Any], str]:
    video = Path(entry["video_path"])
    if not video.is_absolute():
        video = material / video
    manifest_path = material / entry["analysis_dir"] / "vision_manifest.json"
    if not video.is_file():
        raise MalformedInput("declared source is missing")
    if not manifest_path.is_file():
        raise MalformedInput("declared vision manifest is missing")
    manifest_sha = sha256_file(manifest_path)
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or not isinstance(
        manifest.get("video_identity"), str
    ):
        raise MalformedInput("vision manifest has no video_identity")
    require_hash_match(
        expected_sha256=entry["video_sha256"],
        actual_sha256=sha256_file(video),
        expected_identity=entry["video_identity"],
        actual_identity=video_identity(video),
        manifest_identity=manifest["video_identity"],
        expected_manifest_sha256=entry["vision_manifest_sha256"],
        actual_manifest_sha256=manifest_sha,
    )
    if _manifest_language(manifest) != entry["language"]:
        raise MalformedInput("manifest language does not match the declaration")
    require_permitted_source(material, entry, prohibited)
    return manifest, manifest_sha


def _manifest_language(manifest: Mapping[str, Any]) -> str:
    analysis = manifest.get("analysis")
    if not isinstance(analysis, dict) or not isinstance(analysis.get("language"), str):
        raise MalformedInput("vision manifest has no analysis language")
    return str(analysis["language"])


def _source_pts(manifest: Mapping[str, Any]) -> dict[str, Any]:
    frames = manifest.get("frame_results")
    if not isinstance(frames, list):
        raise MalformedInput("vision manifest has no frame_results list")
    usable = sum(1 for frame in frames if _usable_source_pts(frame))
    return {
        "frame_count": len(frames),
        "usable_source_pts_count": usable,
        "complete": usable == len(frames),
    }


def _usable_source_pts(frame: Any) -> bool:
    if not isinstance(frame, dict):
        return False
    pts = frame.get("source_pts")
    return isinstance(pts, int) and not isinstance(pts, bool)


def _interval_count(manifest: Mapping[str, Any]) -> int | None:
    """Return the frozen interval count, or null for the sealed timing gap.

    Only ``MalformedInput`` raised by exact-time adjacency for a missing
    ``source_pts`` or frozen time base becomes null. Every other manifest,
    time-base, integrity or tool failure propagates.
    """
    view = reduce_manifest(manifest)
    try:
        return len(eligible_intervals(view))
    except MalformedInput as exc:
        if _TIMING_FAILURE not in str(exc):
            raise
        return None


def resolve_geometry(
    repo: Path, stage_id: str | None, battle_mode_id: str | None
) -> dict[str, Any]:
    """Geometry status for one latch. A pin mismatch propagates."""
    if stage_id is None:
        return _geometry("not_applicable_no_stage", None, battle_mode_id, [])
    default_rel = f"{GEOMETRY_DIR}/{stage_id}/default.yaml"
    if _required_geometry_missing(repo, stage_id, battle_mode_id, default_rel):
        missing = [{"role": "default", "path": default_rel, "sha256": None}]
        return _geometry("missing", stage_id, battle_mode_id, missing)
    files = [_pinned_file(repo, "default", default_rel)]
    files.extend(_selected_geometry(repo, stage_id, battle_mode_id))
    return _geometry("resolved", stage_id, battle_mode_id, files)


def _geometry(
    status: str,
    stage_id: str | None,
    battle_mode_id: str | None,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": status,
        "stage_id": stage_id,
        "battle_mode_id": battle_mode_id,
        "files": files,
    }


def _required_geometry_missing(
    repo: Path, stage_id: str, battle_mode_id: str | None, default_rel: str
) -> bool:
    try:
        require_pinned_geometry(repo, stage_id, battle_mode_id)
    except PinMismatch as exc:
        if "no pinned geometry file" in str(exc) and not (repo / default_rel).is_file():
            return True
        raise
    return not (repo / default_rel).is_file()


def _selected_geometry(
    repo: Path, stage_id: str, battle_mode_id: str | None
) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    if battle_mode_id is not None:
        rel = f"{GEOMETRY_DIR}/{stage_id}/{battle_mode_id}.yaml"
        if (repo / rel).is_file():
            files.append(_pinned_file(repo, "battle_mode", rel))
    mask = f"{GEOMETRY_DIR}/{stage_id}/stage_mask.yaml"
    if (repo / mask).is_file():
        files.append(_pinned_file(repo, "stage_mask", mask))
    return files


def _pinned_file(repo: Path, role: str, rel: str) -> dict[str, Any]:
    digest = verify_pin(repo / rel, STAGE_GEOMETRY_PINS[rel])
    return {"role": role, "path": rel, "sha256": digest}


def _intro_latch(recording: str, latch: Mapping[str, Any]) -> dict[str, Any]:
    return {"blind_input_recording": recording, **latch}


def _reasons(
    source_pts: Mapping[str, Any],
    latch: Mapping[str, Any],
    geometry: Mapping[str, Any],
    count: int | None,
) -> list[str]:
    reasons: list[str] = []
    if source_pts["complete"] is not True:
        reasons.append("missing_source_pts")
    if latch["stage_id"] is None:
        reasons.append("stage_identity_not_latched")
    if geometry["status"] == "missing":
        reasons.append("required_geometry_missing")
    if isinstance(count, int) and not isinstance(count, bool) and count < 8:
        reasons.append("fewer_than_8_eligible_intervals")
    return reasons


def _is_eligible(
    source_pts: Mapping[str, Any],
    latch: Mapping[str, Any],
    geometry: Mapping[str, Any],
    count: int | None,
    reasons: Sequence[str],
) -> bool:
    return (
        source_pts["complete"] is True
        and latch["stage_id"] is not None
        and geometry["status"] == "resolved"
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 8
        and not reasons
    )


def _eligibility_payload(
    repo: Path,
    source_path: Path,
    source_sha: str,
    blind: Mapping[str, Any],
    blind_path: Path,
    determinations: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": ELIGIBILITY_SCHEMA,
        "arm_r_source_manifest": {
            "path": _recorded_path(repo, source_path),
            "sha256": source_sha,
            "schema": SOURCE_SCHEMA,
        },
        "blind_input": {
            "path": _recorded_path(repo, blind_path),
            "sha256": _payload_sha256(blind),
            "schema": BLIND_INPUT_SCHEMA,
            "rule": INTRO_LATCH_RULE,
            "scope": SCOPE_ARM_R,
        },
        "entries": determinations,
    }


def _recorded_path(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _payload_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def _refuse_existing(blind_path: Path, eligibility_path: Path) -> None:
    if blind_path.exists() or eligibility_path.exists():
        raise SealViolation("Arm R Step 4 artifact already exists")


def _validate_envelope(
    eligibility: Mapping[str, Any], blind: Mapping[str, Any], source_sha256: str
) -> None:
    if set(eligibility) != _TOP_KEYS or eligibility["schema"] != ELIGIBILITY_SCHEMA:
        raise MalformedInput("eligibility record must have exactly the sealed keys")
    source = eligibility["arm_r_source_manifest"]
    blind_ref = eligibility["blind_input"]
    if not isinstance(source, dict) or set(source) != _SOURCE_REF_KEYS:
        raise MalformedInput("arm_r_source_manifest must have exactly the sealed keys")
    if not isinstance(blind_ref, dict) or set(blind_ref) != _BLIND_REF_KEYS:
        raise MalformedInput("blind_input reference must have exactly the sealed keys")
    if source["schema"] != SOURCE_SCHEMA or source["sha256"] != source_sha256:
        raise MalformedInput("arm_r_source_manifest reference does not match")
    if not isinstance(source["path"], str) or not source["path"]:
        raise MalformedInput("arm_r_source_manifest path is missing")
    if (
        blind_ref["schema"] != BLIND_INPUT_SCHEMA
        or blind_ref["rule"] != INTRO_LATCH_RULE
        or blind_ref["scope"] != SCOPE_ARM_R
        or blind_ref["sha256"] != _payload_sha256(blind)
    ):
        raise MalformedInput("blind_input reference does not match the payload")
    if not isinstance(blind_ref["path"], str) or not blind_ref["path"]:
        raise MalformedInput("blind_input path is missing")


def _validate_determination(
    declaration: Mapping[str, str], recording: Mapping[str, Any], entry: Mapping[str, Any]
) -> None:
    if set(entry) != _ENTRY_KEYS:
        raise MalformedInput("eligibility entry must have exactly the sealed keys")
    _validate_identity(declaration, recording, entry)
    _validate_integrity(entry["integrity"])
    _validate_source_pts(entry["source_pts"])
    _validate_intro(recording, entry["intro_latch"])
    _validate_geometry(entry["geometry"], entry["intro_latch"])
    _validate_decision(entry)


def _validate_identity(
    declaration: Mapping[str, str], recording: Mapping[str, Any], entry: Mapping[str, Any]
) -> None:
    for key in ("video_sha256", "video_identity", "vision_manifest_sha256"):
        if entry[key] != declaration[key] or recording[key] != declaration[key]:
            raise MalformedInput(f"duplicated {key} does not match the declaration")
    if entry["analysis_dir"] != declaration["analysis_dir"]:
        raise MalformedInput("eligibility analysis_dir does not match the declaration")
    if (
        recording["vision_manifest_path"]
        != f"{declaration['analysis_dir']}/vision_manifest.json"
    ):
        raise MalformedInput("vision_manifest_path does not match analysis_dir")


def _validate_integrity(integrity: Any) -> None:
    if not isinstance(integrity, dict) or set(integrity) != set(_INTEGRITY_KEYS):
        raise MalformedInput("integrity must have exactly the sealed keys")
    if any(integrity[key] != "pass" for key in _INTEGRITY_KEYS):
        raise MalformedInput("integrity values must be pass")


def _validate_source_pts(source_pts: Any) -> None:
    if not isinstance(source_pts, dict) or set(source_pts) != _SOURCE_PTS_KEYS:
        raise MalformedInput("source_pts must have exactly the sealed keys")
    frame_count = _nonnegative_int(source_pts["frame_count"], "frame_count")
    usable = _nonnegative_int(
        source_pts["usable_source_pts_count"], "usable_source_pts_count"
    )
    if usable > frame_count or source_pts["complete"] is not (usable == frame_count):
        raise MalformedInput("source_pts counts are inconsistent")


def _validate_intro(recording: Mapping[str, Any], intro: Any) -> None:
    if not isinstance(intro, dict) or set(intro) != _INTRO_KEYS:
        raise MalformedInput("intro_latch must have exactly the sealed keys")
    if intro["blind_input_recording"] != recording["recording"]:
        raise MalformedInput("blind_input_recording does not match the blind input")
    for key in (
        "stage_id",
        "battle_mode_id",
        "stage_latched_at",
        "battle_mode_latched_at",
        "intro_reading_count",
    ):
        if intro[key] != recording[key]:
            raise MalformedInput(f"duplicated intro_latch.{key} does not match")


def _validate_geometry(geometry: Any, intro: Mapping[str, Any]) -> None:
    if not isinstance(geometry, dict) or set(geometry) != _GEOMETRY_KEYS:
        raise MalformedInput("geometry must have exactly the sealed keys")
    if geometry["status"] not in _GEOMETRY_STATUS:
        raise MalformedInput("geometry status is not sealed")
    if geometry["stage_id"] != intro["stage_id"]:
        raise MalformedInput("geometry stage_id does not match the intro latch")
    if geometry["battle_mode_id"] != intro["battle_mode_id"]:
        raise MalformedInput("geometry battle_mode_id does not match the intro latch")
    _validate_geometry_files(geometry)


def _validate_geometry_files(geometry: Mapping[str, Any]) -> None:
    files = geometry["files"]
    if not isinstance(files, list):
        raise MalformedInput("geometry files must be a list")
    roles = [_geometry_file(item) for item in files]
    if roles != [role for role in _ROLE_ORDER if role in roles] or len(roles) != len(
        set(roles)
    ):
        raise MalformedInput("geometry files are not in sealed order")
    status = geometry["status"]
    if status == "not_applicable_no_stage":
        if geometry["stage_id"] is not None or files:
            raise MalformedInput("no-stage geometry must be empty")
        return
    if geometry["stage_id"] is None:
        raise MalformedInput("geometry stage_id is missing")
    if status == "missing" and not any(item["sha256"] is None for item in files):
        raise MalformedInput("missing geometry needs a null digest")
    if status == "resolved":
        _validate_resolved_files(files)


def _geometry_file(item: Any) -> str:
    if (
        not isinstance(item, dict)
        or set(item) != _FILE_KEYS
        or item["role"] not in _ROLE_ORDER
    ):
        raise MalformedInput("geometry file must have the sealed role, path and sha256")
    if not isinstance(item["path"], str) or not item["path"]:
        raise MalformedInput("geometry path is missing")
    digest = item["sha256"]
    if digest is not None and (not isinstance(digest, str) or not _HEX64.match(digest)):
        raise MalformedInput("geometry digest is not 64 lowercase hex or null")
    return str(item["role"])


def _validate_resolved_files(files: Sequence[Mapping[str, Any]]) -> None:
    if not files or any(item["sha256"] is None for item in files):
        raise MalformedInput("resolved geometry needs pinned digests")
    for item in files:
        pinned = STAGE_GEOMETRY_PINS.get(item["path"])
        if pinned is None or item["sha256"] != pinned:
            raise MalformedInput("resolved geometry does not match the committed pin")


def _validate_decision(entry: Mapping[str, Any]) -> None:
    count = entry["eligible_interval_count"]
    if count is not None:
        _nonnegative_int(count, "eligible_interval_count")
    if entry["ineligibility_reasons"] != _expected_reasons(entry):
        raise MalformedInput("ineligibility reasons do not match the sealed rules")
    if entry["eligible"] is not _expected_eligible(entry):
        raise MalformedInput("eligible does not match the sealed equivalence")


def _expected_reasons(entry: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    if entry["source_pts"]["complete"] is not True:
        reasons.append("missing_source_pts")
    if entry["intro_latch"]["stage_id"] is None:
        reasons.append("stage_identity_not_latched")
    if entry["geometry"]["status"] == "missing":
        reasons.append("required_geometry_missing")
    count = entry["eligible_interval_count"]
    if isinstance(count, int) and not isinstance(count, bool) and count < 8:
        reasons.append("fewer_than_8_eligible_intervals")
    return reasons


def _expected_eligible(entry: Mapping[str, Any]) -> bool:
    count = entry["eligible_interval_count"]
    return (
        all(entry["integrity"][key] == "pass" for key in _INTEGRITY_KEYS)
        and entry["source_pts"]["complete"] is True
        and entry["intro_latch"]["stage_id"] is not None
        and entry["geometry"]["status"] == "resolved"
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count >= 8
        and entry["ineligibility_reasons"] == []
    )


def _nonnegative_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MalformedInput(f"{where} must be a non-negative integer")
    return value
