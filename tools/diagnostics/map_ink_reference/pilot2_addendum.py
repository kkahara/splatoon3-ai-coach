"""Amendment 9 H6 addendum and corpus prerequisite."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from map_ink_reference.constants import (
    PILOT2_ADDENDUM_SCHEMA,
    PILOT_ADDENDUM,
    SCOPE_PILOT2,
)
from map_ink_reference.errors import MalformedInput, ProcedureOrderError, SealViolation
from map_ink_reference.io_utils import read_json, sha256_file, write_json
from map_ink_reference.pilot2_pipeline import (
    GATE,
    Pilot2Scope,
    addendum_path,
    pilot2_groups,
    preflight,
    read_pilot2_record,
    verify_groups,
)
from map_ink_reference.pilot_pipeline import PilotScope
from map_ink_reference.pilot_pipeline import verify_groups as verify_pilot1
from map_ink_reference.seal import (
    chain_entry,
    commit_parents,
    head_commit,
    require_sealed,
    strictly_after,
)

_KEYS = frozenset(
    {
        "schema",
        "scope",
        "amendment9_commit",
        "tool_commit",
        "pilot1_group_commits",
        "supplementary_records",
        "pilot2_records",
        "changes",
        "interface_commit",
    }
)
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def amendment9_commit() -> str:
    """Return the frozen C9 chain identity."""
    return chain_entry("amendment9")[0]


def _pilot2_digests(scope: Pilot2Scope, chosen: list[str]) -> dict[str, str]:
    paths = [path for group in pilot2_groups(scope, chosen)[:5] for path in group]
    for path in paths:
        read_pilot2_record(path)
    return {
        path.relative_to(scope.records).as_posix(): sha256_file(path) for path in paths
    }


def _supplementary_digests(scope: Pilot2Scope) -> dict[str, str]:
    selection = read_pilot2_record(scope.records / "pilot_selection.json")
    records = selection.get("supplementary_records")
    if not isinstance(records, dict):
        raise MalformedInput("pilot2 selection lacks supplementary provenance")
    for rel, digest in records.items():
        if (
            not isinstance(rel, str)
            or not rel.startswith("supplementary/")
            or not isinstance(digest, str)
            or not _DIGEST.fullmatch(digest)
        ):
            raise MalformedInput("supplementary record provenance is malformed")
        path = scope.records / rel
        require_sealed(scope.repo, path)
        if sha256_file(path) != digest:
            raise SealViolation(f"supplementary record differs: {rel}")
    return dict(records)


def _pilot1_commits(repo: Path) -> dict[str, str]:
    pilot1 = PilotScope.from_repo(repo)
    _, commits = verify_pilot1(pilot1, 5, complete=True)
    if (pilot1.records / PILOT_ADDENDUM).exists():
        raise ProcedureOrderError("pilot-1 addendum is forbidden by Amendment 9")
    return {f"G{index}": commit for index, commit in enumerate(commits, start=1)}


def passing_h5(scope: Pilot2Scope) -> str:
    """Require and return the sealed passing H5 commit."""
    gate_path = scope.records / GATE
    commit = require_sealed(scope.repo, gate_path)
    if read_pilot2_record(gate_path).get("passed") is not True:
        raise ProcedureOrderError("a failing H5 is a mandatory STOP")
    return commit


def write_addendum(scope: Pilot2Scope, *, predecessor: str) -> Path:
    """Write H6 directly from a sealed passing H5, with no interface commit."""
    output = addendum_path(scope)
    preflight([output])
    chosen, commits = verify_groups(scope, 5, predecessor=predecessor, complete=True)
    h5 = passing_h5(scope)
    if commits[-1] != h5 or head_commit(scope.repo) != h5:
        raise ProcedureOrderError("H6 must be generated directly at H5")
    payload = {
        "schema": PILOT2_ADDENDUM_SCHEMA,
        "scope": SCOPE_PILOT2,
        "amendment9_commit": amendment9_commit(),
        "tool_commit": h5,
        "pilot1_group_commits": _pilot1_commits(scope.repo),
        "supplementary_records": _supplementary_digests(scope),
        "pilot2_records": _pilot2_digests(scope, chosen),
        "changes": [],
        "interface_commit": None,
    }
    write_json(output, payload)
    return output


def _payload(scope: Pilot2Scope) -> dict[str, Any]:
    payload = read_json(addendum_path(scope))
    if not isinstance(payload, dict) or set(payload) != _KEYS:
        raise MalformedInput("pilot2 addendum must have exactly the sealed keys")
    if (
        payload["schema"] != PILOT2_ADDENDUM_SCHEMA
        or payload["scope"] != SCOPE_PILOT2
        or payload["amendment9_commit"] != amendment9_commit()
        or payload["changes"] != []
        or payload["interface_commit"] is not None
    ):
        raise MalformedInput("pilot2 addendum differs from the frozen literals")
    if not isinstance(payload["tool_commit"], str) or not _COMMIT.fullmatch(
        payload["tool_commit"]
    ):
        raise MalformedInput("pilot2 addendum tool_commit is malformed")
    groups = payload["pilot1_group_commits"]
    if not isinstance(groups, dict) or set(groups) != {f"G{i}" for i in range(1, 6)}:
        raise MalformedInput("pilot2 addendum must list exactly G1-G5")
    if not all(
        isinstance(value, str) and _COMMIT.fullmatch(value) for value in groups.values()
    ):
        raise MalformedInput("pilot1 group identity is malformed")
    for key in ("supplementary_records", "pilot2_records"):
        records = payload[key]
        if not isinstance(records, dict) or not all(
            isinstance(path, str)
            and isinstance(digest, str)
            and _DIGEST.fullmatch(digest)
            for path, digest in records.items()
        ):
            raise MalformedInput(f"{key} is malformed")
    return payload


def verify_sealed_addendum(scope: Pilot2Scope, *, predecessor: str) -> dict[str, Any]:
    """Verify H6, passing H5 and all frozen provenance before corpus work."""
    h6 = require_sealed(scope.repo, addendum_path(scope))
    payload = _payload(scope)
    chosen, commits = verify_groups(scope, 6, predecessor=predecessor, complete=True)
    h5 = passing_h5(scope)
    if commits[4] != h5 or commits[5] != h6:
        raise ProcedureOrderError("H5/H6 group identities differ")
    if (
        payload["tool_commit"] != h5
        or not strictly_after(scope.repo, h5, h6)
        or commit_parents(scope.repo, h6) != [h5]
    ):
        raise ProcedureOrderError("H6 must follow H5 directly")
    if payload["pilot1_group_commits"] != _pilot1_commits(scope.repo):
        raise SealViolation("pilot1 group identities differ from the addendum")
    if payload["supplementary_records"] != _supplementary_digests(scope):
        raise SealViolation("supplementary record digests differ")
    if payload["pilot2_records"] != _pilot2_digests(scope, chosen):
        raise SealViolation("pilot2 record digests differ")
    return payload
