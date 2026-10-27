"""Amendment 7 testing requirements 1–14 (synthetic fixtures only).

Neither the prepared pilot sources nor any corpus recording is used: every
repository, video, manifest and record here is written by the tests. The
real repository is read only for tool source at the sealed C7 commit
(corpus byte-identity) and the preregistration chain constants.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import map_ink_reference.cli as cli
import map_ink_reference.execution as execution
import map_ink_reference.pipeline_blind as blind
import map_ink_reference.seal as seal
import pytest
from map_ink_reference.cli import build_parser
from map_ink_reference.constants import (
    CORRECTNESS_APPLICABLE,
    PILOT_RECORDS_DIR,
    PILOT_SELECTION,
    PILOT_WORK_DIR,
    PREREGISTRATION_CHAIN,
    PURPOSE_PILOT,
    RECORDS_DIR,
    SCOPE_PILOT,
    STAGE_1,
    WORK_DIR,
)
from map_ink_reference.errors import (
    IsolationViolation,
    MalformedInput,
    PinMismatch,
    ProcedureOrderError,
    ReferenceProcedureError,
    SealViolation,
)
from map_ink_reference.extraction import extract_recording
from map_ink_reference.gate import evaluate_gate, pair_labels, population
from map_ink_reference.io_utils import read_json, sha256_file, write_json
from map_ink_reference.pilot import select_pilot
from map_ink_reference.pilot_addendum import (
    display_defaults,
    verify_crop_zoom,
    verify_sealed_addendum,
    write_addendum,
)
from map_ink_reference.pilot_pipeline import (
    DUPLICATES,
    G3_NAMES,
    G4_NAMES,
    GATE,
    POINTS,
    PilotScope,
    addendum_path,
    export,
    extract_plans,
    extraction_path,
    frames_dir,
    gate,
    pilot_groups,
    plan_path,
    selection_path,
    session_dir,
    session_inputs,
    write_plans,
    write_points,
    write_selection,
)
from map_ink_reference.pins import INTRO_LATCH_CODE_PINS
from map_ink_reference.plan import build_frame_plan, stage_frame_ids
from map_ink_reference.points import (
    StageFrame,
    duplicate_point_ids,
    pilot_duplicate_count,
    sample_stage_points,
)
from map_ink_reference.records import export_session_records
from map_ink_reference.stage_identity import StageIdentity, step2a_paths
from map_ink_reference.unblinded_pins import A1_IMPLEMENTATION_PINS as UNBLINDED_A1
from mir_amendment7 import (
    CONSTANTS_REL,
    SESSION_REL,
    TEST_REL,
    TOOL,
    TOOL_REL,
    Workspace,
    build_checkpoints,
    commit_repair8,
    commit_repair9,
    constants_source,
)
from mir_helpers import commit, git, init_repo
from mir_step2a import SYNTH_STAGE, SyntheticStep2a
from test_mir_isolation import _code_tokens, _local_closure

REAL_REPO = Path(__file__).resolve().parents[2]
C7 = "0a62f699894db5296b43102f1e77f7240a6f10de"
SUPERSEDED_BY_AMENDMENT9 = pytest.mark.skip(
    reason="Amendment 9 removes pilot-1 G6 and all interface commits"
)
COMMAND_ARGS = {
    "preconditions": [],
    "blind-inputs": ["--scope", "pilot"],
    "pilot-select": [],
    "pilot-plan": [],
    "pilot-extract": [],
    "pilot-points": [],
    "pilot-annotate": [],
    "pilot-export": [],
    "pilot-gate": [],
    "pilot-addendum": ["--none"],
    "pilot1-close-gate": [],
    "pilot1-gate-report": [],
    "pilot2-select": [],
    "pilot2-plan": [],
    "pilot2-extract": [],
    "pilot2-points": [],
    "pilot2-annotate": [],
    "pilot2-export": [],
    "pilot2-gate": [],
    "pilot2-addendum": [],
    "plan": ["--recording", CORRECTNESS_APPLICABLE[0]],
    "extract": ["--recording", CORRECTNESS_APPLICABLE[0], "--video", "v.mp4"],
    "points": ["--stage", "1"],
    "annotate": ["--stage", "1"],
    "export": ["--stage", "1"],
    "gate": ["--stage", "1"],
    "compare": ["--stage", "1"],
    "criterion-4a": [],
    "report": [],
    "corpus-capacity-precheck": [],
    "stage1-recovery-verify": [],
}
PILOT = sorted(c for c in COMMAND_ARGS if c.startswith(("pilot-", "pilot1-", "pilot2-")))
CORPUS = sorted(execution.CORPUS_COMMANDS)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def checkpoints(tmp_path_factory) -> dict[str, Workspace]:
    return build_checkpoints(tmp_path_factory.mktemp("amendment7"))


@pytest.fixture
def at(checkpoints, tmp_path: Path, monkeypatch) -> Callable[[str], Workspace]:
    """Independent, patched copy of a checkpoint."""
    count = iter(range(1000))

    def make(name: str) -> Workspace:
        workspace = checkpoints[name].copy_to(tmp_path / f"copy{next(count)}")
        workspace.patch(monkeypatch)
        return workspace

    return make


def _subcommands() -> dict:
    parser = build_parser()
    action = next(item for item in parser._actions if item.dest == "command")
    return dict(action.choices)


def _recorders(monkeypatch) -> list[str]:
    """Replace every command function; record which ones are dispatched."""
    called: list[str] = []
    parser = build_parser()
    for name, argv in COMMAND_ARGS.items():
        func = parser.parse_args([name, *argv]).func
        monkeypatch.setattr(cli, func.__name__, lambda args, n=name: called.append(n))
    return called


def _main(name: str) -> None:
    cli.main([name, *COMMAND_ARGS[name]])


def _stops(name: str, called: list[str]) -> None:
    with pytest.raises(ReferenceProcedureError):
        _main(name)
    assert name not in called


def _edit(ws: Workspace, edits: dict[str, str | None], message: str) -> str:
    """Commit text edits (``None`` deletes) and return the commit."""
    for rel, text in edits.items():
        if text is None:
            git(ws.repo, "rm", "-q", rel)
        else:
            ws.write_tool(rel, text)
            git(ws.repo, "add", "-f", rel)
    git(ws.repo, "commit", "-q", "-m", message)
    return ws.head()


def _strip(payload: dict, *keys: str) -> dict:
    return {k: v for k, v in payload.items() if k not in keys}


SESSION_TEXT = (Path(cli.__file__).parent / "session.py").read_text()
HANDLE_ENTRY = (
    '            "progress": [index, len(queue)],\n'
    '            "context": self._handle(\n'
    "                lambda: encode_png(\n"
    "                    context_view(self._load(asset), point.x, point.y, settings)\n"
    "                )\n"
    "            ),\n"
    "        }"
)
PROGRESS = '            "progress": [index, len(queue)],\n        }'


def _session(*replacements: tuple[str, str]) -> str:
    text = SESSION_TEXT
    for old, new in replacements:
        assert old in text
        text = text.replace(old, new, 1)
    return text


ADD_IMPORT = ("    point_view,\n", "    context_view,\n    point_view,\n")
APPEND_HANDLE = (PROGRESS, HANDLE_ENTRY)
RENDER_FIX = '"""Synthetic render."""\n\n\ndef context_view(*args):\n    return None\n'


def _append(entry: str) -> tuple[str, str]:
    return PROGRESS, PROGRESS.replace("        }", f"            {entry}\n        }}")


# --------------------------------------------------------------------------
# Requirement 1: pilot commands take no recording option; outside recordings
# --------------------------------------------------------------------------


def test_c7_is_the_fourth_authorized_chain_entry() -> None:
    assert PREREGISTRATION_CHAIN[3] == (
        "amendment7",
        C7,
        "26ed135efbfb2127523601447f0edb1f4cef132daff4d1afc1bfb902fe6fb5a8",
    )
    assert seal.chain_entry("amendment7") == PREREGISTRATION_CHAIN[3][1:]


def test_pilot_commands_have_only_sealed_options() -> None:
    commands = _subcommands()
    for name in PILOT:
        dests = {action.dest for action in commands[name]._actions}
        if name in {"pilot-annotate", "pilot2-annotate"}:
            assert dests == {"help", "port"}
        elif name == "pilot-addendum":
            assert dests == {"help", "none", "item", "interface_commit"}
        else:
            assert dests == {"help"}
        assert not {"recording", "recordings", "video", "stage"} & dests


def test_pilot_operations_reject_records_of_other_recordings(at) -> None:
    ws = at("g1")
    write_plans(ws.scope, TOOL)
    extract_plans(ws.scope, TOOL)
    write_points(ws.scope, TOOL)
    chosen = ws.chosen()
    extra = plan_path(ws.scope, "pilot-c")
    shutil.copy(plan_path(ws.scope, chosen[0]), extra)
    ws.commit([*pilot_groups(ws.scope, chosen)[1], extra], "G2 with an extra recording")
    with pytest.raises(ProcedureOrderError, match="only its records"):
        session_inputs(ws.scope)


def test_point_manifest_of_other_recordings_is_rejected(at) -> None:
    ws = at("g1")
    write_plans(ws.scope, TOOL)
    extract_plans(ws.scope, TOOL)
    write_points(ws.scope, TOOL)
    manifest = read_json(ws.scope.records / POINTS)
    manifest["recordings"] = [manifest["recordings"][0], "pilot-c"]
    write_json(ws.scope.records / POINTS, manifest)
    ws.commit(pilot_groups(ws.scope, ws.chosen())[1], "G2")
    with pytest.raises(IsolationViolation, match="outside the selection"):
        session_inputs(ws.scope)


def test_extraction_requires_plans_bound_to_the_verified_manifest(at) -> None:
    ws = at("g1")
    write_plans(ws.scope, TOOL)
    chosen = ws.chosen()
    plan = read_json(plan_path(ws.scope, chosen[1]))
    write_json(
        plan_path(ws.scope, chosen[1]), {**plan, "vision_manifest_sha256": "0" * 64}
    )
    with pytest.raises(PinMismatch, match="not bound"):
        extract_plans(ws.scope, TOOL)
    assert not ws.scope.work.exists()
    assert not extraction_path(ws.scope, chosen[0]).exists()


def test_extraction_reruns_declared_source_checks_before_decoding(at) -> None:
    ws = at("g1")
    write_plans(ws.scope, TOOL)
    video = ws.repo / "videos" / f"{ws.chosen()[1]}.avi"
    video.write_bytes(video.read_bytes() + b"x")
    with pytest.raises(PinMismatch):
        extract_plans(ws.scope, TOOL)
    assert not ws.scope.work.exists()


# --------------------------------------------------------------------------
# Requirement 2: corpus commands reject pilot recordings and pilot records
# --------------------------------------------------------------------------


def test_corpus_parser_rejects_pilot_recordings() -> None:
    for argv in (
        ["plan", "--recording", "pilot-a"],
        ["extract", "--recording", "pilot-a", "--video", "v"],
        ["points", "--stage", "2", "--recordings", "pilot-a"],
    ):
        with pytest.raises(SystemExit):
            build_parser().parse_args(argv)


def test_corpus_functions_reject_pilot_recordings(at) -> None:
    ws = at("g5_pass")
    with pytest.raises(IsolationViolation):
        blind.write_plan(ws.repo, ws.records, "pilot-a", TOOL)
    with pytest.raises(IsolationViolation):
        blind.run_extraction(ws.records, ws.repo / WORK_DIR, "pilot-a", Path("v"), TOOL)
    assert not blind.plan_path(ws.records, "pilot-a").exists()


@pytest.mark.parametrize(
    "call",
    [
        lambda ws: blind.session_inputs(ws.records, ws.repo / WORK_DIR, 1),
        lambda ws: blind.stage_pairs(ws.records, 1),
    ],
)
def test_corpus_readers_reject_pilot_scoped_records(at, call) -> None:
    ws = at("g5_pass")
    for names in ([POINTS, DUPLICATES, *G3_NAMES], list(G4_NAMES)):
        for name in names:
            shutil.copy(ws.scope.records / name, ws.records / name)
        ws.commit([ws.records / name for name in names], "pilot records in corpus path")
    with pytest.raises(IsolationViolation):
        call(ws)


def test_every_corpus_record_read_goes_through_the_scope_check() -> None:
    import ast

    tree = ast.parse(Path(blind.__file__).read_text())
    raw = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "read_json"
    ]
    assert len(raw) == 2  # the vision manifest in write_plan, and _read_record


def test_guard_rejects_pilot_scoped_records_in_the_corpus_path(at, monkeypatch) -> None:
    ws = at("g5_pass")
    stray = ws.records / "labels_stage1.json"
    shutil.copy(ws.scope.records / "labels_stage1.json", stray)
    with pytest.raises(IsolationViolation):
        execution.reject_pilot_scoped_records(ws.repo)
    stray.unlink()
    execution.reject_pilot_scoped_records(ws.repo)


# --------------------------------------------------------------------------
# Requirement 3: no collisions; writes inside P and W; no regeneration
# --------------------------------------------------------------------------


def test_pilot_and_corpus_paths_cannot_collide(tmp_path: Path) -> None:
    repo = tmp_path
    scope = PilotScope.from_repo(repo)
    records, work = repo / RECORDS_DIR, repo / WORK_DIR
    assert scope.records == repo / PILOT_RECORDS_DIR
    assert scope.work == repo / PILOT_WORK_DIR
    assert "pilot" not in CORRECTNESS_APPLICABLE
    chosen = ["pilot-a", "pilot-b"]
    pilot_paths = {p for group in pilot_groups(scope, chosen) for p in group}
    pilot_paths |= {addendum_path(scope), session_dir(scope)}
    pilot_paths |= {frames_dir(scope, rid) for rid in chosen}
    corpus_paths = set()
    for rid in CORRECTNESS_APPLICABLE:
        corpus_paths |= {
            blind.plan_path(records, rid),
            blind.extraction_path(records, rid),
        }
        corpus_paths.add(work / "frames" / rid)
    for stage in (1, 2):
        corpus_paths |= {
            blind.points_path(records, stage),
            blind.gate_path(records, stage),
        }
        corpus_paths |= {records / f"duplicates_stage{stage}.json"}
        corpus_paths.add(work / f"session_stage{stage}")
    corpus_paths |= {records / name for name in (*G3_NAMES, *G4_NAMES)}
    assert not pilot_paths & corpus_paths
    for path in corpus_paths:
        assert not path.is_relative_to(scope.records)
        assert not path.is_relative_to(scope.work)
    for path in pilot_paths:
        assert path.is_relative_to(scope.records) or path.is_relative_to(scope.work)


def _files(repo: Path) -> set[str]:
    return {
        p.relative_to(repo).as_posix()
        for p in repo.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(repo).parts
    }


def test_pilot_writes_stay_inside_p_and_w(checkpoints) -> None:
    before = _files(checkpoints["repaired"].repo)
    after = _files(checkpoints["g5_pass"].repo)
    new = after - before
    assert new
    assert all(p.startswith((PILOT_RECORDS_DIR, PILOT_WORK_DIR)) for p in new), new


def _prepare_points(ws: Workspace) -> None:
    write_plans(ws.scope, TOOL)
    extract_plans(ws.scope, TOOL)


NO_REGENERATION = [
    ("repaired", None, lambda s: selection_path(s), write_selection, []),
    (
        "g1",
        None,
        lambda s: plan_path(s, _chosen(s)[1]),
        write_plans,
        [lambda s: plan_path(s, _chosen(s)[0])],
    ),
    (
        "g1",
        lambda ws: write_plans(ws.scope, TOOL),
        lambda s: frames_dir(s, _chosen(s)[1]),
        extract_plans,
        [
            lambda s: frames_dir(s, _chosen(s)[0]),
            lambda s: extraction_path(s, _chosen(s)[0]),
        ],
    ),
    (
        "g1",
        lambda ws: write_plans(ws.scope, TOOL),
        lambda s: extraction_path(s, _chosen(s)[1]),
        extract_plans,
        [
            lambda s: frames_dir(s, _chosen(s)[0]),
            lambda s: extraction_path(s, _chosen(s)[0]),
        ],
    ),
    (
        "g1",
        _prepare_points,
        lambda s: s.records / DUPLICATES,
        write_points,
        [lambda s: s.records / POINTS],
    ),
    (
        "annotated",
        None,
        lambda s: s.records / G4_NAMES[1],
        export,
        [lambda s, n=name: s.records / n for name in (*G3_NAMES, G4_NAMES[0])],
    ),
    (
        "annotated",
        None,
        lambda s: s.records / G3_NAMES[3],
        export,
        [lambda s, n=name: s.records / n for name in (*G3_NAMES[:3], *G4_NAMES)],
    ),
    ("g4", None, lambda s: s.records / GATE, gate, []),
]


def _chosen(scope: PilotScope) -> list[str]:
    return read_json(selection_path(scope))["chosen"]


@pytest.mark.parametrize("case", NO_REGENERATION, ids=lambda c: c[3].__name__)
def test_existing_pilot_output_stops_before_any_write(at, case) -> None:
    checkpoint, prepare, target, operation, untouched = case
    ws = at(checkpoint)
    if prepare is not None:
        prepare(ws)
    path = target(ws.scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    if checkpoint != "annotated" and path.suffix != ".json":
        path.mkdir()
    else:
        path.write_text("{}")
    sentinel = path.read_bytes() if path.is_file() else None
    with pytest.raises(ProcedureOrderError, match="already exists"):
        operation(ws.scope, TOOL)
    for other in untouched:
        assert not other(ws.scope).exists()
    if sentinel is not None:
        assert path.read_bytes() == sentinel


def test_existing_addendum_stops_before_any_write(at) -> None:
    ws = at("g5_pass")
    addendum_path(ws.scope).write_text("{}")
    with pytest.raises(ProcedureOrderError, match="already exists"):
        write_addendum(ws.scope, changes=[], interface_commit=None)
    assert addendum_path(ws.scope).read_text() == "{}"


# --------------------------------------------------------------------------
# Requirements 4 and 5: shared semantics on identical synthetic input
# --------------------------------------------------------------------------


def test_pilot_selection_is_the_frozen_selection(at) -> None:
    ws = at("g5_pass")
    selection = read_json(selection_path(ws.scope))
    assert selection["scope"] == SCOPE_PILOT
    assert _strip(selection, "scope", "tool") == select_pilot(ws.repo, ws.records)
    eligible = sorted(
        (row for row in selection["declared"] if row["eligible"]),
        key=lambda row: row["video_identity"],
    )
    assert selection["chosen"] == [row["recording"] for row in eligible[:2]]
    assert len(selection["declared"]) == 5 and len(eligible) == 4


def test_pilot_selection_with_fewer_than_two_eligible_stops(
    tmp_path: Path, monkeypatch
) -> None:
    repo = init_repo(tmp_path / "repo")
    (repo / "README").write_text("x")
    commit(repo, [repo / "README"], "init")
    step2a = SyntheticStep2a(repo, repo / RECORDS_DIR, monkeypatch)
    step2a.sources = [step2a.sources[0], step2a.sources[2]]
    step2a.seal()
    scope = PilotScope.from_repo(repo)
    with pytest.raises(MalformedInput, match="eligible"):
        write_selection(scope, TOOL)
    assert not selection_path(scope).exists()


def test_pilot_plans_extraction_and_points_equal_the_shared_functions(
    at, tmp_path: Path
) -> None:
    ws = at("g5_pass")
    entries = {
        e["recording"]: e for e in read_json(step2a_paths(ws.records)[1])["recordings"]
    }
    chosen = ws.chosen()
    frames: list[StageFrame] = []
    for rid in chosen:
        entry = entries[rid]
        manifest = ws.repo / entry["vision_manifest_path"]
        plan = read_json(plan_path(ws.scope, rid))
        assert plan["scope"] == SCOPE_PILOT and plan["purpose"] == PURPOSE_PILOT
        expected = build_frame_plan(
            rid,
            read_json(manifest),
            manifest_sha256=sha256_file(manifest),
            purpose=PURPOSE_PILOT,
        )
        assert _strip(plan, "scope", "tool") == expected
        video = ws.repo / "videos" / f"{rid}.avi"
        provenance = read_json(extraction_path(ws.scope, rid))
        shared = extract_recording(
            expected,
            video,
            tmp_path / "shared" / rid,
            expected_video_sha256=sha256_file(video),
        )
        assert Path(provenance["video_path"]).parts[-2:] == video.parts[-2:]
        assert _strip(provenance, "scope", "tool", "video_path") == _strip(
            shared, "video_path"
        )
        for frame in shared["frames"]:
            pilot_png = frames_dir(ws.scope, rid) / frame["png"]
            assert (
                pilot_png.read_bytes()
                == (tmp_path / "shared" / rid / frame["png"]).read_bytes()
            )
        index = {f["frame_id"]: f for f in shared["frames"]}
        frames += [
            StageFrame(
                rid,
                fid,
                int(index[fid]["source_pts"]),
                tmp_path / "shared" / rid / index[fid]["png"],
                index[fid]["png_sha256"],
            )
            for fid in stage_frame_ids(expected, STAGE_1)
        ]
    manifest = read_json(ws.scope.records / POINTS)
    identities = {
        rid: StageIdentity(**manifest["stage_identities"][rid]) for rid in chosen
    }
    assert {i.stage_id for i in identities.values()} == {SYNTH_STAGE}
    points = sample_stage_points(
        frames,
        video_identities={rid: entries[rid]["video_identity"] for rid in chosen},
        stage_identities=identities,
        geometry_dir=ws.geometry,
    )
    assert manifest["points"] == points and manifest["recordings"] == chosen
    assert manifest["pilot_blind_input_manifest_sha256"] == sha256_file(
        step2a_paths(ws.records)[1]
    )
    dups = read_json(ws.scope.records / DUPLICATES)
    digest = sha256_file(ws.scope.records / POINTS)
    assert dups == {
        "stage": STAGE_1,
        "scope": SCOPE_PILOT,
        "point_manifest_sha256": digest,
        "duplicate_ids": duplicate_point_ids(
            [p["point_id"] for p in points],
            digest,
            count=pilot_duplicate_count(len(points)),
        ),
    }


def test_pilot_session_records_and_gate_equal_the_shared_functions(
    at, tmp_path: Path
) -> None:
    ws = at("g5_fail")
    shared = tmp_path / "shared"
    written = export_session_records(session_dir(ws.scope), shared, tool=TOOL)
    assert sorted(written) == sorted((*G3_NAMES, *G4_NAMES))
    for name in written:
        pilot = read_json(ws.scope.records / name)
        assert pilot["scope"] == SCOPE_PILOT
        assert _strip(pilot, "scope") == read_json(shared / name)
    manifest = read_json(ws.scope.records / POINTS)
    swatches = read_json(shared / "swatches.json")["swatches"]
    tokens = read_json(shared / "token_key.json")["tokens"]
    valid = [
        r for r in manifest["recordings"] if swatches[tokens[r]]["status"] == "valid"
    ]
    members = population(
        read_json(ws.scope.records / DUPLICATES)["duplicate_ids"],
        {p["point_id"]: p["recording_id"] for p in manifest["points"]},
        valid,
    )
    queue = read_json(shared / "queue_stage1.json")["items"]
    labels = {
        e["item_id"]: e["label"]
        for e in read_json(shared / "labels_stage1.json")["labels"]
    }
    refs = read_json(shared / "queue_key_stage1.json")["point_refs"]
    expected = evaluate_gate(pair_labels(members, queue, labels, refs)).to_dict()
    result = read_json(ws.scope.records / GATE)
    assert _strip(result, "scope", "stage", "tool") == expected
    assert result["scope"] == SCOPE_PILOT and result["passed"] is False


def test_every_pilot_record_carries_the_pilot_scope(at) -> None:
    ws = at("g5_pass")
    paths = [p for group in pilot_groups(ws.scope, ws.chosen()) for p in group]
    for path in paths:
        assert read_json(path)["scope"] == SCOPE_PILOT, path.name
    assert not addendum_path(ws.scope).exists()
    assert read_json(ws.scope.records / "swatches.json")["scope"] == SCOPE_PILOT


# --------------------------------------------------------------------------
# Requirement 6: no pilot path reaches comparison or a classifier
# --------------------------------------------------------------------------


def test_unblinded_commands_never_read_pilot_records() -> None:
    pilot_modules = {"pilot_pipeline", "pilot_addendum", "execution"}
    pilot_tokens = {
        "PILOT_RECORDS_DIR",
        "PILOT_WORK_DIR",
        "PILOT_SELECTION",
        "PILOT_ADDENDUM",
        "PilotScope",
        PILOT_SELECTION,
    }
    for name in ("pipeline_unblinded", "criterion_4a", "report"):
        closure = _local_closure(name)
        assert not closure & pilot_modules, (name, closure & pilot_modules)
        for module in closure - {"constants"}:
            assert not set(_code_tokens(module)) & pilot_tokens, (name, module)


def test_there_is_no_pilot_comparison_command() -> None:
    commands = set(_subcommands())
    assert set(COMMAND_ARGS) == commands
    assert {c for c in commands if c.startswith("pilot-")} == execution.PILOT_COMMANDS
    assert execution.CORPUS_COMMANDS == {
        "plan",
        "extract",
        "points",
        "annotate",
        "export",
        "gate",
        "criterion-4a",
        "compare",
        "report",
        "corpus-capacity-precheck",
        "stage1-recovery-verify",
    }
    pilot = {c for c in commands if "pilot" in c}
    assert not {c for c in pilot if any(w in c for w in ("compar", "criterion"))}
    assert {c for c in pilot if "report" in c} == {"pilot1-gate-report"}


# --------------------------------------------------------------------------
# Requirement 7: every parser subcommand passes the common guard first
# --------------------------------------------------------------------------


def _break_chain(ws: Workspace, monkeypatch) -> None:
    monkeypatch.setattr(seal, "PREREGISTRATION_CHAIN", (("amendment7", ws.c7, "0" * 64),))


def _break_a1(ws: Workspace, monkeypatch) -> None:
    (ws.repo / next(iter(ws.a1))).write_text("modified a1\n")


def _break_clean(ws: Workspace, monkeypatch) -> None:
    (ws.repo / TOOL_REL / "render.py").write_text("# uncommitted\n")


def _break_identity(ws: Workspace, monkeypatch) -> None:
    _edit(ws, {f"{TOOL_REL}/render.py": "# second tool commit\n"}, "unauthorized")


@pytest.mark.parametrize(
    "breaker", [_break_chain, _break_a1, _break_clean, _break_identity]
)
def test_every_command_stops_when_a_common_check_fails(at, monkeypatch, breaker) -> None:
    ws = at("repaired")
    called = _recorders(monkeypatch)
    _main("preconditions")
    assert called == ["preconditions"]
    called.clear()
    breaker(ws, monkeypatch)
    for name in COMMAND_ARGS:
        _stops(name, called)
    assert called == []


def test_the_dispatcher_guards_before_dispatch(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(execution, "guard", lambda repo, command: seen.append(command))
    called = _recorders(monkeypatch)
    for name in COMMAND_ARGS:
        _main(name)
    assert seen == called == list(COMMAND_ARGS)


# --------------------------------------------------------------------------
# Requirements 8, 9 and 11: addendum lifecycle, pilot STOPs, failing G5
# --------------------------------------------------------------------------


@SUPERSEDED_BY_AMENDMENT9
def test_pilot_addendum_needs_only_history_through_g5(at, monkeypatch) -> None:
    ws = at("g5_pass")
    cli.main(["pilot-addendum", "--none"])
    payload = read_json(addendum_path(ws.scope))
    assert payload["changes"] == [] and payload["interface_commit"] is None
    assert payload["tool_commit"] == ws.head()
    assert set(payload["pilot_records"]) == {
        p.relative_to(ws.scope.records).as_posix()
        for group in pilot_groups(ws.scope, ws.chosen())
        for p in group
    }
    with pytest.raises(SealViolation):
        verify_sealed_addendum(ws.scope)
    ws.commit([addendum_path(ws.scope)], "G6")
    assert verify_sealed_addendum(ws.scope) == payload


@SUPERSEDED_BY_AMENDMENT9
def test_corpus_commands_need_a_sealed_valid_addendum(at, monkeypatch) -> None:
    ws = at("g5_pass")
    called = _recorders(monkeypatch)
    for name in CORPUS:
        _stops(name, called)
    write_addendum(ws.scope, changes=[], interface_commit=None)
    for name in CORPUS:
        _stops(name, called)
    ws.commit([addendum_path(ws.scope)], "G6")
    for name in CORPUS:
        _main(name)
    assert called == CORPUS


@SUPERSEDED_BY_AMENDMENT9
def test_pilot_commands_stop_once_an_addendum_exists(at, monkeypatch) -> None:
    ws = at("g5_pass")
    called = _recorders(monkeypatch)
    write_addendum(ws.scope, changes=[], interface_commit=None)
    for name in PILOT:
        _stops(name, called)
    ws.commit([addendum_path(ws.scope)], "G6")
    for name in PILOT:
        _stops(name, called)
    assert called == []


@SUPERSEDED_BY_AMENDMENT9
def test_failing_g5_is_a_mandatory_stop(at, monkeypatch) -> None:
    ws = at("g5_fail")
    called = _recorders(monkeypatch)
    for name in PILOT + CORPUS:
        _stops(name, called)
    with pytest.raises(ProcedureOrderError, match="mandatory STOP"):
        write_addendum(ws.scope, changes=[], interface_commit=None)
    assert not addendum_path(ws.scope).exists()
    _edit(ws, {f"{TOOL_REL}/annotate.html": "<html>fix</html>\n"}, "interface")
    with pytest.raises(ProcedureOrderError, match="mandatory STOP"):
        execution.verify_tool_identity(ws.repo)


@SUPERSEDED_BY_AMENDMENT9
def test_pilot_commands_pass_the_guard_before_g5(at, monkeypatch) -> None:
    ws = at("g4")
    called = _recorders(monkeypatch)
    for name in PILOT:
        _main(name)
    assert called == PILOT
    assert ws.scope.records.is_dir()


def _sealed(ws: Workspace, payload: dict) -> None:
    write_json(addendum_path(ws.scope), payload)
    ws.commit([addendum_path(ws.scope)], "G6")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["pilot_records"].pop("labels_stage1.json"),
        lambda p: p["pilot_records"].update({"extra.json": "0" * 64}),
        lambda p: p["pilot_records"].update({"labels_stage1.json": "0" * 64}),
        lambda p: p.update(changes=["marker"]),
        lambda p: p.update(interface_commit="a" * 40),
        lambda p: p.update(changes=["layout", "marker"], interface_commit="a" * 40),
        lambda p: p.update(changes=["bogus"], interface_commit="a" * 40),
        lambda p: p.update(amendment7_commit="b" * 40),
        lambda p: p.update(tool_commit="c" * 40),
        lambda p: p.update(schema="other"),
        lambda p: p.update(scope="corpus"),
        lambda p: p.update(note="free text"),
    ],
)
@SUPERSEDED_BY_AMENDMENT9
def test_invalid_sealed_addendum_stops_corpus_commands(at, monkeypatch, mutate) -> None:
    ws = at("g5_pass")
    write_addendum(ws.scope, changes=[], interface_commit=None)
    payload = read_json(addendum_path(ws.scope))
    mutate(payload)
    _sealed(ws, payload)
    with pytest.raises(ReferenceProcedureError):
        verify_sealed_addendum(ws.scope)
    called = _recorders(monkeypatch)
    _stops("compare", called)


@pytest.mark.parametrize(
    ("changes", "interface"),
    [
        (["marker", "crop_zoom"], "a" * 40),
        (["marker", "marker"], "a" * 40),
        (["bogus"], "a" * 40),
        (["marker"], None),
        ([], "a" * 40),
        (["marker"], "not-a-commit"),
    ],
)
def test_invalid_changes_or_interface_commit_stop(at, changes, interface) -> None:
    ws = at("g5_pass")
    with pytest.raises(MalformedInput):
        write_addendum(ws.scope, changes=changes, interface_commit=interface)
    assert not addendum_path(ws.scope).exists()


def test_interface_commit_must_be_the_tool_history_commit(at) -> None:
    ws = at("g5_pass")
    with pytest.raises(SealViolation, match="tool history"):
        write_addendum(ws.scope, changes=["marker"], interface_commit=ws.head())
    _edit(ws, {f"{TOOL_REL}/annotate.html": "<html>layout</html>\n"}, "interface")
    with pytest.raises(SealViolation, match="tool history"):
        write_addendum(ws.scope, changes=[], interface_commit=None)


def test_modified_sealed_record_stops(at) -> None:
    ws = at("g5_pass")
    labels = ws.scope.records / "labels_stage1.json"
    labels.write_text(labels.read_text() + " ")
    with pytest.raises(SealViolation):
        write_addendum(ws.scope, changes=[], interface_commit=None)
    ws.commit([labels], "modify a sealed record")
    with pytest.raises(ProcedureOrderError, match="another commit"):
        write_addendum(ws.scope, changes=[], interface_commit=None)


@SUPERSEDED_BY_AMENDMENT9
def test_record_modified_after_g6_stops(at) -> None:
    ws = at("g5_pass")
    ws.run_g6()
    verify_sealed_addendum(ws.scope)
    labels = ws.scope.records / "labels_stage1.json"
    labels.write_text(labels.read_text() + " ")
    ws.commit([labels], "modify after G6")
    with pytest.raises(ProcedureOrderError, match="another commit"):
        verify_sealed_addendum(ws.scope)


def test_extra_committed_pilot_record_stops(at) -> None:
    ws = at("g5_pass")
    extra = ws.scope.records / "extra.json"
    write_json(extra, {"scope": SCOPE_PILOT})
    ws.commit([extra], "extra pilot record")
    with pytest.raises(ProcedureOrderError, match="another commit"):
        write_addendum(ws.scope, changes=[], interface_commit=None)


def test_missing_record_stops(at) -> None:
    ws = at("g5_pass")
    git(ws.repo, "rm", "-q", f"{PILOT_RECORDS_DIR}/token_key.json")
    git(ws.repo, "commit", "-q", "-m", "remove a record")
    with pytest.raises(SealViolation):
        write_addendum(ws.scope, changes=[], interface_commit=None)


def _exported(ws: Workspace) -> tuple[list, list]:
    export(ws.scope, TOOL)
    return (
        [ws.scope.records / n for n in G3_NAMES],
        [ws.scope.records / n for n in G4_NAMES],
    )


@pytest.mark.parametrize(
    "seal_groups",
    [
        lambda ws, g3, g4: (ws.commit(g4, "G4 first"), ws.commit(g3, "G3 second")),
        lambda ws, g3, g4: (
            ws.commit(g3[:2], "G3 part"),
            ws.commit(g3[2:], "G3 rest"),
            ws.commit(g4, "G4"),
        ),
        lambda ws, g3, g4: (
            ws.commit(g3 + g4[:1], "G3 with a key"),
            ws.commit(g4[1:], "G4"),
        ),
    ],
    ids=["wrong-order", "split-group", "mixed-group"],
)
def test_misordered_or_split_groups_stop_the_gate(at, seal_groups) -> None:
    ws = at("annotated")
    g3, g4 = _exported(ws)
    seal_groups(ws, g3, g4)
    with pytest.raises(ProcedureOrderError):
        gate(ws.scope, TOOL)
    assert not (ws.scope.records / GATE).exists()


def test_g2_split_or_plans_sealed_separately_stop_annotation(at) -> None:
    ws = at("g1")
    write_plans(ws.scope, TOOL)
    ws.commit([plan_path(ws.scope, rid) for rid in ws.chosen()], "plans alone")
    extract_plans(ws.scope, TOOL)
    write_points(ws.scope, TOOL)
    rest = pilot_groups(ws.scope, ws.chosen())[1][2:]
    ws.commit(rest, "rest of G2")
    with pytest.raises(ProcedureOrderError, match="split"):
        session_inputs(ws.scope)


def test_g1_must_follow_the_corpus_blind_input_manifest(at) -> None:
    from map_ink_reference.pilot_pipeline import verify_groups

    ws = at("g1")
    assert verify_groups(ws.scope, 1)[0] == ws.chosen()
    corpus = seal.adding_commit(ws.repo, step2a_paths(ws.records)[2])
    commits = _after_c7(ws)
    assert commits.index(corpus) == len(commits) - 2
    git(ws.repo, "reset", "-q", "--hard", f"{corpus}~1")
    _replay(ws, [commits[-1], corpus])
    with pytest.raises(ProcedureOrderError, match="out of order"):
        verify_groups(ws.scope, 1)
    with pytest.raises(ProcedureOrderError):
        write_plans(ws.scope, TOOL)


@SUPERSEDED_BY_AMENDMENT9
def test_g6_must_be_alone_and_strictly_after_g5(at) -> None:
    ws = at("g5_pass")
    write_addendum(ws.scope, changes=[], interface_commit=None)
    (ws.repo / "README.md").write_text("unrelated\n")
    ws.commit([addendum_path(ws.scope), ws.repo / "README.md"], "G6 with another file")
    with pytest.raises(ProcedureOrderError, match="only its records"):
        verify_sealed_addendum(ws.scope)


def test_g6_in_the_g5_commit_stops(at) -> None:
    ws = at("g4")
    gate(ws.scope, TOOL)
    payload = {
        "schema": "amendment7-pilot-addendum/v1",
        "scope": SCOPE_PILOT,
        "amendment7_commit": ws.c7,
        "tool_commit": ws.head(),
        "pilot_records": {
            p.relative_to(ws.scope.records).as_posix(): sha256_file(p)
            for group in pilot_groups(ws.scope, ws.chosen())
            for p in group
        },
        "changes": [],
        "interface_commit": None,
    }
    write_json(addendum_path(ws.scope), payload)
    ws.commit([ws.scope.records / GATE, addendum_path(ws.scope)], "G5 and G6 together")
    with pytest.raises(ProcedureOrderError):
        verify_sealed_addendum(ws.scope)


# --------------------------------------------------------------------------
# Requirement 12: interface commit restrictions; crop / zoom rules
# --------------------------------------------------------------------------


def _marker_commit(ws: Workspace) -> str:
    return _edit(
        ws,
        {
            f"{TOOL_REL}/render.py": RENDER_FIX,
            SESSION_REL: _session(ADD_IMPORT, APPEND_HANDLE),
            f"{TOOL_REL}/annotate.html": "<html>marker</html>\n",
        },
        "interface: marker and context thumbnail",
    )


@SUPERSEDED_BY_AMENDMENT9
def test_valid_interface_commit_and_sealed_addendum(at, monkeypatch) -> None:
    ws = at("g5_pass")
    interface = _marker_commit(ws)
    execution.verify_tool_identity(ws.repo)
    ws.run_g6(["marker"], interface)
    payload = verify_sealed_addendum(ws.scope)
    assert payload["interface_commit"] == interface
    called = _recorders(monkeypatch)
    _main("compare")
    assert called == ["compare"]


@SUPERSEDED_BY_AMENDMENT9
def test_valid_crop_zoom_interface_commit(at) -> None:
    ws = at("g5_pass")
    interface = _edit(ws, {CONSTANTS_REL: ws.constants(crop=112, zoom=3)}, "crop")
    with pytest.raises(SealViolation):
        write_addendum(ws.scope, changes=["marker"], interface_commit=interface)
    ws.run_g6(["crop_zoom"], interface)
    verify_sealed_addendum(ws.scope)


def _session_edit(*replacements: tuple[str, str]) -> Callable[[Workspace], dict]:
    return lambda ws: {SESSION_REL: _session(*replacements)}


INVALID_INTERFACE = {
    "outside-path": lambda ws: {f"{TOOL_REL}/plan.py": "# changed\n"},
    "outside-tree": lambda ws: {
        f"{TOOL_REL}/render.py": RENDER_FIX,
        "src/other.py": "# changed\n",
    },
    "modified-test": lambda ws: {f"{TEST_REL}/test_existing.py": "# changed\n"},
    "deleted-test": lambda ws: {f"{TEST_REL}/test_existing.py": None},
    "constants-beyond-values": lambda ws: {
        CONSTANTS_REL: ws.constants().replace("MIN_CROP_SIZE = 64", "MIN_CROP_SIZE = 32")
    },
    "crop-out-of-range": lambda ws: {CONSTANTS_REL: ws.constants(crop=200)},
    "zoom-not-integer": lambda ws: {CONSTANTS_REL: ws.constants(zoom=4.5)},
    "zoom-boolean": lambda ws: {CONSTANTS_REL: ws.constants(zoom=True)},
    "session-import-removed": _session_edit(("    zoom_nearest,\n)", ")")),
    "session-import-reordered": _session_edit(
        ("    padded_crop,\n    point_view,\n", "    point_view,\n    padded_crop,\n")
    ),
    "session-import-aliased": _session_edit(
        ("    point_view,\n", "    context_view as cv,\n    point_view,\n")
    ),
    "session-entry-changed": _session_edit(
        ('"labels": list(LABELS),', '"labels": list(LABELS)[:2],')
    ),
    "session-entry-reordered": _session_edit(
        (
            '            "swatch_A": self._swatch_image(rid, token, "A"),\n'
            '            "swatch_B": self._swatch_image(rid, token, "B"),\n',
            '            "swatch_B": self._swatch_image(rid, token, "B"),\n'
            '            "swatch_A": self._swatch_image(rid, token, "A"),\n',
        )
    ),
    "session-entry-removed": _session_edit(('            "labels": list(LABELS),\n', "")),
    "session-entry-other-form": _session_edit(_append('"extra": len(queue),')),
    "session-entry-duplicate-key": _session_edit(_append('"kind": "other",')),
    "session-entry-unknown-render-name": _session_edit(
        (PROGRESS, HANDLE_ENTRY.replace("context_view(", "unknown_view("))
    ),
    "session-other-statement": _session_edit(
        ('index = len(self.state["labels"])', 'index = len(self.state["labels"]) - 1')
    ),
    "session-new-method": _session_edit(
        (
            "    def _label_task(self)",
            "    def extra(self) -> None:\n        return None\n\n"
            "    def _label_task(self)",
        )
    ),
}


@pytest.mark.parametrize("name", sorted(INVALID_INTERFACE))
def test_invalid_interface_commit_fails_tool_identity(at, monkeypatch, name) -> None:
    ws = at("g5_pass")
    _edit(ws, INVALID_INTERFACE[name](ws), f"interface: {name}")
    with pytest.raises(ReferenceProcedureError):
        execution.verify_tool_identity(ws.repo)
    called = _recorders(monkeypatch)
    _stops("pilot-addendum", called)


@pytest.mark.parametrize(
    "literal",
    [
        '"thumbnail": True,',
        '"marker_colour": [255, 0, 0],',
        '"offset": -2,',
        '"x": None,',
    ],
)
@SUPERSEDED_BY_AMENDMENT9
def test_appended_literal_session_entries_are_allowed(at, literal) -> None:
    ws = at("g5_pass")
    interface = _edit(ws, {SESSION_REL: _session(_append(literal))}, "layout literal")
    execution.verify_tool_identity(ws.repo)
    ws.run_g6(["layout"], interface)
    verify_sealed_addendum(ws.scope)


@pytest.mark.parametrize(
    ("edits", "changes"),
    [
        ({f"{TOOL_REL}/render.py": RENDER_FIX}, ["instructions"]),
        ({f"{TOOL_REL}/annotate.html": "<html>x</html>\n"}, ["crop_zoom"]),
        ({f"{TOOL_REL}/server.py": "# fix\n"}, ["interface_fix"]),
        ({f"{TOOL_REL}/annotate.html": "<html>x</html>\n"}, ["marker", "interface_fix"]),
    ],
    ids=["uncovered-file", "item-without-change", "fix-without-test", "fix-no-test-2"],
)
@SUPERSEDED_BY_AMENDMENT9
def test_items_must_agree_with_the_interface_commit(at, edits, changes) -> None:
    ws = at("g5_pass")
    interface = _edit(ws, edits, "interface")
    execution.verify_tool_identity(ws.repo)
    with pytest.raises(SealViolation):
        write_addendum(ws.scope, changes=changes, interface_commit=interface)
    assert not addendum_path(ws.scope).exists()


@SUPERSEDED_BY_AMENDMENT9
def test_interface_fix_with_an_added_test_is_allowed(at) -> None:
    ws = at("g5_pass")
    interface = _edit(
        ws,
        {f"{TOOL_REL}/server.py": "# fix\n", f"{TEST_REL}/test_fix.py": "# new test\n"},
        "interface fix",
    )
    ws.run_g6(["interface_fix"], interface)
    verify_sealed_addendum(ws.scope)


def test_interface_commit_before_a_passing_g5_stops(at) -> None:
    ws = at("g4")
    _edit(ws, {f"{TOOL_REL}/annotate.html": "<html>early</html>\n"}, "early interface")
    with pytest.raises(SealViolation):
        execution.verify_tool_identity(ws.repo)


@SUPERSEDED_BY_AMENDMENT9
def test_second_interface_commit_stops(at) -> None:
    ws = at("g5_pass")
    _edit(ws, {f"{TOOL_REL}/annotate.html": "<html>one</html>\n"}, "interface one")
    execution.verify_tool_identity(ws.repo)
    _edit(ws, {f"{TOOL_REL}/annotate.html": "<html>two</html>\n"}, "interface two")
    with pytest.raises(SealViolation, match="more than one"):
        execution.verify_tool_identity(ws.repo)


@pytest.fixture
def constants_repo(tmp_path: Path):
    repo = init_repo(tmp_path / "repo")

    def at_values(crop, zoom, extra: str = "") -> str:
        path = repo / CONSTANTS_REL
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            constants_source("a" * 40, "b" * 64, crop=crop, zoom=zoom) + extra
        )
        git(repo, "add", "-f", str(path))
        git(repo, "commit", "-q", "--allow-empty", "-m", f"{crop} {zoom}")
        return git(repo, "rev-parse", "HEAD").strip()

    return repo, at_values


@pytest.mark.parametrize(
    ("crop", "zoom", "changes", "ok"),
    [
        (96, 4, [], True),
        (96, 4, ["crop_zoom"], False),
        (64, 4, ["crop_zoom"], True),
        (160, 4, ["crop_zoom"], True),
        (96, 5, ["crop_zoom"], True),
        (96, 5, [], False),
        (100, 4, [], False),
        (63, 4, ["crop_zoom"], False),
        (161, 4, ["crop_zoom"], False),
        (96, 4.0, [], False),
        (96, True, ["crop_zoom"], False),
        (96.0, 4, [], False),
        ('"96"', 4, [], False),
    ],
)
def test_crop_zoom_rules(constants_repo, crop, zoom, changes, ok) -> None:
    repo, at_values = constants_repo
    commit_id = at_values(crop, zoom)
    if ok:
        verify_crop_zoom(repo, commit_id, changes)
    else:
        with pytest.raises(SealViolation):
            verify_crop_zoom(repo, commit_id, changes)


def test_display_defaults_must_be_single_assignments(constants_repo) -> None:
    repo, at_values = constants_repo
    assert display_defaults(repo, at_values(96, 4)) == (96, 4)
    with pytest.raises(SealViolation):
        display_defaults(repo, at_values(96, 4, "DEFAULT_ZOOM = 4\n"))
    with pytest.raises(SealViolation):
        display_defaults(repo, at_values(96, 4, "DEFAULT_ZOOM += 1\n"))


# --------------------------------------------------------------------------
# Requirement 13: tool identity rule
# --------------------------------------------------------------------------


def _replay(ws: Workspace, commits: list[str]) -> Workspace:
    """Cherry-pick ``commits``; repair 8 is recommitted naming the replayed C8."""
    for item in commits:
        if item == ws.repair8:
            ws = replace(ws, repair8=commit_repair8(ws.repo, ws.c8, ws.digest8))
        elif item == ws.repair9:
            ws = replace(ws, repair9=commit_repair9(ws.repo, ws.c9, ws.digest9))
        else:
            git(ws.repo, "cherry-pick", "--allow-empty", item)
            if item == ws.c8:
                ws = replace(ws, c8=ws.head())
            elif item == ws.c9:
                ws = replace(ws, c9=ws.head())
    return ws


def _repin(ws: Workspace, monkeypatch) -> None:
    """Pin the rebuilt first protected commit and the replayed C8."""
    first = seal.protected_commits(ws.repo, ws.c7)[0]
    replace(ws, repair7=first).patch(monkeypatch)


def _after_c7(ws: Workspace) -> list[str]:
    return git(ws.repo, "rev-list", "--reverse", f"{ws.c7}..HEAD").split()


def test_repair_is_the_first_protected_commit_after_c7(at) -> None:
    ws = at("g5_pass")
    tool = seal.protected_commits(ws.repo, ws.c7)
    assert tool == [ws.repair7, ws.repair8, ws.repair9]
    assert tool[0] == _after_c7(ws)[0]
    execution.verify_tool_identity(ws.repo)


def test_no_repair_after_c7_stops(at, monkeypatch) -> None:
    ws = at("repaired")
    git(ws.repo, "reset", "-q", "--hard", ws.c7)
    with pytest.raises(SealViolation, match="not committed"):
        execution.verify_tool_identity(ws.repo)


def test_merge_commit_after_c7_stops(at) -> None:
    ws = at("repaired")
    git(ws.repo, "checkout", "-q", "-b", "side", ws.c7)
    (ws.repo / "side.txt").write_text("side\n")
    commit(ws.repo, [ws.repo / "side.txt"], "side")
    git(ws.repo, "checkout", "-q", "-")
    git(ws.repo, "merge", "-q", "--no-ff", "-m", "merge", "side")
    with pytest.raises(SealViolation, match="merge commit"):
        execution.verify_tool_identity(ws.repo)


def _rebuild_repair(ws: Workspace, edits: dict[str, str], monkeypatch) -> None:
    replay = _after_c7(ws)[1:]
    git(ws.repo, "reset", "-q", "--hard", ws.c7)
    _edit(
        ws,
        {f"{TEST_REL}/test_repair.py": '"""Synthetic repair test."""\n', **edits},
        "repair",
    )
    _repin(_replay(ws, replay), monkeypatch)


def test_repair_outside_its_file_set_stops(at, monkeypatch) -> None:
    ws = at("repaired")
    _rebuild_repair(
        ws,
        {
            CONSTANTS_REL: constants_source(ws.c7, ws.digest),
            f"{TOOL_REL}/plan.py": "# frozen module changed\n",
        },
        monkeypatch,
    )
    with pytest.raises(SealViolation, match="outside its file set"):
        execution.verify_tool_identity(ws.repo)


@pytest.mark.parametrize(
    "source",
    [
        lambda ws: constants_source(None, None),
        lambda ws: constants_source(ws.c7, "0" * 64),
        lambda ws: constants_source("1" * 40, ws.digest),
    ],
    ids=["no-entry", "wrong-digest", "wrong-commit"],
)
def test_repair_without_the_c7_chain_entry_stops(at, monkeypatch, source) -> None:
    ws = at("repaired")
    _rebuild_repair(ws, {CONSTANTS_REL: source(ws)}, monkeypatch)
    with pytest.raises(SealViolation, match="chain entry"):
        execution.verify_tool_identity(ws.repo)


def test_repair_changing_display_defaults_stops(at, monkeypatch) -> None:
    ws = at("repaired")
    _rebuild_repair(
        ws, {CONSTANTS_REL: constants_source(ws.c7, ws.digest, crop=112)}, monkeypatch
    )
    with pytest.raises(SealViolation, match="display defaults"):
        execution.verify_tool_identity(ws.repo)


@SUPERSEDED_BY_AMENDMENT9
def test_repair_after_g1_stops(at, monkeypatch) -> None:
    ws = at("g1")
    repairs = [ws.repair7, ws.repair8]
    rest = [c for c in _after_c7(ws) if c not in repairs]
    git(ws.repo, "reset", "-q", "--hard", ws.c7)
    _repin(_replay(ws, rest + repairs), monkeypatch)
    with pytest.raises(SealViolation, match="before G1"):
        execution.verify_tool_identity(ws.repo)


def test_c7_changing_more_than_the_preregistration_stops(at, monkeypatch) -> None:
    ws = at("repaired")
    replay = _after_c7(ws)[1:]
    git(ws.repo, "reset", "-q", "--hard", f"{ws.c7}~1")
    prereg = ws.repo / seal.PREREGISTRATION_PATH
    prereg.parent.mkdir(parents=True, exist_ok=True)
    prereg.write_text("# synthetic preregistration through Amendment 7\n")
    (ws.repo / "README.md").write_text("other change\n")
    c7 = commit(ws.repo, [prereg, ws.repo / "README.md"], "C7 with another file")
    moved = replace(ws, c7=c7)
    moved.patch(monkeypatch)
    _edit(moved, {CONSTANTS_REL: constants_source(c7, ws.digest)}, "repair")
    _repin(_replay(moved, replay), monkeypatch)
    with pytest.raises(SealViolation, match="only the preregistration"):
        execution.verify_tool_identity(moved.repo)


@pytest.mark.parametrize("with_interface", [False, True])
@SUPERSEDED_BY_AMENDMENT9
def test_protected_commit_after_g6_stops(at, monkeypatch, with_interface) -> None:
    ws = at("g5_pass")
    if with_interface:
        ws.run_g6(["marker"], _marker_commit(ws))
    else:
        ws.run_g6()
    execution.verify_tool_identity(ws.repo)
    _edit(ws, {f"{TOOL_REL}/annotate.html": "<html>late</html>\n"}, "after G6")
    with pytest.raises(SealViolation):
        execution.verify_tool_identity(ws.repo)
    called = _recorders(monkeypatch)
    for name in CORPUS:
        _stops(name, called)


@SUPERSEDED_BY_AMENDMENT9
def test_interface_commit_after_g6_is_not_an_ancestor_of_g6(at) -> None:
    ws = at("g5_pass")
    write_addendum(ws.scope, changes=[], interface_commit=None)
    payload = read_json(addendum_path(ws.scope))
    addendum_path(ws.scope).unlink()
    interface = _edit(ws, {f"{TOOL_REL}/annotate.html": "<html>x</html>\n"}, "interface")
    _sealed(ws, {**payload, "changes": ["layout"], "interface_commit": interface})
    with pytest.raises(ReferenceProcedureError):
        verify_sealed_addendum(ws.scope)
    git(ws.repo, "reset", "-q", "--hard", "HEAD~2")
    _sealed(ws, {**payload, "changes": ["layout"], "interface_commit": "d" * 40})
    _edit(ws, {f"{TOOL_REL}/annotate.html": "<html>x</html>\n"}, "interface after G6")
    with pytest.raises(SealViolation):
        execution.verify_tool_identity(ws.repo)


# --------------------------------------------------------------------------
# Requirement 10: corpus behaviour unchanged (byte-identical on fixtures)
# --------------------------------------------------------------------------


def _c7_module(name: str):
    """A tool module exactly as sealed at C7 (tool source only)."""
    source = subprocess.run(
        ["git", "-C", str(REAL_REPO), "show", f"{C7}:{TOOL_REL}/{name}.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    spec = importlib.util.spec_from_loader(f"map_ink_reference._c7_{name}", loader=None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(source, f"c7:{name}.py", "exec"), module.__dict__)
    return module


def test_corpus_records_are_byte_identical_to_c7(tmp_path: Path, monkeypatch) -> None:
    from test_mir_pipeline import Run, _manifest_names

    old_blind, old_records = _c7_module("pipeline_blind"), _c7_module("records")
    run = Run(tmp_path, monkeypatch)
    monkeypatch.setattr(old_blind, "verify_geometry_pins", blind.verify_geometry_pins)

    def same(path: Path, write_old, write_new) -> None:
        write_old()
        old = path.read_bytes()
        path.unlink()
        write_new()
        assert path.read_bytes() == old, path.name

    for rid in CORRECTNESS_APPLICABLE:
        same(
            blind.plan_path(run.records, rid),
            lambda r=rid: old_blind.write_plan(run.repo, run.records, r, TOOL),
            lambda r=rid: blind.write_plan(run.repo, run.records, r, TOOL),
        )
    for rid in CORRECTNESS_APPLICABLE:
        blind.plan_path(run.records, rid).unlink()
    run.plan_and_extract()
    for name in _manifest_names(1):
        same(
            run.records / name,
            lambda: old_blind.write_point_manifest(
                run.records, run.work, 1, list(CORRECTNESS_APPLICABLE), TOOL
            ),
            lambda: run.points(1, CORRECTNESS_APPLICABLE),
        )
    names = [f"frame_plans/{r}.json" for r in CORRECTNESS_APPLICABLE]
    names += [f"extraction/{r}.json" for r in CORRECTNESS_APPLICABLE]
    run.commit_records(names + _manifest_names(1), "stage 1 manifest")
    old_inputs = old_blind.session_inputs(run.records, run.work, 1)
    assert old_inputs == blind.session_inputs(run.records, run.work, 1)
    run.annotate(1)
    state = run.work / "session_stage1"
    old_files = old_records.export_session_records(state, tmp_path / "old", tool=TOOL)
    new_files = export_session_records(state, tmp_path / "new", tool=TOOL)
    assert old_files == new_files
    for name in new_files:
        assert (tmp_path / "old" / name).read_bytes() == (
            tmp_path / "new" / name
        ).read_bytes()
    assert "scope" not in read_json(tmp_path / "new" / "swatches.json")
    run.seal_session(1)
    same(
        blind.gate_path(run.records, 1),
        lambda: old_blind.run_gate(run.records, 1, TOOL),
        lambda: blind.run_gate(run.records, 1, TOOL),
    )


def test_pilot_scope_only_adds_the_scope_key_to_session_records(
    checkpoints, tmp_path: Path
) -> None:
    state = session_dir(checkpoints["g5_pass"].scope)
    corpus = export_session_records(state, tmp_path / "corpus", tool=TOOL)
    pilot = export_session_records(
        state, tmp_path / "pilot", tool=TOOL, scope=SCOPE_PILOT
    )
    assert set(corpus) == set(pilot)
    for name in corpus:
        with_scope = read_json(tmp_path / "pilot" / name)
        assert with_scope.pop("scope") == SCOPE_PILOT
        assert with_scope == read_json(tmp_path / "corpus" / name)


# --------------------------------------------------------------------------
# Requirement 14: production dependency pins
# --------------------------------------------------------------------------


def test_production_pin_constants_include_the_seven_sealed_files() -> None:
    assert set(execution.PRODUCTION_PINS) == {
        "src/splatoon3_ai_coach/vision/map_ink.py",
        "src/splatoon3_ai_coach/config/models.py",
        "src/splatoon3_ai_coach/vision/stage_maps.py",
        "src/splatoon3_ai_coach/vision/stage_mask.py",
        "src/splatoon3_ai_coach/media/video.py",
        "src/splatoon3_ai_coach/media/video_identity.py",
        "src/splatoon3_ai_coach/types.py",
    }
    assert (
        execution.PRODUCTION_PINS["src/splatoon3_ai_coach/types.py"]
        == "ad6f423fc8a5422a5febc66972143e3d5c322c5a19210bdc29071188569983ea"
    )


def test_map_ink_and_config_pins_equal_the_earlier_amendment_pins() -> None:
    from map_ink_reference.unblinded_pins import MAP_INK_CODE_PIN

    rel, digest = MAP_INK_CODE_PIN
    assert execution.PRODUCTION_PINS[rel] == digest
    config = "src/splatoon3_ai_coach/config/models.py"
    assert execution.PRODUCTION_PINS[config] == INTRO_LATCH_CODE_PINS[config]
    assert execution.A1_IMPLEMENTATION_PINS == UNBLINDED_A1


@pytest.mark.parametrize("rel", sorted(execution.PRODUCTION_PINS))
@pytest.mark.parametrize("where", ["working-tree", "committed"])
def test_each_production_pin_mismatch_stops_every_command(
    at, monkeypatch, rel, where
) -> None:
    ws = at("repaired")
    path = ws.repo / rel
    original = path.read_bytes()
    path.write_bytes(original + b"changed\n")
    if where == "committed":
        commit(ws.repo, [path], "change a pinned file")
        path.write_bytes(original)
    called = _recorders(monkeypatch)
    for check in ("verify_preconditions", "verify_clean_tooling", "verify_tool_identity"):
        monkeypatch.setattr(execution, check, lambda *args, **kwargs: None)
    for name in COMMAND_ARGS:
        with pytest.raises(
            SealViolation, match=f"{where} production pin mismatch: {rel}"
        ):
            _main(name)
    assert called == []
    expected = original if where == "committed" else original + b"changed\n"
    assert path.read_bytes() == expected


def test_uncommitted_pinned_file_stops_the_full_guard(at, monkeypatch) -> None:
    ws = at("repaired")
    rel = "src/splatoon3_ai_coach/types.py"
    (ws.repo / rel).write_bytes(b"uncommitted\n")
    called = _recorders(monkeypatch)
    for name in COMMAND_ARGS:
        _stops(name, called)
    assert (ws.repo / rel).read_bytes() == b"uncommitted\n"
    assert hashlib.sha256(b"uncommitted\n").hexdigest() != ws.production[rel]


def test_real_repository_is_never_written_by_these_tests() -> None:
    status = subprocess.run(
        ["git", "-C", str(REAL_REPO), "status", "--porcelain", "--", PILOT_RECORDS_DIR],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert status == ""
    assert json.dumps(sorted(COMMAND_ARGS))
