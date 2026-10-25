"""Criterion 4a (Amendment 4 ruling 5): frozen rule over pinned A1 artifacts.

For all seven recordings, from the pinned A1 ``match_identity.json`` and the
pinned A1 ``map_observations.json``:

- ``team_color_calibration`` null: pass iff the file has zero entries;
- otherwise: pass iff every entry has
  ``round(video_time, 3) >= team_color_calibration.calibrated_at``.

4a is PASS iff all seven pass, otherwise FAIL. A pin mismatch or unparsable
input is a STOP. The record is computed after the step 5 gate record and
before step 6 (``criterion_4a.json``, step 5a); the final report takes 4a
only from that record.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from map_ink_reference.constants import CORRECTNESS_APPLICABLE, COVERAGE_ONLY, STAGE_1
from map_ink_reference.errors import MalformedInput, ProcedureOrderError, SealViolation
from map_ink_reference.io_utils import read_json, write_json
from map_ink_reference.pins import verify_pin
from map_ink_reference.pipeline_blind import _repo_of, gate_path
from map_ink_reference.pipeline_unblinded import (
    criterion_4a_path,
    load_identity_sidecar,
    results_path,
)
from map_ink_reference.seal import require_sealed, require_strictly_after
from map_ink_reference.unblinded_pins import A1_OBSERVATION_PINS, a1_observations_path
from map_ink_reference.verdicts import FAIL, PASS

RULE = "amendment4-criterion-4a-v1"
RECORD_KEYS = frozenset({"rule", "tool", "recordings", "verdict_4a"})
ALL_RECORDINGS = (*CORRECTNESS_APPLICABLE, *COVERAGE_ONLY)


def _number(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MalformedInput(f"{where} must be a number")
    return float(value)


def observation_times(observations: Any) -> list[float]:
    """``video_time`` of every entry (unparsable → STOP)."""
    if not isinstance(observations, list):
        raise MalformedInput("map observations must be a list")
    times = []
    for index, entry in enumerate(observations):
        if not isinstance(entry, dict) or "video_time" not in entry:
            raise MalformedInput(f"observation {index} has no video_time")
        times.append(_number(entry["video_time"], f"observation {index} video_time"))
    return times


def evaluate_recording(identity: Mapping[str, Any], observations: Any) -> dict[str, Any]:
    """The frozen per-recording rule."""
    if "team_color_calibration" not in identity:
        raise MalformedInput("match_identity.json has no team_color_calibration key")
    calibration = identity["team_color_calibration"]
    times = observation_times(observations)
    if calibration is None:
        calibrated_at = None
        passed = not times
    else:
        if not isinstance(calibration, dict) or "calibrated_at" not in calibration:
            raise MalformedInput("team_color_calibration has no calibrated_at")
        calibrated_at = _number(calibration["calibrated_at"], "calibrated_at")
        passed = all(round(t, 3) >= calibrated_at for t in times)
    return {
        "calibration_present": calibration is not None,
        "calibrated_at": calibrated_at,
        "entry_count": len(times),
        "earliest_observation_time": min(times) if times else None,
        "pass": passed,
    }


def verdict(rows: Sequence[Mapping[str, Any]]) -> str:
    """PASS iff every one of the seven recordings passes."""
    if len(rows) != len(ALL_RECORDINGS):
        raise MalformedInput("criterion 4a needs all seven recordings")
    return PASS if all(r["pass"] for r in rows) else FAIL


def compute_criterion_4a(repo: Path) -> dict[str, dict[str, Any]]:
    """Per-recording results with input digests (pins verified; STOP on mismatch)."""
    out = {}
    for rid in ALL_RECORDINGS:
        identity, identity_sha = load_identity_sidecar(repo, rid, "a1")
        obs_rel = a1_observations_path(rid)
        obs_sha = verify_pin(repo / obs_rel, A1_OBSERVATION_PINS[rid])
        row = evaluate_recording(identity, read_json(repo / obs_rel))
        out[rid] = {
            **row,
            "match_identity_sha256": identity_sha,
            "map_observations_sha256": obs_sha,
        }
    return out


def write_criterion_4a(records: Path, tool: Mapping[str, Any]) -> dict[str, Any]:
    """Step 5a: after the sealed Stage 1 gate record, before any Stage 1 comparison."""
    repo = _repo_of(records)
    out = criterion_4a_path(records)
    if out.exists():
        raise SealViolation(f"{out.name} already exists; it is never recomputed")
    require_sealed(repo, gate_path(records, STAGE_1))
    if results_path(records, STAGE_1).exists():
        raise ProcedureOrderError("criterion 4a must precede the Stage 1 comparison")
    rows = compute_criterion_4a(repo)
    payload = {
        "rule": RULE,
        "tool": dict(tool),
        "recordings": rows,
        "verdict_4a": verdict(list(rows.values())),
    }
    write_json(out, payload)
    return payload


def read_sealed_criterion_4a(repo: Path, records: Path) -> str:
    """4a verdict from the sealed record (order and internal consistency checked)."""
    path = criterion_4a_path(records)
    require_strictly_after(repo, gate_path(records, STAGE_1), path)
    if results_path(records, STAGE_1).exists():
        require_strictly_after(repo, path, results_path(records, STAGE_1))
    payload = read_json(path)
    if not isinstance(payload, dict) or set(payload) != RECORD_KEYS:
        raise MalformedInput("criterion 4a record has unexpected content")
    rows = payload["recordings"]
    if not isinstance(rows, dict) or set(rows) != set(ALL_RECORDINGS):
        raise MalformedInput("criterion 4a record must cover all seven recordings")
    if payload["rule"] != RULE or payload["verdict_4a"] != verdict(list(rows.values())):
        raise MalformedInput("criterion 4a record is internally inconsistent")
    return payload["verdict_4a"]
