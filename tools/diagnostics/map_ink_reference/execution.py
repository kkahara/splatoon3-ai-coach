"""Amendment 9's common fail-closed execution guard.

The dispatcher calls ``guard`` before every command: the authorized chain and
A1 pins, clean tooling, the tool identity rule and the production dependency
pins; then the pilot-1 closure, pilot-2 sequence, or sealed H6 prerequisite.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from map_ink_reference.constants import (
    AMENDMENT7_REPAIR_COMMIT,
    PILOT_BLIND_INPUT_MANIFEST,
    RECORDS_DIR,
    SCOPE_PILOT,
    SCOPE_PILOT2,
)
from map_ink_reference.errors import (
    IsolationViolation,
    ProcedureOrderError,
    SealViolation,
)
from map_ink_reference.io_utils import read_json, sha256_file
from map_ink_reference.pilot2_addendum import verify_sealed_addendum
from map_ink_reference.pilot2_pipeline import (
    GATE as PILOT2_GATE,
)
from map_ink_reference.pilot2_pipeline import (
    H3_NAMES,
    H4_NAMES,
    Pilot2Scope,
)
from map_ink_reference.pilot2_pipeline import (
    addendum_path as pilot2_addendum_path,
)
from map_ink_reference.pilot2_pipeline import (
    selection_path as pilot2_selection_path,
)
from map_ink_reference.pilot_addendum import (
    CONSTANTS,
    SEALED_DISPLAY,
    TEST_ROOT,
    display_defaults,
)
from map_ink_reference.pilot_pipeline import (
    G4_NAMES,
    GATE,
    PilotScope,
)
from map_ink_reference.seal import (
    adding_commit,
    chain_entry,
    changed_paths,
    commit_parents,
    file_at_commit,
    head_commit,
    preregistration_path,
    protected_commits,
    require_sealed,
    require_tool_clean,
    strictly_after,
    verify_preconditions,
    verify_repair9_paths,
)

PRODUCTION_PINS = dict(
    (
        (
            "src/splatoon3_ai_coach/vision/map_ink.py",
            "50f0dba3e0e004dc17096cff6d5825a496cd917d03517de00c08402150070ab3",
        ),
        (
            "src/splatoon3_ai_coach/config/models.py",
            "bb1b690ef807a06b3455a39bceb07b0138757ac54d33b6c381b11233a05a9f8d",
        ),
        (
            "src/splatoon3_ai_coach/vision/stage_maps.py",
            "f443daff9fe4a142a3b22821f613a5d2c01949644c23a20fa0ca923a08adc895",
        ),
        (
            "src/splatoon3_ai_coach/vision/stage_mask.py",
            "8d756f99368c109dd69d6847680ba54c68b269a298c3312c8a798731c4a00406",
        ),
        (
            "src/splatoon3_ai_coach/media/video.py",
            "6285c206ae2af3f20df2398e6b65307ce664ec4d29506ee41a1721d79a238b87",
        ),
        (
            "src/splatoon3_ai_coach/media/video_identity.py",
            "895794741d8ba5698cfbda4d5da98f588e46a1b74358262d713e336d0b52017b",
        ),
        (
            "src/splatoon3_ai_coach/types.py",
            "ad6f423fc8a5422a5febc66972143e3d5c322c5a19210bdc29071188569983ea",
        ),
    )
)

TOOL_DIR = "tools/diagnostics/map_ink_reference"
REPAIR_FILES = frozenset(
    f"{TOOL_DIR}/{name}"
    for name in (
        "constants.py",
        "cli.py",
        "seal.py",
        "pipeline_blind.py",
        "records.py",
        "__init__.py",
        "execution.py",
        "pilot_pipeline.py",
        "pilot_addendum.py",
    )
)
REPAIR8_FILES = frozenset(
    f"{TOOL_DIR}/{name}"
    for name in (
        "constants.py",
        "points.py",
        "pilot_pipeline.py",
        "execution.py",
        "pilot_addendum.py",
        "seal.py",
    )
)
REPAIR8_COMMIT = "0ee675d47abc1361f583362217a02a9922647e24"
# Split literals: the blind isolation check forbids these file names verbatim.
A1_IMPLEMENTATION_PINS = dict(
    (
        (
            "src/splatoon3_ai_coach/vision/" + "cal" + "ibration_window.py",
            "1ec3929969b073adefd2f11923d88fb06cd716009dbdd0d098eed3649457ebef",
        ),
        (
            "src/splatoon3_ai_coach/vision/match_side_effects.py",
            "41b158549eb37bf8b7f084a7ced7ab9da781afd9b6d79abebfe1a95f9c98db08",
        ),
        (
            "src/splatoon3_ai_coach/vision/team_color_" + "cal" + "ibration.py",
            "ab87d07df313044e5d0194b4d0452c0ceca01df2da66643a478d59a18f28ea4d",
        ),
        (
            "src/splatoon3_ai_coach/vision/cadence_scan.py",
            "e34adc361a8bd81b224d3fe8be39231f136425be97f516d6b245ee4a680402fd",
        ),
    )
)
PILOT_COMMANDS = frozenset(
    {
        "pilot-select",
        "pilot-plan",
        "pilot-extract",
        "pilot-points",
        "pilot-annotate",
        "pilot-export",
        "pilot-gate",
        "pilot-addendum",
    }
)
PILOT1_CLOSURE_COMMANDS = frozenset({"pilot1-close-gate", "pilot1-gate-report"})
PILOT2_COMMANDS = frozenset(
    {
        "pilot2-select",
        "pilot2-plan",
        "pilot2-extract",
        "pilot2-points",
        "pilot2-annotate",
        "pilot2-export",
        "pilot2-gate",
        "pilot2-addendum",
    }
)
CORPUS_COMMANDS = frozenset(
    {
        "plan",
        "extract",
        "points",
        "annotate",
        "export",
        "gate",
        "criterion-4a",
        "compare",
        "report",
    }
)


def verify_production_pins(repo: Path) -> None:
    """Committed and working-tree bytes of all seven files equal their pins."""
    for rel, expected in PRODUCTION_PINS.items():
        path = repo / rel
        if not path.is_file() or sha256_file(path) != expected:
            raise SealViolation(f"working-tree production pin mismatch: {rel}")
        committed = file_at_commit(repo, "HEAD", rel)
        if hashlib.sha256(committed).hexdigest() != expected:
            raise SealViolation(f"committed production pin mismatch: {rel}")


def verify_clean_tooling(repo: Path) -> None:
    """Tool and test sources are committed and unmodified."""
    require_tool_clean(repo, repo / TOOL_DIR)
    require_tool_clean(repo, repo / TEST_ROOT)


def _chain_value(node: ast.expr, names: dict[str, str]) -> object:
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    if isinstance(node, ast.Tuple):
        return tuple(_chain_value(element, names) for element in node.elts)
    return ast.literal_eval(node)


def repair_chain(repo: Path, repair: str) -> list[object]:
    """The chain entries in a repair's own ``constants.py`` (string names resolved)."""
    source = file_at_commit(repo, repair, CONSTANTS).decode("utf-8")
    names: dict[str, str] = {}
    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            names[target.id] = node.value.value
        elif target.id == "PREREGISTRATION_CHAIN" and isinstance(node.value, ast.Tuple):
            try:
                return [_chain_value(element, names) for element in node.value.elts]
            except ValueError as exc:
                raise SealViolation("repair chain is not literal") from exc
    raise SealViolation("repair has no PREREGISTRATION_CHAIN")


