"""Extraction: decode planned frames to lossless PNGs with provenance (blind).

Never loads a calibration and never applies a classifier. Frames are located
by ``source_pts`` and checked against ``source_frame_index``; every PNG is read
back and compared with the decoded array.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import av
import cv2
import numpy as np

from map_ink_reference.errors import MalformedInput, PinMismatch
from map_ink_reference.io_utils import sha256_array, sha256_file
from splatoon3_ai_coach.media.video import VideoLoader
from splatoon3_ai_coach.media.video_identity import video_identity


def decoder_versions() -> dict[str, Any]:
    """Library versions recorded with every extraction."""
    return {
        "pyav": av.__version__,
        "ffmpeg_libraries": {k: list(v) for k, v in av.library_versions.items()},
        "opencv": cv2.__version__,
        "numpy": np.__version__,
    }


def verify_video(video_path: Path, expected_sha256: str, expected_identity: str) -> None:
    """STOP unless the file hash and content identity both match."""
    actual = sha256_file(video_path)
    if actual != expected_sha256:
        raise PinMismatch(
            f"video SHA-256 mismatch: expected {expected_sha256}, got {actual}"
        )
    identity = video_identity(video_path)
    if identity != expected_identity:
        raise PinMismatch(f"video_identity mismatch: {identity} != {expected_identity}")


def _write_png(path: Path, image: np.ndarray) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise MalformedInput(f"failed to write {path}")
    reread = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if reread is None or not np.array_equal(reread, image):
        raise MalformedInput(f"PNG round trip is not lossless for {path}")
    return sha256_file(path)


def _check_frame(record: Mapping[str, Any], frame_index: int, shape: tuple, size) -> None:
    if frame_index != record["source_frame_index"]:
        raise MalformedInput(
            f"frame {record['frame_id']}: decoder index {frame_index} != "
            f"source_frame_index {record['source_frame_index']}"
        )
    if (shape[1], shape[0]) != (size[0], size[1]):
        raise MalformedInput(f"frame {record['frame_id']}: size {shape} != plan {size}")


def extract_plan_frames(
    plan: Mapping[str, Any], video_path: Path, out_dir: Path
) -> list[dict[str, Any]]:
    """Decode every planned frame once and write ``<frame_id>.png``."""
    wanted = {int(r["source_pts"]): r for r in plan["frames"]}
    if len(wanted) != len(plan["frames"]):
        raise MalformedInput("plan has duplicate source_pts")
    written: dict[int, dict[str, Any]] = {}
    with VideoLoader(video_path) as loader:
        for frame in loader.frames():
            record = (
                wanted.get(frame.source_pts) if frame.source_pts is not None else None
            )
            if record is None:
                continue
            if frame.source_pts in written:
                raise MalformedInput(f"source_pts {frame.source_pts} decoded twice")
            _check_frame(record, frame.frame_index, frame.image.shape, plan["frame_size"])
            png = out_dir / f"{record['frame_id']}.png"
            written[frame.source_pts] = {
                "frame_id": record["frame_id"],
                "source_pts": frame.source_pts,
                "source_frame_index": frame.frame_index,
                "png": png.name,
                "png_sha256": _write_png(png, frame.image),
                "decoded_array_sha256": sha256_array(frame.image),
            }
            if len(written) == len(wanted):
                break
    missing = sorted(set(wanted) - set(written))
    if missing:
        raise MalformedInput(f"planned frames not decoded (source_pts): {missing[:10]}")
    return [written[pts] for pts in sorted(written)]


def extract_recording(
    plan: Mapping[str, Any],
    video_path: Path,
    out_dir: Path,
    *,
    expected_video_sha256: str,
) -> dict[str, Any]:
    """Verify the video, extract all planned frames and return provenance."""
    verify_video(video_path, expected_video_sha256, str(plan["video_identity"]))
    frames = extract_plan_frames(plan, video_path, out_dir)
    return {
        "recording_id": plan["recording_id"],
        "video_path": str(video_path),
        "video_sha256": expected_video_sha256,
        "video_identity": plan["video_identity"],
        "decoder": decoder_versions(),
        "frames": frames,
    }
