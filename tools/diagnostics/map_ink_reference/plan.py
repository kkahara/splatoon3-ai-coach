"""Per-recording frame plan: eligible population, stage selections, cue frames.

Blind. Built from a reduced vision manifest only. The plan names every frame
that extraction must write: the whole eligible population (needed for
``q_all`` without re-decoding) plus each interval's team-identity cue frame.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from map_ink_reference.constants import PURPOSE_EVALUATION, PURPOSE_PILOT
from map_ink_reference.errors import MalformedInput
from map_ink_reference.population import (
    CadenceFrame,
    Interval,
    ManifestView,
    cue_frame,
    eligible_intervals,
    reduce_manifest,
    select_stage1,
    select_stage2,
)


def _frame_record(frame: CadenceFrame) -> dict[str, Any]:
    if frame.source_frame_index is None:
        raise MalformedInput(f"frame {frame.frame_id} lacks source_frame_index")
    _ = frame.exact_time  # requires source_pts and time base
    return {
        "frame_id": frame.frame_id,
        "timestamp": frame.timestamp,
        "source_pts": frame.source_pts,
        "source_time_base": [frame.tb_num, frame.tb_den],
        "source_frame_index": frame.source_frame_index,
    }


def _interval_record(
    view: ManifestView, index: int, interval: Interval
) -> dict[str, Any]:
    cue = cue_frame(view, interval)
    return {
        "interval_index": index,
        "frame_ids": [f.frame_id for f in interval.frames],
        "middle_frame_id": interval.middle_frame.frame_id,
        "is_long": interval.is_long,
        "cue_frame_id": None if cue is None else cue.frame_id,
    }


def build_frame_plan(
    recording_id: str,
    manifest: Mapping[str, Any],
    *,
    manifest_sha256: str,
    purpose: str = PURPOSE_EVALUATION,
) -> dict[str, Any]:
    """Deterministic frame plan for one recording (no image content used)."""
    if purpose not in (PURPOSE_EVALUATION, PURPOSE_PILOT):
        raise MalformedInput(f"unknown purpose {purpose!r}")
    view = reduce_manifest(manifest)
    intervals = eligible_intervals(view)
    stage1 = select_stage1(intervals)
    stage2 = select_stage2(intervals, stage1)
    position = {interval.frames[0].frame_id: i for i, interval in enumerate(intervals)}
    by_id = {frame.frame_id: frame for frame in view.frames}
    needed: dict[str, CadenceFrame] = {}
    for interval in intervals:
        for frame in interval.frames:
            needed[frame.frame_id] = frame
        cue = cue_frame(view, interval)
        if cue is not None:
            needed[cue.frame_id] = cue
    return {
        "recording_id": recording_id,
        "purpose": purpose,
        "video_identity": view.video_identity,
        "vision_manifest_sha256": manifest_sha256,
        "frame_size": [view.width, view.height],
        "match_end_time": view.match_end_time,
        "intervals": [_interval_record(view, i, iv) for i, iv in enumerate(intervals)],
        "stage1_interval_indices": [position[iv.frames[0].frame_id] for iv in stage1],
        "stage2_interval_indices": [position[iv.frames[0].frame_id] for iv in stage2],
        "frames": [
            _frame_record(by_id[fid]) for fid in sorted(needed, key=_order(by_id))
        ],
    }


def _order(by_id: Mapping[str, CadenceFrame]):
    return lambda frame_id: by_id[frame_id].timestamp


def stage_frame_ids(plan: Mapping[str, Any], stage: int) -> list[str]:
    """Selected (middle) frame ids of a stage, in selection order."""
    key = {1: "stage1_interval_indices", 2: "stage2_interval_indices"}.get(stage)
    if key is None:
        raise MalformedInput(f"unknown stage {stage}")
    intervals = plan["intervals"]
    return [intervals[i]["middle_frame_id"] for i in plan[key]]


def stage_cue_frame_ids(plan: Mapping[str, Any], stage: int) -> list[str]:
    """Cue frame ids (one per selected interval that has one) for a stage."""
    key = "stage1_interval_indices" if stage == 1 else "stage2_interval_indices"
    intervals = plan["intervals"]
    cues = [intervals[i]["cue_frame_id"] for i in plan[key]]
    return [cue for cue in cues if cue is not None]


def eligible_frame_ids(plan: Mapping[str, Any]) -> list[str]:
    """All frames of the eligible population (for ``q_all``)."""
    return [fid for interval in plan["intervals"] for fid in interval["frame_ids"]]
