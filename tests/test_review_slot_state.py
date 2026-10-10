"""Parity of the promoted X = 0 rule (``review.slot_state``) with the validated one."""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
import review_roster_timing as tools_timing
import review_special_roster_sync as tools_sync

from splatoon3_ai_coach.review.models import (
    CoverageReport,
    CursorObservation,
    CursorSlotObservation,
    ReviewTimelineDataset,
    ReviewTimelineSample,
    SlotState,
)
from splatoon3_ai_coach.review.slot_state import (
    SCREEN_WIDTH,
    SEARCH_PX,
    lamp_states,
    nearest_transition_px,
    slot_state,
)

ROOT = Path(__file__).resolve().parents[1] / "analysis/review_validation"
HELDOUT_BATCHES = [
    (ROOT / "heldout/sync/boundary/cases.json", ROOT / "heldout"),
    (ROOT / "heldout/sync/boundary_batch2/cases.json", ROOT / "heldout"),
    (ROOT / "heldout2/boundary/cases.json", ROOT / "heldout2"),
]
STATES = ["alive", "dead", "unknown"]


def _random_seq(rng: random.Random) -> list[tuple[float, str]]:
    xs = sorted(rng.sample(range(0, 400), rng.randint(0, 40)))
    return [(float(x), rng.choice(STATES)) for x in xs]


def test_constants_match_tools() -> None:
    assert SEARCH_PX == tools_timing.SEARCH_PX
    assert SCREEN_WIDTH == tools_timing.SCREEN_WIDTH


@pytest.mark.parametrize("seed", range(20))
def test_slot_state_matches_tools_on_synthetic_sequences(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(50):
        seq = _random_seq(rng)
        g = rng.uniform(-20, 420)
        window = rng.choice([0.0, 0.0, 2.0, 5.0])
        ours = slot_state(seq, g, window).model_dump()
        theirs = tools_sync.slot_state(seq, g, window)
        assert ours == theirs, (seq, g, window)


@pytest.mark.parametrize("seed", range(10))
def test_nearest_transition_px_matches_tools_transition_px(seed: int) -> None:
    rng = random.Random(1000 + seed)
    for _ in range(50):
        seq = _random_seq(rng)
        g = rng.uniform(-20, 420)
        dist = nearest_transition_px(seq, g)
        theirs = tools_sync.slot_state(seq, g, 0.0)["transition_px"]
        assert (None if dist is None else round(dist, 1)) == theirs


def test_nearest_transition_px_geometry() -> None:
    seq = [(100.0, "alive"), (110.0, "alive"), (120.0, "dead"), (130.0, "unknown")]
    assert nearest_transition_px(seq, 105.0) == 5.0
    assert nearest_transition_px(seq, 115.0) == 0.0
    assert nearest_transition_px(seq, 126.0) == 6.0
    assert nearest_transition_px([(100.0, "alive"), (110.0, "alive")], 105.0) is None


def test_slot_state_exact_on_frame_and_transition_edges() -> None:
    seq = [(10.0, "alive"), (20.0, "dead")]
    assert slot_state(seq, 10.0).state == "unknown"
    assert slot_state(seq, 10.0).reason == "near_roster_transition"
    assert slot_state(seq, 20.0).reason == "no_lamp_frames"
    assert slot_state([(10.0, "alive"), (20.0, "alive")], 10.0).state == "alive"
    for g in (5.0, 10.0, 15.0, 20.0, 25.0):
        assert slot_state(seq, g).model_dump() == tools_sync.slot_state(seq, g, 0.0)


def _sample(
    index: int, x: float | None, states: dict[str, SlotState]
) -> ReviewTimelineSample:
    sid = f"s{index}"
    return ReviewTimelineSample(
        sample_id=sid,
        capture_index=index,
        image_path=f"{sid}.png",
        image_sha256="0" * 64,
        clock_status="ok",
        cursor=CursorObservation(x=x),
        slots=[
            CursorSlotObservation(slot_id=slot, state=state, sample_id=sid)
            for slot, state in states.items()
        ],
    )


def test_lamp_states_matches_tools_including_merges() -> None:
    a, d, u = SlotState.alive, SlotState.dead, SlotState.unknown
    samples = [
        _sample(0, 10, {"top_1": a, "top_2": d}),
        _sample(1, 10, {"top_1": d, "top_2": u}),
        _sample(2, 12, {"top_1": a, "top_2": u}),
        _sample(3, None, {"top_1": d}),
        _sample(4, 14, {"top_1": u, "top_2": u}),
    ]
    implausible = _sample(5, 16, {"top_1": d})
    implausible.clock_status = "implausible"
    ds = ReviewTimelineDataset(
        samples=[*samples, implausible],
        coverage=CoverageReport(sample_count=6, clocked_sample_count=5),
    )
    assert lamp_states(ds) == tools_timing.lamp_states(ds)
    assert lamp_states(ds)["top_1"] == [
        (10.0, "unknown"),
        (12.0, "alive"),
        (14.0, "unknown"),
    ]


def _heldout_cases() -> list[tuple[dict, Path]]:
    out = []
    for cases_path, imports in HELDOUT_BATCHES:
        if cases_path.exists():
            out += [(c, imports) for c in json.loads(cases_path.read_text("utf-8"))]
    return out


@pytest.mark.skipif(not _heldout_cases(), reason="held-out cases not present")
def test_promoted_rule_reproduces_every_heldout_pipeline_state_x0() -> None:
    cache: dict[Path, tuple[ReviewTimelineDataset, dict]] = {}
    checked = 0
    for case, imports in _heldout_cases():
        path = imports / case["recording"] / "review_timeline.json"
        if path not in cache:
            ds = ReviewTimelineDataset.model_validate_json(path.read_text("utf-8"))
            cache[path] = (ds, lamp_states(ds))
        ds, lamps = cache[path]
        g = _glyph(ds, case)
        state = slot_state(lamps.get(case["slot"], []), g).state
        assert state == case["pipeline_state_x0"], case["case_id"]
        checked += 1
    assert checked == 209


def _glyph(ds: ReviewTimelineDataset, case: dict) -> float:
    matches = [
        e.x_norm * SCREEN_WIDTH
        for e in ds.special_events
        if e.slot_id == case["special_slot"]
        and round(e.x_norm * SCREEN_WIDTH, 1) == case["glyph_x"]
    ]
    assert len(matches) == 1, case["case_id"]
    return matches[0]
