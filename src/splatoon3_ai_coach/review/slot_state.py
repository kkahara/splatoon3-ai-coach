"""Roster-lamp slot state at a timeline x (the validated X = 0 rule).

Each Review frame shows the roster at its cursor x, so a slot's lamp is only
known at sampled x positions. This module is a parity-locked port of
``tools/diagnostics/review_roster_timing.lamp_states`` and
``tools/diagnostics/review_special_roster_sync.slot_state``, the rule that
passed SPECIAL_USE gate D (tag ``special-use-timing-frozen``). Do not change
its semantics here; ``tests/test_review_slot_state.py`` pins parity.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Literal

from pydantic import BaseModel

from splatoon3_ai_coach.review.models import ReviewTimelineDataset

SCREEN_WIDTH = 1920
SEARCH_PX = 40

LampSequence = list[tuple[float, str]]


class SlotStateAt(BaseModel):
    """Lamp state of one slot at one x, with the algorithm's reason."""

    state: Literal["alive", "dead", "unknown"]
    reason: Literal["near_roster_transition", "no_lamp_frames"] | None = None
    transition_px: float | None = None


def lamp_states(ds: ReviewTimelineDataset) -> dict[str, LampSequence]:
    """Per slot: (cursor x, state) with frames at one x merged; mixed -> unknown."""
    by_x: dict[str, dict[float, set[str]]] = defaultdict(lambda: defaultdict(set))
    for s in ds.samples:
        if s.cursor.x is None or s.clock_status == "implausible":
            continue
        for o in s.slots:
            by_x[o.slot_id][float(s.cursor.x)].add(o.state.value)
    out: dict[str, LampSequence] = {}
    for slot, xs in by_x.items():
        seq = []
        for x in sorted(xs):
            known = xs[x] - {"unknown"}
            seq.append((x, known.pop() if len(known) == 1 else "unknown"))
        out[slot] = seq
    return out


def slot_state(seq: LampSequence, g: float, window_px: float = 0.0) -> SlotStateAt:
    """Lamp state at ``g`` px; ``window_px = 0`` is the validated X = 0 rule.

    The last known frame at ``x <= g`` and the first at ``x > g`` (each within
    ``SEARCH_PX``) must agree and no lamp transition bracket may lie within
    ``window_px`` of ``g``.
    """
    known = [(x, s) for x, s in seq if s != "unknown"]
    before = [p for p in known if p[0] <= g and g - p[0] <= SEARCH_PX]
    after = [p for p in known if p[0] > g and p[0] - g <= SEARCH_PX]
    transitions = [
        (a[0], b[0]) for a, b in zip(known, known[1:], strict=False) if a[1] != b[1]
    ]
    dist = min((_gap(t, g) for t in transitions), default=None)
    transition_px = None if dist is None else round(dist, 1)
    if not before or not after:
        return SlotStateAt(
            state="unknown", reason="no_lamp_frames", transition_px=transition_px
        )
    prev, nxt = before[-1][1], after[0][1]
    if prev != nxt or (dist is not None and dist <= window_px):
        return SlotStateAt(
            state="unknown", reason="near_roster_transition", transition_px=transition_px
        )
    return SlotStateAt(state=prev, transition_px=transition_px)


def _gap(bracket: tuple[float, float], g: float) -> float:
    a, b = bracket
    return 0.0 if a < g <= b else min(abs(g - a), abs(g - b))
