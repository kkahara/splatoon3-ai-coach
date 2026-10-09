#!/usr/bin/env python3
"""Special-use timing against the roster special-ready glow (interval-censored).

A slot's roster icon turns pale while its special is ready and returns to full
team colour when the special is used (or the player dies). The glow is read
per frame as the mean HSV saturation of the icon's bright pixels; each slot
gets its own two-level threshold (1-D two-means over its alive frames) with an
uncertain band between the levels.

For every accepted special event this finds the same-slot glow->plain bracket
``(x_a, x_b]`` around the glyph with the lamp alive on both sides, and reports
``glow end - glyph center`` in match-forward seconds (+ = glow ends later).
Glow lost at a death is excluded. Statistics are shared with
``review_roster_timing.py``. Read-only; nothing here feeds the pipeline.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from review_roster_timing import SCREEN_WIDTH, SEARCH_PX, _groups, render, summarize

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.review.models import ReviewTimelineDataset

MAX_BRACKET_PX = 20
MIN_LEVEL_RATIO = 0.85
UNCERTAIN_FRACTION = 0.2


def main() -> int:
    """Measure every recording and write ``glow_timing.md`` / ``.json``."""
    args = _parse_args()
    config = load_config(default_config_path()).review
    rows: list[dict[str, Any]] = []
    levels: dict[str, Any] = {}
    for import_dir in args.imports:
        ds = ReviewTimelineDataset.model_validate_json(
            (import_dir / "review_timeline.json").read_text("utf-8")
        )
        rid = ds.source_recording_id or import_dir.name
        series = glow_series(ds, config)
        thresholds = {slot: slot_levels(points) for slot, points in series.items()}
        levels[rid] = thresholds
        rows += measure(ds, rid, series, thresholds)
    summary = {name: summarize(items) for name, items in _groups(rows).items()}
    args.out.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "levels": levels, "specials": rows}
    (args.out / "glow_timing.json").write_text(json.dumps(payload, indent=2), "utf-8")
    text = render(summary, rows).replace(
        "Roster-lamp alive->dead timing vs death glyphs",
        "Special-ready glow end vs special glyphs",
    )
    (args.out / "glow_timing.md").write_text(text, "utf-8")
    print(json.dumps(summary["all"], indent=2))
    return 0


def glow_series(
    ds: ReviewTimelineDataset, config: ReviewTimelineConfig
) -> dict[str, list[tuple[float, float, str]]]:
    """Per slot: (cursor x, icon saturation, lamp state), one entry per distinct x."""
    out: dict[str, dict[float, tuple[float, str]]] = defaultdict(dict)
    for sample in ds.samples:
        x = sample.cursor.x
        if x is None or sample.clock_status == "implausible":
            continue
        if all(float(x) in out[o.slot_id] for o in sample.slots):
            continue
        image = cv2.imread(sample.image_path)
        if image is None:
            continue
        for obs in sample.slots:
            if float(x) not in out[obs.slot_id]:
                value = icon_saturation(image, float(x), obs.slot_id, config)
                out[obs.slot_id][float(x)] = (value, obs.state.value)
    return {s: sorted((x, v, st) for x, (v, st) in xs.items()) for s, xs in out.items()}


def icon_saturation(
    image: np.ndarray, cursor_x: float, slot_id: str, config: ReviewTimelineConfig
) -> float:
    """Mean saturation of bright pixels in the slot's roster box (cursor grid)."""
    height, width = image.shape[:2]
    slot_w = width * config.roster_row_width_fraction / 4
    center = float(np.clip(cursor_x, 2 * slot_w, width - 2 * slot_w))
    row, index = slot_id.split("_")
    band = config.roster_top_band if row == "top" else config.roster_bottom_band
    y1, y2 = int(height * band[0]), int(height * band[1])
    left = center - 2 * slot_w + (int(index) - 1) * slot_w
    roi = image[y1:y2, int(round(left)) : int(round(left + slot_w))]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV).astype(np.float32)
    bright = hsv[..., 2] > 90
    return float(hsv[..., 1][bright].mean()) if bright.any() else -1.0


def slot_levels(points: list[tuple[float, float, str]]) -> dict[str, Any] | None:
    """Two-means split of alive-frame saturation; ``None`` without two levels."""
    values = np.array([v for _, v, st in points if st == "alive" and v >= 0])
    if values.size < 10:
        return None
    lo, hi = float(np.percentile(values, 10)), float(np.percentile(values, 90))
    for _ in range(20):
        cut = (lo + hi) / 2
        glow, plain = values[values < cut], values[values >= cut]
        if glow.size == 0 or plain.size == 0:
            return None
        lo, hi = float(glow.mean()), float(plain.mean())
    if lo / hi > MIN_LEVEL_RATIO:
        return None
    margin = UNCERTAIN_FRACTION * (hi - lo)
    return {
        "glow": round(lo, 1),
        "plain": round(hi, 1),
        "cut": [(lo + hi) / 2 - margin, (lo + hi) / 2 + margin],
    }


def glow_state(value: float, lamp: str, levels: dict[str, Any] | None) -> str:
    """``glow`` / ``plain`` / ``dead`` / ``unknown`` for one frame."""
    if lamp == "dead":
        return "dead"
    if lamp != "alive" or levels is None or value < 0:
        return "unknown"
    low, high = levels["cut"]
    return "glow" if value < low else "plain" if value > high else "unknown"


def measure(
    ds: ReviewTimelineDataset,
    rid: str,
    series: dict[str, list[tuple[float, float, str]]],
    thresholds: dict[str, Any],
) -> list[dict[str, Any]]:
    """One row per accepted special event with its glow-end bracket."""
    axis = ds.axis_calibration
    if axis is None:
        return []
    spp = abs(axis.slope) / SCREEN_WIDTH
    rows = []
    for event in ds.special_events:
        if event.special_id == "unknown":
            continue
        g = event.x_norm * SCREEN_WIDTH
        levels = thresholds.get(event.slot_id)
        states = [
            (x, glow_state(v, st, levels)) for x, v, st in series.get(event.slot_id, [])
        ]
        row: dict[str, Any] = {
            "recording": rid,
            "slot": event.slot_id,
            "team": event.team_row,
            "marker_id": f"{event.special_id}@{g:.0f}",
            "special_id": event.special_id,
            "glyph_x": round(g, 1),
            "elapsed": round(event.elapsed_estimate, 2),
            "levels": levels is not None,
        }
        bracket = _glow_end(states, g)
        if bracket is not None:
            row["glow_end_px"] = list(bracket)
            row["offset_s"] = [
                round((bracket[0] - g) * spp, 3),
                round((bracket[1] - g) * spp, 3),
            ]
            row["width_s"] = round((bracket[1] - bracket[0]) * spp, 3)
        rows.append(row)
    return rows


def _glow_end(states: list[tuple[float, str]], g: float) -> tuple[float, float] | None:
    known = [(x, s) for x, s in states if s != "unknown"]
    ends = [
        (a[0], b[0])
        for a, b in zip(known, known[1:], strict=False)
        if a[1] == "glow"
        and b[1] == "plain"
        and b[0] - a[0] <= MAX_BRACKET_PX
        and abs((a[0] + b[0]) / 2 - g) <= SEARCH_PX
    ]
    return min(ends, key=lambda b: abs((b[0] + b[1]) / 2 - g), default=None)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("imports", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
