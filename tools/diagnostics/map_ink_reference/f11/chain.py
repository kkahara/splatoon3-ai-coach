"""F11 chain entries 8 and 9. Entries 1–7 stay in the Amendment 10 chain."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from map_ink_reference.constants import PREREGISTRATION_CHAIN, PREREGISTRATION_PATH
from map_ink_reference.errors import PinMismatch, ProcedureOrderError, SealViolation
from map_ink_reference.f11.protected_paths import F11_PROTECTED_PATHS
from map_ink_reference.seal import (
    changed_paths,
    file_at_commit,
    protected_commits,
    require_ancestor,
    require_sealed,
    strictly_after,
)

C11_COMMIT = "2fcd3a4e1e42340897098fd7d898f610b210bf67"
C11_PREREG_SHA256 = "705f75f2e66d9371cb86ab57e25dcd3d8962f8046276aa9e7878a9c4457d5a96"
C12_COMMIT = "17b744ef49c1ab3c9dad32b3f83d9644056b7efd"
C12_PREREG_SHA256 = "7c289a4b0665c3e94c5cd12ea80e2e5f9ce8225c00cd048e1e4d25498a2db0b1"
ENTRY_8 = ("amendment11", C11_COMMIT, C11_PREREG_SHA256)
ENTRY_9 = ("amendment12", C12_COMMIT, C12_PREREG_SHA256)
REPAIR_ALLOWLIST: tuple[str, ...] = (
    "tools/diagnostics/map_ink_reference/f11/chain.py",
    "tools/diagnostics/map_ink_reference/f11/commands.py",
    "tools/diagnostics/map_ink_reference/f11/eligibility.py",
    "tests/map_ink_reference/f11/test_f11_guard.py",
    "tests/map_ink_reference/f11/test_f11_eligibility.py",
)


def f11_chain() -> tuple[tuple[str, str, str], ...]:
    """Entries 1–8 as sealed, plus Amendment 12."""
    return (*PREREGISTRATION_CHAIN, ENTRY_8, ENTRY_9)


def verify_f11_chain(repo: Path) -> None:
    """Entries are ancestral, additive and digest-pinned; the work tree is entry 9."""
    if len(PREREGISTRATION_CHAIN) != 7:
        raise SealViolation("Amendment 10 chain is not the sealed seven entries")
    previous = b""
    previous_commit: str | None = None
    for name, commit, digest in f11_chain():
        _verify_entry(repo, name, commit, digest, previous, previous_commit)
        previous = file_at_commit(repo, commit, PREREGISTRATION_PATH)
        previous_commit = commit
    current = repo / PREREGISTRATION_PATH
    require_sealed(repo, current)
    if current.read_bytes() != previous:
        raise PinMismatch("preregistration differs from Amendment 12")
    require_f11_protected_history(f11_protected_change_sets(repo))


def f11_protected_change_sets(repo: Path) -> tuple[frozenset[str], ...]:
    """Changed-path sets of every protected-root commit after C11, oldest first."""
    return tuple(
        frozenset(changed_paths(repo, commit))
        for commit in protected_commits(repo, C11_COMMIT)
    )


def require_f11_protected_history(change_sets: Sequence[frozenset[str]]) -> None:
    """Accept the tooling commit, or that commit plus the one five-path repair.

    The first protected-root commit after C11 must be exactly the sealed F11
    path set. At most one later protected-root commit is accepted, and its
    changed paths must be exactly the Amendment 12 repair allowlist. Any other
    protected commit or path is a seal violation.
    """
    tooling = frozenset(F11_PROTECTED_PATHS)
    repair = frozenset(REPAIR_ALLOWLIST)
    if not change_sets or change_sets[0] != tooling:
        raise SealViolation("F11 protected history does not start at the tooling commit")
    if len(change_sets) == 1:
        return
    if len(change_sets) == 2 and change_sets[1] == repair:
        return
    raise SealViolation("unauthorized F11 protected commit")


def _verify_entry(
    repo: Path,
    name: str,
    commit: str,
    digest: str,
    previous: bytes,
    previous_commit: str | None,
) -> None:
    require_ancestor(repo, commit)
    if previous_commit is not None and not strictly_after(repo, previous_commit, commit):
        raise ProcedureOrderError(f"{name} is not sealed after the previous entry")
    content = file_at_commit(repo, commit, PREREGISTRATION_PATH)
    actual = hashlib.sha256(content).hexdigest()
    if actual != digest:
        raise PinMismatch(f"{name}: preregistration digest {actual} != pinned {digest}")
    if previous and not content.startswith(previous):
        raise SealViolation(f"{name} alters earlier sealed preregistration text")
