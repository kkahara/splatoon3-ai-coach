"""Synthetic Amendment 9 replacement-pilot and firewall tests."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace

import map_ink_reference.cli as cli
import map_ink_reference.execution as execution
import map_ink_reference.extraction as frozen_extraction
import map_ink_reference.gate as frozen_gate
import map_ink_reference.pilot2_pipeline as pilot2_pipeline
import map_ink_reference.pilot2_sources as pilot2_sources
import map_ink_reference.points as frozen_points
import map_ink_reference.records as frozen_records
import pytest
from loguru import logger
from map_ink_reference.constants import (
    PREREGISTRATION_CHAIN,
    SCOPE_PILOT2,
)
from map_ink_reference.errors import MalformedInput, ProcedureOrderError, SealViolation
from map_ink_reference.io_utils import read_json, sha256_file, write_json
from map_ink_reference.pilot import PilotSource
from map_ink_reference.pilot2_addendum import (
    verify_sealed_addendum,
    write_addendum,
)
from map_ink_reference.pilot2_pipeline import (
    GATE,
    H3_NAMES,
    H4_NAMES,
    Pilot2Scope,
    export,
    extract_plans,
    gate,
    pilot2_groups,
    plan_path,
    read_pilot2_record,
    selection_path,
    session_dir,
    session_inputs,
    write_plans,
    write_points,
    write_selection,
)
from map_ink_reference.pilot_pipeline import (
    G3_NAMES,
    G4_NAMES,
)
from map_ink_reference.pilot_pipeline import (
    GATE as PILOT1_GATE,
)
from map_ink_reference.pilot_pipeline import (
    export as export_pilot1,
)
from map_ink_reference.pilot_pipeline import gate as pilot1_gate
from map_ink_reference.plan import build_frame_plan
from map_ink_reference.session import AnnotationSession
from mir_amendment7 import TOOL, Workspace, build_checkpoints
from mir_helpers import Counter, commit
from test_mir_session import COLOURS, find_pixel

REAL_REPO = Path(__file__).resolve().parents[2]
C9 = "736b42a19e326de73e4bd418fdb7e4fcd1a4bf8b"
C9_DIGEST = "18172a384fb0efb973c2696957af57bd28fd24dd1cc30126eb6b0d0605013b71"


@pytest.fixture(scope="module")
def checkpoints9(tmp_path_factory) -> dict[str, Workspace]:
    return build_checkpoints(tmp_path_factory.mktemp("amendment9"))


@pytest.fixture
def at9(checkpoints9, tmp_path: Path, monkeypatch) -> Workspace:
    ws = checkpoints9["annotated"].copy_to(tmp_path / "repo")
    ws.patch(monkeypatch)
    return ws


def _annotate_pilot2(
    scope: Pilot2Scope,
    predecessor: str,
    labels: tuple[str, ...] = ("ink_A",),
) -> None:
    counter = Counter(900)
    session = AnnotationSession(
        session_inputs(scope, predecessor=predecessor),
        session_dir(scope),
        seed_source=counter.seed,
        id_source=counter.new_id,
    )
    submitted = 0
    while (task := session.current_task())["kind"] != "complete":
        if task["kind"] == "swatch":
            rid = {token: rid for rid, token in session.state["tokens"].items()}[
                task["token"]
            ]
            frame = session.inputs.recordings[rid].selected[0]
            picks = [
                {"frame_index": 0, "x": x, "y": y}
                for x, y in (find_pixel(frame.png_path, colour) for colour in COLOURS)
            ]
            session.submit_swatch(task["token"], picks)
        elif task["kind"] == "team":
            phase = "start" if session.state["phase"] == "team_start" else "end"
            left = session.state["team"][task["token"]][phase]["left"]
            session.submit_team(task["token"], "left" if left == "A" else "right")
        else:
            session.submit_label(task["item_id"], labels[submitted % len(labels)])
            submitted += 1


def _repair9(ws: Workspace) -> str:
    return execution.verify_repairs(ws.repo)[2]


def _run_to_h3(ws: Workspace, labels: tuple[str, ...] = ("ink_A",)) -> tuple[str, str]:
    export_pilot1(ws.scope, TOOL)
    ws.commit([ws.scope.records / name for name in G3_NAMES], "G3")
    pilot2 = Pilot2Scope.from_repo(ws.repo)
    write_selection(pilot2, TOOL)
    ws.commit([selection_path(pilot2)], "H1")
    write_plans(pilot2, TOOL, predecessor=_repair9(ws))
    extract_plans(pilot2, TOOL, predecessor=_repair9(ws))
    write_points(pilot2, TOOL, predecessor=_repair9(ws))
    ws.commit(pilot2_groups(pilot2, read_json(selection_path(pilot2))["chosen"])[1], "H2")
    _annotate_pilot2(pilot2, _repair9(ws), labels)
    export(pilot2, TOOL, predecessor=_repair9(ws))
    h3 = ws.commit([pilot2.records / name for name in H3_NAMES], "H3")
    return h3, _repair9(ws)


def _run_corridor(
    ws: Workspace,
    monkeypatch,
    labels: tuple[str, ...] = ("ink_A",),
) -> tuple[str, str]:
    h3, repair9 = _run_to_h3(ws, labels)
    g4 = ws.commit([ws.scope.records / name for name in G4_NAMES], "G4")
    assert g4 != h3
    execution.guard(ws.repo, "pilot1-close-gate")
    cli.cmd_pilot1_close_gate(SimpleNamespace())
    ws.commit([ws.scope.records / PILOT1_GATE], "G5")
    pilot2 = Pilot2Scope.from_repo(ws.repo)
    ws.commit([pilot2.records / name for name in H4_NAMES], "H4")
    original = pilot2_pipeline.read_json

    def deny_pilot1_outcomes(path: Path):
        forbidden = {PILOT1_GATE, *G3_NAMES, *G4_NAMES}
        assert not (path.is_relative_to(ws.scope.records) and path.name in forbidden)
        return original(path)

    monkeypatch.setattr(pilot2_pipeline, "read_json", deny_pilot1_outcomes)
    original_guard_read = execution.read_json

    def deny_guard_outcomes(path: Path):
        forbidden = {PILOT1_GATE, *G3_NAMES, *G4_NAMES}
        assert not (path.is_relative_to(ws.scope.records) and path.name in forbidden)
        return original_guard_read(path)

    monkeypatch.setattr(execution, "read_json", deny_guard_outcomes)
    execution.guard(ws.repo, "pilot2-gate")
    gate(pilot2, TOOL, predecessor=repair9)
    h5 = ws.commit([pilot2.records / GATE], "H5")
    return h5, repair9


def test_chain_entry_6_is_exact() -> None:
    assert PREREGISTRATION_CHAIN[5] == ("amendment9", C9, C9_DIGEST)
    assert len(PREREGISTRATION_CHAIN) == 7


def test_c9_before_repair9_stops(at9: Workspace) -> None:
    from mir_helpers import git

    git(at9.repo, "reset", "-q", "--hard", at9.c9)
    with pytest.raises(SealViolation, match="repair 9"):
        execution.verify_tool_identity(at9.repo)


def test_repair9_preserves_forbidden_scientific_files() -> None:
    repair8 = "0ee675d47abc1361f583362217a02a9922647e24"
    repair9 = "d428730432e01823ce70bf85ab738d7e3cde571d"
    paths = (
        "pilot_pipeline.py",
        "gate.py",
        "points.py",
        "queueing.py",
        "session.py",
        "server.py",
        "render.py",
        "records.py",
        "plan.py",
        "extraction.py",
        "domain.py",
        "population.py",
        "trigger.py",
        "stage_identity.py",
        "pins.py",
        "pilot.py",
        "pipeline_unblinded.py",
        "unblinded_pins.py",
    )
    for name in paths:
        rel = f"tools/diagnostics/map_ink_reference/{name}"
        before = execution.file_at_commit(REAL_REPO, repair8, rel)
        assert execution.file_at_commit(REAL_REPO, repair9, rel) == before


def test_existing_pilot_gate_body_is_byte_identical() -> None:
    rel = "tools/diagnostics/map_ink_reference/cli.py"
    before = ast.parse(
        execution.file_at_commit(
            REAL_REPO, "0ee675d47abc1361f583362217a02a9922647e24", rel
        )
    )
    after = ast.parse((REAL_REPO / rel).read_text())

    def body(tree):
        node = next(
            item
            for item in tree.body
            if isinstance(item, ast.FunctionDef) and item.name == "cmd_pilot_gate"
        )
        return hashlib.sha256(ast.dump(node).encode()).hexdigest()

    assert body(after) == body(before)


def test_pilot2_reuses_frozen_stage1_functions() -> None:
    assert pilot2_pipeline.extract_recording is frozen_extraction.extract_recording
    assert pilot2_pipeline.sample_stage_points is frozen_points.sample_stage_points
    assert pilot2_pipeline.duplicate_point_ids is frozen_points.duplicate_point_ids
    assert pilot2_pipeline.pilot_duplicate_count is frozen_points.pilot_duplicate_count
    assert pilot2_pipeline.export_session_records is frozen_records.export_session_records
    assert pilot2_pipeline.population is frozen_gate.population
    assert pilot2_pipeline.verify_queue_duplicates is frozen_gate.verify_queue_duplicates
    assert pilot2_pipeline.pair_labels is frozen_gate.pair_labels
    assert pilot2_pipeline.evaluate_gate is frozen_gate.evaluate_gate


def test_close_gate_indexes_no_outcome_and_calls_gate_once() -> None:
    tree = ast.parse(
        (REAL_REPO / "tools/diagnostics/map_ink_reference/cli.py").read_text()
    )
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_pilot1_close_gate"
    )
    assert not any(isinstance(node, ast.Subscript) for node in ast.walk(function))
    calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "gate"
    ]
    assert len(calls) == 1
    assert "pilot gate record written" in ast.unparse(function)


def test_pilot1_gate_payload_is_unchanged_apart_from_tool(
    at9: Workspace, tmp_path: Path
) -> None:
    payloads = []
    for index in range(2):
        ws = at9.copy_to(tmp_path / f"gate-{index}")
        export_pilot1(ws.scope, TOOL)
        ws.commit([ws.scope.records / name for name in G3_NAMES], "G3")
        ws.commit([ws.scope.records / name for name in G4_NAMES], "G4")
        tool = {**TOOL, "commit": str(index) * 40}
        payloads.append(pilot1_gate(ws.scope, tool))
    assert payloads[0]["tool"] != payloads[1]["tool"]
    assert {k: v for k, v in payloads[0].items() if k != "tool"} == {
        k: v for k, v in payloads[1].items() if k != "tool"
    }


def test_pilot2_selection_is_fresh_and_two_smallest(at9: Workspace) -> None:
    scope = Pilot2Scope.from_repo(at9.repo)
    write_selection(scope, TOOL)
    payload = read_pilot2_record(selection_path(scope))
    pilot1 = read_json(at9.scope.records / "pilot_selection.json")
    selected1 = {
        row["video_identity"]
        for row in pilot1["declared"]
        if row["recording"] in pilot1["chosen"]
    }
    eligible = [
        row
        for row in pilot1["declared"]
        if row["eligible"] and row["video_identity"] not in selected1
    ]
    expected = [
        row["recording"]
        for row in sorted(eligible, key=lambda row: row["video_identity"])[:2]
    ]
    assert payload["chosen"] == expected
    assert payload["scope"] == SCOPE_PILOT2


def _source(name: str, value: int) -> PilotSource:
    digit = f"{value:064x}"
    return PilotSource(
        analysis_dir=f"analysis/{name}",
        video_path=f"videos/{name}.mkv",
        video_sha256=digit,
        video_identity=digit,
        authorization="development/pilot media; not evaluation, held-out or corpus media",
        declared_by="synthetic",
    )


def _row(source: PilotSource, eligible: bool) -> dict:
    return {
        "recording": source.recording,
        "video_identity": source.video_identity,
        "stage_id": "synth",
        "battle_mode_id": None,
        "eligible_intervals": 8 if eligible else 7,
        "eligible": eligible,
    }


def _patch_selection(
    monkeypatch, supplementary: tuple[list[PilotSource], list[dict], dict] | None
) -> None:
    base = [_source("base-a", 1), _source("base-b", 2), _source("base-c", 3)]
    rows = [_row(base[0], True), _row(base[1], True), _row(base[2], False)]
    entries = [
        {"recording": source.recording, "row": row}
        for source, row in zip(base, rows, strict=True)
    ]
    pilot1 = {
        "chosen": ["base-a", "base-b"],
        "declared": rows,
    }
    monkeypatch.setattr(
        pilot2_sources,
        "_pilot1_material",
        lambda scope: (base, entries, pilot1),
    )
    monkeypatch.setattr(
        pilot2_sources,
        "_bind",
        lambda sources, values: {
            source.recording: value for source, value in zip(sources, values, strict=True)
        },
    )
    monkeypatch.setattr(pilot2_sources, "_eligibility", lambda repo, entry: entry["row"])
    monkeypatch.setattr(
        pilot2_sources, "_sealed_supplementary", lambda scope: supplementary
    )
    monkeypatch.setattr(pilot2_sources, "corpus_exclusions", lambda repo: (set(), set()))
    monkeypatch.setattr(pilot2_sources, "check_declared_source", lambda *args: None)
    monkeypatch.setattr(pilot2_sources, "verify_source_video", lambda *args: None)


def test_base_shortfall_writes_no_h1(tmp_path: Path, monkeypatch) -> None:
    _patch_selection(monkeypatch, None)
    scope = Pilot2Scope.from_repo(tmp_path)
    with pytest.raises(ProcedureOrderError, match="fewer than two"):
        write_selection(scope, TOOL)
    assert not selection_path(scope).exists()


def test_one_five_source_fallback_is_deterministic(tmp_path: Path, monkeypatch) -> None:
    extra = [_source(f"extra-{index}", index + 10) for index in range(5)]
    rows = [_row(source, index != 4) for index, source in enumerate(extra)]
    entries = [
        {"recording": source.recording, "row": row}
        for source, row in zip(extra, rows, strict=True)
    ]
    _patch_selection(monkeypatch, (extra, entries, {"supplementary/a.json": "a" * 64}))
    payload = pilot2_sources.select_pilot2(Pilot2Scope.from_repo(tmp_path))
    assert payload["chosen"] == ["extra-0", "extra-1"]
    assert len(extra) == 5


def test_second_shortfall_stops(tmp_path: Path, monkeypatch) -> None:
    extra = [_source(f"extra-{index}", index + 10) for index in range(5)]
    rows = [_row(source, False) for source in extra]
    entries = [
        {"recording": source.recording, "row": row}
        for source, row in zip(extra, rows, strict=True)
    ]
    _patch_selection(monkeypatch, (extra, entries, {}))
    with pytest.raises(ProcedureOrderError, match="supplementary union"):
        pilot2_sources.select_pilot2(Pilot2Scope.from_repo(tmp_path))


def test_supplementary_preparation_enforces_closed_record_and_one_analysis(
    tmp_path: Path,
) -> None:
    pool_path = tmp_path / "pool.json"
    candidates = [
        {
            "candidate_id": f"candidate-{index}",
            "video_path": f"videos/candidate-{index}.mkv",
            "analysis_dir": f"analysis/candidate-{index}",
            "recorded_stage": "makomart",
            "recorded_on": "2026-10-26",
            "authorization": _source("authorization", index + 20).authorization,
        }
        for index in range(5)
    ]
    write_json(
        pool_path,
        {
            "schema": pilot2_sources.SUPPLEMENTARY_POOL_SCHEMA,
            "amendment5_commit": pilot2_sources.chain_entry("amendment5")[0],
            "declared_by": "synthetic",
            "candidates": candidates,
        },
    )
    pool = pilot2_sources._validate_pool(pool_path)
    digest = "a" * 64
    preparation = {
        "schema": pilot2_sources.SUPPLEMENTARY_PREPARATION_SCHEMA,
        "pool_sha256": sha256_file(pool_path),
        "code_commit": "b" * 40,
        "config_sha256": "c" * 64,
        "analyze_command": "s3-coach analyze <video_path> --out <analysis_dir>",
        "python_version": "synthetic",
        "candidates": [
            {
                "candidate_id": row["candidate_id"],
                "status": "prepared",
                "video_sha256": digest,
                "video_identity": f"{index + 1:064x}",
                "vision_manifest_sha256": digest,
                "manifest_video_identity": f"{index + 1:064x}",
                "attempts": [{"exit_status": 0}],
                "exclusion": None,
            }
            for index, row in enumerate(candidates)
        ],
    }
    preparation_path = tmp_path / "preparation.json"
    write_json(preparation_path, preparation)
    pilot2_sources._validate_preparation(preparation_path, pool_path, pool)

    preparation["candidates"][0]["attempts"].append({"exit_status": 0})
    write_json(preparation_path, preparation)
    with pytest.raises(MalformedInput, match="one final success"):
        pilot2_sources._validate_preparation(preparation_path, pool_path, pool)

    preparation["candidates"][0]["attempts"].pop()
    preparation["candidates"][0]["observed_label"] = "ink_A"
    write_json(preparation_path, preparation)
    with pytest.raises(MalformedInput, match="exactly the sealed keys"):
        pilot2_sources._validate_preparation(preparation_path, pool_path, pool)


def test_pilot2_plan_uses_frozen_lower_level_function(at9: Workspace) -> None:
    scope = Pilot2Scope.from_repo(at9.repo)
    write_selection(scope, TOOL)
    at9.commit([selection_path(scope)], "H1")
    write_plans(scope, TOOL, predecessor=_repair9(at9))
    rid = read_json(selection_path(scope))["chosen"][0]
    actual = read_pilot2_record(plan_path(scope, rid))
    manifest_path = at9.repo / "analysis" / rid / "vision_manifest.json"
    expected = build_frame_plan(
        rid,
        read_json(manifest_path),
        manifest_sha256=actual["vision_manifest_sha256"],
        purpose=actual["purpose"],
    )
    assert {
        key: value for key, value in actual.items() if key not in {"scope", "tool"}
    } == expected


def test_closed_corridor_blocks_every_other_command(at9: Workspace) -> None:
    _run_to_h3(at9)
    with pytest.raises(ProcedureOrderError, match="closed H3-to-H5"):
        execution.guard(at9.repo, "preconditions")
    with pytest.raises(ProcedureOrderError):
        execution.guard(at9.repo, "pilot2-gate")


def test_old_pilot_gate_always_stops(at9: Workspace) -> None:
    with pytest.raises(ProcedureOrderError, match="closed by Amendment 9"):
        execution.guard(at9.repo, "pilot-gate")


def test_report_stops_before_h5(at9: Workspace) -> None:
    with pytest.raises(ProcedureOrderError):
        execution.guard(at9.repo, "pilot1-gate-report")


def test_close_gate_stops_before_h3(at9: Workspace) -> None:
    with pytest.raises(ProcedureOrderError):
        execution.guard(at9.repo, "pilot1-close-gate")


def test_full_corridor_report_and_h6(at9: Workspace, monkeypatch) -> None:
    h5, repair9 = _run_corridor(at9, monkeypatch)
    assert at9.head() == h5
    execution.guard(at9.repo, "pilot1-gate-report")
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), format="{message}")
    try:
        cli.cmd_pilot1_gate_report(SimpleNamespace())
    finally:
        logger.remove(sink)
    report = "".join(messages)
    assert all(
        field in report
        for field in ("numerator=", "denominator=", "upper_bound=", "passed=")
    )
    assert not any(
        field in report
        for field in ("label", "mapping", "team answer", "disagreement", "recording")
    )
    scope = Pilot2Scope.from_repo(at9.repo)
    path = write_addendum(scope, predecessor=repair9)
    payload = read_json(path)
    assert payload["changes"] == []
    assert payload["interface_commit"] is None
    at9.commit([path], "H6")
    assert verify_sealed_addendum(scope, predecessor=repair9) == payload
    execution.guard(at9.repo, "plan")


def test_failing_h5_blocks_h6_and_corpus(at9: Workspace, monkeypatch) -> None:
    _, repair9 = _run_corridor(at9, monkeypatch, ("ink_A", "ink_B"))
    scope = Pilot2Scope.from_repo(at9.repo)
    assert read_pilot2_record(scope.records / GATE)["passed"] is False
    with pytest.raises(ProcedureOrderError, match="failing H5"):
        write_addendum(scope, predecessor=repair9)
    with pytest.raises((ProcedureOrderError, SealViolation)):
        execution.guard(at9.repo, "plan")


def test_extra_protected_commit_stops(at9: Workspace) -> None:
    path = at9.repo / "tests/map_ink_reference/extra.py"
    path.write_text("# extra\n")
    commit(at9.repo, [path], "unauthorized protected commit")
    with pytest.raises(SealViolation, match="exactly three"):
        execution.verify_tool_identity(at9.repo)


def test_instruction_block_is_static_and_present_on_all_stages() -> None:
    page = (REAL_REPO / "tools/diagnostics/map_ink_reference/annotate.html").read_text()
    assert page.count("${instructions()}") == 3
    assert (
        "You can determine that the centre pixel does not\n"
        "    depict map ink in either swatch colour."
    ) in page
    assert (
        "you cannot\n    determine which one of <code>ink_A</code>, <code>ink_B</code> or"
    ) in page
    assert "Only if those do not decide" in page
    instruction = page.split("const instructions=()=>", 1)[1].split(
        "async function api", 1
    )[0]
    for prohibited in ("walls", "terrain", "outlines", "edges", "markers", "text"):
        assert prohibited not in instruction.lower()
