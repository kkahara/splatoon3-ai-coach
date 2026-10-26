"""Amendment 8 testing requirements 1–6 (synthetic fixtures only).

Neither the prepared pilot sources nor any corpus recording is used: every
repository, video, manifest and record here is written by the tests. The
real repository is read only for tool source at the Amendment 7 repair
(corpus byte-identity), the chain constants and the preregistration digest
at the sealed C8 commit.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction
from math import ceil
from pathlib import Path

import map_ink_reference.execution as execution
import map_ink_reference.pipeline_blind as blind
import map_ink_reference.seal as seal
import numpy as np
import pytest
from map_ink_reference.constants import (
    AMENDMENT4_COMMIT,
    AMENDMENT7_REPAIR_COMMIT,
    CORRECTNESS_APPLICABLE,
    DUPLICATE_MIN_GAP,
    DUPLICATE_SEED_PREFIX,
    PREREGISTRATION_CHAIN,
    PREREGISTRATION_PATH,
)
from map_ink_reference.errors import (
    MalformedInput,
    ProcedureOrderError,
    ReferenceProcedureError,
)
from map_ink_reference.gate import population
from map_ink_reference.io_utils import read_json, sha256_file, write_json
from map_ink_reference.pilot_pipeline import (
    DUPLICATES,
    POINTS,
    addendum_path,
    extract_plans,
    pilot_groups,
    session_inputs,
    write_plans,
    write_points,
)
from map_ink_reference.points import (
    duplicate_count,
    duplicate_point_ids,
    pilot_duplicate_count,
)
from map_ink_reference.queueing import build_queue
from map_ink_reference.records import export_session_records
from mir_amendment7 import (
    C8_TEXT,
    C9_TEXT,
    CHAIN_END,
    CONSTANTS_REL,
    REPAIR8_TEST,
    TEST_REL,
    TOOL,
    TOOL_REL,
    Workspace,
    build_checkpoints,
    commit_repair8,
    commit_repair9,
    constants_source,
)
from mir_helpers import Counter, commit, git
from test_mir_amendment7 import (
    COMMAND_ARGS,
    _edit,
    _main,
    _marker_commit,
    _recorders,
    _replay,
    _stops,
)

REAL_REPO = Path(__file__).resolve().parents[2]
C8 = "04caeda69ccca22941800bd0ff4195df0aae813b"
C8_SHA256 = "6959f153dc6a2b51622a4480126e7853534c2abf81a9debdda1092fc9dabcb97"
C8_SIZE = 163371
REPAIR7 = "3883dd7bd14877705f61fe44b3b46a2c46a47afc"
REPAIR8_TEXT = '"""Synthetic repair 8 test."""\n'
SUPERSEDED_HISTORY = pytest.mark.skip(
    reason="Amendment 9 replaces the movable Repair-8/interface history topology"
)


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def checkpoints(tmp_path_factory) -> dict[str, Workspace]:
    return build_checkpoints(tmp_path_factory.mktemp("amendment8"))


@pytest.fixture
def at(checkpoints, tmp_path: Path, monkeypatch) -> Callable[[str], Workspace]:
    """Independent, patched copy of a checkpoint."""
    count = iter(range(1000))

    def make(name: str) -> Workspace:
        workspace = checkpoints[name].copy_to(tmp_path / f"copy{next(count)}")
        workspace.patch(monkeypatch)
        return workspace

    return make


def _git_show(commit_id: str, rel: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(REAL_REPO), "show", f"{commit_id}:{rel}"],
        capture_output=True,
        check=True,
    ).stdout


def _after(ws: Workspace, commit_id: str) -> list[str]:
    return git(ws.repo, "rev-list", "--reverse", f"{commit_id}..HEAD").split()


def _repair8_edits(ws: Workspace, constants: str | None = None) -> dict[str, str]:
    source = constants or constants_source(ws.c7, ws.digest, c8=ws.c8, digest8=ws.digest8)
    return {CONSTANTS_REL: source, REPAIR8_TEST: REPAIR8_TEXT}


def _rebuild_repair8(ws: Workspace, edits: dict[str, str], monkeypatch) -> Workspace:
    """Replace repair 8 by a commit of ``edits`` on C8; replay the rest."""
    rest = [
        commit_id
        for commit_id in _after(ws, ws.repair8)
        if commit_id not in {ws.c9, ws.repair9}
    ]
    git(ws.repo, "reset", "-q", "--hard", ws.c8)
    repair8 = _edit(ws, edits, "repair 8")
    prereg = ws.repo / PREREGISTRATION_PATH
    prereg.write_text(prereg.read_text() + C9_TEXT)
    c9 = commit(ws.repo, [prereg], "C9")
    digest9 = hashlib.sha256(prereg.read_bytes()).hexdigest()
    repair9 = commit_repair9(ws.repo, c9, digest9)
    rebuilt = replace(
        _replay(ws, rest),
        repair8=repair8,
        c9=c9,
        digest9=digest9,
        repair9=repair9,
    )
    rebuilt.patch(monkeypatch)
    return rebuilt


def _identity_stops(ws: Workspace, match: str | None = None) -> None:
    with pytest.raises(ReferenceProcedureError, match=match):
        execution.verify_tool_identity(ws.repo)


# --------------------------------------------------------------------------
# Requirement 1: the pilot duplicate count is ceil(2N / 3), exactly
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n_points", "expected"),
    [
        (0, 0),
        (1, 1),
        (2, 2),
        (3, 2),
        (4, 3),
        (5, 4),
        (6, 4),
        (7, 5),
        (9, 6),
        (10, 7),
        (71, 48),
        (72, 48),
        (73, 49),
        (143, 96),
        (144, 96),
        (145, 97),
    ],
)
def test_pilot_duplicate_count_values(n_points: int, expected: int) -> None:
    assert pilot_duplicate_count(n_points) == expected


def test_pilot_duplicate_count_is_the_exact_integer_ceiling() -> None:
    for n_points in range(0, 3000):
        count = pilot_duplicate_count(n_points)
        assert count == ceil(Fraction(2 * n_points, 3))
        assert 3 * count >= 2 * n_points > 3 * (count - 1)
        assert 0 <= count <= n_points
    big = 10**30 + 1
    assert pilot_duplicate_count(big) == -(-2 * big // 3)
    assert isinstance(pilot_duplicate_count(144), int)


def test_pilot_duplicate_count_rejects_negative_populations() -> None:
    with pytest.raises(MalformedInput):
        pilot_duplicate_count(-1)


def test_the_corpus_count_does_not_satisfy_the_pilot_rule() -> None:
    assert duplicate_count(144) == 15 and pilot_duplicate_count(144) == 96
    assert duplicate_count(72) == 8 and pilot_duplicate_count(72) == 48
    for n_points in range(2, 3000):
        assert duplicate_count(n_points) != pilot_duplicate_count(n_points)


# --------------------------------------------------------------------------
# Requirement 2: frozen selection with the pilot count; other counts STOP
# --------------------------------------------------------------------------


def _frozen_selection(ids: list[str], digest: str, count: int) -> list[str]:
    """Independent statement of the frozen rule (seed text, PCG64, no replacement)."""
    seed_text = f"amendment3-dups|{digest}".encode()
    seed = int.from_bytes(hashlib.sha256(seed_text).digest()[:8], "big")
    rng = np.random.Generator(np.random.PCG64(seed))
    return [ids[int(i)] for i in rng.choice(len(ids), size=count, replace=False)]


@pytest.mark.parametrize("n_points", [1, 3, 10, 72, 144, 145])
def test_pilot_selection_is_the_frozen_rule_with_the_pilot_count(n_points: int) -> None:
    assert DUPLICATE_SEED_PREFIX == "amendment3-dups|"
    ids = [f"p{i:04d}" for i in range(n_points)]
    digest = hashlib.sha256(f"manifest {n_points}".encode()).hexdigest()
    count = pilot_duplicate_count(n_points)
    chosen = duplicate_point_ids(ids, digest, count=count)
    assert chosen == _frozen_selection(ids, digest, count)
    assert len(chosen) == len(set(chosen)) == count
    assert set(chosen) <= set(ids)


def _placement_holds(items: list[dict]) -> None:
    """Each second copy is >= 50 items after its first, or was placed at the end."""
    first = {}
    for position, item in enumerate(items):
        if item["copy"] == 0:
            first[item["point_ref"]] = position
            continue
        gap = position - first[item["point_ref"]]
        assert gap >= DUPLICATE_MIN_GAP or all(i["copy"] == 1 for i in items[position:])


def test_pilot_count_duplicates_keep_the_unchanged_placement() -> None:
    ids = [f"p{i:04d}" for i in range(144)]
    dups = duplicate_point_ids(ids, "0" * 64, count=pilot_duplicate_count(144))
    items, refs = build_queue(ids, dups, 7, Counter(1).new_id)
    assert len(items) == 144 + 96
    assert sum(item["copy"] for item in items) == 96
    assert sorted(refs[i["point_ref"]] for i in items if i["copy"] == 1) == sorted(dups)
    _placement_holds(items)


def test_sealed_pilot_duplicates_use_the_pilot_count(at) -> None:
    ws = at("g2")
    points = read_json(ws.scope.records / POINTS)["points"]
    record = read_json(ws.scope.records / DUPLICATES)
    digest = sha256_file(ws.scope.records / POINTS)
    ids = [p["point_id"] for p in points]
    count = pilot_duplicate_count(len(ids))
    assert count != duplicate_count(len(ids))
    assert record["point_manifest_sha256"] == digest
    assert record["duplicate_ids"] == _frozen_selection(ids, digest, count)
    session_inputs(ws.scope)


def test_sealed_pilot_queue_keeps_the_frozen_placement(at) -> None:
    ws = at("g4")
    records = ws.scope.records
    manifest = read_json(records / POINTS)
    swatches = read_json(records / "swatches.json")["swatches"]
    tokens = read_json(records / "token_key.json")["tokens"]
    valid = [
        r for r in manifest["recordings"] if swatches[tokens[r]]["status"] == "valid"
    ]
    members = population(
        read_json(records / DUPLICATES)["duplicate_ids"],
        {p["point_id"]: p["recording_id"] for p in manifest["points"]},
        valid,
    )
    items = read_json(records / "queue_stage1.json")["items"]
    refs = read_json(records / "queue_key_stage1.json")["point_refs"]
    seconds = sorted(refs[i["point_ref"]] for i in items if int(i["copy"]) == 1)
    assert seconds == sorted(members) and members
    _placement_holds([{**i, "copy": int(i["copy"])} for i in items])


def test_queueing_and_gate_modules_are_unchanged_from_c8() -> None:
    for name in ("queueing.py", "gate.py"):
        rel = f"{TOOL_REL}/{name}"
        assert (REAL_REPO / rel).read_bytes() == _git_show(C8, rel), name


@pytest.mark.parametrize(
    "rule",
    [
        duplicate_count,
        lambda n: pilot_duplicate_count(n) - 1,
        lambda n: pilot_duplicate_count(n) + 1,
        lambda n: n,
        lambda n: 0,
    ],
    ids=["corpus-count", "one-fewer", "one-more", "every-point", "none"],
)
def test_sealed_pilot_duplicates_with_another_count_stop(at, rule) -> None:
    ws = at("g1")
    write_plans(ws.scope, TOOL)
    extract_plans(ws.scope, TOOL)
    write_points(ws.scope, TOOL)
    record = read_json(ws.scope.records / DUPLICATES)
    ids = [p["point_id"] for p in read_json(ws.scope.records / POINTS)["points"]]
    count = rule(len(ids))
    assert count != pilot_duplicate_count(len(ids)) and 0 <= count <= len(ids)
    record["duplicate_ids"] = duplicate_point_ids(
        ids, record["point_manifest_sha256"], count=count
    )
    write_json(ws.scope.records / DUPLICATES, record)
    ws.commit(pilot_groups(ws.scope, ws.chosen())[1], "G2 pilot")
    with pytest.raises(MalformedInput, match="frozen duplicate rule"):
        session_inputs(ws.scope)


# --------------------------------------------------------------------------
# Requirement 3: corpus behaviour unchanged
# --------------------------------------------------------------------------


def _module_at(commit_id: str, name: str):
    """A tool module exactly as committed at ``commit_id`` (tool source only)."""
    source = _git_show(commit_id, f"{TOOL_REL}/{name}.py").decode("utf-8")
    spec = importlib.util.spec_from_loader(
        f"map_ink_reference._a8_{commit_id[:7]}_{name}", loader=None
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(source, f"{commit_id[:7]}:{name}.py", "exec"), module.__dict__)
    return module


@pytest.mark.parametrize("n_points", [0, 1, 9, 10, 11, 72, 144, 145, 1000])
def test_default_duplicate_selection_is_unchanged(n_points: int) -> None:
    old = _module_at(REPAIR7, "points")
    ids = [f"p{i:04d}" for i in range(n_points)]
    digest = hashlib.sha256(f"corpus {n_points}".encode()).hexdigest()
    expected = old.duplicate_point_ids(ids, digest)
    assert len(expected) == duplicate_count(n_points)
    assert duplicate_point_ids(ids, digest) == expected
    assert duplicate_point_ids(ids, digest, count=None) == expected
    assert duplicate_point_ids(ids, digest, count=duplicate_count(n_points)) == expected
    assert old.duplicate_count(n_points) == duplicate_count(n_points)


@pytest.mark.parametrize("count", [True, False, -1, 11, 1.0, "1"])
def test_invalid_explicit_counts_are_malformed(count) -> None:
    ids = [f"p{i}" for i in range(10)]
    with pytest.raises(MalformedInput):
        duplicate_point_ids(ids, "0" * 64, count=count)


def test_count_is_keyword_only() -> None:
    with pytest.raises(TypeError):
        duplicate_point_ids(["a", "b"], "0" * 64, 1)  # type: ignore[misc]


def test_corpus_records_are_byte_identical_to_the_amendment7_repair(
    tmp_path: Path, monkeypatch
) -> None:
    from test_mir_pipeline import Run, _manifest_names

    old_points = _module_at(REPAIR7, "points")
    old_blind = _module_at(REPAIR7, "pipeline_blind")
    old_records = _module_at(REPAIR7, "records")
    for name in ("StageFrame", "duplicate_point_ids", "sample_stage_points"):
        monkeypatch.setattr(old_blind, name, getattr(old_points, name))
    run = Run(tmp_path, monkeypatch)
    monkeypatch.setattr(old_blind, "verify_geometry_pins", blind.verify_geometry_pins)

    def same(path: Path, write_old, write_new) -> None:
        write_old()
        old = path.read_bytes()
        write_new()
        assert path.read_bytes() == old, path.name

    for rid in CORRECTNESS_APPLICABLE:
        same(
            blind.plan_path(run.records, rid),
            lambda r=rid: old_blind.write_plan(run.repo, run.records, r, TOOL),
            lambda r=rid: blind.write_plan(run.repo, run.records, r, TOOL),
        )
    run.plan_and_extract()
    for name in _manifest_names(1):
        same(
            run.records / name,
            lambda: old_blind.write_point_manifest(
                run.records, run.work, 1, list(CORRECTNESS_APPLICABLE), TOOL
            ),
            lambda: run.points(1, CORRECTNESS_APPLICABLE),
        )
    dups = read_json(run.records / "duplicates_stage1.json")
    n_points = len(read_json(run.records / "points_stage1.json")["points"])
    assert len(dups["duplicate_ids"]) == duplicate_count(n_points)
    names = [f"frame_plans/{r}.json" for r in CORRECTNESS_APPLICABLE]
    names += [f"extraction/{r}.json" for r in CORRECTNESS_APPLICABLE]
    run.commit_records(names + _manifest_names(1), "stage 1 manifest")
    assert old_blind.session_inputs(run.records, run.work, 1) == blind.session_inputs(
        run.records, run.work, 1
    )
    run.annotate(1)
    state = run.work / "session_stage1"
    old_files = old_records.export_session_records(state, tmp_path / "old", tool=TOOL)
    new_files = export_session_records(state, tmp_path / "new", tool=TOOL)
    assert old_files == new_files
    for name in new_files:
        old = (tmp_path / "old" / name).read_bytes()
        assert old == (tmp_path / "new" / name).read_bytes()
    run.seal_session(1)
    same(
        blind.gate_path(run.records, 1),
        lambda: old_blind.run_gate(run.records, 1, TOOL),
        lambda: blind.run_gate(run.records, 1, TOOL),
    )


# --------------------------------------------------------------------------
# Authorized chain: entries 1–4 unchanged, entry 5 is C8
# --------------------------------------------------------------------------


def test_chain_entries_1_to_4_are_unchanged() -> None:
    assert PREREGISTRATION_CHAIN[:4] == (
        (
            "amendment4",
            AMENDMENT4_COMMIT,
            "4ffeeaf899185d84beea1e97c59b0baa46798cb7390cf294fae3343804e757e1",
        ),
        (
            "amendment5",
            "ca4e08821ca9c80f45125c168fe3caf3af536a3e",
            "2a0a23ba0d4db8484eb7752861384546855dc65374d975580c7d11a77df6cb57",
        ),
        (
            "amendment6",
            "85d1104c1c969d3c9bdf7b179dda675d9c7748d5",
            "e978e63f4da562f85eb4d7c42ff918df0f769b0e85ff012e2670307d71ee88df",
        ),
        (
            "amendment7",
            "0a62f699894db5296b43102f1e77f7240a6f10de",
            "26ed135efbfb2127523601447f0edb1f4cef132daff4d1afc1bfb902fe6fb5a8",
        ),
    )
    assert AMENDMENT4_COMMIT == "570f188c3a2322c20065b095d740a6ad2538c57a"
    assert tuple(execution.repair_chain(REAL_REPO, REPAIR7)) == PREREGISTRATION_CHAIN[:4]


def test_chain_entry_5_exactly_identifies_c8() -> None:
    assert len(PREREGISTRATION_CHAIN) == 6
    assert PREREGISTRATION_CHAIN[4] == ("amendment8", C8, C8_SHA256)
    assert seal.chain_entry("amendment8") == (C8, C8_SHA256)
    content = _git_show(C8, PREREGISTRATION_PATH)
    assert len(content) == C8_SIZE
    assert hashlib.sha256(content).hexdigest() == C8_SHA256
    assert seal.commit_parents(REAL_REPO, C8) == [REPAIR7]
    assert set(seal.changed_paths(REAL_REPO, C8)) == {PREREGISTRATION_PATH}


def test_the_amendment7_repair_is_pinned() -> None:
    assert AMENDMENT7_REPAIR_COMMIT == REPAIR7
    assert execution.AMENDMENT7_REPAIR_COMMIT == REPAIR7


# --------------------------------------------------------------------------
# Requirement 4: Amendment 8 identities remain fixed inside Amendment 9 history
# --------------------------------------------------------------------------


@pytest.mark.parametrize("checkpoint", ["repaired", "g1", "g2", "g4", "g5_pass"])
def test_the_first_two_repairs_are_preserved_in_valid_history(at, checkpoint) -> None:
    ws = at(checkpoint)
    assert seal.protected_commits(ws.repo, ws.c7) == [
        ws.repair7,
        ws.repair8,
        ws.repair9,
    ]
    execution.verify_tool_identity(ws.repo)


def test_repair8_may_change_every_file_of_its_set(at, monkeypatch) -> None:
    ws = at("repaired")
    edits = _repair8_edits(ws)
    for name in sorted(execution.REPAIR8_FILES - {CONSTANTS_REL}):
        edits[name] = f"# synthetic {name}\n"
    edits[f"{TEST_REL}/test_mir_amendment8_synthetic.py"] = "# new test\n"
    ws = _rebuild_repair8(ws, edits, monkeypatch)
    execution.verify_tool_identity(ws.repo)
    assert execution.REPAIR8_FILES == {
        f"{TOOL_REL}/{name}"
        for name in (
            "constants.py",
            "points.py",
            "pilot_pipeline.py",
            "execution.py",
            "pilot_addendum.py",
            "seal.py",
        )
    }


@pytest.mark.parametrize(
    "outside",
    [
        f"{TOOL_REL}/plan.py",
        f"{TOOL_REL}/session.py",
        f"{TOOL_REL}/gate.py",
        f"{TOOL_REL}/queueing.py",
        "src/other.py",
        "configs/other.yaml",
    ],
)
def test_repair8_outside_its_file_set_stops(at, monkeypatch, outside) -> None:
    ws = at("repaired")
    edits = {**_repair8_edits(ws), outside: "# changed\n"}
    ws = _rebuild_repair8(ws, edits, monkeypatch)
    _identity_stops(ws, "outside its file set")


def _swap(old: str, new: str) -> Callable[[Workspace], str]:
    def make(ws: Workspace) -> str:
        source = constants_source(ws.c7, ws.digest, c8=ws.c8, digest8=ws.digest8)
        assert old in source
        return source.replace(old, new, 1)

    return make


BAD_CHAINS = {
    "no-entry-5": lambda ws: constants_source(ws.c7, ws.digest),
    "entry-6": lambda ws: constants_source(
        ws.c7, ws.digest, c8=ws.c8, digest8=ws.digest8
    ).replace(
        CHAIN_END,
        '    ("amendment9", "' + "9" * 40 + '", "' + "9" * 64 + '"),\n' + CHAIN_END,
    ),
    "entry-5-wrong-digest": lambda ws: _swap(ws.digest8, "0" * 64)(ws),
    "entry-5-wrong-commit": lambda ws: _swap(ws.c8, "1" * 40)(ws),
    "entry-5-wrong-name": lambda ws: _swap('"amendment8"', '"amendment9"')(ws),
    "entry-1-changed": _swap("4" * 40, "a" * 40),
    "entry-2-changed": _swap("d" * 64, "b" * 64),
    "entry-3-removed": lambda ws: _swap(
        '    ("amendment6", "' + "6" * 40 + '", "' + "e" * 64 + '"),\n', ""
    )(ws),
    "entry-4-changed": lambda ws: _swap(ws.digest, "c" * 64)(ws),
    "entries-reordered": lambda ws: _swap(
        '    ("amendment4", "' + "4" * 40 + '", "' + "f" * 64 + '"),\n'
        '    ("amendment5", "' + "5" * 40 + '", "' + "d" * 64 + '"),\n',
        '    ("amendment5", "' + "5" * 40 + '", "' + "d" * 64 + '"),\n'
        '    ("amendment4", "' + "4" * 40 + '", "' + "f" * 64 + '"),\n',
    )(ws),
}


@pytest.mark.parametrize("name", sorted(BAD_CHAINS))
def test_repair8_without_a_valid_chain_stops(at, monkeypatch, name) -> None:
    ws = at("repaired")
    edits = _repair8_edits(ws, BAD_CHAINS[name](ws))
    ws = _rebuild_repair8(ws, edits, monkeypatch)
    _identity_stops(ws, "chain|entry|entries")


def test_repair8_with_only_a_test_change_stops(at, monkeypatch) -> None:
    ws = at("repaired")
    ws = _rebuild_repair8(ws, {REPAIR8_TEST: REPAIR8_TEXT}, monkeypatch)
    _identity_stops(ws, "five entries")


@SUPERSEDED_HISTORY
def test_repair8_before_c8_stops(at, monkeypatch) -> None:
    ws = at("repaired")
    rest = [c for c in _after(ws, ws.repair7) if c not in (ws.c8, ws.repair8)]
    git(ws.repo, "reset", "-q", "--hard", ws.repair7)
    repair8 = _edit(ws, _repair8_edits(ws), "repair 8 before C8")
    git(ws.repo, "cherry-pick", "--allow-empty", ws.c8)
    moved = replace(ws, c8=ws.head(), repair8=repair8)
    moved = _replay(moved, rest)
    moved.patch(monkeypatch)
    assert seal.protected_commits(ws.repo, ws.c7) == [ws.repair7, repair8]
    _identity_stops(moved, "strictly after C8")


def test_repair8_equal_to_c8_stops(at, monkeypatch) -> None:
    ws = at("repaired")
    rest = [c for c in _after(ws, ws.repair7) if c not in (ws.c8, ws.repair8)]
    git(ws.repo, "reset", "-q", "--hard", ws.repair7)
    prereg = ws.repo / PREREGISTRATION_PATH
    prereg.write_text(prereg.read_text() + C8_TEXT)
    git(ws.repo, "add", "-f", PREREGISTRATION_PATH)
    both = _edit(ws, _repair8_edits(ws), "C8 and repair 8 together")
    moved = _replay(replace(ws, c8=both, repair8=both), rest)
    moved.patch(monkeypatch)
    _identity_stops(moved, "C8 must change only the preregistration")


@SUPERSEDED_HISTORY
def test_repair8_after_g1_stops(at, monkeypatch) -> None:
    ws = at("g1")
    rest = _after(ws, ws.repair8)
    git(ws.repo, "reset", "-q", "--hard", ws.c8)
    ws = _replay(ws, rest)
    ws = replace(ws, repair8=commit_repair8(ws.repo, ws.c8, ws.digest8))
    ws.patch(monkeypatch)
    _identity_stops(ws, "before G1")


def test_first_protected_commit_must_be_the_pinned_repair(at, monkeypatch) -> None:
    ws = at("repaired")
    execution.verify_tool_identity(ws.repo)
    monkeypatch.setattr(execution, "AMENDMENT7_REPAIR_COMMIT", "f" * 40)
    _identity_stops(ws, "Amendment 7 repair")


@SUPERSEDED_HISTORY
def test_c8_changing_more_than_the_preregistration_stops(at, monkeypatch) -> None:
    ws = at("repaired")
    rest = [c for c in _after(ws, ws.repair7) if c not in (ws.c8, ws.repair8)]
    git(ws.repo, "reset", "-q", "--hard", ws.repair7)
    prereg = ws.repo / PREREGISTRATION_PATH
    prereg.write_text(prereg.read_text() + C8_TEXT)
    (ws.repo / "README.md").write_text("other change\n")
    c8 = commit(ws.repo, [prereg, ws.repo / "README.md"], "C8 with another file")
    moved = replace(ws, c8=c8)
    moved = replace(moved, repair8=commit_repair8(ws.repo, c8, ws.digest8))
    moved = _replay(moved, rest)
    moved.patch(monkeypatch)
    _identity_stops(moved, "C8 must change only the preregistration")


def test_interface_commit_after_a_passing_g5_stops(at) -> None:
    ws = at("g5_pass")
    _marker_commit(ws)
    _identity_stops(ws, "exactly three")


def test_interface_commit_before_a_passing_g5_stops(at) -> None:
    for checkpoint in ("repaired", "g1", "g2", "g4"):
        ws = at(checkpoint)
        _marker_commit(ws)
        _identity_stops(ws)


def test_interface_commit_after_a_failing_g5_stops(at) -> None:
    ws = at("g5_fail")
    _marker_commit(ws)
    _identity_stops(ws, "exactly three")


def test_fourth_protected_commit_stops(at) -> None:
    ws = at("g5_pass")
    _marker_commit(ws)
    _edit(ws, {f"{TOOL_REL}/annotate.html": "<html>again</html>\n"}, "fourth")
    _identity_stops(ws, "exactly three")


@pytest.mark.parametrize(
    "third",
    [
        lambda ws: {f"{TOOL_REL}/points.py": "# second repair 8\n"},
        lambda ws: {f"{TOOL_REL}/execution.py": "# second repair 8\n"},
        lambda ws: {f"{TEST_REL}/test_repair8.py": "# changed\n"},
        lambda ws: {CONSTANTS_REL: ws.constants().replace("MIN_CROP_SIZE = 64", "x = 1")},
    ],
    ids=["points", "execution", "modified-test", "constants"],
)
def test_unauthorized_third_commit_stops(at, third) -> None:
    ws = at("g5_pass")
    _edit(ws, third(ws), "unauthorized third commit")
    _identity_stops(ws)


@SUPERSEDED_HISTORY
def test_protected_commit_between_the_repairs_stops(at, monkeypatch) -> None:
    ws = at("repaired")
    rest = _after(ws, ws.repair8)
    git(ws.repo, "reset", "-q", "--hard", ws.c8)
    _edit(ws, {f"{TOOL_REL}/annotate.html": "<html>early</html>\n"}, "between")
    ws = replace(ws, repair8=commit_repair8(ws.repo, ws.c8, ws.digest8))
    ws = _replay(ws, rest)
    ws.patch(monkeypatch)
    _identity_stops(ws, "repair 8 changes a path outside its file set")


@SUPERSEDED_HISTORY
def test_interface_without_repair8_stops(at, monkeypatch) -> None:
    ws = at("g5_pass")
    rest = _after(ws, ws.repair8)
    git(ws.repo, "reset", "-q", "--hard", ws.c8)
    ws = _replay(ws, rest)
    ws.patch(monkeypatch)
    _identity_stops(ws, "repair 8 is not committed")
    _marker_commit(ws)
    _identity_stops(ws, "repair 8 changes a path outside its file set")


def test_any_protected_commit_after_repair9_stops(at, monkeypatch) -> None:
    ws = at("g5_pass")
    execution.verify_tool_identity(ws.repo)
    _edit(ws, {f"{TOOL_REL}/annotate.html": "<html>late</html>\n"}, "after G6")
    _identity_stops(ws)
    called = _recorders(monkeypatch)
    for name in COMMAND_ARGS:
        _stops(name, called)


def _merge_side(ws: Workspace, base: str) -> None:
    git(ws.repo, "checkout", "-q", "-b", "side", base)
    (ws.repo / "side.txt").write_text("side\n")
    commit(ws.repo, [ws.repo / "side.txt"], "side")
    git(ws.repo, "checkout", "-q", "-")
    git(ws.repo, "merge", "-q", "--no-ff", "-m", "merge", "side")


def test_merge_after_repair8_stops(at) -> None:
    ws = at("repaired")
    _merge_side(ws, ws.c8)
    _identity_stops(ws, "merge commit")


def test_merge_between_the_repairs_stops(at, monkeypatch) -> None:
    ws = at("repaired")
    rest = _after(ws, ws.repair8)
    git(ws.repo, "reset", "-q", "--hard", ws.c8)
    _merge_side(ws, ws.repair7)
    ws = replace(ws, repair8=commit_repair8(ws.repo, ws.c8, ws.digest8))
    ws = _replay(ws, rest)
    ws.patch(monkeypatch)
    _identity_stops(ws, "merge commit")


# --------------------------------------------------------------------------
# Requirement 5: every command STOPs after C8 until repair 8 is committed
# --------------------------------------------------------------------------


def _without_repair8(ws: Workspace) -> Workspace:
    rest = _after(ws, ws.repair8)
    git(ws.repo, "reset", "-q", "--hard", ws.c8)
    return _replay(ws, rest)


def _all_commands_stop(called: list[str]) -> None:
    for name in COMMAND_ARGS:
        _stops(name, called)
    assert called == []


def test_every_command_stops_after_c9_until_repair9(at, monkeypatch) -> None:
    ws = at("repaired")
    git(ws.repo, "reset", "-q", "--hard", ws.c9)
    called = _recorders(monkeypatch)
    ws.patch(monkeypatch)
    _all_commands_stop(called)


def test_every_command_stops_on_an_invalid_repair8(at, monkeypatch) -> None:
    ws = at("repaired")
    edits = _repair8_edits(ws, BAD_CHAINS["entry-1-changed"](ws))
    _rebuild_repair8(ws, edits, monkeypatch)
    _all_commands_stop(_recorders(monkeypatch))


def test_the_dispatcher_guards_before_every_command(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(execution, "guard", lambda repo, command: seen.append(command))
    called = _recorders(monkeypatch)
    for name in COMMAND_ARGS:
        _main(name)
    assert seen == called == list(COMMAND_ARGS)


def test_guard_runs_the_common_checks_in_order(at, monkeypatch) -> None:
    ws = at("repaired")
    order: list[str] = []
    for name in (
        "verify_preconditions",
        "verify_clean_tooling",
        "verify_tool_identity",
        "verify_production_pins",
    ):
        original = getattr(execution, name)

        def record(*args, _name=name, _original=original, **kwargs):
            order.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(execution, name, record)
    execution.guard(ws.repo, "preconditions")
    assert order == [
        "verify_preconditions",
        "verify_clean_tooling",
        "verify_tool_identity",
        "verify_production_pins",
    ]


# --------------------------------------------------------------------------
# Amendment 9 supersedes the pilot-1 G6 and interface-commit lifecycle
# --------------------------------------------------------------------------


def test_pilot1_addendum_command_is_closed(at) -> None:
    ws = at("g5_pass")
    assert not addendum_path(ws.scope).exists()
    with pytest.raises(ProcedureOrderError):
        execution.guard(ws.repo, "pilot-addendum")
    assert not addendum_path(ws.scope).exists()


def test_no_post_gate_interface_commit_is_permitted(at) -> None:
    ws = at("g5_pass")
    _marker_commit(ws)
    _identity_stops(ws, "exactly three")
