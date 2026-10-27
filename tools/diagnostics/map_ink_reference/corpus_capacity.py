"""Outcome-blind Amendment 10 corpus-capacity checks.

This module reads only pinned v3 manifest metadata and the sealed corpus
blind-input manifest. It never opens media, extracted images, calibration,
observations, annotations, comparisons, or criterion results.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from map_ink_reference.constants import CORRECTNESS_APPLICABLE
from map_ink_reference.errors import MalformedInput
from map_ink_reference.io_utils import read_json
from map_ink_reference.pins import MEDIA_PINS, verify_pin
from map_ink_reference.plan import build_frame_plan
from map_ink_reference.stage_identity import load_corpus_blind_input

FROZEN_CAPACITIES: dict[str, tuple[int, int, int]] = {
    "18-18-00": (11, 8, 3),
    "20-33-31": (20, 8, 8),
    "22-31-53": (14, 8, 6),
    "22-37-47": (7, 7, 0),
    "22-57-30": (21, 8, 8),
}


def plan_capacities(plan: Mapping[str, Any]) -> tuple[int, int, int]:
    """Return ``(eligible intervals, K1, Stage 2 capacity)`` from a plan."""
    intervals = plan.get("intervals")
    stage1 = plan.get("stage1_interval_indices")
    stage2 = plan.get("stage2_interval_indices")
    if not all(isinstance(value, list) for value in (intervals, stage1, stage2)):
        raise MalformedInput("plan capacity fields are malformed")
    if len(set(stage1)) != len(stage1) or len(set(stage2)) != len(stage2):
        raise MalformedInput("plan interval indices are not unique")
    if set(stage1) & set(stage2):
        raise MalformedInput("Stage 1 and Stage 2 intervals overlap")
    valid = set(range(len(intervals)))
    if not set(stage1) <= valid or not set(stage2) <= valid:
        raise MalformedInput("plan interval index is out of range")
    return len(intervals), len(stage1), len(stage2)


def stage2_capacity(plan: Mapping[str, Any]) -> int:
    """Return the frozen Stage 2 capacity encoded by a plan."""
    return plan_capacities(plan)[2]


def _verify_extraction_identifiers(plan: Mapping[str, Any]) -> None:
    """Require every planned frame to carry the identifiers extraction needs."""
    required = {
        "frame_id",
        "source_pts",
        "source_time_base",
        "source_frame_index",
    }
    frames = plan.get("frames")
    if not isinstance(frames, list) or any(
        not isinstance(frame, dict) or not required <= set(frame) for frame in frames
    ):
        raise MalformedInput("planned frame lacks an extraction identifier")


def precheck_recording(repo: Path, rid: str) -> dict[str, int | str | bool]:
    """Check one pinned recording without writing or opening image data."""
    if rid not in FROZEN_CAPACITIES:
        raise MalformedInput(f"recording={rid} passed=false")
    pins = MEDIA_PINS[rid]
    manifest_path = repo / pins.vision_manifest_path
    digest = verify_pin(manifest_path, pins.vision_manifest_sha256)
    plan = build_frame_plan(rid, read_json(manifest_path), manifest_sha256=digest)
    _verify_extraction_identifiers(plan)
    intervals, k1, k2 = plan_capacities(plan)
    if (intervals, k1, k2) != FROZEN_CAPACITIES[rid] or k1 < 6:
        raise MalformedInput(f"recording={rid} passed=false")
    return {
        "recording_id": rid,
        "interval_count": intervals,
        "k1": k1,
        "stage2_capacity": k2,
        "passed": True,
    }


def run_precheck(repo: Path, records: Path) -> list[dict[str, int | str | bool]]:
    """Run the write-free precheck for all five correctness recordings."""
    try:
        load_corpus_blind_input(repo, records)
    except Exception:
        raise MalformedInput("passed=false") from None
    rows = []
    for rid in CORRECTNESS_APPLICABLE:
        try:
            rows.append(precheck_recording(repo, rid))
        except Exception:
            raise MalformedInput(f"recording={rid} passed=false") from None
    return rows
