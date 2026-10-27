"""Read-only verification of Amendment 10's three partial Stage 1 products.

Image decoding is internal to digest verification. No pixel-derived value is
returned, logged, persisted, or included in an error.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn

import cv2
import numpy as np

from map_ink_reference.errors import MalformedInput
from map_ink_reference.io_utils import (
    canonical_json,
    read_json,
    sha256_array,
    sha256_file,
)
from map_ink_reference.pins import MEDIA_PINS, verify_pin
from map_ink_reference.pipeline_blind import extraction_path, plan_path
from map_ink_reference.plan import build_frame_plan

RECOVERY_RECORDINGS = ("18-18-00", "20-33-31", "22-31-53")


def _failure(check: str, rid: str, frame_id: str | None = None) -> NoReturn:
    """Raise an allowlisted failure with no pixel-derived value."""
    suffix = "" if frame_id is None else f" frame_id={frame_id}"
    raise MalformedInput(f"check={check} recording_id={rid}{suffix} passed=false")


def _normalized_plan(payload: Any, rid: str) -> tuple[dict[str, Any], str]:
    """Return a plan without its permitted provenance field."""
    if not isinstance(payload, dict):
        _failure("plan_schema", rid)
    tool = payload.get("tool")
    if not isinstance(tool, dict) or not isinstance(tool.get("tool_commit"), str):
        _failure("plan_tool", rid)
    return {key: value for key, value in payload.items() if key != "tool"}, tool[
        "tool_commit"
    ]


def _expected_frames(plan: Mapping[str, Any], rid: str) -> list[tuple[str, int, int]]:
    """Return the extraction identity tuple for each planned frame."""
    frames = plan.get("frames")
    if not isinstance(frames, list):
        _failure("plan_frames", rid)
    try:
        return [
            (
                str(frame["frame_id"]),
                int(frame["source_pts"]),
                int(frame["source_frame_index"]),
            )
            for frame in frames
        ]
    except (KeyError, TypeError, ValueError):
        _failure("plan_frames", rid)


def _extraction_frames(payload: Any, rid: str) -> tuple[list[dict[str, Any]], str]:
    """Validate extraction-level identity and return frame records."""
    if not isinstance(payload, dict):
        _failure("extraction_schema", rid)
    tool = payload.get("tool")
    if not isinstance(tool, dict) or not isinstance(tool.get("tool_commit"), str):
        _failure("extraction_tool", rid)
    pins = MEDIA_PINS[rid]
    if (
        payload.get("recording_id") != rid
        or payload.get("video_sha256") != pins.video_sha256
    ):
        _failure("extraction_video", rid)
    frames = payload.get("frames")
    if not isinstance(frames, list):
        _failure("extraction_frames", rid)
    return frames, tool["tool_commit"]


def _frame_identity(frames: list[dict[str, Any]], rid: str) -> list[tuple[str, int, int]]:
    """Return extraction frame identity tuples without pixel data."""
    try:
        return [
            (
                str(frame["frame_id"]),
                int(frame["source_pts"]),
                int(frame["source_frame_index"]),
            )
            for frame in frames
        ]
    except (KeyError, TypeError, ValueError):
        _failure("extraction_frames", rid)


def _decode_digest(path: Path, rid: str, frame_id: str) -> str:
    """Decode only in memory and return the frozen array digest."""
    try:
        encoded = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if image is None:
            _failure("png_decode", rid, frame_id)
        return sha256_array(image)
    except MalformedInput:
        raise
    except Exception:
        _failure("png_decode", rid, frame_id)


def _verify_pngs(
    frame_dir: Path, frames: list[dict[str, Any]], rid: str
) -> tuple[int, list[dict[str, str | bool]]]:
    """Verify exact membership and both recorded image digests."""
    if not frame_dir.is_dir():
        _failure("frame_directory", rid)
    try:
        names = [str(frame["png"]) for frame in frames]
    except (KeyError, TypeError):
        _failure("png_membership", rid)
    if (
        len(set(names)) != len(names)
        or any(Path(name).name != name for name in names)
        or {path.name for path in frame_dir.iterdir()} != set(names)
    ):
        _failure("png_membership", rid)
    checks: list[dict[str, str | bool]] = []
    for frame, name in zip(frames, names, strict=True):
        frame_id = str(frame.get("frame_id", ""))
        path = frame_dir / name
        if not path.is_file() or sha256_file(path) != frame.get("png_sha256"):
            _failure("png_sha256", rid, frame_id)
        if _decode_digest(path, rid, frame_id) != frame.get("decoded_array_sha256"):
            _failure("decoded_array_sha256", rid, frame_id)
        checks.extend(
            (
                {"check": "png_sha256", "frame_id": frame_id, "passed": True},
                {
                    "check": "decoded_array_sha256",
                    "frame_id": frame_id,
                    "passed": True,
                },
            )
        )
    return len(names), checks


def _verify_recording(repo: Path, records: Path, work: Path, rid: str) -> dict[str, Any]:
    """Internal verification after the public failure boundary."""
    if rid not in RECOVERY_RECORDINGS:
        _failure("recording_set", rid)
    pins = MEDIA_PINS[rid]
    manifest = repo / pins.vision_manifest_path
    manifest_digest = verify_pin(manifest, pins.vision_manifest_sha256)
    expected = build_frame_plan(rid, read_json(manifest), manifest_sha256=manifest_digest)
    plan_file = plan_path(records, rid)
    actual, plan_tool = _normalized_plan(read_json(plan_file), rid)
    if actual != expected:
        _failure("plan_equality", rid)
    extraction_file = extraction_path(records, rid)
    extraction = read_json(extraction_file)
    frames, extraction_tool = _extraction_frames(extraction, rid)
    if extraction.get("video_identity") != expected["video_identity"]:
        _failure("extraction_video", rid)
    if _frame_identity(frames, rid) != _expected_frames(expected, rid):
        _failure("frame_list", rid)
    count, image_checks = _verify_pngs(work / "frames" / rid, frames, rid)
    expected_digest = hashlib.sha256(canonical_json(expected).encode()).hexdigest()
    return {
        "recording_id": rid,
        "plan_sha256": sha256_file(plan_file),
        "recomputed_plan_sha256": expected_digest,
        "extraction_sha256": sha256_file(extraction_file),
        "planned_png_count": len(expected["frames"]),
        "verified_png_count": count,
        "checks": [
            {"check": check, "passed": True}
            for check in (
                "manifest_pin",
                "plan_equality",
                "extraction_video",
                "frame_list",
                "png_membership",
            )
        ]
        + image_checks,
        "plan_tool_commit": plan_tool,
        "extraction_tool_commit": extraction_tool,
        "passed": True,
    }


def verify_recording(repo: Path, records: Path, work: Path, rid: str) -> dict[str, Any]:
    """Verify one partial product without writing or exposing pixels."""
    try:
        return _verify_recording(repo, records, work, rid)
    except MalformedInput as exc:
        if str(exc).startswith("check="):
            raise
        _failure("input", rid)
    except Exception:
        _failure("input", rid)


def verify_existing_outputs(
    repo: Path, records: Path, work: Path
) -> list[dict[str, Any]]:
    """Verify exactly the three stopped-run products, read-only."""
    return [verify_recording(repo, records, work, rid) for rid in RECOVERY_RECORDINGS]