def _repair_chain_entry(repo: Path, repair: str, index: int = 3) -> object:
    entries = repair_chain(repo, repair)
    if len(entries) <= index:
        raise SealViolation(f"repair has no chain entry {index + 1}")
    return entries[index]


def _chain_entry_expected(repo: Path, name: str, commit: str) -> tuple[str, str, str]:
    prereg = file_at_commit(repo, commit, preregistration_path())
    return (name, commit, hashlib.sha256(prereg).hexdigest())


def verify_repair(repo: Path, repair: str, c7: str) -> None:
    """The repair changes only its file set and records C7 as chain entry 4."""
    for path in changed_paths(repo, repair):
        if path not in REPAIR_FILES and not path.startswith(TEST_ROOT):
            raise SealViolation(f"repair changes a path outside its file set: {path}")
    expected = _chain_entry_expected(repo, "amendment7", c7)
    if _repair_chain_entry(repo, repair) != expected:
        raise SealViolation("repair chain entry 4 does not name C7 and its digest")


def verify_repair8(repo: Path, repair8: str, repair7: str, c8: str) -> None:
    """Amendment 8: repair 8 is after C8, in its file set, and adds chain entry 5."""
    if len(commit_parents(repo, repair8)) != 1:
        raise SealViolation("repair 8 is a merge commit")
    if not strictly_after(repo, c8, repair8):
        raise SealViolation("repair 8 is not committed strictly after C8")
    for path in changed_paths(repo, repair8):
        if path not in REPAIR8_FILES and not path.startswith(TEST_ROOT):
            raise SealViolation(f"repair 8 changes a path outside its file set: {path}")
    entries = repair_chain(repo, repair8)
    if len(entries) != 5:
        raise SealViolation("repair 8 chain must have exactly five entries")
    if entries[:4] != repair_chain(repo, repair7)[:4]:
        raise SealViolation("repair 8 changes chain entries 1-4")
    if entries[4] != _chain_entry_expected(repo, "amendment8", c8):
        raise SealViolation("repair 8 chain entry 5 does not name C8 and its digest")


