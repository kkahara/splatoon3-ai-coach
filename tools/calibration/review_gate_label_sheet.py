#!/usr/bin/env python3
"""Pre-filled GO-gate label drafts and review sheets for a current Review import.

Selects a deterministic, stratified set of samples from ``review_timeline.json``
(countdown, overtime, implausible clocks, regulation spread across the match,
frames with dead roster slots) and writes:

* ``sheet_NN.png``: per sample, the enlarged clock label, the cursor column
  with a red tick at the detected x, and both roster rows with detector states;
* ``draft_labels.yaml``: ``ValidationLabels`` pre-filled from the detector with
  ``review_status: draft``. A human corrects it, sets
  ``review_status: reviewed`` and saves it as ``labels.yaml``.

Drafts are proposals, never ground truth. Old sample IDs are not reused.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.paths import default_config_path

SLOTS = [f"{row}_{i}" for row in ("top", "bottom") for i in range(1, 5)]
PER_SHEET = 6
TILE_W = 1100


def main() -> int:
    """Write sheets and a draft label file for one import directory."""
    args = _parse_args()
    data = json.loads((args.import_dir / "review_timeline.json").read_text("utf-8"))
    samples = [s for s in data["samples"] if s["cursor"].get("x") is not None]
    chosen = select_samples(samples, args.regulation_count)
    config = load_config(default_config_path()).review
    args.out.mkdir(parents=True, exist_ok=True)
    for index in range(0, len(chosen), PER_SHEET):
        tiles = [_tile(s, config) for s in chosen[index : index + PER_SHEET]]
        cv2.imwrite(
            str(args.out / f"sheet_{index // PER_SHEET + 1:02d}.png"), np.vstack(tiles)
        )
    draft = _draft(args.import_dir, chosen)
    (args.out / "draft_labels.yaml").write_text(
        yaml.safe_dump(draft, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    print(f"{len(chosen)} samples -> {args.out}")
    return 0


def select_samples(samples: list[dict], regulation_count: int) -> list[dict]:
    """Deterministic stratified selection, ordered by capture index."""
    by_kind: dict[str, list[dict]] = {"countdown": [], "overtime": [], "implausible": []}
    regulation = []
    for sample in sorted(samples, key=lambda s: s["capture_index"]):
        display = sample.get("displayed_clock") or ""
        if sample.get("clock_status") == "implausible":
            by_kind["implausible"].append(sample)
        elif display.startswith("-"):
            by_kind["countdown"].append(sample)
        elif display.startswith("+"):
            by_kind["overtime"].append(sample)
        elif sample.get("elapsed_seconds") is not None:
            regulation.append(sample)
    chosen = by_kind["countdown"][:2] + _spread(by_kind["overtime"], 3)
    chosen += by_kind["implausible"]
    dead = [s for s in regulation if any(sl["state"] == "dead" for sl in s["slots"])]
    chosen += _spread(dead, regulation_count // 3)
    rest = [s for s in regulation if s not in chosen]
    chosen += _spread(rest, regulation_count - regulation_count // 3)
    unique = {s["sample_id"]: s for s in chosen}
    return sorted(unique.values(), key=lambda s: s["capture_index"])


def _spread(samples: list[dict], count: int) -> list[dict]:
    """Evenly spaced picks over clock value (deterministic)."""
    ordered = sorted(
        samples, key=lambda s: (s.get("elapsed_seconds") or 0, s["capture_index"])
    )
    if len(ordered) <= count:
        return ordered
    step = len(ordered) / count
    return [ordered[int(i * step)] for i in range(count)]


def _draft(import_dir: Path, chosen: list[dict]) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "review_status": "draft",
        "source_artifact": str(import_dir / "review_timeline.json"),
        "source_manifest": str(import_dir / "timeline_samples_manifest.yaml"),
        "cursor_tolerance_px": 8,
        "samples": [_draft_sample(s) for s in chosen],
    }


def _draft_sample(sample: dict) -> dict[str, Any]:
    states = {sl["slot_id"]: sl["state"] for sl in sample["slots"]}
    return {
        "sample_id": sample["sample_id"],
        "frame_index": sample.get("frame_index"),
        "expected_acceptance": "accepted",
        "expected_clock_state": "readable",
        # Implausible reads are drafted with no value: type the displayed clock.
        "expected_elapsed_seconds": sample.get("elapsed_seconds"),
        "expected_cursor_x": sample["cursor"]["x"],
        "expected_slots": {slot: states.get(slot, "unknown") for slot in SLOTS},
        "detector_clock": sample.get("displayed_clock"),
    }


def _tile(sample: dict, config: Any) -> np.ndarray:
    image = cv2.imread(sample["image_path"])
    h, w = image.shape[:2]
    x = int(sample["cursor"]["x"])
    video = config.video
    half = int(w * video.clock_roi_width_fraction / 2)
    clock = image[
        int(h * video.clock_roi_top_fraction) : int(h * video.clock_roi_bottom_fraction),
        max(0, x - half) : x + half,
    ]
    cursor = image[int(h * 0.86) : h, max(0, x - 40) : x + 40].copy()
    cursor[:, min(40, x) : min(40, x) + 1] = (0, 0, 255)
    rows = [
        _row_crop(image, x, band, config)
        for band in (config.roster_top_band, config.roster_bottom_band)
    ]
    lines = [[_scale(clock, 3), _scale(cursor, 2)]] + [[_scale(r, 2)] for r in rows]
    heights = [max(p.shape[0] for p in line) for line in lines]
    height = 60 + sum(heights) + 10 * len(lines)
    tile = np.zeros((height, TILE_W, 3), dtype=np.uint8)
    top = 60
    for line, line_height in zip(lines, heights, strict=True):
        left = 5
        for part in line:
            width = min(part.shape[1], TILE_W - left)
            tile[top : top + part.shape[0], left : left + width] = part[:, :width]
            left += width + 10
        top += line_height + 10
    states = {sl["slot_id"]: sl["state"][0].upper() for sl in sample["slots"]}
    roster = " ".join(f"{s}:{states.get(s, '?')}" for s in SLOTS)
    clock_text = f"{sample.get('displayed_clock')} [{sample.get('clock_status')}]"
    head = f"{sample['sample_id']}  clock={clock_text}  x={x}"
    cv2.putText(tile, head, (5, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    cv2.putText(tile, roster, (5, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    cv2.line(tile, (0, height - 1), (TILE_W, height - 1), (80, 80, 80), 1)
    return tile


def _row_crop(
    image: np.ndarray, x: int, band: tuple[float, float], config: Any
) -> np.ndarray:
    h, w = image.shape[:2]
    half = int(w * config.roster_row_width_fraction * 0.65)
    return image[int(h * band[0]) : int(h * band[1]), max(0, x - half) : x + half]


def _scale(image: np.ndarray, factor: int) -> np.ndarray:
    return cv2.resize(image, None, fx=factor, fy=factor, interpolation=cv2.INTER_NEAREST)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("import_dir", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--regulation-count", type=int, default=18)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
