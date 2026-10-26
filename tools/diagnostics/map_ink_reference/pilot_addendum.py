"""Amendment 7 pilot addendum (G6) and the single post-pilot interface commit.

``write_addendum`` creates the prospective G6 record from history through a
sealed passing G5 and the optional interface commit. ``verify_sealed_addendum``
checks a committed G6 before any corpus command. An uncommitted addendum is
never treated as sealed G6.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from map_ink_reference.constants import (
    MAX_CROP_SIZE,
    MIN_CROP_SIZE,
    PILOT_ADDENDUM_SCHEMA,
    PILOT_ALLOWED_CHANGES,
    SCOPE_PILOT,
)
from map_ink_reference.errors import (
    MalformedInput,
    ProcedureOrderError,
    SealViolation,
)
from map_ink_reference.io_utils import read_json, sha256_file, write_json
from map_ink_reference.pilot_pipeline import (
    GATE,
    PilotScope,
    addendum_path,
    pilot_groups,
    preflight,
    read_pilot_record,
    verify_groups,
)
from map_ink_reference.seal import (
    chain_entry,
    changed_paths,
    commit_parents,
    file_at_commit,
    head_commit,
    protected_commits,
    require_sealed,
    strictly_after,
)

TOOL_ROOT = "tools/diagnostics/map_ink_reference/"
TEST_ROOT = "tests/map_ink_reference/"
CONSTANTS = TOOL_ROOT + "constants.py"
RENDER = TOOL_ROOT + "render.py"
SERVER = TOOL_ROOT + "server.py"
PAGE = TOOL_ROOT + "annotate.html"
SESSION = TOOL_ROOT + "session.py"
INTERFACE_FILES = frozenset({CONSTANTS, RENDER, SERVER, PAGE, SESSION})
ITEM_FILES = {
    "crop_zoom": frozenset({CONSTANTS}),
    "marker": frozenset({RENDER, SESSION, PAGE}),
    "layout": frozenset({PAGE, SESSION}),
    "instructions": frozenset({PAGE}),
    "team_cue_wording": frozenset({PAGE}),
    "interface_fix": frozenset({PAGE, SERVER, RENDER, SESSION}),
}
DISPLAY_NAMES = ("DEFAULT_CROP_SIZE", "DEFAULT_ZOOM")
SEALED_DISPLAY = (96, 4)
ADDENDUM_KEYS = frozenset(
    {
        "schema",
        "scope",
        "amendment7_commit",
        "tool_commit",
        "pilot_records",
        "changes",
        "interface_commit",
    }
)
HANDLE_FORM = (
    "self._handle(lambda: encode_png(F(self._load(asset), point.x, point.y, settings)))"
)
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def amendment7_commit() -> str:
    """C7: this amendment's authorized chain entry."""
    return chain_entry("amendment7")[0]


def _source(repo: Path, commit: str, rel: str) -> str:
    return file_at_commit(repo, commit, rel).decode("utf-8")


def display_defaults(repo: Path, commit: str) -> tuple[int, int]:
    """``(DEFAULT_CROP_SIZE, DEFAULT_ZOOM)`` at ``commit``: integers, C in 64–160."""
    found: dict[str, int] = {}
    for node in ast.parse(_source(repo, commit, CONSTANTS)).body:
        targets = getattr(node, "targets", None) or [getattr(node, "target", None)]
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not set(names) & set(DISPLAY_NAMES):
            continue
        value = getattr(node, "value", None)
        if (
            not isinstance(node, ast.Assign)
            or len(names) != 1
            or names[0] in found
            or not isinstance(value, ast.Constant)
            or type(value.value) is not int
        ):
            raise SealViolation("display defaults must be single integer literals")
        found[names[0]] = value.value
    if set(found) != set(DISPLAY_NAMES):
        raise SealViolation("display defaults are missing from constants.py")
    crop, zoom = found["DEFAULT_CROP_SIZE"], found["DEFAULT_ZOOM"]
    if not MIN_CROP_SIZE <= crop <= MAX_CROP_SIZE:
        raise SealViolation(f"DEFAULT_CROP_SIZE {crop} is outside 64–160")
    return crop, zoom


