"""Sealing checks: records must be committed (and unmodified) before use.

A record is sealed when git tracks it, it has no staged or unstaged change,
and the commit that first added it is known. Ordering checks compare those
commits by ancestry.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from map_ink_reference.constants import (
    PREREGISTRATION_COMMIT,
    PREREGISTRATION_PATH,
    PREREGISTRATION_SHA256,
)
from map_ink_reference.errors import ProcedureOrderError, SealViolation
from map_ink_reference.pins import verify_pin


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


def head_commit(repo: Path) -> str:
    """Current HEAD commit."""
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def require_sealed(repo: Path, path: Path) -> str:
    """Return the commit that first added ``path``; raise unless sealed."""
    rel = path.resolve().relative_to(repo.resolve()).as_posix()
    if _git(repo, "ls-files", "--error-unmatch", "--", rel, check=False).returncode:
        raise SealViolation(f"{rel} is not committed")
    if _git(repo, "diff", "--quiet", "HEAD", "--", rel, check=False).returncode:
        raise SealViolation(f"{rel} differs from its committed version")
    log = _git(repo, "log", "--diff-filter=A", "--format=%H", "--", rel).stdout.split()
    if not log:
        raise SealViolation(f"cannot find the commit that added {rel}")
    return log[-1]


def require_sealed_all(repo: Path, paths: Sequence[Path]) -> dict[str, str]:
    """Seal-check many records; return ``{path: adding commit}``."""
    return {str(p): require_sealed(repo, p) for p in paths}


def require_strictly_after(repo: Path, earlier: Path, later: Path) -> None:
    """``later`` must be committed in a descendant commit of ``earlier``'s."""
    first = require_sealed(repo, earlier)
    second = require_sealed(repo, later)
    if first == second:
        raise ProcedureOrderError(f"{later.name} must be committed after {earlier.name}")
    if _git(repo, "merge-base", "--is-ancestor", first, second, check=False).returncode:
        raise ProcedureOrderError(f"{later.name} was not committed after {earlier.name}")


def require_in_order(repo: Path, paths: Sequence[Path]) -> None:
    """Each record is committed strictly after the previous one."""
    for earlier, later in zip(paths, paths[1:], strict=False):
        require_strictly_after(repo, earlier, later)


def require_ancestor(repo: Path, commit: str) -> None:
    """``commit`` must exist and be an ancestor of (or equal to) HEAD."""
    if _git(repo, "merge-base", "--is-ancestor", commit, "HEAD", check=False).returncode:
        raise SealViolation(f"commit {commit} is not an ancestor of HEAD")


def require_tool_clean(repo: Path, tool_dir: Path) -> str:
    """Real runs need committed tooling; return HEAD for provenance."""
    rel = tool_dir.resolve().relative_to(repo.resolve()).as_posix()
    status = _git(repo, "status", "--porcelain", "--", rel).stdout.strip()
    if status:
        raise SealViolation(f"tooling has uncommitted changes:\n{status}")
    return head_commit(repo)


def verify_preconditions(repo: Path, a1_pins: Mapping[str, str]) -> None:
    """Sealing step 1: Amendments 3 and 4 committed; A1 committed and matching."""
    prereg = repo / PREREGISTRATION_PATH
    verify_pin(prereg, PREREGISTRATION_SHA256)
    require_sealed(repo, prereg)
    require_ancestor(repo, PREREGISTRATION_COMMIT)
    for rel, digest in a1_pins.items():
        verify_pin(repo / rel, digest)
        require_sealed(repo, repo / rel)
