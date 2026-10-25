"""Seeded point sampling and the frozen duplicate rule (blind)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from map_ink_reference.constants import (
    DUPLICATE_RATE_DENOMINATOR,
    DUPLICATE_SEED_PREFIX,
    MIN_POINT_SPACING_PX,
    POINT_SEED_PREFIX,
    POINTS_PER_FRAME,
)
from map_ink_reference.domain import point_domain
from map_ink_reference.errors import MalformedInput, PinMismatch
from map_ink_reference.io_utils import seed_from_text, sha256_file
from map_ink_reference.stage_identity import StageIdentity


def point_seed(video_identity: str, source_pts: int) -> int:
    """Seed = first 8 bytes BE of SHA-256("amendment3-points|" + id + "|" + pts)."""
    return seed_from_text(f"{POINT_SEED_PREFIX}{video_identity}|{source_pts}")


def draw_points(domain: np.ndarray, seed: int) -> list[tuple[int, int]]:
    """Shuffle row-major domain indices and accept greedily with >= 32 px spacing.

    Returns up to 9 ``(x, y)`` points in acceptance order.
    """
    if domain.ndim != 2 or domain.dtype != bool:
        raise MalformedInput("domain must be a 2-D boolean mask")
    width = domain.shape[1]
    flat = np.flatnonzero(domain)
    order = np.random.Generator(np.random.PCG64(seed)).permutation(flat)
    accepted: list[tuple[int, int]] = []
    min_sq = MIN_POINT_SPACING_PX * MIN_POINT_SPACING_PX
    for index in order:
        y, x = divmod(int(index), width)
        if all((x - ax) ** 2 + (y - ay) ** 2 >= min_sq for ax, ay in accepted):
            accepted.append((x, y))
            if len(accepted) == POINTS_PER_FRAME:
                break
    return accepted


@dataclass(frozen=True)
class StageFrame:
    """One selected frame handed to point sampling."""

    recording_id: str
    frame_id: str
    source_pts: int
    png_path: Path
    png_sha256: str


def load_verified_png(path: Path, expected_sha256: str) -> np.ndarray:
    """Load an extracted PNG after verifying its hash."""
    actual = sha256_file(path) if path.is_file() else None
    if actual != expected_sha256:
        raise PinMismatch(f"extracted frame hash mismatch for {path}")
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise MalformedInput(f"cannot read {path}")
    return image


def sample_stage_points(
    frames: Sequence[StageFrame],
    *,
    video_identities: Mapping[str, str],
    stage_identities: Mapping[str, StageIdentity],
    geometry_dir: Path,
) -> list[dict[str, Any]]:
    """Point records for every selected frame, in frame order."""
    points: list[dict[str, Any]] = []
    for frame in frames:
        image = load_verified_png(frame.png_path, frame.png_sha256)
        domain = point_domain(image, geometry_dir, stage_identities[frame.recording_id])
        seed = point_seed(video_identities[frame.recording_id], frame.source_pts)
        for k, (x, y) in enumerate(draw_points(domain, seed)):
            points.append(
                {
                    "point_id": f"{frame.recording_id}|{frame.frame_id}|{k}",
                    "recording_id": frame.recording_id,
                    "frame_id": frame.frame_id,
                    "x": x,
                    "y": y,
                }
            )
    return points


def duplicate_count(n_points: int) -> int:
    """``ceil(0.10 * N)`` computed exactly as ``ceil(N / 10)``."""
    if n_points < 0:
        raise MalformedInput("negative point count")
    return -(-n_points // DUPLICATE_RATE_DENOMINATOR)


def duplicate_point_ids(
    point_ids: Sequence[str], point_manifest_sha256: str
) -> list[str]:
    """Duplicate selection seeded from the point-manifest hash (no replacement)."""
    if len(set(point_ids)) != len(point_ids):
        raise MalformedInput("point ids must be unique")
    count = duplicate_count(len(point_ids))
    seed = seed_from_text(f"{DUPLICATE_SEED_PREFIX}{point_manifest_sha256}")
    rng = np.random.Generator(np.random.PCG64(seed))
    chosen = rng.choice(len(point_ids), size=count, replace=False)
    return [point_ids[int(i)] for i in chosen]
