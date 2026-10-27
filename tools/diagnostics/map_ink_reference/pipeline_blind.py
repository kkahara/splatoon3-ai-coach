"""File-level orchestration of the blind steps (plan, extract, points, session, gate).

Nothing here reads calibrations, map-ink observations, classifier output,
comparison results, team-identity answers or the A/B mapping. Stage identity
comes only from the sealed blind-input manifest; Stage 2 membership only from
the sealed trigger's recording ids (Amendment 4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from map_ink_reference.constants import (
    CORRECTNESS_APPLICABLE,
    SCOPE_PILOT,
    STAGE_1,
    STAGE_2,
)
from map_ink_reference.errors import IsolationViolation, MalformedInput
from map_ink_reference.extraction import extract_recording
from map_ink_reference.gate import (
    evaluate_gate,
    pair_labels,
    pooled_pairs,
    population,
    verify_queue_duplicates,
)
from map_ink_reference.io_utils import read_json, sha256_file, write_json
from map_ink_reference.pins import MEDIA_PINS, verify_geometry_pins, verify_pin
from map_ink_reference.plan import build_frame_plan, stage_cue_frame_ids, stage_frame_ids
from map_ink_reference.points import (
    StageFrame,
    duplicate_point_ids,
    sample_stage_points,
)
from map_ink_reference.records import (
    SWATCHES,
    TOKEN_KEY,
    labels_name,
    queue_key_name,
    queue_name,
)
from map_ink_reference.seal import require_sealed, require_strictly_after
from map_ink_reference.session import (
    FrameAsset,
    RecordingAssets,
    SessionInputs,
    SessionPoint,
    load_sealed_swatches,
)
from map_ink_reference.stage_identity import (
    load_corpus_blind_input,
    require_step2a_sealed,
)
from map_ink_reference.trigger import require_stage2_recordings


def plan_path(records: Path, rid: str) -> Path:
    """Committed frame plan for a recording."""
    return records / "frame_plans" / f"{rid}.json"


def extraction_path(records: Path, rid: str) -> Path:
    """Committed extraction provenance for a recording."""
    return records / "extraction" / f"{rid}.json"


def points_path(records: Path, stage: int) -> Path:
    """Committed point manifest of a stage."""
    return records / f"points_stage{stage}.json"


def gate_path(records: Path, stage: int) -> Path:
    """Committed gate result of a stage."""
    return records / f"gate_stage{stage}.json"


def _require_absent(path: Path, description: str) -> None:
    """Refuse to overwrite an existing corpus output."""
    if path.exists():
        raise MalformedInput(f"{description} already exists")


def write_plan(repo: Path, records: Path, rid: str, tool: Mapping[str, Any]) -> Path:
    """Verify the pinned manifest and write the recording's frame plan (after 2a)."""
    require_step2a_sealed(repo, records)
    _require_corpus_recordings([rid])
    out = plan_path(records, rid)
    _require_absent(out, f"frame plan for {rid}")
    pins = MEDIA_PINS[rid]
    manifest_path = repo / pins.vision_manifest_path
    digest = verify_pin(manifest_path, pins.vision_manifest_sha256)
    plan = build_frame_plan(rid, read_json(manifest_path), manifest_sha256=digest)
    write_json(out, {**plan, "tool": dict(tool)})
    return out


def run_extraction(
    records: Path, work: Path, rid: str, video_path: Path, tool: Mapping[str, Any]
) -> Path:
    """Extract all planned frames of a recording (pinned video only; after 2a)."""
    require_step2a_sealed(_repo_of(records), records)
    _require_corpus_recordings([rid])
    out = extraction_path(records, rid)
    frame_dir = work / "frames" / rid
    _require_absent(out, f"extraction record for {rid}")
    _require_absent(frame_dir, f"frame directory for {rid}")
    plan = _read_record(plan_path(records, rid))
    provenance = extract_recording(
        plan,
        video_path,
        frame_dir,
        expected_video_sha256=MEDIA_PINS[rid].video_sha256,
    )
    write_json(out, {**provenance, "tool": dict(tool)})
    return out


def _frame_index(records: Path, rid: str) -> dict[str, dict[str, Any]]:
    provenance = _read_record(extraction_path(records, rid))
    return {f["frame_id"]: f for f in provenance["frames"]}