def verify_crop_zoom(repo: Path, commit: str, changes: Sequence[str]) -> None:
    """``crop_zoom`` is listed if and only if either value differs from 96 / 4."""
    if ("crop_zoom" in changes) != (display_defaults(repo, commit) != SEALED_DISPLAY):
        raise SealViolation("crop_zoom disagrees with DEFAULT_CROP_SIZE / DEFAULT_ZOOM")


def _masked_constants(source: str) -> str:
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in DISPLAY_NAMES for t in node.targets
        ):
            node.value = ast.Constant(None)
    return ast.dump(tree)


def _render_import(tree: ast.Module) -> ast.ImportFrom:
    nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "map_ink_reference.render"
    ]
    if len(nodes) != 1:
        raise SealViolation("session.py must have exactly one render import")
    return nodes[0]


def _label_dict(tree: ast.Module) -> ast.Dict:
    methods = [
        n
        for cls in tree.body
        if isinstance(cls, ast.ClassDef) and cls.name == "AnnotationSession"
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "_label_task"
    ]
    returns = [n for m in methods for n in ast.walk(m) if isinstance(n, ast.Return)]
    if (
        len(methods) != 1
        or len(returns) != 1
        or not isinstance(returns[0].value, ast.Dict)
    ):
        raise SealViolation("AnnotationSession._label_task must return one dict literal")
    return returns[0].value


def _added_imports(old: list[ast.alias], new: list[ast.alias]) -> set[str]:
    """Existing names keep their order and alias; added names are new and unaliased."""
    matched, added = 0, []
    for alias in new:
        if matched < len(old) and ast.dump(alias) == ast.dump(old[matched]):
            matched += 1
        else:
            added.append(alias)
    names = [alias.name for alias in new]
    if matched != len(old) or len(set(names)) != len(names):
        raise SealViolation("session.py render imports were removed or reordered")
    if any(alias.asname is not None for alias in added):
        raise SealViolation("session.py adds an aliased render import")
    return {alias.name for alias in added}


def _literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return node.value is None or type(node.value) in (str, int, float, bool)
    return (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.USub, ast.UAdd))
        and isinstance(node.operand, ast.Constant)
        and type(node.operand.value) in (int, float)
    )


def _allowed_entry(node: ast.AST, render_names: Sequence[str]) -> bool:
    """A literal constant (or list/tuple of them), or the sealed render-handle form."""
    if _literal(node):
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        return all(_literal(element) for element in node.elts)
    dump = ast.dump(node)
    return any(
        dump
        == ast.dump(ast.parse(HANDLE_FORM.replace("F(", f"{name}("), mode="eval").body)
        for name in render_names
    )


def verify_session_change(repo: Path, base: str, commit: str) -> None:
    """``session.py`` may only add render imports and append label-task entries."""
    before = ast.parse(_source(repo, base, SESSION))
    after = ast.parse(_source(repo, commit, SESSION))
    old_import, new_import = _render_import(before), _render_import(after)
    added = _added_imports(old_import.names, new_import.names)
    old_dict, new_dict = _label_dict(before), _label_dict(after)
    count = len(old_dict.keys)
    if len(new_dict.keys) < count:
        raise SealViolation("session.py label-task entries were removed")
    existing = list(zip(old_dict.keys, old_dict.values, strict=True))
    kept = zip(new_dict.keys[:count], new_dict.values[:count], strict=True)
    for (old_key, old_value), (new_key, new_value) in zip(existing, kept, strict=True):
        if (
            old_key is None
            or new_key is None
            or ast.dump(old_key) != ast.dump(new_key)
            or ast.dump(old_value) != ast.dump(new_value)
        ):
            raise SealViolation("session.py label-task entries were reordered or changed")
    seen = {k.value for k, _ in existing if isinstance(k, ast.Constant)}
    render_names = [alias.name for alias in new_import.names]
    for key, value in zip(new_dict.keys[count:], new_dict.values[count:], strict=True):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise SealViolation("appended session entry has no string key")
        if key.value in seen:
            raise SealViolation(f"appended session entry repeats a key: {key.value}")
        seen.add(key.value)
        if not _allowed_entry(value, render_names):
            raise SealViolation(f"appended session entry has another form: {key.value}")
    new_import.names = [a for a in new_import.names if a.name not in added]
    new_dict.keys, new_dict.values = new_dict.keys[:count], new_dict.values[:count]
    if ast.dump(before) != ast.dump(after):
        raise SealViolation("session.py changes outside its sealed allowlist")


