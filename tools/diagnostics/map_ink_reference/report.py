"""Final report: 4b and criterion 4 verdicts plus descriptive statistics.

Descriptive statistics never enter a verdict. The criterion 4a verdict is
taken only from the sealed ``criterion_4a.json`` (Amendment 4 ruling 5);
observation counts are the entry counts of the pinned ``map_observations.json``
files (ruling 2); post-seal stage-identity discrepancies are listed
prominently but are descriptive only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from loguru import logger

from map_ink_reference.constants import (
    CORRECTNESS_APPLICABLE,
    COVERAGE_ONLY,
    STAGE_1,
    STAGE_2,
)
from map_ink_reference.criterion_4a import read_sealed_criterion_4a
from map_ink_reference.errors import MalformedInput
from map_ink_reference.io_utils import read_json, write_json
from map_ink_reference.mapping import ALLY, OPPONENT
from map_ink_reference.pins import corpus_manifest_pins, v3_dir, verify_pin
from map_ink_reference.pipeline_blind import _repo_of, gate_path
from map_ink_reference.pipeline_unblinded import (
    require_trigger_matches,
    results_path,
    stage1_inconclusive,
    stage_identity_crosscheck,
    verified_stage_identities,
)
from map_ink_reference.population import eligible_intervals, reduce_manifest
from map_ink_reference.seal import require_sealed
from map_ink_reference.trigger import trigger_path
from map_ink_reference.unblinded_pins import (
    A1_OBSERVATION_PINS,
    V3_OBSERVATION_PINS,
    a1_observations_path,
    v3_observations_path,
)
from map_ink_reference.verdicts import criterion4, verdict_4b

_TEAM = (ALLY, OPPONENT)


def _final_points(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    frames = [f for look in record["frames"].values() for f in look if f["judgeable"]]
    return [p for f in frames for p in f["points"]]


def accuracy(points: Sequence[Mapping[str, Any]], which: str) -> float | None:
    """Fraction of points whose ``which`` class equals the reference."""
    if not points:
        return None
    return sum(p[which] == p["reference"] for p in points) / len(points)


def inversion_rate(points: Iterable[Mapping[str, Any]], which: str) -> float | None:
    """Team-reference points classified as the opposite team, among team-classified."""
    eligible = [p for p in points if p["reference"] in _TEAM and p[which] in _TEAM]
    if not eligible:
        return None
    return sum(p[which] != p["reference"] for p in eligible) / len(eligible)


def recording_summary(record: Mapping[str, Any]) -> dict[str, Any]:
    """Per-recording descriptive row."""
    points = _final_points(record)
    test = record.get("test") or {}
    judgeable = sum(f["judgeable"] for look in record["frames"].values() for f in look)
    return {
        "recording_id": record["recording_id"],
        "verdict": record["verdict"],
        "T": test.get("t_obs"),
        "N": judgeable,
        "q_all": record["identity"]["q_all"],
        "F_nz": test.get("f_nz"),
        "p_reg": test.get("p_reg"),
        "p_imp": test.get("p_imp"),
        "judgeable_frames": judgeable,
        "judgeable_points": len(points),
        "inversion_rate_a1": inversion_rate(points, "a1"),
        "inversion_rate_v3": inversion_rate(points, "v3"),
    }


def observation_count(path: Path, pinned_sha256: str) -> dict[str, Any]:
    """Entry count of a pinned ``map_observations.json`` (mismatch or missing: STOP)."""
    digest = verify_pin(path, pinned_sha256)
    payload = read_json(path)
    if not isinstance(payload, list):
        raise MalformedInput(f"{path} is not a list of observations")
    return {"count": len(payload), "sha256": digest}


def coverage(repo: Path) -> dict[str, Any]:
    """Eligible frames/intervals and pinned observation counts for all seven."""
    manifests = corpus_manifest_pins()
    out: dict[str, Any] = {}
    for rid in (*CORRECTNESS_APPLICABLE, *COVERAGE_ONLY):
        rel = f"{v3_dir(rid)}/vision_manifest.json"
        verify_pin(repo / rel, manifests[rel])
        intervals = eligible_intervals(reduce_manifest(read_json(repo / rel)))
        out[rid] = {
            "eligible_frames": sum(len(i.frames) for i in intervals),
            "eligible_intervals": len(intervals),
            "v3_observations": observation_count(
                repo / v3_observations_path(rid), V3_OBSERVATION_PINS[rid]
            ),
            "a1_observations": observation_count(
                repo / a1_observations_path(rid), A1_OBSERVATION_PINS[rid]
            ),
        }
    return out


def _sealed_json(repo: Path, path: Path) -> dict[str, Any]:
    require_sealed(repo, path)
    return read_json(path)


def _sealed_results(repo: Path, records: Path) -> dict[str, Any]:
    """Sealed gates and results; absent looks stay None (fail closed if unsealed)."""
    gate1 = _sealed_json(repo, gate_path(records, STAGE_1))
    out: dict[str, Any] = {
        "gate1": gate1,
        "gate2": None,
        "stage1": None,
        "stage2": None,
        "stage1_payload": None,
        "stage2_payload": None,
    }
    if not gate1["passed"]:
        return out
    out["stage1_payload"] = _sealed_json(repo, results_path(records, STAGE_1))
    out["stage1"] = out["stage1_payload"]["recordings"]
    if stage1_inconclusive(out["stage1"]):
        require_trigger_matches(repo, records)
        out["gate2"] = _sealed_json(repo, gate_path(records, STAGE_2))
        if out["gate2"]["passed"]:
            out["stage2_payload"] = _sealed_json(repo, results_path(records, STAGE_2))
            out["stage2"] = out["stage2_payload"]["recordings"]
    elif trigger_path(records).exists():
        raise MalformedInput("a Stage 2 trigger exists but no recording is INCONCLUSIVE")
    return out


def _final_rows(sealed: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Final per-recording records (empty when a gate voids the results)."""
    gate2 = sealed["gate2"]
    if sealed["stage1"] is None or (gate2 is not None and not gate2["passed"]):
        return []
    finals = {r["recording_id"]: r for r in sealed["stage1"]}
    finals.update({r["recording_id"]: r for r in sealed["stage2"] or []})
    return list(finals.values())


