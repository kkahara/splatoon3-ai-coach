"""F11 execution guard. No F11 command runs before the tooling commit."""

from __future__ import annotations

import ast
from pathlib import Path

from map_ink_reference.errors import PinMismatch, SealViolation
from map_ink_reference.execution import A1_IMPLEMENTATION_PINS, verify_production_pins
from map_ink_reference.f11.chain import C11_COMMIT, verify_f11_chain
from map_ink_reference.f11.protected_paths import F11_PROTECTED_PATHS
from map_ink_reference.pins import verify_pin
from map_ink_reference.seal import (
    changed_paths,
    commit_parents,
    file_at_commit,
    protected_commits,
    require_sealed,
    require_tool_clean,
    strictly_after,
)

_PATHS_FILE = "tools/diagnostics/map_ink_reference/f11/protected_paths.py"
_F11_ROOTS = (
    "tools/diagnostics/map_ink_reference/f11",
    "tests/map_ink_reference/f11",
)


def guard(repo: Path) -> str:
    """Verify the F11 chain, pins and sealed path set. Return the tooling commit."""
    verify_f11_chain(repo)
    verify_production_pins(repo)
    for rel, digest in A1_IMPLEMENTATION_PINS.items():
        verify_pin(repo / rel, digest)
        require_sealed(repo, repo / rel)
    tooling = tooling_commit(repo)
    if tooling is None:
        raise SealViolation("F11 tooling commit is not sealed")
    verify_path_set(repo, tooling)
    for root in _F11_ROOTS:
        require_tool_clean(repo, repo / root)
    return tooling


def tooling_commit(repo: Path) -> str | None:
    """First commit after C11 that touches the declared F11 path set."""
    declared = set(F11_PROTECTED_PATHS)
    for commit in protected_commits(repo, C11_COMMIT):
        if declared.intersection(changed_paths(repo, commit)):
            return commit
    return None


def verify_path_set(repo: Path, tooling: str) -> None:
    """The declared set matches the tooling commit and the working tree."""
    after_c11 = strictly_after(repo, C11_COMMIT, tooling)
    if not after_c11:
        raise SealViolation("F11 tooling commit is not after C11")
    if len(commit_parents(repo, tooling)) != 1:
        raise SealViolation("F11 tooling commit is a merge commit")
    changed = set(changed_paths(repo, tooling))
    declared = set(F11_PROTECTED_PATHS)
    if changed != declared:
        raise SealViolation("F11 tooling commit does not match the declared path set")
    if _tuple_at(repo, tooling) != F11_PROTECTED_PATHS:
        raise SealViolation("F11 protected-tool path set changed")
    present = _present_files(repo)
    if present != declared:
        raise SealViolation("F11 tool or test code is outside the declared path set")
    for rel in F11_PROTECTED_PATHS:
        require_sealed(repo, repo / rel)


def _tuple_at(repo: Path, commit: str) -> tuple[str, ...]:
    source = file_at_commit(repo, commit, _PATHS_FILE).decode("utf-8")
    for node in ast.parse(source).body:
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id != "F11_PROTECTED_PATHS" or node.value is None:
            continue
        value = ast.literal_eval(node.value)
        if not isinstance(value, tuple):
            raise PinMismatch("F11 path set is not a tuple of paths")
        if any(not isinstance(item, str) for item in value):
            raise PinMismatch("F11 path set is not a tuple of paths")
        return value
    raise SealViolation("tooling commit has no F11_PROTECTED_PATHS")


def _present_files(repo: Path) -> set[str]:
    found: set[str] = set()
    for root in _F11_ROOTS:
        base = repo / root
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            found.add(path.relative_to(repo).as_posix())
    return found
