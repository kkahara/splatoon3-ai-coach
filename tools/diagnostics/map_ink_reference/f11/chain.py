"""F11 chain entry 8. Entries 1–7 stay in the Amendment 10 chain unchanged."""

from __future__ import annotations

import hashlib
from pathlib import Path

from map_ink_reference.constants import PREREGISTRATION_CHAIN, PREREGISTRATION_PATH
from map_ink_reference.errors import PinMismatch, ProcedureOrderError, SealViolation
from map_ink_reference.seal import (
    file_at_commit,
    require_ancestor,
    require_sealed,
    strictly_after,
)

C11_COMMIT = "2fcd3a4e1e42340897098fd7d898f610b210bf67"
C11_PREREG_SHA256 = "705f75f2e66d9371cb86ab57e25dcd3d8962f8046276aa9e7878a9c4457d5a96"
ENTRY_8 = ("amendment11", C11_COMMIT, C11_PREREG_SHA256)


def f11_chain() -> tuple[tuple[str, str, str], ...]:
    """Entries 1–7 as sealed, plus Amendment 11."""
    return (*PREREGISTRATION_CHAIN, ENTRY_8)


def verify_f11_chain(repo: Path) -> None:
    """Entries are ancestral, additive and digest-pinned; the work tree is entry 8."""
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
        raise PinMismatch("preregistration differs from Amendment 11")


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
