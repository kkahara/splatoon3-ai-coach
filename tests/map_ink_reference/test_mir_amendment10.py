"""Synthetic-only tests for Amendment 10 corpus capacity and recovery."""

from __future__ import annotations

import hashlib
from pathlib import Path

import cv2
import pytest
from map_ink_reference import corpus_capacity, execution, pipeline_unblinded, seal
from map_ink_reference.constants import (
    CORRECTNESS_APPLICABLE,
    PREREGISTRATION_CHAIN,
    PREREGISTRATION_PATH,
)
from map_ink_reference.errors import MalformedInput, SealViolation
from map_ink_reference.io_utils import (
    canonical_json,
    read_json,
    sha256_array,
    sha256_file,
    write_json,
)
from map_ink_reference.pilot2_addendum import addendum_path
from map_ink_reference.pilot2_pipeline import Pilot2Scope
from map_ink_reference.pins import MediaPins
from map_ink_reference.pipeline_blind import run_extraction, write_plan
from map_ink_reference.plan import build_frame_plan, stage_frame_ids
from map_ink_reference.stage1_recovery import (
    RECOVERY_RECORDINGS,
    verify_existing_outputs,
)
from map_ink_reference.verdicts import (
    INCONCLUSIVE,
    NOT_EVALUABLE,
    PRESERVED,
    REGRESSED,
    verdict_4b,
)
from mir_amendment7 import CHAIN_END, TOOL_REL, Workspace, build_repaired
from mir_helpers import ink_image, interval_specs, manifest_from_specs
from test_mir_amendment7 import COMMAND_ARGS

C10 = "137e26ac6f4262f45f0ac305c7f5fd015a05760c"
C10_DIGEST = "d50ce5e06dfff05fe7ef940c07e389fe0b64a43726bfad30effa5565bdb4c8e2"
TOOL = {"tool_commit": "a" * 40}


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_chain_entry_7_is_exact_and_preserves_first_six() -> None:
    repair9 = (
        ("amendment4", "570f188c3a2322c20065b095d740a6ad2538c57a"),
        ("amendment5", "ca4e08821ca9c80f45125c168fe3caf3af536a3e"),
        ("amendment6", "85d1104c1c969d3c9bdf7b179dda675d9c7748d5"),
        ("amendment7", "0a62f699894db5296b43102f1e77f7240a6f10de"),
        ("amendment8", "04caeda69ccca22941800bd0ff4195df0aae813b"),
        ("amendment9", "736b42a19e326de73e4bd418fdb7e4fcd1a4bf8b"),
    )
    assert (
        tuple((name, commit) for name, commit, _ in PREREGISTRATION_CHAIN[:6]) == repair9
    )
    assert PREREGISTRATION_CHAIN[6] == ("amendment10", C10, C10_DIGEST)
    assert len(PREREGISTRATION_CHAIN) == 7


