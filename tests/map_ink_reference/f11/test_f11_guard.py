"""F11 guard tests. The tooling commit does not exist until it is sealed."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from map_ink_reference.errors import PinMismatch, SealViolation
from map_ink_reference.f11.chain import (
    C12_COMMIT,
    REPAIR_ALLOWLIST,
    f11_chain,
    f11_protected_change_sets,
    require_f11_protected_history,
    verify_f11_chain,
)
from map_ink_reference.f11.guard import guard, tooling_commit
from map_ink_reference.f11.protected_paths import F11_PROTECTED_PATHS
from map_ink_reference.seal import changed_paths, file_at_commit

_CHAIN = "tools/diagnostics/map_ink_reference/f11/chain.py"
_F11_ROOTS = (
    "tools/diagnostics/map_ink_reference/f11",
    "tests/map_ink_reference/f11",
)


def _repo() -> Path:
    return Path(__file__).resolve().parents[3]


def _f11_porcelain(repo: Path) -> set[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--", *_F11_ROOTS],
        check=True,
        capture_output=True,
        text=True,
    )
    return {line[3:] for line in proc.stdout.splitlines() if line}


def test_chain_entries_through_amendment_12() -> None:
    chain = f11_chain()
    assert chain[7][0] == "amendment11"
    assert chain[8] == (
        "amendment12",
        C12_COMMIT,
        "7c289a4b0665c3e94c5cd12ea80e2e5f9ce8225c00cd048e1e4d25498a2db0b1",
    )
    assert len(chain) == 9
    verify_f11_chain(_repo())


def test_pre_repair_chain_stops_on_the_current_preregistration() -> None:
    repo = _repo()
    source = file_at_commit(repo, C12_COMMIT, _CHAIN).decode("utf-8")
    assert C12_COMMIT not in source
    assert "ENTRY_9" not in source
    namespace: dict[str, object] = {"__name__": "pre_repair_chain"}
    exec(compile(source, _CHAIN, "exec"), namespace)
    verify = namespace["verify_f11_chain"]
    with pytest.raises(PinMismatch, match="Amendment 11"):
        verify(repo)  # type: ignore[operator]


def test_declared_paths_are_exactly_the_f11_files() -> None:
    repo = _repo()
    present: set[str] = set()
    for root in _F11_ROOTS:
        for path in (repo / root).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                present.add(path.relative_to(repo).as_posix())
    assert present == set(F11_PROTECTED_PATHS)


def test_repair_allowlist_is_inside_the_protected_set() -> None:
    assert len(REPAIR_ALLOWLIST) == 5
    assert set(REPAIR_ALLOWLIST) <= set(F11_PROTECTED_PATHS)


def test_repair_diff_is_exactly_the_five_authorized_paths() -> None:
    repo = _repo()
    proc = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", C12_COMMIT, "--", *_F11_ROOTS],
        check=True,
        capture_output=True,
        text=True,
    )
    assert {line for line in proc.stdout.splitlines() if line} == set(REPAIR_ALLOWLIST)


def test_tooling_commit_path_set_is_unchanged() -> None:
    repo = _repo()
    tooling = tooling_commit(repo)
    assert tooling is not None
    assert set(changed_paths(repo, tooling)) == set(F11_PROTECTED_PATHS)


def test_authorized_protected_history_is_accepted() -> None:
    tooling = frozenset(F11_PROTECTED_PATHS)
    repair = frozenset(REPAIR_ALLOWLIST)
    require_f11_protected_history((tooling,))
    require_f11_protected_history((tooling, repair))
    require_f11_protected_history(f11_protected_change_sets(_repo()))


def test_unauthorized_protected_history_is_rejected() -> None:
    tooling = frozenset(F11_PROTECTED_PATHS)
    repair = frozenset(REPAIR_ALLOWLIST)
    extra = frozenset({"tools/diagnostics/map_ink_reference/population.py"})
    with pytest.raises(SealViolation, match="unauthorized"):
        require_f11_protected_history((tooling, extra))
    with pytest.raises(SealViolation, match="unauthorized"):
        require_f11_protected_history((tooling, repair, repair))
    widened = repair | frozenset({"tools/diagnostics/map_ink_reference/f11/cli.py"})
    with pytest.raises(SealViolation, match="unauthorized"):
        require_f11_protected_history((tooling, widened))
    with pytest.raises(SealViolation, match="does not start"):
        require_f11_protected_history((repair,))


def test_no_f11_command_runs_before_the_tooling_commit() -> None:
    repo = _repo()
    if tooling_commit(repo) is None:
        with pytest.raises(SealViolation, match="tooling commit is not sealed"):
            guard(repo)
        return
    dirty = _f11_porcelain(repo)
    if dirty:
        assert dirty <= set(REPAIR_ALLOWLIST)
        verify_f11_chain(repo)
        return
    assert guard(repo) == tooling_commit(repo)
