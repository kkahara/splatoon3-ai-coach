"""Sealing checks: records must be committed (and unmodified) before use.

A record is sealed when git tracks it, it has no staged or unstaged change,
and the commit that first added it is known. Ordering checks compare those
commits by ancestry.
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from map_ink_reference.constants import PREREGISTRATION_CHAIN, PREREGISTRATION_PATH
from map_ink_reference.errors import PinMismatch, ProcedureOrderError, SealViolation
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


def _file_at_commit(repo: Path, commit: str, rel: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{rel}"],
        capture_output=True,
        check=False,
    )
    if proc.returncode:
        raise SealViolation(f"{rel} is not readable at commit {commit}")
    return proc.stdout


def _verify_chain_entry(
    repo: Path, entry: tuple[str, str, str], previous: tuple[str, bytes] | None
) -> bytes:
    name, commit, digest = entry
    require_ancestor(repo, commit)
    earlier, earlier_content = previous if previous is not None else (None, b"")
    if earlier is not None:
        is_after = not _git(
            repo, "merge-base", "--is-ancestor", earlier, commit, check=False
        ).returncode
        if commit == earlier or not is_after:
            raise ProcedureOrderError(f"{name} is not sealed after the previous entry")
    content = _file_at_commit(repo, commit, PREREGISTRATION_PATH)
    actual = hashlib.sha256(content).hexdigest()
    if actual != digest:
        raise PinMismatch(f"{name}: preregistration digest {actual} != pinned {digest}")
    if not content.startswith(earlier_content):
        raise SealViolation(f"{name} alters earlier sealed preregistration text")
    return content


def verify_preregistration_chain(repo: Path) -> None:
    """Amendment 6: the explicit authorized chain, and the current file at its end.

    ``HEAD`` may descend from the last chain commit; the current file must be
    committed, unmodified and byte-identical to the file at that commit.
    """
    if not PREREGISTRATION_CHAIN:
        raise SealViolation("no authorized preregistration chain")
    previous: tuple[str, bytes] | None = None
    for entry in PREREGISTRATION_CHAIN:
        previous = (entry[1], _verify_chain_entry(repo, entry, previous))
    prereg = repo / PREREGISTRATION_PATH
    require_sealed(repo, prereg)
    if prereg.read_bytes() != previous[1]:
        raise PinMismatch(
            "preregistration differs from the last authorized amendment "
            f"({PREREGISTRATION_CHAIN[-1][0]})"
        )


def verify_preconditions(repo: Path, a1_pins: Mapping[str, str]) -> None:
    """Sealing step 1: authorized preregistration chain; A1 committed and matching."""
    verify_preregistration_chain(repo)
    for rel, digest in a1_pins.items():
        verify_pin(repo / rel, digest)
        require_sealed(repo, repo / rel)


# Amendment 7 tool identity rule: history primitives over committed state.
PROTECTED_ROOTS = (
    "tools/diagnostics/map_ink_reference/",
    "tests/map_ink_reference/",
)


def chain_entry(name: str) -> tuple[str, str]:
    """``(commit, preregistration SHA-256)`` of a named authorized chain entry."""
    for entry_name, commit, digest in PREREGISTRATION_CHAIN:
        if entry_name == name:
            return commit, digest
    raise SealViolation(f"{name} is not in the authorized execution chain")


def preregistration_path() -> str:
    """Repository-relative path of the preregistration file."""
    return PREREGISTRATION_PATH


def file_at_commit(repo: Path, commit: str, rel: str) -> bytes:
    """Committed bytes of ``rel`` at ``commit``."""
    return _file_at_commit(repo, commit, rel)


def commit_parents(repo: Path, commit: str) -> list[str]:
    """Parent commits of ``commit``."""
    return _git(repo, "rev-list", "--parents", "-n", "1", commit).stdout.split()[1:]


def changed_paths(repo: Path, commit: str) -> dict[str, str]:
    """``{path: status}`` of one commit against its single parent (no renames)."""
    out = _git(
        repo,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "-r",
        "--no-renames",
        "--root",
        commit,
    ).stdout
    entries = [line.split("\t", 1) for line in out.splitlines() if line]
    return {path: status for status, path in entries}


def strictly_after(repo: Path, earlier: str, later: str) -> bool:
    """``later`` is a proper descendant of ``earlier``."""
    if earlier == later:
        return False
    proc = _git(repo, "merge-base", "--is-ancestor", earlier, later, check=False)
    return proc.returncode == 0


def adding_commit(repo: Path, path: Path) -> str | None:
    """The commit in HEAD's history that first added ``path``, if any."""
    rel = path.resolve().relative_to(repo.resolve()).as_posix()
    log = _git(repo, "log", "--diff-filter=A", "--format=%H", "--", rel).stdout.split()
    return log[-1] if log else None


def path_commits(repo: Path, path: Path) -> list[str]:
    """Commits in HEAD's history that touch ``path``, oldest first."""
    rel = path.resolve().relative_to(repo.resolve()).as_posix()
    return _git(repo, "log", "--reverse", "--format=%H", "--", rel).stdout.split()


def protected_commits(repo: Path, base: str) -> list[str]:
    """Commits after ``base`` (exclusive) touching a protected path, oldest first.

    The whole range must be a linear history with no merge commits.
    """
    require_ancestor(repo, base)
    out = _git(repo, "rev-list", "--reverse", "--parents", f"{base}..HEAD").stdout
    touching = []
    for line in out.splitlines():
        commit, *parents = line.split()
        if len(parents) != 1:
            raise SealViolation(f"merge commit after {base[:7]}: {commit[:7]}")
        if any(path.startswith(PROTECTED_ROOTS) for path in changed_paths(repo, commit)):
            touching.append(commit)
    return touching
