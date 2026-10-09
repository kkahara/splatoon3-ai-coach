#!/usr/bin/env python3
"""Freeze the Review event-lane calibration corpus as a regression fixture.

For each recording directory (``review timeline-import`` output with reviewed
``sheet/labels.yaml``), copies the static frames production selects (bytes
unchanged), and writes ``corpus.yaml`` with the axis, the frames' cursor
positions, the human-confirmed labels, and the outcome under the current
config: which labels production accepts and which are known misses.

``tests/test_review_lane_corpus.py`` replays the frozen frames through the
production path and asserts zero false positives against the human labels and
an unchanged accepted / known-miss split.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import cv2
import yaml

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.review.event_lane import (
    calibrate_axis,
    extract_lane_events,
    select_static_samples,
)
from splatoon3_ai_coach.review.models import ReviewTimelineDataset

MATCH_WINDOW = 5.0


def main() -> int:
    """Write one fixture directory per recording under ``--out``."""
    args = _parse_args()
    config = load_config(resolve_config_path(args.config)).review
    for directory in args.recordings:
        freeze(directory, args.out / directory.name, config)
        print(f"froze {directory.name} -> {args.out / directory.name}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    return parser.parse_args()


def freeze(directory: Path, out: Path, config: ReviewTimelineConfig) -> None:
    """Copy selected frames and write ``corpus.yaml`` for one recording."""
    dataset = ReviewTimelineDataset.model_validate_json(
        (directory / "review_timeline.json").read_text(encoding="utf-8")
    )
    labels = yaml.safe_load((directory / "sheet" / "labels.yaml").read_text("utf-8"))
    if labels.get("review_status") != "reviewed":
        raise SystemExit(f"{directory}: labels are not reviewed")
    axis = calibrate_axis(dataset.samples, config)
    if axis is None:
        raise SystemExit(f"{directory}: no axis")
    selected = select_static_samples(dataset.samples, config.static_frame_count)
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    for sample in selected:
        name = f"{sample.sample_id}.jpg"
        shutil.copyfile(sample.image_path, out / name)
        frames.append(
            {
                "sample_id": sample.sample_id,
                "capture_index": sample.capture_index,
                "file": name,
                "cursor_x": sample.cursor.x,
            }
        )
    images = {s.sample_id: cv2.imread(s.image_path) for s in selected}
    markers, specials = extract_lane_events(images, selected, axis, config)
    gold = [
        {
            "kind": e["kind"],
            "slot": e["slot"],
            "special_id": e.get("special_id"),
            "elapsed": round(float(e["elapsed"]), 2),
            "cluster_id": e.get("cluster_id"),
        }
        for e in labels["events"]
        if e.get("status") == "confirmed"
    ]
    predicted = [
        ("special", e.slot_id, e.special_id, e.elapsed_estimate) for e in specials
    ]
    predicted += [
        ("death", m.player_slot_id, None, m.elapsed_estimate)
        for m in markers
        if m.kind == "death"
    ]
    matched = match_labels(gold, predicted, MATCH_WINDOW)
    for index, item in enumerate(gold):
        item["accepted"] = index in matched
    corpus = {
        "recording_id": directory.name,
        "split": labels.get("split"),
        "match_window_seconds": MATCH_WINDOW,
        "axis": axis.model_dump(),
        "frames": frames,
        "labels": gold,
    }
    (out / "corpus.yaml").write_text(yaml.safe_dump(corpus, sort_keys=False), "utf-8")


def match_labels(
    gold: list[dict[str, object]],
    predicted: list[tuple[str, str | None, str | None, float]],
    window: float,
) -> set[int]:
    """Gold indexes matched one-to-one (same kind, slot, special; nearest first)."""
    pairs = sorted(
        (abs(float(elapsed) - float(g["elapsed"])), gi, pi)  # type: ignore[arg-type]
        for gi, g in enumerate(gold)
        for pi, (kind, slot, special_id, elapsed) in enumerate(predicted)
        if kind == g["kind"]
        and slot == g["slot"]
        and (kind == "death" or special_id == g["special_id"])
        and abs(float(elapsed) - float(g["elapsed"])) <= window  # type: ignore[arg-type]
    )
    used_gold: set[int] = set()
    used_pred: set[int] = set()
    for _, gi, pi in pairs:
        if gi not in used_gold and pi not in used_pred:
            used_gold.add(gi)
            used_pred.add(pi)
    return used_gold


if __name__ == "__main__":
    raise SystemExit(main())