def _verify_items(
    repo: Path, commit: str, changed: Mapping[str, str], changes: Sequence[str]
) -> None:
    files = {path for path in changed if not path.startswith(TEST_ROOT)}
    covered = set().union(*(ITEM_FILES[item] for item in changes))
    if not files <= covered:
        raise SealViolation("interface changes are not covered by the listed items")
    for item in changes:
        if not ITEM_FILES[item] & files:
            raise SealViolation(f"listed item has no interface change: {item}")
    if "interface_fix" in changes and not any(
        path.startswith(TEST_ROOT) and status == "A" for path, status in changed.items()
    ):
        raise SealViolation("interface_fix requires an added test file")
    verify_crop_zoom(repo, commit, changes)


def verify_interface_commit(
    repo: Path, commit: str, gate_commit: str, changes: Sequence[str] | None
) -> None:
    """Operation 8: the single interface commit after a sealed passing G5.

    ``changes`` (the addendum items) adds the item → file map and ``crop_zoom``
    checks; the tool identity rule checks the rest before G6 exists.
    """
    if len(commit_parents(repo, commit)) != 1:
        raise SealViolation("interface commit is a merge commit")
    if not strictly_after(repo, gate_commit, commit):
        raise ProcedureOrderError("interface commit is not strictly after G5")
    changed = changed_paths(repo, commit)
    for path, status in changed.items():
        if path in INTERFACE_FILES:
            if status != "M":
                raise SealViolation(f"interface commit adds or deletes {path}")
        elif path.startswith(TEST_ROOT):
            if status != "A":
                raise SealViolation(f"interface commit changes an existing test: {path}")
        else:
            raise SealViolation(f"interface commit changes a path outside its set {path}")
    if CONSTANTS in changed and _masked_constants(
        _source(repo, gate_commit, CONSTANTS)
    ) != _masked_constants(_source(repo, commit, CONSTANTS)):
        raise SealViolation("interface commit changes constants beyond the two values")
    display_defaults(repo, commit)
    if SESSION in changed:
        verify_session_change(repo, gate_commit, commit)
    if changes is not None:
        _verify_items(repo, commit, changed, changes)


def validate_changes(changes: Any, interface_commit: Any) -> list[str]:
    """Closed ordered item list; ``interface_commit`` null iff no items."""
    if not isinstance(changes, list) or any(
        not isinstance(item, str) or item not in PILOT_ALLOWED_CHANGES for item in changes
    ):
        raise MalformedInput("changes must use the closed allowed-item ids")
    if changes != [item for item in PILOT_ALLOWED_CHANGES if item in changes] or len(
        set(changes)
    ) != len(changes):
        raise MalformedInput("changes must be unique and in the sealed order")
    if (not changes) != (interface_commit is None):
        raise MalformedInput("interface_commit must be null if and only if no changes")
    if interface_commit is not None and (
        not isinstance(interface_commit, str) or not _COMMIT.fullmatch(interface_commit)
    ):
        raise MalformedInput("interface_commit is not a 40-hex commit")
    return list(changes)


def passing_gate_commit(scope: PilotScope) -> str:
    """The sealed G5 commit; a failing pilot gate is a mandatory STOP."""
    gate = scope.records / GATE
    commit = require_sealed(scope.repo, gate)
    if read_pilot_record(gate).get("passed") is not True:
        raise ProcedureOrderError("a failing pilot gate is a mandatory STOP")
    return commit


def _verify_interface_history(
    scope: PilotScope, gate_commit: str, changes: Sequence[str], interface: str | None
) -> None:
    """T is the repair alone, or the repair and exactly this interface commit."""
    tool = protected_commits(scope.repo, amendment7_commit())
    if tool[1:] != ([interface] if interface else []):
        raise SealViolation("interface_commit disagrees with the tool history")
    if interface is not None:
        verify_interface_commit(scope.repo, interface, gate_commit, changes)
    verify_crop_zoom(scope.repo, head_commit(scope.repo), changes)