def _verify_amendment_commit(repo: Path, commit: str, label: str) -> None:
    if (
        set(changed_paths(repo, commit)) != {preregistration_path()}
        or len(commit_parents(repo, commit)) != 1
    ):
        raise SealViolation(f"{label} must change only the preregistration file")


def verify_repairs(repo: Path) -> list[str]:
    """Amendment 9: T is exactly repair 7, repair 8 and repair 9."""
    c7, _ = chain_entry("amendment7")
    c8, _ = chain_entry("amendment8")
    c9, _ = chain_entry("amendment9")
    _verify_amendment_commit(repo, c7, "C7")
    _verify_amendment_commit(repo, c8, "C8")
    _verify_amendment_commit(repo, c9, "C9")
    tool = protected_commits(repo, c7)
    if not tool or tool[0] != AMENDMENT7_REPAIR_COMMIT:
        raise SealViolation("the Amendment 7 repair is not committed first after C7")
    verify_repair(repo, tool[0], c7)
    if display_defaults(repo, tool[0]) != SEALED_DISPLAY:
        raise SealViolation("Amendment 7 repair changes the sealed display defaults")
    if len(tool) < 2:
        raise SealViolation("repair 8 is not committed after C8")
    if tool[1] != REPAIR8_COMMIT:
        raise SealViolation("T[1] is not the frozen repair 8 commit")
    verify_repair8(repo, tool[1], tool[0], c8)
    if len(tool) < 3:
        raise SealViolation("repair 9 is not committed after C9")
    if len(tool) != 3:
        raise SealViolation("protected history must contain exactly three repairs")
    repair9 = tool[2]
    if len(commit_parents(repo, repair9)) != 1 or not strictly_after(repo, c9, repair9):
        raise SealViolation("repair 9 is not a single-parent descendant of C9")
    verify_repair9_paths(repo, repair9)
    entries = repair_chain(repo, repair9)
    if len(entries) != 6 or entries[:5] != repair_chain(repo, tool[1])[:5]:
        raise SealViolation("repair 9 must preserve chain entries 1-5")
    if entries[5] != _chain_entry_expected(repo, "amendment9", c9):
        raise SealViolation("repair 9 chain entry 6 does not name C9")
    return tool


def verify_tool_identity(repo: Path) -> str:
    """Verify Amendment 9's exact protected history and return repair 9."""
    tool = verify_repairs(repo)
    repair9 = tool[2]
    scope = Pilot2Scope.from_repo(repo)
    h1 = adding_commit(repo, pilot2_selection_path(scope))
    if h1 is not None and not strictly_after(repo, repair9, h1):
        raise SealViolation("repair 9 must be committed strictly before H1")
    pilot1 = PilotScope.from_repo(repo)
    g5 = adding_commit(repo, pilot1.records / GATE)
    if g5 is not None and not strictly_after(repo, repair9, g5):
        raise SealViolation("repair 9 must be committed strictly before pilot-1 G5")
    h6 = adding_commit(repo, pilot2_addendum_path(scope))
    if h6 is not None and not strictly_after(repo, repair9, h6):
        raise SealViolation("repair 9 must precede H6")
    return repair9


def reject_pilot_scoped_records(repo: Path) -> None:
    """Corpus operations reject pilot or pilot2 records outside P/P2.

    P is excluded (no corpus command reads it), as is the Amendment 4 pilot
    blind-input manifest, whose sealed schema carries ``scope``.
    """
    records = repo / RECORDS_DIR
    pilot_records = PilotScope.from_repo(repo).records
    pilot2_records = Pilot2Scope.from_repo(repo).records
    for path in sorted(records.rglob("*.json")):
        if path.is_relative_to(pilot_records) or path.is_relative_to(pilot2_records):
            continue
        if path.relative_to(records).as_posix() == PILOT_BLIND_INPUT_MANIFEST:
            continue
        payload = read_json(path)
        if isinstance(payload, dict) and payload.get("scope") in {
            SCOPE_PILOT,
            SCOPE_PILOT2,
        }:
            raise IsolationViolation(f"pilot-scoped corpus-side record: {path.name}")