def stage_frames(
    records: Path, work: Path, stage: int, recordings: Sequence[str]
) -> list[StageFrame]:
    """Selected frames of a stage with their pinned PNGs."""
    frames: list[StageFrame] = []
    for rid in recordings:
        plan = _read_record(plan_path(records, rid))
        index = _frame_index(records, rid)
        for fid in stage_frame_ids(plan, stage):
            entry = index[fid]
            frames.append(
                StageFrame(
                    rid,
                    fid,
                    int(entry["source_pts"]),
                    work / "frames" / rid / entry["png"],
                    entry["png_sha256"],
                )
            )
    return frames


def stage_recordings(records: Path, stage: int, recordings: Sequence[str]) -> list[str]:
    """Stage 1: the correctness-applicable set; Stage 2: exactly the sealed trigger."""
    if stage == STAGE_1:
        if sorted(recordings) != sorted(CORRECTNESS_APPLICABLE):
            raise MalformedInput(
                "Stage 1 must cover exactly the correctness-applicable set"
            )
        return list(CORRECTNESS_APPLICABLE)
    if stage == STAGE_2:
        return require_stage2_recordings(_repo_of(records), records, recordings)
    raise MalformedInput(f"unknown stage {stage}")


def write_point_manifest(
    records: Path,
    work: Path,
    stage: int,
    recordings: Sequence[str],
    tool: Mapping[str, Any],
) -> tuple[Path, Path]:
    """Sample points for a stage; write the manifest and its duplicate list.

    Stage identities come only from the sealed corpus blind-input manifest.
    """
    repo = _repo_of(records)
    recordings = stage_recordings(records, stage, recordings)
    blind_input = load_corpus_blind_input(repo, records)
    stage_identities = {rid: blind_input.identities[rid] for rid in recordings}
    geometry_dir = verify_geometry_pins(repo)
    frames = stage_frames(records, work, stage, recordings)
    identities = {
        rid: _read_record(plan_path(records, rid))["video_identity"] for rid in recordings
    }
    points = sample_stage_points(
        frames,
        video_identities=identities,
        stage_identities=stage_identities,
        geometry_dir=geometry_dir,
    )
    manifest = {
        "stage": stage,
        "tool": dict(tool),
        "blind_input_manifest_sha256": blind_input.manifest_sha256,
        "recordings": list(recordings),
        "stage_identities": {
            rid: {"stage_id": s.stage_id, "battle_mode_id": s.battle_mode_id}
            for rid, s in stage_identities.items()
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
    out = points_path(records, stage)
    digest = write_json(out, manifest)
    dups = duplicate_point_ids([p["point_id"] for p in points], digest)
    dup_out = records / f"duplicates_stage{stage}.json"
    write_json(
        dup_out, {"stage": stage, "point_manifest_sha256": digest, "duplicate_ids": dups}
    )
    return out, dup_out


def _assets(records: Path, work: Path, rid: str, ids: Sequence[str]) -> tuple:
    index = _frame_index(records, rid)
    return tuple(
        FrameAsset(
            fid, work / "frames" / rid / index[fid]["png"], index[fid]["png_sha256"]
        )
        for fid in ids
    )


def session_inputs(records: Path, work: Path, stage: int) -> SessionInputs:
    """Assemble a session from the sealed point manifest (and Stage 1 swatches)."""
    manifest_file = points_path(records, stage)
    require_sealed(_repo_of(records), manifest_file)
    manifest = _read_record(manifest_file)
    _require_corpus_recordings(manifest["recordings"])
    digest = sha256_file(manifest_file)
    dups = _read_record(records / f"duplicates_stage{stage}.json")
    if dups["point_manifest_sha256"] != digest:
        raise MalformedInput("duplicate list does not belong to this point manifest")
    if dups["duplicate_ids"] != duplicate_point_ids(
        [p["point_id"] for p in manifest["points"]], digest
    ):
        raise MalformedInput("duplicate list differs from the frozen duplicate rule")
    recordings = {
        rid: _recording_assets(records, work, stage, rid)
        for rid in manifest["recordings"]
    }
    inputs = SessionInputs(
        stage=stage,
        recordings=recordings,
        points=[
            SessionPoint(
                p["point_id"], p["recording_id"], p["frame_id"], int(p["x"]), int(p["y"])
            )
            for p in manifest["points"]
        ],
        duplicate_ids=list(dups["duplicate_ids"]),
        point_manifest_sha256=digest,
    )
    if stage == STAGE_2:
        _attach_stage1_swatches(records, work, inputs)
    return inputs


def _recording_assets(records: Path, work: Path, stage: int, rid: str) -> RecordingAssets:
    plan = _read_record(plan_path(records, rid))
    return RecordingAssets(
        selected=_assets(records, work, rid, stage_frame_ids(plan, stage)),
        cues=_assets(records, work, rid, stage_cue_frame_ids(plan, stage)),
    )


def _attach_stage1_swatches(records: Path, work: Path, inputs: SessionInputs) -> None:
    repo = _repo_of(records)
    swatch_file, key_file = records / SWATCHES, records / TOKEN_KEY
    require_sealed(repo, swatch_file)
    require_sealed(repo, key_file)
    _read_record(swatch_file)
    swatches = load_sealed_swatches(swatch_file)
    tokens = _read_record(key_file)["tokens"]
    inputs.tokens = {rid: tokens[rid] for rid in inputs.recordings}
    inputs.swatches = {tokens[rid]: swatches[tokens[rid]] for rid in inputs.recordings}
    for rid, assets in list(inputs.recordings.items()):
        record = inputs.swatches[tokens[rid]]
        if record["status"] != "valid":
            raise MalformedInput(f"Stage 2 recording {rid} has no valid swatch pair")
        ids = sorted({px["frame_id"] for px in record["pixels"]})
        inputs.recordings[rid] = RecordingAssets(
            assets.selected, assets.cues, _assets(records, work, rid, ids)
        )


def _repo_of(records: Path) -> Path:
    for parent in records.resolve().parents:
        if (parent / ".git").exists():
            return parent
    raise MalformedInput(f"{records} is not inside a git repository")


def stage_pairs(records: Path, stage: int) -> dict[str, tuple[str | None, str | None]]:
    """D_s pairs with both recorded copies, after the labels → keys seal order."""
    repo = _repo_of(records)
    labels_file = records / labels_name(stage)
    require_strictly_after(repo, labels_file, records / queue_key_name(stage))
    if stage == STAGE_1:
        require_strictly_after(repo, labels_file, records / TOKEN_KEY)
    else:
        require_sealed(repo, records / TOKEN_KEY)
    require_sealed(repo, records / queue_name(stage))
    require_sealed(repo, records / SWATCHES)
    require_sealed(repo, points_path(records, stage))
    require_sealed(repo, records / f"duplicates_stage{stage}.json")
    manifest = _read_record(points_path(records, stage))
    _require_corpus_recordings(manifest["recordings"])
    dups = _read_record(records / f"duplicates_stage{stage}.json")["duplicate_ids"]
    swatches = _read_record(records / SWATCHES)["swatches"]
    tokens = _read_record(records / TOKEN_KEY)["tokens"]
    valid = [
        rid
        for rid in manifest["recordings"]
        if swatches.get(tokens[rid], {}).get("status") == "valid"
    ]
    point_recording = {p["point_id"]: p["recording_id"] for p in manifest["points"]}
    members = population(dups, point_recording, valid)
    queue = _read_record(records / queue_name(stage))["items"]
    refs = _read_record(records / queue_key_name(stage))["point_refs"]
    verify_queue_duplicates(members, queue, refs)
    labels = {e["item_id"]: e["label"] for e in _read_record(labels_file)["labels"]}
    return pair_labels(members, queue, labels, refs)


def _read_record(path: Path) -> Any:
    """Corpus readers never consume a record carrying the pilot scope."""
    payload = read_json(path)
    if isinstance(payload, dict) and payload.get("scope") == SCOPE_PILOT:
        raise IsolationViolation(f"pilot record supplied to corpus path: {path.name}")
    return payload


def _require_corpus_recordings(recordings: Sequence[str]) -> None:
    """Corpus operations accept only the correctness-applicable recordings."""
    outside = [rid for rid in recordings if rid not in CORRECTNESS_APPLICABLE]
    if outside:
        raise IsolationViolation(f"recording outside the corpus: {outside}")


def run_gate(records: Path, stage: int, tool: Mapping[str, Any]) -> dict[str, Any]:
    """Compute and write the stage's gate (Stage 2 pools D_1 ∪ D_2)."""
    pairs = stage_pairs(records, STAGE_1)
    if stage == STAGE_2:
        pairs = pooled_pairs(pairs, stage_pairs(records, STAGE_2))
    result = evaluate_gate(pairs).to_dict()
    payload = {"stage": stage, "tool": dict(tool), "pooled": stage == STAGE_2, **result}
    write_json(gate_path(records, stage), payload)
    return payload
