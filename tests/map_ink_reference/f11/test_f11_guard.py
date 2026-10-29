"""F11 guard tests. The tooling commit does not exist until it is sealed."""

from __future__ import annotations

from pathlib import Path

import pytest
from map_ink_reference.errors import SealViolation
from map_ink_reference.f11.chain import verify_f11_chain
from map_ink_reference.f11.guard import guard, tooling_commit
from map_ink_reference.f11.protected_paths import F11_PROTECTED_PATHS


def _repo() -> Path:
    return Path(__file__).resolve().parents[3]


def test_chain_entry_8_matches_the_sealed_preregistration() -> None:
    verify_f11_chain(_repo())


def test_declared_paths_are_exactly_the_f11_files() -> None:
    repo = _repo()
    present: set[str] = set()
    for root in (
        "tools/diagnostics/map_ink_reference/f11",
        "tests/map_ink_reference/f11",
    ):
        for path in (repo / root).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                present.add(path.relative_to(repo).as_posix())
    assert present == set(F11_PROTECTED_PATHS)


def test_no_f11_command_runs_before_the_tooling_commit() -> None:
    repo = _repo()
    if tooling_commit(repo) is None:
        with pytest.raises(SealViolation, match="tooling commit is not sealed"):
            guard(repo)
    else:
        assert guard(repo) == tooling_commit(repo)