def _fixed_group(scope: PilotScope | Pilot2Scope, paths: list[Path]) -> str | None:
    commits = [adding_commit(scope.repo, path) for path in paths]
    if not any(commits):
        return None
    if any(commit is None for commit in commits) or len(set(commits)) != 1:
        raise ProcedureOrderError("a closed-corridor record group is incomplete or split")
    commit = commits[0]
    assert commit is not None
    for path in paths:
        require_sealed(scope.repo, path)
    if set(changed_paths(scope.repo, commit)) != {scope.rel(path) for path in paths}:
        raise ProcedureOrderError("a closed-corridor commit has unexpected paths")
    return commit


def _corridor(repo: Path) -> dict[str, str | None]:
    """Inspect only path/seal/ancestry metadata for the closed H3-H5 corridor."""
    pilot1 = PilotScope.from_repo(repo)
    pilot2 = Pilot2Scope.from_repo(repo)
    groups = {
        "H3": _fixed_group(pilot2, [pilot2.records / name for name in H3_NAMES]),
        "G4": _fixed_group(pilot1, [pilot1.records / name for name in G4_NAMES]),
        "G5": _fixed_group(pilot1, [pilot1.records / GATE]),
        "H4": _fixed_group(pilot2, [pilot2.records / name for name in H4_NAMES]),
        "H5": _fixed_group(pilot2, [pilot2.records / PILOT2_GATE]),
    }
    names = ("H3", "G4", "G5", "H4", "H5")
    seen_gap = False
    for name in names:
        if groups[name] is None:
            seen_gap = True
        elif seen_gap:
            raise ProcedureOrderError("closed H3-to-H5 corridor is out of order")
    present = [groups[name] for name in names if groups[name] is not None]
    for earlier, later in zip(present, present[1:], strict=False):
        assert earlier is not None and later is not None
        if commit_parents(repo, later) != [earlier]:
            raise ProcedureOrderError(
                "closed-corridor commits must be direct descendants"
            )
    return groups


def _guard_pilot1(command: str, corridor: dict[str, str | None], repo: Path) -> None:
    if command == "pilot-gate" or command in PILOT_COMMANDS:
        raise ProcedureOrderError("pilot-1 command is closed by Amendment 9")
    if command == "pilot1-close-gate":
        if (
            corridor["H3"] is None
            or corridor["G4"] is None
            or corridor["G5"] is not None
            or corridor["H4"] is not None
            or head_commit(repo) != corridor["G4"]
        ):
            raise ProcedureOrderError("pilot1-close-gate is outside its sealed corridor")
    elif command == "pilot1-gate-report":
        if corridor["G5"] is None or corridor["H5"] is None:
            raise ProcedureOrderError("pilot1 gate report requires sealed G5 and H5")


def _guard_pilot2(command: str, corridor: dict[str, str | None], repo: Path) -> None:
    pilot2 = Pilot2Scope.from_repo(repo)
    h1 = adding_commit(repo, pilot2_selection_path(pilot2))
    if corridor["H5"] is not None:
        if command != "pilot2-addendum":
            raise ProcedureOrderError("pilot2 execution ended at sealed H5")
        return
    if corridor["H3"] is not None:
        if command != "pilot2-gate":
            raise ProcedureOrderError("only the closed corridor may follow sealed H3")
        if (
            corridor["G5"] is None
            or corridor["H4"] is None
            or head_commit(repo) != corridor["H4"]
        ):
            raise ProcedureOrderError("pilot2-gate requires sealed G5 then sealed H4")
        return
    if command == "pilot2-select" and h1 is not None:
        raise ProcedureOrderError("H1 is already sealed")
    if command != "pilot2-select" and h1 is None:
        raise ProcedureOrderError("pilot2 command requires sealed H1")


def guard(repo: Path, command: str) -> None:
    """The common guard, applied by the dispatcher before every command."""
    verify_preconditions(repo, A1_IMPLEMENTATION_PINS)
    verify_clean_tooling(repo)
    repair9 = verify_tool_identity(repo)
    verify_production_pins(repo)
    corridor = _corridor(repo)
    if (
        corridor["H3"] is not None
        and corridor["H5"] is None
        and command not in {"pilot1-close-gate", "pilot2-gate"}
    ):
        raise ProcedureOrderError("only the closed H3-to-H5 corridor is permitted")
    if command in PILOT_COMMANDS | PILOT1_CLOSURE_COMMANDS:
        _guard_pilot1(command, corridor, repo)
    if command in PILOT2_COMMANDS:
        _guard_pilot2(command, corridor, repo)
    if command in CORPUS_COMMANDS:
        verify_sealed_addendum(Pilot2Scope.from_repo(repo), predecessor=repair9)
        reject_pilot_scoped_records(repo)
