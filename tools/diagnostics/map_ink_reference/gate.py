"""Duplicate admissibility gate (blind; computed from recorded labels alone).

Population D_s and pair classification are frozen by Amendment 3:

| copies                                 | numerator | denominator |
| same label, not cant_tell              | no        | yes         |
| both cant_tell                         | no        | no          |
| one cant_tell, one other label         | yes       | yes         |
| two different non-cant_tell labels     | yes       | yes         |
| either copy has no recorded label      | yes       | yes         |
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from map_ink_reference.constants import CANT_TELL, GATE_CAP, GATE_CONFIDENCE, LABELS
from map_ink_reference.errors import MalformedInput

AGREE = "agree"
EXCLUDED = "excluded"
DISAGREE = "disagree"


def classify_pair(first: str | None, second: str | None) -> str:
    """Frozen classification of one duplicate pair from its two copies."""
    for label in (first, second):
        if label is not None and label not in LABELS:
            raise MalformedInput(f"unknown label {label!r}")
    if first is None or second is None:
        return DISAGREE
    if first == CANT_TELL and second == CANT_TELL:
        return EXCLUDED
    return AGREE if first == second else DISAGREE


def _binom_cdf(x: int, n: int, p: float) -> float:
    return sum(math.comb(n, k) * p**k * (1.0 - p) ** (n - k) for k in range(x + 1))


def clopper_pearson_upper(x: int, n: int, confidence: float = GATE_CONFIDENCE) -> float:
    """Exact one-sided upper Clopper–Pearson bound for x events in n trials."""
    if n <= 0 or not 0 <= x <= n:
        raise MalformedInput(f"invalid binomial counts x={x}, n={n}")
    if x == n:
        return 1.0
    alpha = 1.0 - confidence
    low, high = 0.0, 1.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if _binom_cdf(x, n, mid) > alpha:
            low = mid
        else:
            high = mid
    return high


def population(
    duplicate_ids: Sequence[str],
    point_recording: Mapping[str, str],
    valid_swatch_recordings: Iterable[str],
) -> list[str]:
    """D_s: every rule-produced duplicate except recordings without swatches."""
    valid = set(valid_swatch_recordings)
    return [pid for pid in duplicate_ids if point_recording[pid] in valid]


def verify_queue_duplicates(
    members: Sequence[str],
    queue_items: Sequence[Mapping[str, Any]],
    point_refs: Mapping[str, str],
) -> None:
    """The planned queue must repeat exactly the D_s points (fail closed)."""
    repeated = {point_refs[i["point_ref"]] for i in queue_items if int(i["copy"]) == 1}
    if repeated != set(members):
        raise MalformedInput(
            "queue duplicates differ from the frozen duplicate population: "
            f"missing {sorted(set(members) - repeated)[:5]}, "
            f"unexpected {sorted(repeated - set(members))[:5]}"
        )


def pair_labels(
    members: Sequence[str],
    queue_items: Sequence[Mapping[str, Any]],
    labels: Mapping[str, str | None],
    point_refs: Mapping[str, str],
) -> dict[str, tuple[str | None, str | None]]:
    """Both copies' recorded labels for every D_s member (missing → None)."""
    ref_of = {pid: ref for ref, pid in point_refs.items()}
    copies: dict[str, dict[int, str]] = {}
    for item in queue_items:
        copies.setdefault(item["point_ref"], {})[int(item["copy"])] = item["item_id"]
    out: dict[str, tuple[str | None, str | None]] = {}
    for pid in members:
        ref = ref_of.get(pid)
        items = copies.get(ref, {}) if ref is not None else {}
        if set(items) - {0, 1}:
            raise MalformedInput(f"unexpected copy index for {pid}")
        out[pid] = (
            labels.get(items[0]) if 0 in items else None,
            labels.get(items[1]) if 1 in items else None,
        )
    return out


@dataclass(frozen=True)
class GateResult:
    """Gate outcome for one look."""

    numerator: int
    denominator: int
    excluded: int
    upper_bound: float | None
    cap: float
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        """JSON payload."""
        return asdict(self)


def evaluate_gate(pairs: Mapping[str, tuple[str | None, str | None]]) -> GateResult:
    """Apply the frozen classification and the 0.10 Clopper–Pearson cap."""
    outcome = [classify_pair(a, b) for a, b in pairs.values()]
    numerator = outcome.count(DISAGREE)
    denominator = numerator + outcome.count(AGREE)
    excluded = outcome.count(EXCLUDED)
    if denominator == 0:
        return GateResult(numerator, 0, excluded, None, GATE_CAP, False)
    upper = clopper_pearson_upper(numerator, denominator)
    return GateResult(
        numerator, denominator, excluded, upper, GATE_CAP, upper <= GATE_CAP
    )


def pooled_pairs(
    stage1: Mapping[str, tuple[str | None, str | None]],
    stage2: Mapping[str, tuple[str | None, str | None]],
) -> dict[str, tuple[str | None, str | None]]:
    """D_1 ∪ D_2; D_1 pairs keep their Stage 1 copies."""
    overlap = set(stage1) & set(stage2)
    if overlap:
        raise MalformedInput(
            f"Stage 1 and Stage 2 duplicate sets overlap: {sorted(overlap)}"
        )
    return {
        **{f"1|{k}": v for k, v in stage1.items()},
        **{f"2|{k}": v for k, v in stage2.items()},
    }