def _seal_synthetic_c10(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    """Create C10 directly after synthetic H6 and authorize its chain entry."""
    scope = Pilot2Scope.from_repo(workspace.repo)
    h6_path = addendum_path(scope)
    h6_path.parent.mkdir(parents=True, exist_ok=True)
    h6_path.write_text('{"synthetic": true}\n')
    workspace.commit([h6_path], "H6")
    prereg = workspace.repo / PREREGISTRATION_PATH
    prereg.write_text(prereg.read_text() + "## synthetic Amendment 10\n")
    c10 = workspace.commit([prereg], "C10")
    digest10 = sha256_file(prereg)
    monkeypatch.setattr(
        seal,
        "PREREGISTRATION_CHAIN",
        (
            ("amendment7", workspace.c7, workspace.digest),
            ("amendment8", workspace.c8, workspace.digest8),
            ("amendment9", workspace.c9, workspace.digest9),
            ("amendment10", c10, digest10),
        ),
    )
    return c10, digest10


def _commit_synthetic_repair10(workspace: Workspace, c10: str, digest10: str) -> str:
    """Commit the minimum synthetic Repair 10 shape."""
    constants = workspace.repo / TOOL_REL / "constants.py"
    entry = f'    ("amendment10", "{c10}", "{digest10}"),\n'
    constants.write_text(constants.read_text().replace(CHAIN_END, entry + CHAIN_END))
    paths = [constants]
    for name in ("corpus_capacity.py", "stage1_recovery.py"):
        path = workspace.repo / TOOL_REL / name
        path.write_text(f'"""Synthetic {name}."""\n')
        paths.append(path)
    return workspace.commit(paths, "Repair 10")


def test_c10_stops_every_command_then_exactly_four_repairs_are_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = build_repaired(tmp_path / "build", monkeypatch)
    workspace.patch(monkeypatch)
    c10, digest10 = _seal_synthetic_c10(workspace, monkeypatch)
    for command in COMMAND_ARGS:
        with pytest.raises(SealViolation, match="repair 10"):
            execution.guard(workspace.repo, command)
    repair10 = _commit_synthetic_repair10(workspace, c10, digest10)
    assert execution.verify_repairs(workspace.repo) == [
        workspace.repair7,
        workspace.repair8,
        workspace.repair9,
        repair10,
    ]
    assert execution.verify_tool_identity(workspace.repo) == repair10
    extra = workspace.repo / "tests/map_ink_reference/extra_repair.py"
    extra.write_text("# unauthorized fifth protected commit\n")
    workspace.commit([extra], "extra protected commit")
    with pytest.raises(SealViolation, match="exactly four"):
        execution.verify_repairs(workspace.repo)


def test_seven_interval_stage1_uses_each_interval_once() -> None:
    specs = interval_specs([2, 4, 1, 1, 3, 5, 6])
    plan = build_frame_plan("r", manifest_from_specs(specs), manifest_sha256="x")
    assert plan["stage1_interval_indices"] == [0, 1, 4, 5, 6, 2, 3]
    assert len(stage_frame_ids(plan, 1)) == 7
    assert len(set(stage_frame_ids(plan, 1))) == 7
    assert plan["stage2_interval_indices"] == []
    assert corpus_capacity.plan_capacities(plan) == (7, 7, 0)


def test_eight_plus_interval_plan_matches_repair9_golden_bytes() -> None:
    plan = build_frame_plan(
        "synthetic",
        manifest_from_specs(interval_specs([4, 2, 2, 3, 2, 6, 2, 1, 1, 1, 1])),
        manifest_sha256="x",
    )
    digest = hashlib.sha256(canonical_json(plan).encode()).hexdigest()
    assert digest == "9bb49e4602b0812da705e6231cc024613a7295d4353b0706378a16e9db8c958d"


@pytest.mark.parametrize(
    ("lengths", "expected"),
    [
        ([4, 2, 2, 3, 2, 6, 2, 1, 1, 1, 1], (11, 8, 3)),
        ([5, 3, 2, 2, 2, 2] + [1] * 14, (20, 8, 8)),
        ([7, 2, 4, 2, 3, 3, 3] + [1] * 7, (14, 8, 6)),
        ([2, 4, 3, 5, 6, 1, 1], (7, 7, 0)),
        ([3, 2, 2, 5, 6, 3, 8, 7, 3, 4, 9, 6, 2, 8] + [1] * 7, (21, 8, 8)),
    ],
)
def test_capacity_shapes(lengths: list[int], expected: tuple[int, int, int]) -> None:
    plan = build_frame_plan(
        "r", manifest_from_specs(interval_specs(lengths)), manifest_sha256="x"
    )
    assert corpus_capacity.plan_capacities(plan) == expected


def test_synthetic_precheck_matches_frozen_table_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lengths = {
        "18-18-00": [4, 2, 2, 3, 2, 6, 2, 1, 1, 1, 1],
        "20-33-31": [5, 3, 2, 2, 2, 2] + [1] * 14,
        "22-31-53": [7, 2, 4, 2, 3, 3, 3] + [1] * 7,
        "22-37-47": [2, 4, 3, 5, 6, 1, 1],
        "22-57-30": [3, 2, 2, 5, 6, 3, 8, 7, 3, 4, 9, 6, 2, 8] + [1] * 7,
    }
    allowed: set[Path] = set()
    for rid, shape in lengths.items():
        path = tmp_path / "manifests" / f"{rid}.json"
        write_json(path, manifest_from_specs(interval_specs(shape)))
        pin = MediaPins(rid, "v" * 64, sha256_file(path))
        monkeypatch.setitem(corpus_capacity.MEDIA_PINS, rid, pin)
        monkeypatch.setattr(
            type(pin),
            "vision_manifest_path",
            property(lambda self: f"manifests/{self.recording_id}.json"),
        )
        allowed.add(path)
    monkeypatch.setattr(
        corpus_capacity, "load_corpus_blind_input", lambda repo, records: object()
    )
    original = corpus_capacity.read_json

    def restricted_read(path: Path):
        assert path in allowed
        return original(path)

    monkeypatch.setattr(corpus_capacity, "read_json", restricted_read)
    before = _tree_bytes(tmp_path)
    rows = corpus_capacity.run_precheck(tmp_path, tmp_path / "records")
    assert _tree_bytes(tmp_path) == before
    assert [
        (row["interval_count"], row["k1"], row["stage2_capacity"]) for row in rows
    ] == list(corpus_capacity.FROZEN_CAPACITIES.values())


def test_precheck_fails_closed_below_judgeability_minimum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = CORRECTNESS_APPLICABLE[0]
    path = tmp_path / "manifest.json"
    write_json(path, manifest_from_specs(interval_specs([1] * 5)))
    pin = MediaPins(rid, "v" * 64, sha256_file(path))
    monkeypatch.setitem(corpus_capacity.MEDIA_PINS, rid, pin)
    monkeypatch.setitem(corpus_capacity.FROZEN_CAPACITIES, rid, (5, 5, 0))
    monkeypatch.setattr(
        type(pin), "vision_manifest_path", property(lambda self: "manifest.json")
    )
    with pytest.raises(MalformedInput, match=f"recording={rid} passed=false"):
        corpus_capacity.precheck_recording(tmp_path, rid)


def _write_plan_file(records: Path, rid: str, lengths: list[int]) -> None:
    plan = build_frame_plan(
        rid, manifest_from_specs(interval_specs(lengths)), manifest_sha256="x"
    )
    write_json(records / "frame_plans" / f"{rid}.json", {**plan, "tool": TOOL})


def test_trigger_excludes_capacity_zero_and_is_absent_when_empty(
    tmp_path: Path,
) -> None:
    records = tmp_path / "records"
    _write_plan_file(records, "18-18-00", [2] * 9)
    _write_plan_file(records, "22-37-47", [2, 4, 3, 5, 6, 1, 1])
    results = [
        {"recording_id": "18-18-00", "verdict": INCONCLUSIVE},
        {"recording_id": "22-37-47", "verdict": INCONCLUSIVE},
    ]
    assert pipeline_unblinded.capacity_exhausted_ids(records, results) == ["22-37-47"]
    assert pipeline_unblinded.stage2_trigger_ids(records, results) == ["18-18-00"]
    pipeline_unblinded._write_trigger(records, results, "0" * 64)
    assert read_json(records / "stage2_trigger.json")["recordings"] == ["18-18-00"]
    (records / "stage2_trigger.json").unlink()
    pipeline_unblinded._write_trigger(records, results[1:], "0" * 64)
    assert not (records / "stage2_trigger.json").exists()


def test_capacity_exhausted_four_preserved_is_pass() -> None:
    stage1 = {f"r{i}": PRESERVED for i in range(4)} | {"x": INCONCLUSIVE}
    assert (
        verdict_4b(
            stage1,
            gate1_passed=True,
            n_applicable=5,
            capacity_exhausted={"x"},
        )
        == "PASS"
    )


def test_capacity_exhausted_4b_fail_closed_combinations() -> None:
    assert (
        verdict_4b(
            {
                "a": PRESERVED,
                "b": PRESERVED,
                "c": PRESERVED,
                "d": NOT_EVALUABLE,
                "x": INCONCLUSIVE,
            },
            gate1_passed=True,
            n_applicable=5,
            capacity_exhausted={"x"},
        )
        == NOT_EVALUABLE
    )
    assert (
        verdict_4b(
            {
                "a": PRESERVED,
                "b": PRESERVED,
                "c": PRESERVED,
                "d": PRESERVED,
                "x": NOT_EVALUABLE,
            },
            gate1_passed=True,
            n_applicable=5,
        )
        == "PASS"
    )
    assert (
        verdict_4b(
            {
                "a": PRESERVED,
                "b": PRESERVED,
                "c": PRESERVED,
                "d": INCONCLUSIVE,
                "x": INCONCLUSIVE,
            },
            gate1_passed=True,
            gate2_passed=False,
            n_applicable=5,
            capacity_exhausted={"x"},
        )
        == NOT_EVALUABLE
    )
    assert (
        verdict_4b(
            {
                "a": REGRESSED,
                "b": PRESERVED,
                "c": PRESERVED,
                "d": PRESERVED,
                "x": INCONCLUSIVE,
            },
            gate1_passed=True,
            n_applicable=5,
            capacity_exhausted={"x"},
        )
        == "FAIL"
    )
    assert (
        verdict_4b(
            {
                "a": PRESERVED,
                "b": PRESERVED,
                "c": PRESERVED,
                "d": PRESERVED,
                "x": INCONCLUSIVE,
            },
            gate1_passed=True,
            stage2={"x": INCONCLUSIVE},
            gate2_passed=True,
            n_applicable=5,
        )
        == NOT_EVALUABLE
    )
    assert (
        verdict_4b(
            {
                "a": PRESERVED,
                "b": PRESERVED,
                "c": PRESERVED,
                "d": PRESERVED,
                "x": INCONCLUSIVE,
            },
            gate1_passed=True,
            gate2_passed=False,
            n_applicable=5,
        )
        == NOT_EVALUABLE
    )


def _synthetic_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    records, work = tmp_path / "records", tmp_path / "work"
    for index, rid in enumerate(RECOVERY_RECORDINGS):
        manifest = manifest_from_specs(interval_specs([2] * 10), video_identity=rid)
        manifest_path = tmp_path / "manifests" / f"{rid}.json"
        digest = write_json(manifest_path, manifest)
        pin = MediaPins(rid, f"{index:064x}", digest)
        monkeypatch.setitem(
            __import__(
                "map_ink_reference.stage1_recovery", fromlist=["MEDIA_PINS"]
            ).MEDIA_PINS,
            rid,
            pin,
        )
        monkeypatch.setattr(
            type(pin),
            "vision_manifest_path",
            property(lambda self: f"manifests/{self.recording_id}.json"),
        )
        plan = build_frame_plan(rid, manifest, manifest_sha256=digest)
        write_json(records / "frame_plans" / f"{rid}.json", {**plan, "tool": TOOL})
        frames = []
        for number, frame in enumerate(plan["frames"]):
            image = ink_image(index * 100 + number)
            png = work / "frames" / rid / f"{frame['frame_id']}.png"
            png.parent.mkdir(parents=True, exist_ok=True)
            assert cv2.imwrite(str(png), image)
            frames.append(
                {
                    "frame_id": frame["frame_id"],
                    "source_pts": frame["source_pts"],
                    "source_frame_index": frame["source_frame_index"],
                    "png": png.name,
                    "png_sha256": sha256_file(png),
                    "decoded_array_sha256": sha256_array(image),
                }
            )
        write_json(
            records / "extraction" / f"{rid}.json",
            {
                "recording_id": rid,
                "video_sha256": pin.video_sha256,
                "video_identity": plan["video_identity"],
                "frames": frames,
                "tool": TOOL,
            },
        )
    return records, work


def test_recovery_verifier_accepts_exact_products_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    records, work = _synthetic_recovery(tmp_path, monkeypatch)
    before = _tree_bytes(tmp_path)
    rows = verify_existing_outputs(tmp_path, records, work)
    assert _tree_bytes(tmp_path) == before
    assert [row["recording_id"] for row in rows] == list(RECOVERY_RECORDINGS)
    assert all(row["passed"] and row["planned_png_count"] for row in rows)
    assert all(
        check["passed"]
        and set(check) <= {"check", "frame_id", "passed"}
        and check["check"]
        in {
            "manifest_pin",
            "plan_equality",
            "extraction_video",
            "frame_list",
            "png_membership",
            "png_sha256",
            "decoded_array_sha256",
        }
        for row in rows
        for check in row["checks"]
    )
    assert all(
        set(row)
        == {
            "recording_id",
            "plan_sha256",
            "recomputed_plan_sha256",
            "extraction_sha256",
            "planned_png_count",
            "verified_png_count",
            "checks",
            "plan_tool_commit",
            "extraction_tool_commit",
            "passed",
        }
        for row in rows
    )


@pytest.mark.parametrize(
    ("mutation", "check"),
    [
        ("altered_plan", "plan_equality"),
        ("different_selection", "plan_equality"),
        ("missing_png", "png_membership"),
        ("extra_png", "png_membership"),
        ("modified_png", "png_sha256"),
        ("wrong_video_hash", "extraction_video"),
        ("wrong_video_identity", "extraction_video"),
        ("wrong_frame_set", "frame_list"),
        ("missing_record", "input"),
    ],
)
def test_recovery_verifier_rejects_synthetic_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    check: str,
) -> None:
    records, work = _synthetic_recovery(tmp_path, monkeypatch)
    rid = RECOVERY_RECORDINGS[0]
    plan_path = records / "frame_plans" / f"{rid}.json"
    extraction_path = records / "extraction" / f"{rid}.json"
    plan, extraction = read_json(plan_path), read_json(extraction_path)
    png = work / "frames" / rid / extraction["frames"][0]["png"]
    if mutation == "altered_plan":
        plan["recording_id"] = "altered"
        write_json(plan_path, plan)
    elif mutation == "different_selection":
        plan["stage1_interval_indices"].reverse()
        write_json(plan_path, plan)
    elif mutation == "missing_png":
        png.unlink()
    elif mutation == "extra_png":
        (png.parent / "extra.png").write_bytes(b"x")
    elif mutation == "modified_png":
        png.write_bytes(b"not a png")
    elif mutation == "wrong_video_hash":
        extraction["video_sha256"] = "f" * 64
        write_json(extraction_path, extraction)
    elif mutation == "wrong_video_identity":
        extraction["video_identity"] = "wrong"
        write_json(extraction_path, extraction)
    elif mutation == "wrong_frame_set":
        extraction["frames"].pop()
        write_json(extraction_path, extraction)
    else:
        extraction_path.unlink()
    before = _tree_bytes(tmp_path)
    with pytest.raises(MalformedInput) as caught:
        verify_existing_outputs(tmp_path, records, work)
    assert _tree_bytes(tmp_path) == before
    message = str(caught.value)
    assert f"check={check}" in message
    assert "passed=false" in message
    assert not any(
        word in message.lower()
        for word in (
            "pixel",
            "array",
            "image",
            "colour",
            "histogram",
            "thumbnail",
            "crop",
        )
    )


