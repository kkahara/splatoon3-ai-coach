"""Frozen Review event-lane calibration corpus (human-reviewed labels).

Replays the static frames frozen by ``tools/calibration/review_special_fixture.py``
through the production path with ``configs/default.yaml`` and checks:

* no emitted special or death is a false positive against the labels;
* the accepted / known-miss split is unchanged (a recognition failure must
  stay a miss, never become a wrong accepted read).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import pytest
import yaml

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.review.event_lane import extract_lane_events
from splatoon3_ai_coach.review.models import (
    AxisCalibrationEvidence,
    CursorObservation,
    ReviewTimelineSample,
)

CORPUS = Path(__file__).resolve().parent / "fixtures" / "review" / "lane_corpus"
RECORDINGS = sorted(p.name for p in CORPUS.iterdir() if (p / "corpus.yaml").exists())


def _corpus(recording: str) -> dict:
    return yaml.safe_load((CORPUS / recording / "corpus.yaml").read_text("utf-8"))


def _predict(recording: str) -> tuple[list[tuple], list]:
    corpus = _corpus(recording)
    config = load_config(default_config_path()).review
    images, samples = {}, []
    for frame in corpus["frames"]:
        image = cv2.imread(str(CORPUS / recording / frame["file"]))
        assert image is not None
        images[frame["sample_id"]] = image
        x = frame["cursor_x"]
        samples.append(
            ReviewTimelineSample(
                sample_id=frame["sample_id"],
                capture_index=frame["capture_index"],
                image_path=frame["file"],
                image_sha256="0" * 64,
                cursor=CursorObservation(
                    x=x, x_norm=None if x is None else x / image.shape[1], confidence=1.0
                ),
            )
        )
    axis = AxisCalibrationEvidence(**corpus["axis"])
    markers, specials = extract_lane_events(images, samples, axis, config)
    predicted = [
        ("special", e.slot_id, e.special_id, e.elapsed_estimate)
        for e in specials
        if e.special_id != "unknown"
    ]
    predicted += [
        ("death", m.player_slot_id, None, m.elapsed_estimate)
        for m in markers
        if m.kind == "death"
    ]
    return predicted, specials


def _match(gold: list[dict], predicted: list[tuple], window: float) -> tuple[set, set]:
    pairs = sorted(
        (abs(elapsed - g["elapsed"]), gi, pi)
        for gi, g in enumerate(gold)
        for pi, (kind, slot, special_id, elapsed) in enumerate(predicted)
        if kind == g["kind"]
        and slot == g["slot"]
        and (kind == "death" or special_id == g["special_id"])
        and abs(elapsed - g["elapsed"]) <= window
    )
    used_gold: set[int] = set()
    used_pred: set[int] = set()
    for _, gi, pi in pairs:
        if gi not in used_gold and pi not in used_pred:
            used_gold.add(gi)
            used_pred.add(pi)
    return used_gold, used_pred


@pytest.mark.parametrize("recording", RECORDINGS)
def test_no_false_positives_and_frozen_accept_miss_split(recording: str) -> None:
    corpus = _corpus(recording)
    gold = corpus["labels"]
    predicted, _ = _predict(recording)
    matched_gold, matched_pred = _match(gold, predicted, corpus["match_window_seconds"])
    false_positives = [p for i, p in enumerate(predicted) if i not in matched_pred]
    assert false_positives == []
    accepted = {i for i, g in enumerate(gold) if g["accepted"]}
    assert matched_gold == accepted


def test_corpus_covers_both_splits_and_kinds() -> None:
    splits = {_corpus(r)["split"] for r in RECORDINGS}
    assert splits == {"tuning", "held_out"}
    labels = [g for r in RECORDINGS for g in _corpus(r)["labels"]]
    assert sum(g["kind"] == "special" for g in labels) >= 80
    assert sum(g["kind"] == "death" for g in labels) >= 80


def test_unrecognized_triple_splashdown_stays_rejected() -> None:
    corpus = _corpus("21-49-20")
    [label] = [g for g in corpus["labels"] if g["cluster_id"] == "r3-x0.9021"]
    assert label["special_id"] == "triple_splashdown"
    assert not label["accepted"]
    _, specials = _predict("21-49-20")
    at_glyph = [
        e for e in specials if e.slot_id == "top_4" and abs(e.x_norm - 0.9021) < 0.006
    ]
    assert all(e.special_id == "unknown" for e in at_glyph)


def test_held_out_known_misses_stay_misses() -> None:
    misses = {
        (r, g["kind"], g["slot"], g["special_id"])
        for r in RECORDINGS
        if _corpus(r)["split"] == "held_out"
        for g in _corpus(r)["labels"]
        if not g["accepted"]
    }
    assert ("21-41-07", "special", "bottom_3", "big_bubbler") in misses
    assert sum(kind == "special" for _, kind, _, _ in misses) == 1
