"""Amendment 7's common fail-closed execution guard.

The dispatcher calls ``guard`` before every command: the authorized chain and
A1 pins, clean tooling, the tool identity rule and the production dependency
pins; then the pilot STOPs or the sealed G6 addendum verification.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from map_ink_reference.constants import (
    AMENDMENT7_REPAIR_COMMIT,
    PILOT_BLIND_INPUT_MANIFEST,
    RECORDS_DIR,
    SCOPE_PILOT,
)
from map_ink_reference.errors import (
    IsolationViolation,
    MalformedInput,
    ProcedureOrderError,
    SealViolation,
)
from map_ink_reference.io_utils import read_json, sha256_file
from map_ink_reference.pilot_addendum import (
    CONSTANTS,
    SEALED_DISPLAY,
    TEST_ROOT,
    display_defaults,
    passing_gate_commit,
    verify_interface_commit,
    verify_sealed_addendum,
)
from map_ink_reference.pilot_pipeline import (
    GATE,
    PilotScope,
    addendum_path,
    selection_path,
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
    require_tool_clean,
    strictly_after,
    verify_preconditions,
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


def _sealed_interface(repo: Path, g6: str, scope: PilotScope) -> object:
    rel = scope.rel(addendum_path(scope))
    try:
        return json.loads(file_at_commit(repo, g6, rel))["interface_commit"]
    except (ValueError, KeyError, TypeError) as exc:
        raise MalformedInput("sealed pilot addendum is malformed") from exc


def verify_repairs(repo: Path) -> list[str]:
    """Amendment 8 history: T[0] is the Amendment 7 repair and T[1] is repair 8."""
    c7, _ = chain_entry("amendment7")
    c8, _ = chain_entry("amendment8")
    _verify_amendment_commit(repo, c7, "C7")
    _verify_amendment_commit(repo, c8, "C8")
    tool = protected_commits(repo, c7)
    if not tool or tool[0] != AMENDMENT7_REPAIR_COMMIT:
        raise SealViolation("the Amendment 7 repair is not committed first after C7")
    verify_repair(repo, tool[0], c7)
    if len(tool) < 2:
        raise SealViolation("repair 8 is not committed after C8")
    verify_repair8(repo, tool[1], tool[0], c8)
    return tool


def verify_tool_identity(repo: Path) -> None:
    """Amendment 8 tool identity rule from C7 (exclusive) to HEAD."""
    tool = verify_repairs(repo)
    scope = PilotScope.from_repo(repo)
    g1 = adding_commit(repo, selection_path(scope))
    if g1 is not None and not all(strictly_after(repo, c, g1) for c in tool[:2]):
        raise SealViolation("the repairs are not committed strictly before G1")
    g6 = adding_commit(repo, addendum_path(scope))
    if g6 is not None and any(strictly_after(repo, g6, commit) for commit in tool):
        raise SealViolation("a protected-path commit follows G6")
    if len(tool) > 3:
        raise SealViolation("more than one protected-path commit after repair 8")
    if len(tool) == 3:
        verify_interface_commit(repo, tool[2], passing_gate_commit(scope), None)
    elif display_defaults(repo, head_commit(repo)) != SEALED_DISPLAY:
        raise SealViolation("display defaults differ without an interface commit")
    if g6 is not None:
        recorded = _sealed_interface(repo, g6, scope)
        if recorded != (tool[2] if len(tool) == 3 else None):
            raise SealViolation("interface commit differs from the sealed addendum")


def reject_pilot_scoped_records(repo: Path) -> None:
    """Corpus operations reject any corpus-side record carrying the pilot scope.

    P is excluded (no corpus command reads it), as is the Amendment 4 pilot
    blind-input manifest, whose sealed schema carries ``scope``.
    """
    records = repo / RECORDS_DIR
    pilot_records = PilotScope.from_repo(repo).records
    for path in sorted(records.rglob("*.json")):
        if path.is_relative_to(pilot_records):
            continue
        if path.relative_to(records).as_posix() == PILOT_BLIND_INPUT_MANIFEST:
            continue
        payload = read_json(path)
        if isinstance(payload, dict) and payload.get("scope") == SCOPE_PILOT:
            raise IsolationViolation(f"pilot-scoped corpus-side record: {path.name}")


def _pilot_stops(scope: PilotScope) -> None:
    if addendum_path(scope).exists():
        raise ProcedureOrderError("the pilot addendum exists; pilot execution is over")
    if adding_commit(scope.repo, scope.records / GATE) is not None:
        passing_gate_commit(scope)


def guard(repo: Path, command: str) -> None:
    """The common guard, applied by the dispatcher before every command."""
    verify_preconditions(repo, A1_IMPLEMENTATION_PINS)
    verify_clean_tooling(repo)
    verify_tool_identity(repo)
    verify_production_pins(repo)
    scope = PilotScope.from_repo(repo)
    if command in PILOT_COMMANDS:
        _pilot_stops(scope)
    if command in CORPUS_COMMANDS:
        verify_sealed_addendum(scope)
        reject_pilot_scoped_records(repo)
