"""Pilot-scoped orchestration over the frozen blind Stage 1 functions.

Every scientific step calls the function the corpus Stage 1 path calls. This
module adds only the Amendment 7 pilot namespace, the sealed commit groups
G1–G5, the ``scope`` field and the no-regeneration rule.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from map_ink_reference.constants import (
    PILOT_ADDENDUM,
    PILOT_RECORDS_DIR,
    PILOT_SELECTION,
    PILOT_WORK_DIR,
    PURPOSE_PILOT,
    RECORDS_DIR,
    SCOPE_PILOT,
    STAGE_1,
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
    load_pilot_source_manifest,
    select_pilot,
    verify_source_video,
)
from map_ink_reference.pins import verify_geometry_pins, verify_pin
from map_ink_reference.plan import build_frame_plan, stage_cue_frame_ids, stage_frame_ids
from map_ink_reference.points import StageFrame, duplicate_point_ids, sample_stage_points
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
from map_ink_reference.stage_identity import (
    StageIdentity,
    load_pilot_blind_input,
    step2a_paths,
)

POINTS = "points_stage1.json"
DUPLICATES = "duplicates_stage1.json"
GATE = "gate_stage1.json"
# Split literal: the blind isolation check reserves the full name for records.py.
TEAM_RECORD = "team_" + "identity_stage1.json"
G3_NAMES = (labels_name(STAGE_1), TEAM_RECORD, queue_name(STAGE_1), SWATCHES)
G4_NAMES = (TOKEN_KEY, queue_key_name(STAGE_1))
PILOT_COUNT = 2


@dataclass(frozen=True)
class PilotScope:
    """The repository, sealed pilot records (P) and working pilot output (W)."""

    repo: Path
    records: Path
    work: Path

    @classmethod
    def from_repo(cls, repo: Path) -> PilotScope:
        """Return the Amendment 7 pilot namespace of ``repo``."""
        return cls(repo, repo / PILOT_RECORDS_DIR, repo / PILOT_WORK_DIR)

    def rel(self, path: Path) -> str:
        """Repository-relative POSIX path."""
        return path.resolve().relative_to(self.repo.resolve()).as_posix()


def selection_path(scope: PilotScope) -> Path:
    """G1: committed pilot selection record."""
    return scope.records / PILOT_SELECTION


def plan_path(scope: PilotScope, rid: str) -> Path:
    """G2: committed pilot frame plan."""
    return scope.records / "frame_plans" / f"{rid}.json"


def extraction_path(scope: PilotScope, rid: str) -> Path:
    """G2: committed pilot extraction provenance."""
    return scope.records / "extraction" / f"{rid}.json"


def frames_dir(scope: PilotScope, rid: str) -> Path:
    """Working PNG directory of a pilot recording."""
    return scope.work / "frames" / rid


def session_dir(scope: PilotScope) -> Path:
    """Working Stage 1 pilot session state."""
    return scope.work / "session_stage1"


def addendum_path(scope: PilotScope) -> Path:
    """G6: the pilot addendum."""
    return scope.records / PILOT_ADDENDUM


def pilot_groups(scope: PilotScope, chosen: Sequence[str]) -> list[list[Path]]:
    """Record paths of G1–G5 for the sealed chosen recordings, in sealing order."""
    g2 = [plan_path(scope, rid) for rid in chosen]
    g2 += [extraction_path(scope, rid) for rid in chosen]
    g2 += [scope.records / POINTS, scope.records / DUPLICATES]
    return [
        [selection_path(scope)],
        g2,
        [scope.records / name for name in G3_NAMES],
        [scope.records / name for name in G4_NAMES],
        [scope.records / GATE],
    ]


def preflight(paths: Iterable[Path]) -> None:
    """No pilot output is regenerated: STOP before writing if any target exists."""
    existing = [path for path in paths if path.exists()]
    if existing:
        names = ", ".join(path.name for path in existing)
        raise ProcedureOrderError(f"pilot output already exists: {names}")


def read_pilot_record(path: Path) -> dict[str, Any]:
    """Pilot readers require ``"scope": "pilot"``."""
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("scope") != SCOPE_PILOT:
        raise IsolationViolation(f"record is not pilot-scoped: {path.name}")
    return payload


def group_commit(scope: PilotScope, paths: Sequence[Path]) -> str:
    """The single commit sealing a group, which contains only that group's records."""
    commits = {require_sealed(scope.repo, path) for path in paths}
    if len(commits) != 1:
        raise ProcedureOrderError("pilot records are split across a commit group")
    commit = commits.pop()
    if set(changed_paths(scope.repo, commit)) != {scope.rel(path) for path in paths}:
        raise ProcedureOrderError("a pilot commit group must contain only its records")
    return commit


