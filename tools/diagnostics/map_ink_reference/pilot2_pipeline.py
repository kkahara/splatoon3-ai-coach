"""Amendment 9 pilot-2 orchestration over frozen Stage 1 functions."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from map_ink_reference.constants import (
    PILOT2_ADDENDUM,
    PILOT2_RECORDS_DIR,
    PILOT2_SELECTION,
    PILOT2_WORK_DIR,
    PURPOSE_PILOT,
    SCOPE_PILOT2,
    STAGE_1,
    SUPPLEMENTARY_DIR,
)
from map_ink_reference.errors import (
    IsolationViolation,
    MalformedInput,
    PinMismatch,
    ProcedureOrderError,
)
from map_ink_reference.extraction import extract_recording
from map_ink_reference.gate import (
    evaluate_gate,
    pair_labels,
    population,
    verify_queue_duplicates,
)
from map_ink_reference.io_utils import canonical_json, read_json, sha256_file, write_json
from map_ink_reference.pilot import (
    PilotSource,
    check_declared_source,
    corpus_exclusions,
    verify_source_video,
)
from map_ink_reference.pilot2_sources import select_pilot2, source_bindings
from map_ink_reference.pins import verify_geometry_pins, verify_pin
from map_ink_reference.plan import build_frame_plan, stage_cue_frame_ids, stage_frame_ids
from map_ink_reference.points import (
    StageFrame,
    duplicate_point_ids,
    pilot_duplicate_count,
    sample_stage_points,
)
from map_ink_reference.records import (
    SWATCHES,
    TOKEN_KEY,
    export_session_records,
    labels_name,
    queue_key_name,
    queue_name,
)
from map_ink_reference.seal import (
    changed_paths,
    path_commits,
    require_sealed,
    strictly_after,
)
from map_ink_reference.session import (
    FrameAsset,
    RecordingAssets,
    SessionInputs,
    SessionPoint,
)
from map_ink_reference.stage_identity import StageIdentity

POINTS = "points_stage1.json"
DUPLICATES = "duplicates_stage1.json"
GATE = "gate_stage1.json"
TEAM_RECORD = "team_" + "identity_stage1.json"
H3_NAMES = (labels_name(STAGE_1), TEAM_RECORD, queue_name(STAGE_1), SWATCHES)
H4_NAMES = (TOKEN_KEY, queue_key_name(STAGE_1))
PILOT_COUNT = 2


@dataclass(frozen=True)
class Pilot2Scope:
    """Repository, sealed pilot-2 records (P2) and working output (W2)."""

    repo: Path
    records: Path
    work: Path

    @classmethod
    def from_repo(cls, repo: Path) -> Pilot2Scope:
        """Return the isolated Amendment 9 pilot-2 namespace."""
        return cls(repo, repo / PILOT2_RECORDS_DIR, repo / PILOT2_WORK_DIR)

    def rel(self, path: Path) -> str:
        """Return a repository-relative POSIX path."""
        return path.resolve().relative_to(self.repo.resolve()).as_posix()


def selection_path(scope: Pilot2Scope) -> Path:
    """Return the H1 selection record path."""
    return scope.records / PILOT2_SELECTION


def plan_path(scope: Pilot2Scope, rid: str) -> Path:
    """Return one H2 frame-plan path."""
    return scope.records / "frame_plans" / f"{rid}.json"


def extraction_path(scope: Pilot2Scope, rid: str) -> Path:
    """Return one H2 extraction-provenance path."""
    return scope.records / "extraction" / f"{rid}.json"


def frames_dir(scope: Pilot2Scope, rid: str) -> Path:
    """Return one W2 extracted-frame directory."""
    return scope.work / "frames" / rid


def session_dir(scope: Pilot2Scope) -> Path:
    """Return the W2 Stage 1 annotation-session directory."""
    return scope.work / "session_stage1"


def addendum_path(scope: Pilot2Scope) -> Path:
    """Return the H6 pilot-2 addendum path."""
    return scope.records / PILOT2_ADDENDUM


def pilot2_groups(scope: Pilot2Scope, chosen: Sequence[str]) -> list[list[Path]]:
    """Return exact H1-H6 record groups."""
    h2 = [plan_path(scope, rid) for rid in chosen]
    h2 += [extraction_path(scope, rid) for rid in chosen]
    h2 += [scope.records / POINTS, scope.records / DUPLICATES]
    return [
        [selection_path(scope)],
        h2,
        [scope.records / name for name in H3_NAMES],
        [scope.records / name for name in H4_NAMES],
        [scope.records / GATE],
        [addendum_path(scope)],
    ]


def preflight(paths: Iterable[Path]) -> None:
    """STOP before regenerating any pilot-2 output."""
    existing = [path for path in paths if path.exists()]
    if existing:
        raise ProcedureOrderError(
            "pilot2 output already exists: " + ", ".join(path.name for path in existing)
        )


def read_pilot2_record(path: Path) -> dict[str, Any]:
    """Read a record only when its scope is exactly ``pilot2``."""
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("scope") != SCOPE_PILOT2:
        raise IsolationViolation(f"record is not pilot2-scoped: {path.name}")
    return payload


def group_commit(scope: Pilot2Scope, paths: Sequence[Path]) -> str:
    """Require one exact, isolated commit for a pilot-2 group."""
    commits = {require_sealed(scope.repo, path) for path in paths}
    if len(commits) != 1:
        raise ProcedureOrderError("pilot2 records are split across a commit group")
    commit = commits.pop()
    if set(changed_paths(scope.repo, commit)) != {scope.rel(path) for path in paths}:
        raise ProcedureOrderError("a pilot2 commit group must contain only its records")
    return commit


def sealed_chosen(scope: Pilot2Scope) -> list[str]:
    """Return the two chosen recordings in sealed H1 order."""
    require_sealed(scope.repo, selection_path(scope))
    chosen = read_pilot2_record(selection_path(scope)).get("chosen")
    if (
        not isinstance(chosen, list)
        or len(chosen) != PILOT_COUNT
        or len(set(chosen)) != PILOT_COUNT
        or not all(isinstance(rid, str) and rid for rid in chosen)
    ):
        raise MalformedInput("pilot2 selection must hold two recordings")
    return list(chosen)


def verify_groups(
    scope: Pilot2Scope,
    count: int,
    *,
    predecessor: str,
    complete: bool = False,
) -> tuple[list[str], list[str]]:
    """Verify exact H groups and direct linear order from ``predecessor``."""
    chosen = sealed_chosen(scope)
    groups = pilot2_groups(scope, chosen)
    commits: list[str] = []
    previous = predecessor
    for paths in groups[:count]:
        commit = group_commit(scope, paths)
        if not strictly_after(scope.repo, previous, commit):
            raise ProcedureOrderError("pilot2 groups are out of order")
        commits.append(commit)
        previous = commit
    supplementary_root = scope.rel(scope.records / SUPPLEMENTARY_DIR) + "/"
    touching = [
        commit
        for commit in path_commits(scope.repo, scope.records)
        if not all(
            path.startswith(supplementary_root)
            for path in changed_paths(scope.repo, commit)
        )
    ]
    if (touching if complete else touching[: len(commits)]) != commits:
        raise ProcedureOrderError("another commit changes sealed pilot2 records")
    return chosen, commits


def write_selection(scope: Pilot2Scope, tool: Mapping[str, Any]) -> tuple[Path, str]:
    """Write H1 using only the prospective Amendment 9 selection rule."""
    output = selection_path(scope)
    preflight([output])
    selection = {**select_pilot2(scope), "tool": dict(tool)}
    return output, write_json(output, selection)


def _bindings(scope: Pilot2Scope, chosen: Sequence[str]) -> dict[str, dict[str, Any]]:
    return source_bindings(scope, chosen)


def _verified_manifest(scope: Pilot2Scope, entry: Mapping[str, Any]) -> Path:
    path = scope.repo / entry["vision_manifest_path"]
    verify_pin(path, entry["vision_manifest_sha256"])
    return path


def write_plans(
    scope: Pilot2Scope, tool: Mapping[str, Any], *, predecessor: str
) -> list[Path]:
    """Build both H2 plans with the unchanged frame-plan function."""
    chosen, _ = verify_groups(scope, 1, predecessor=predecessor)
    outputs = [plan_path(scope, rid) for rid in chosen]
    preflight(outputs)
    bindings = _bindings(scope, chosen)
    for rid, output in zip(chosen, outputs, strict=True):
        entry = bindings[rid]
        plan = build_frame_plan(
            rid,
            read_json(_verified_manifest(scope, entry)),
            manifest_sha256=entry["vision_manifest_sha256"],
            purpose=PURPOSE_PILOT,
        )
        write_json(output, {**plan, "scope": SCOPE_PILOT2, "tool": dict(tool)})
    return outputs


def bound_plan(scope: Pilot2Scope, rid: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    """Bind an H2 plan to its selected source and vision manifest."""
    plan = read_pilot2_record(plan_path(scope, rid))
    source: PilotSource = entry["source"]
    if (
        plan.get("recording_id") != rid
        or plan.get("purpose") != PURPOSE_PILOT
        or plan.get("vision_manifest_sha256") != entry["vision_manifest_sha256"]
        or plan.get("video_identity") != source.video_identity
    ):
        raise PinMismatch(f"{rid}: pilot2 plan differs from sealed source data")
    _verified_manifest(scope, entry)
    return plan


def extract_plans(
    scope: Pilot2Scope, tool: Mapping[str, Any], *, predecessor: str
) -> list[Path]:
    """Decode selected sources through the unchanged extractor."""
    chosen, _ = verify_groups(scope, 1, predecessor=predecessor)
    outputs = [extraction_path(scope, rid) for rid in chosen]
    preflight(outputs + [frames_dir(scope, rid) for rid in chosen])
    bindings = _bindings(scope, chosen)
    plans = {rid: bound_plan(scope, rid, bindings[rid]) for rid in chosen}
    exclusions = corpus_exclusions(scope.repo)
    for rid in chosen:
        check_declared_source(scope.repo, bindings[rid]["source"], exclusions)
    videos = {
        rid: verify_source_video(scope.repo, bindings[rid]["source"]) for rid in chosen
    }
    for rid, output in zip(chosen, outputs, strict=True):
        record = extract_recording(
            plans[rid],
            videos[rid],
            frames_dir(scope, rid),
            expected_video_sha256=bindings[rid]["source"].video_sha256,
        )
        write_json(output, {**record, "scope": SCOPE_PILOT2, "tool": dict(tool)})
    return outputs


def _frame_index(scope: Pilot2Scope, rid: str) -> dict[str, dict[str, Any]]:
    record = read_pilot2_record(extraction_path(scope, rid))
    if record.get("recording_id") != rid:
        raise MalformedInput(f"{rid}: extraction belongs to another recording")
    return {frame["frame_id"]: frame for frame in record["frames"]}


def _assets(
    scope: Pilot2Scope,
    rid: str,
    ids: Sequence[str],
    index: Mapping[str, Any],
) -> tuple[FrameAsset, ...]:
    root = frames_dir(scope, rid)
    return tuple(
        FrameAsset(fid, root / index[fid]["png"], index[fid]["png_sha256"]) for fid in ids
    )


def _stage_frames(
    scope: Pilot2Scope, rid: str, plan: Mapping[str, Any]
) -> list[StageFrame]:
    index = _frame_index(scope, rid)
    return [
        StageFrame(
            rid,
            fid,
            int(index[fid]["source_pts"]),
            frames_dir(scope, rid) / index[fid]["png"],
            index[fid]["png_sha256"],
        )
        for fid in stage_frame_ids(plan, STAGE_1)
    ]


def _stage_identities(
    chosen: Sequence[str], bindings: Mapping[str, Mapping[str, Any]]
) -> dict[str, StageIdentity]:
    if any(bindings[rid]["stage_id"] is None for rid in chosen):
        raise MalformedInput("a chosen pilot2 recording has no latched stage")
    return {
        rid: StageIdentity(bindings[rid]["stage_id"], bindings[rid]["battle_mode_id"])
        for rid in chosen
    }


def write_points(
    scope: Pilot2Scope, tool: Mapping[str, Any], *, predecessor: str
) -> tuple[Path, Path]:
    """Sample H2 points and duplicates with unchanged scientific functions."""
    chosen, _ = verify_groups(scope, 1, predecessor=predecessor)
    output, duplicates = scope.records / POINTS, scope.records / DUPLICATES
    preflight([output, duplicates])
    bindings = _bindings(scope, chosen)
    plans = {rid: bound_plan(scope, rid, bindings[rid]) for rid in chosen}
    identities = _stage_identities(chosen, bindings)
    geometry = verify_geometry_pins(scope.repo)
    frames = [frame for rid in chosen for frame in _stage_frames(scope, rid, plans[rid])]
    points = sample_stage_points(
        frames,
        video_identities={rid: plans[rid]["video_identity"] for rid in chosen},
        stage_identities=identities,
        geometry_dir=geometry,
    )
    manifest = {
        "stage": STAGE_1,
        "scope": SCOPE_PILOT2,
        "tool": dict(tool),
        "recordings": list(chosen),
        "stage_identities": {
            rid: {
                "stage_id": identity.stage_id,
                "battle_mode_id": identity.battle_mode_id,
            }
            for rid, identity in identities.items()
        },
        "frames": [
            {
                "recording_id": frame.recording_id,
                "frame_id": frame.frame_id,
                "png_sha256": frame.png_sha256,
            }
            for frame in frames
        ],
        "points": points,
    }
    digest = hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
    duplicate_ids = duplicate_point_ids(
        [point["point_id"] for point in points],
        digest,
        count=pilot_duplicate_count(len(points)),
    )
    write_json(output, manifest)
    write_json(
        duplicates,
        {
            "stage": STAGE_1,
            "scope": SCOPE_PILOT2,
            "point_manifest_sha256": digest,
            "duplicate_ids": duplicate_ids,
        },
    )
    return output, duplicates


def _sealed_points(
    scope: Pilot2Scope, chosen: Sequence[str]
) -> tuple[dict[str, Any], list[str], str]:
    manifest_file = scope.records / POINTS
    manifest = read_pilot2_record(manifest_file)
    if manifest.get("recordings") != list(chosen):
        raise IsolationViolation("pilot2 points cover recordings outside H1")
    digest = sha256_file(manifest_file)
    duplicates = read_pilot2_record(scope.records / DUPLICATES)
    if duplicates.get("point_manifest_sha256") != digest:
        raise MalformedInput("pilot2 duplicate list belongs to another point manifest")
    points = manifest["points"]
    expected = duplicate_point_ids(
        [point["point_id"] for point in points],
        digest,
        count=pilot_duplicate_count(len(points)),
    )
    if duplicates.get("duplicate_ids") != expected:
        raise MalformedInput("pilot2 duplicate list differs from the frozen rule")
    return manifest, list(expected), digest


def session_inputs(scope: Pilot2Scope, *, predecessor: str) -> SessionInputs:
    """Build unchanged human-annotation inputs from sealed H2."""
    chosen, _ = verify_groups(scope, 2, predecessor=predecessor)
    manifest, duplicate_ids, digest = _sealed_points(scope, chosen)
    recordings: dict[str, RecordingAssets] = {}
    for rid in chosen:
        plan = read_pilot2_record(plan_path(scope, rid))
        index = _frame_index(scope, rid)
        recordings[rid] = RecordingAssets(
            selected=_assets(scope, rid, stage_frame_ids(plan, STAGE_1), index),
            cues=_assets(scope, rid, stage_cue_frame_ids(plan, STAGE_1), index),
        )
    return SessionInputs(
        stage=STAGE_1,
        recordings=recordings,
        points=[
            SessionPoint(
                point["point_id"],
                point["recording_id"],
                point["frame_id"],
                int(point["x"]),
                int(point["y"]),
            )
            for point in manifest["points"]
        ],
        duplicate_ids=duplicate_ids,
        point_manifest_sha256=digest,
    )


def export(
    scope: Pilot2Scope, tool: Mapping[str, Any], *, predecessor: str
) -> dict[str, str]:
    """Export completed W2 annotation to H3/H4 without weakening human validation."""
    verify_groups(scope, 2, predecessor=predecessor)
    preflight([scope.records / name for name in (*H3_NAMES, *H4_NAMES)])
    return export_session_records(
        session_dir(scope), scope.records, tool=tool, scope=SCOPE_PILOT2
    )


def gate(
    scope: Pilot2Scope, tool: Mapping[str, Any], *, predecessor: str
) -> dict[str, Any]:
    """Evaluate H5 through the unchanged Stage 1 gate functions."""
    chosen, _ = verify_groups(scope, 4, predecessor=predecessor)
    output = scope.records / GATE
    preflight([output])
    manifest, duplicates, _ = _sealed_points(scope, chosen)
    names = (labels_name(STAGE_1), queue_name(STAGE_1), SWATCHES, *H4_NAMES)
    records = {name: read_pilot2_record(scope.records / name) for name in names}
    swatches = records[SWATCHES]["swatches"]
    tokens = records[TOKEN_KEY]["tokens"]
    valid = [
        rid
        for rid in manifest["recordings"]
        if swatches.get(tokens[rid], {}).get("status") == "valid"
    ]
    point_recording = {
        point["point_id"]: point["recording_id"] for point in manifest["points"]
    }
    members = population(duplicates, point_recording, valid)
    queue = records[queue_name(STAGE_1)]["items"]
    refs = records[queue_key_name(STAGE_1)]["point_refs"]
    verify_queue_duplicates(members, queue, refs)
    labels = {
        item["item_id"]: item["label"] for item in records[labels_name(STAGE_1)]["labels"]
    }
    pairs = pair_labels(members, queue, labels, refs)
    payload = {
        "stage": STAGE_1,
        "scope": SCOPE_PILOT2,
        "tool": dict(tool),
        **evaluate_gate(pairs).to_dict(),
    }
    write_json(output, payload)
    return payload