def _record_digests(scope: PilotScope, chosen: Sequence[str]) -> dict[str, str]:
    paths = [path for group in pilot_groups(scope, chosen) for path in group]
    digests = {}
    for path in paths:
        read_pilot_record(path)
        digests[path.relative_to(scope.records).as_posix()] = sha256_file(path)
    return digests


def write_addendum(
    scope: PilotScope, *, changes: Sequence[str], interface_commit: str | None
) -> Path:
    """Create the prospective G6 record after a sealed passing G5."""
    output = addendum_path(scope)
    preflight([output])
    ordered = validate_changes(list(changes), interface_commit)
    chosen, commits = verify_groups(scope, 5, complete=True)
    gate_commit = passing_gate_commit(scope)
    if gate_commit != commits[-1]:
        raise ProcedureOrderError("G5 is not the sealed gate commit")
    _verify_interface_history(scope, gate_commit, ordered, interface_commit)
    payload = {
        "schema": PILOT_ADDENDUM_SCHEMA,
        "scope": SCOPE_PILOT,
        "amendment7_commit": amendment7_commit(),
        "tool_commit": head_commit(scope.repo),
        "pilot_records": _record_digests(scope, chosen),
        "changes": ordered,
        "interface_commit": interface_commit,
    }
    write_json(output, payload)
    return output


def _addendum_payload(scope: PilotScope) -> dict[str, Any]:
    payload = read_json(addendum_path(scope))
    if not isinstance(payload, dict) or set(payload) != ADDENDUM_KEYS:
        raise MalformedInput("pilot addendum does not have exactly the sealed keys")
    if payload["schema"] != PILOT_ADDENDUM_SCHEMA or payload["scope"] != SCOPE_PILOT:
        raise MalformedInput("pilot addendum has the wrong schema or scope")
    if payload["amendment7_commit"] != amendment7_commit():
        raise SealViolation("pilot addendum names another amendment commit")
    if not isinstance(payload["tool_commit"], str) or not _COMMIT.fullmatch(
        payload["tool_commit"]
    ):
        raise MalformedInput("pilot addendum tool_commit is not a 40-hex commit")
    records = payload["pilot_records"]
    if not isinstance(records, dict) or not all(
        isinstance(value, str) and _DIGEST.fullmatch(value) for value in records.values()
    ):
        raise MalformedInput("pilot addendum record digests are malformed")
    validate_changes(payload["changes"], payload["interface_commit"])
    return payload


def verify_sealed_addendum(scope: PilotScope) -> dict[str, Any]:
    """Verification items 1–8 of a committed G6 (before every corpus command)."""
    repo = scope.repo
    require_sealed(repo, addendum_path(scope))
    payload = _addendum_payload(scope)
    chosen, commits = verify_groups(scope, 6, complete=True)
    expected = _record_digests(scope, chosen)
    if set(payload["pilot_records"]) != set(expected):
        raise MalformedInput("pilot addendum does not list exactly the G1–G5 records")
    for rel, digest in payload["pilot_records"].items():
        if expected[rel] != digest:
            raise SealViolation(f"pilot record digest differs: {rel}")
    gate_commit, g6 = passing_gate_commit(scope), commits[-1]
    if gate_commit != commits[4]:
        raise ProcedureOrderError("G5 is not the sealed gate commit")
    interface = payload["interface_commit"]
    if interface is not None and not strictly_after(repo, interface, g6):
        raise ProcedureOrderError("interface commit is not an ancestor of G6")
    tool_commit = payload["tool_commit"]
    floor = interface or gate_commit
    if not strictly_after(repo, tool_commit, g6) or not (
        tool_commit == floor or strictly_after(repo, floor, tool_commit)
    ):
        raise ProcedureOrderError("addendum tool_commit is not its generation HEAD")
    _verify_interface_history(scope, gate_commit, payload["changes"], interface)
    return payload