def sealed_chosen(scope: PilotScope) -> list[str]:
    """The two chosen recordings of the sealed pilot selection, in order."""
    path = selection_path(scope)
    require_sealed(scope.repo, path)
    chosen = read_pilot_record(path).get("chosen")
    if (
        not isinstance(chosen, list)
        or len(chosen) != PILOT_COUNT
        or len(set(chosen)) != PILOT_COUNT
        or not all(isinstance(rid, str) and rid for rid in chosen)
    ):
        raise MalformedInput("pilot selection does not hold two chosen recordings")
    return list(chosen)


def verify_groups(
    scope: PilotScope, count: int, *, complete: bool = False
) -> tuple[list[str], list[str]]:
    """Verify G1..G``count`` sealed in order; return chosen recordings and commits.

    G1 is strictly after the corpus blind-input manifest; each later group is
    strictly after the previous one. No other commit touches P before the
    last verified group (``complete``: no other commit touches P at all).
    """
    repo = scope.repo
    chosen = sealed_chosen(scope)
    groups = pilot_groups(scope, chosen) + [[addendum_path(scope)]]
    corpus_blind = require_sealed(repo, step2a_paths(repo / RECORDS_DIR)[2])
    commits: list[str] = []
    previous = corpus_blind
    for paths in groups[:count]:
        commit = group_commit(scope, paths)
        if not strictly_after(repo, previous, commit):
            raise ProcedureOrderError("pilot commit groups are out of order")
        commits.append(commit)
        previous = commit
    touching = path_commits(repo, scope.records)
    if (touching if complete else touching[: len(commits)]) != commits:
        raise ProcedureOrderError("another commit changes the sealed pilot records")
    return chosen, commits


def write_selection(scope: PilotScope, tool: Mapping[str, Any]) -> tuple[Path, str]:
    """G1 output: the frozen selection payload plus ``scope``."""
    output = selection_path(scope)
    preflight([output])
    payload = select_pilot(scope.repo, scope.repo / RECORDS_DIR)
    selection = {**payload, "scope": SCOPE_PILOT, "tool": dict(tool)}
    return output, write_json(output, selection)


