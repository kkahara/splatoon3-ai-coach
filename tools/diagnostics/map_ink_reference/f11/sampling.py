"""Seeded F11 frame and point draws. Replay is the same functions."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence

import numpy as np

TARGET_LIMIT = 6
CONTROL_LIMIT = 3
MIN_DISTANCE_PX = 32


def new_salt() -> str:
    """32 bytes from the operating-system random source, as hex."""
    return os.urandom(32).hex()


def derivation_seed(text: str) -> int:
    """First 8 bytes of SHA-256, big-endian, as in Amendment 3."""
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


def _generator(text: str) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(derivation_seed(text)))


def draw_frame_index(
    salt_hex: str, video_identity: str, first_pts: int, length: int
) -> int:
    """Uniform frame index in ``0 .. length-1``."""
    if length <= 0:
        raise ValueError("an interval has no frames")
    text = f"amendment11-frame|{salt_hex}|{video_identity}|{first_pts}"
    return int(_generator(text).integers(0, length))


def draw_target_indices(
    salt_hex: str,
    video_identity: str,
    source_pts: int,
    flat_indices: Sequence[int],
) -> list[int]:
    """Simple random sample of up to 6 target indices, with no spacing rule."""
    population = [int(index) for index in flat_indices]
    count = min(TARGET_LIMIT, len(population))
    if count == 0:
        return []
    text = f"amendment11-target|{salt_hex}|{video_identity}|{source_pts}"
    order = _generator(text).permutation(np.asarray(population, dtype=np.int64))
    return [int(index) for index in order[:count]]


def draw_control_points(
    salt_hex: str,
    video_identity: str,
    source_pts: int,
    candidates: Sequence[tuple[int, int]],
    targets: Sequence[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Up to 3 agreement pixels, each at least 32 px from accepted points."""
    pool = [(int(x), int(y)) for x, y in candidates]
    if not pool:
        return []
    text = f"amendment11-control|{salt_hex}|{video_identity}|{source_pts}"
    order = _generator(text).permutation(len(pool))
    accepted: list[tuple[int, int]] = []
    blocked = [(int(x), int(y)) for x, y in targets]
    for position in order:
        point = pool[int(position)]
        if _far_enough(point, blocked):
            accepted.append(point)
            blocked.append(point)
        if len(accepted) == CONTROL_LIMIT:
            break
    return accepted


def _far_enough(point: tuple[int, int], blocked: Sequence[tuple[int, int]]) -> bool:
    return all(_distance(point, other) >= MIN_DISTANCE_PX for other in blocked)


def _distance(first: tuple[int, int], second: tuple[int, int]) -> float:
    return float(np.hypot(first[0] - second[0], first[1] - second[1]))