def test_corpus_plan_and_extract_refuse_overwrite_before_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = CORRECTNESS_APPLICABLE[0]
    records, work = tmp_path / "records", tmp_path / "work"
    monkeypatch.setattr(
        "map_ink_reference.pipeline_blind.require_step2a_sealed",
        lambda repo, records: None,
    )
    monkeypatch.setattr(
        "map_ink_reference.pipeline_blind._repo_of", lambda records: tmp_path
    )
    existing_plan = records / "frame_plans" / f"{rid}.json"
    existing_plan.parent.mkdir(parents=True)
    existing_plan.write_bytes(b"sentinel-plan")
    with pytest.raises(MalformedInput, match="already exists"):
        write_plan(tmp_path, records, rid, TOOL)
    assert existing_plan.read_bytes() == b"sentinel-plan"

    existing_extraction = records / "extraction" / f"{rid}.json"
    existing_extraction.parent.mkdir(parents=True)
    existing_extraction.write_bytes(b"sentinel-extraction")
    with pytest.raises(MalformedInput, match="already exists"):
        run_extraction(records, work, rid, tmp_path / "unused.avi", TOOL)
    assert existing_extraction.read_bytes() == b"sentinel-extraction"
    existing_extraction.unlink()
    frame_dir = work / "frames" / rid
    frame_dir.mkdir(parents=True)
    sentinel = frame_dir / "sentinel"
    sentinel.write_bytes(b"untouched")
    with pytest.raises(MalformedInput, match="already exists"):
        run_extraction(records, work, rid, tmp_path / "unused.avi", TOOL)
    assert sentinel.read_bytes() == b"untouched"