def _bindings(scope: PilotScope, chosen: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Sealed pilot blind-input entries and declared sources of the chosen recordings."""
    records = scope.repo / RECORDS_DIR
    sources = {
        source.recording: source
        for source in load_pilot_source_manifest(step2a_paths(records)[0])
    }
    entries = {
        entry["recording"]: entry
        for entry in load_pilot_blind_input(scope.repo, records)["recordings"]
    }
    if any(rid not in entries or rid not in sources for rid in chosen):
        raise MalformedInput("a chosen recording is not a declared pilot source")
    return {rid: {**entries[rid], "source": sources[rid]} for rid in chosen}


def _verified_manifest(scope: PilotScope, entry: Mapping[str, Any]) -> Path:
    path = scope.repo / entry["vision_manifest_path"]
    verify_pin(path, entry["vision_manifest_sha256"])
    return path


def write_plans(scope: PilotScope, tool: Mapping[str, Any]) -> list[Path]:
    """Build both pilot plans with the frozen plan function (no decode)."""
    chosen, _ = verify_groups(scope, 1)
    outputs = [plan_path(scope, rid) for rid in chosen]
    preflight(outputs)
    bindings = _bindings(scope, chosen)
    plans = []
    for rid in chosen:
        manifest = _verified_manifest(scope, bindings[rid])
        digest = bindings[rid]["vision_manifest_sha256"]
        plan = build_frame_plan(
            rid, read_json(manifest), manifest_sha256=digest, purpose=PURPOSE_PILOT
        )
        plans.append({**plan, "scope": SCOPE_PILOT, "tool": dict(tool)})
    for output, plan in zip(outputs, plans, strict=True):
        write_json(output, plan)
    return outputs


def bound_plan(scope: PilotScope, rid: str, entry: Mapping[str, Any]) -> dict[str, Any]:
    """A chosen recording's plan, bound to its verified vision manifest digest."""
    path = plan_path(scope, rid)
    if not path.is_file():
        raise ProcedureOrderError(f"pilot plan is missing for a chosen recording: {rid}")
    plan = read_pilot_record(path)
    source: PilotSource = entry["source"]
    if (
        plan.get("recording_id") != rid
        or plan.get("purpose") != PURPOSE_PILOT
        or plan.get("vision_manifest_sha256") != entry["vision_manifest_sha256"]
        or plan.get("video_identity") != source.video_identity
    ):
        raise PinMismatch(f"{rid}: plan is not bound to the sealed pilot manifest")
    _verified_manifest(scope, entry)
    return plan


def extract_plans(scope: PilotScope, tool: Mapping[str, Any]) -> list[Path]:
    """Decode both declared pilot videos with the frozen extractor."""
    chosen, _ = verify_groups(scope, 1)
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
    provenance = [
        extract_recording(
            plans[rid],
            videos[rid],
            frames_dir(scope, rid),
            expected_video_sha256=bindings[rid]["source"].video_sha256,
        )
        for rid in chosen
    ]
    for output, record in zip(outputs, provenance, strict=True):
        write_json(output, {**record, "scope": SCOPE_PILOT, "tool": dict(tool)})
    return outputs


def _frame_index(scope: PilotScope, rid: str) -> dict[str, dict[str, Any]]:
    path = extraction_path(scope, rid)
    if not path.is_file():
        raise ProcedureOrderError(f"pilot extraction is missing: {rid}")
    provenance = read_pilot_record(path)
    if provenance.get("recording_id") != rid:
        raise MalformedInput(f"{rid}: extraction belongs to another recording")
    return {frame["frame_id"]: frame for frame in provenance["frames"]}


def _assets(
    scope: PilotScope, rid: str, ids: Sequence[str], index: Mapping[str, Any]
) -> tuple[FrameAsset, ...]:
    root = frames_dir(scope, rid)
    return tuple(
        FrameAsset(fid, root / index[fid]["png"], index[fid]["png_sha256"]) for fid in ids
    )


def _stage_frames(
    scope: PilotScope, rid: str, plan: Mapping[str, Any]
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
        raise MalformedInput("a chosen pilot recording has no latched stage")
    return {
        rid: StageIdentity(bindings[rid]["stage_id"], bindings[rid]["battle_mode_id"])
        for rid in chosen
    }


def write_points(scope: PilotScope, tool: Mapping[str, Any]) -> tuple[Path, Path]:
    """Sample pilot points and duplicates with the frozen point functions."""
    chosen, _ = verify_groups(scope, 1)
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
        "scope": SCOPE_PILOT,
        "tool": dict(tool),
        "pilot_blind_input_manifest_sha256": sha256_file(
            step2a_paths(scope.repo / RECORDS_DIR)[1]
        ),
        "recordings": list(chosen),
        "stage_identities": {
            rid: {"stage_id": s.stage_id, "battle_mode_id": s.battle_mode_id}
            for rid, s in identities.items()
        },
        "frames": [
            {
                "recording_id": f.recording_id,
                "frame_id": f.frame_id,
                "png_sha256": f.png_sha256,
            }
            for f in frames
        ],
        "points": points,
    }
    digest = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
    dups = duplicate_point_ids([p["point_id"] for p in points], digest)
    write_json(output, manifest)
    write_json(
        duplicates,
        {
            "stage": STAGE_1,
            "scope": SCOPE_PILOT,
            "point_manifest_sha256": digest,
            "duplicate_ids": dups,
        },
    )
    return output, duplicates


def _sealed_points(scope: PilotScope, chosen: Sequence[str]) -> tuple[dict, list, str]:
    """Sealed pilot point manifest and its duplicate list, cross-checked."""
    manifest_file = scope.records / POINTS
    manifest = read_pilot_record(manifest_file)
    if manifest.get("recordings") != list(chosen):
        raise IsolationViolation("pilot points cover recordings outside the selection")
    pilot_blind = step2a_paths(scope.repo / RECORDS_DIR)[1]
    if manifest.get("pilot_blind_input_manifest_sha256") != sha256_file(pilot_blind):
        raise PinMismatch("pilot points are bound to another pilot blind-input manifest")
    digest = sha256_file(manifest_file)
    dups = read_pilot_record(scope.records / DUPLICATES)
    if dups.get("point_manifest_sha256") != digest:
        raise MalformedInput("duplicate list does not belong to this point manifest")
    ids = duplicate_point_ids([p["point_id"] for p in manifest["points"]], digest)
    if dups.get("duplicate_ids") != ids:
        raise MalformedInput("duplicate list differs from the frozen duplicate rule")
    return manifest, list(ids), digest


def session_inputs(scope: PilotScope) -> SessionInputs:
    """The unchanged Stage 1 session inputs from sealed G2 pilot records."""
    chosen, _ = verify_groups(scope, 2)
    manifest, dups, digest = _sealed_points(scope, chosen)
    recordings = {}
    for rid in chosen:
        plan = read_pilot_record(plan_path(scope, rid))
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
                p["point_id"], p["recording_id"], p["frame_id"], int(p["x"]), int(p["y"])
            )
            for p in manifest["points"]
        ],
        duplicate_ids=dups,
        point_manifest_sha256=digest,
    )


