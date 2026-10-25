"""Labelling queue construction with the frozen duplicate placement (blind)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

from map_ink_reference.constants import DUPLICATE_MIN_GAP
from map_ink_reference.errors import MalformedInput


def build_queue(
    point_ids: Sequence[str],
    duplicate_ids: Sequence[str],
    queue_seed: int,
    new_id: Callable[[], str],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Shuffled single queue; each duplicate re-presented >= 50 items later.

    ``point_ids`` are the presentable points (recordings with a valid swatch
    pair). ``duplicate_ids`` keep the duplicate rule's order; ids whose point
    is not presentable are dropped. Returns queue items and the opaque
    ``point_ref -> point_id`` key.
    """
    if len(set(point_ids)) != len(point_ids):
        raise MalformedInput("duplicate point ids in queue input")
    presentable = set(point_ids)
    refs = {pid: new_id() for pid in point_ids}
    if len(set(refs.values())) != len(refs):
        raise MalformedInput("opaque point references collided")
    rng = np.random.Generator(np.random.PCG64(queue_seed))
    order = rng.permutation(len(point_ids))
    queue = [{"point_ref": refs[point_ids[int(i)]], "copy": 0} for i in order]
    for pid in duplicate_ids:
        if pid not in presentable:
            continue
        first = next(i for i, item in enumerate(queue) if item["point_ref"] == refs[pid])
        low = first + DUPLICATE_MIN_GAP
        position = (
            len(queue) if low > len(queue) else int(rng.integers(low, len(queue) + 1))
        )
        queue.insert(position, {"point_ref": refs[pid], "copy": 1})
    for item in queue:
        item["item_id"] = new_id()
    if len({item["item_id"] for item in queue}) != len(queue):
        raise MalformedInput("opaque item ids collided")
    key = {ref: pid for pid, ref in refs.items()}
    return queue, key


def duplicate_gaps(queue: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Index distance between first and repeat presentation per point_ref."""
    first: dict[str, int] = {}
    gaps: dict[str, int] = {}
    for index, item in enumerate(queue):
        ref = item["point_ref"]
        if item["copy"] == 0:
            first[ref] = index
        else:
            gaps[ref] = index - first[ref]
    return gaps