def _verdicts(rows: Sequence[Mapping[str, Any]] | None) -> dict[str, str] | None:
    return None if rows is None else {r["recording_id"]: r["verdict"] for r in rows}


def crosscheck_rows(repo: Path, records: Path, sealed: Mapping[str, Any]) -> list[dict]:
    """Stage-identity cross-check from the sealed results, else computed here."""
    payload = sealed["stage1_payload"]
    if payload is not None:
        return list(payload["stage_identity_crosscheck"])
    _, _, blind = verified_stage_identities(repo, records)
    return stage_identity_crosscheck(repo, blind, list(CORRECTNESS_APPLICABLE))


def discrepancies(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Cross-check rows where v3 or A1 disagrees with the blind input."""
    found = [r for r in rows if not (r["v3_agrees"] and r["a1_agrees"])]
    for row in found:
        logger.warning("post-seal stage-identity discrepancy (descriptive): {}", row)
    return found


def build_report(records: Path, tool: Mapping[str, Any]) -> dict[str, Any]:
    """Compose 4a (sealed), 4b, criterion 4 and descriptive results (``report.json``)."""
    repo = _repo_of(records)
    verdict_4a = read_sealed_criterion_4a(repo, records)
    sealed = _sealed_results(repo, records)
    gate1, gate2 = sealed["gate1"], sealed["gate2"]
    v4b = verdict_4b(
        _verdicts(sealed["stage1"]) or {},
        gate1_passed=bool(gate1["passed"]),
        stage2=_verdicts(sealed["stage2"]),
        gate2_passed=None if gate2 is None else bool(gate2["passed"]),
        n_applicable=len(CORRECTNESS_APPLICABLE),
    )
    finals = _final_rows(sealed)
    points = [p for r in finals for p in _final_points(r)]
    crosscheck = crosscheck_rows(repo, records, sealed)
    payload = {
        "tool": dict(tool),
        "verdict_4a": verdict_4a,
        "verdict_4b": v4b,
        "criterion_4": criterion4(verdict_4a, v4b),
        "post_seal_stage_identity_discrepancies": discrepancies(crosscheck),
        "gates": {"stage1": gate1, "stage2": gate2},
        "descriptive": {
            "pooled_accuracy_a1": accuracy(points, "a1"),
            "pooled_accuracy_v3": accuracy(points, "v3"),
            "recordings": [recording_summary(r) for r in finals],
            "stage_identity_crosscheck": crosscheck,
            "coverage": coverage(repo),
        },
    }
    write_json(records / "report.json", payload)
    return payload