def export(scope: PilotScope, tool: Mapping[str, Any]) -> dict[str, str]:
    """Export the completed pilot session (G3 and G4 files) with the pilot scope."""
    verify_groups(scope, 2)
    preflight([scope.records / name for name in (*G3_NAMES, *G4_NAMES)])
    return export_session_records(
        session_dir(scope), scope.records, tool=tool, scope=SCOPE_PILOT
    )


def gate(scope: PilotScope, tool: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate the frozen Stage 1 duplicate gate over sealed G2–G4 pilot records."""
    chosen, _ = verify_groups(scope, 4)
    output = scope.records / GATE
    preflight([output])
    manifest, dups, _ = _sealed_points(scope, chosen)
    names = (labels_name(STAGE_1), queue_name(STAGE_1), SWATCHES, *G4_NAMES)
    records = {name: read_pilot_record(scope.records / name) for name in names}
    swatches = records[SWATCHES]["swatches"]
    tokens = records[TOKEN_KEY]["tokens"]
    valid = [
        rid
        for rid in manifest["recordings"]
        if swatches.get(tokens[rid], {}).get("status") == "valid"
    ]
    point_recording = {p["point_id"]: p["recording_id"] for p in manifest["points"]}
    members = population(dups, point_recording, valid)
    queue = records[queue_name(STAGE_1)]["items"]
    refs = records[queue_key_name(STAGE_1)]["point_refs"]
    verify_queue_duplicates(members, queue, refs)
    labels = {
        item["item_id"]: item["label"] for item in records[labels_name(STAGE_1)]["labels"]
    }
    pairs = pair_labels(members, queue, labels, refs)
    payload = {
        "stage": STAGE_1,
        "scope": SCOPE_PILOT,
        "tool": dict(tool),
        **evaluate_gate(pairs).to_dict(),
    }
    write_json(output, payload)
    return payload
