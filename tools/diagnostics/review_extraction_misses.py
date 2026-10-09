#!/usr/bin/env python3
"""Stage-by-stage trace of reviewed death glyphs through the frozen lane detector.

For every ``confirmed_death`` glyph in reviewed extraction labels (not ``edge``,
inside the audited span) this replays the production static frames
(``select_static_samples``) through the event-lane stages and reports where
the glyph stops:

* ``cursor``: the cursor exclusion band covers the glyph in that frame;
* ``below_floor``: no row response near the glyph reaches the discovery floor;
* ``nms``: a stronger peak within the NMS radius owns the position;
* ``low_std``: the window fails ``event_glyph_min_std``;
* ``echo``: removed by neighbour-row echo suppression (suppressor reported);
* ``scored``: best/runner-up template, margin and the accepted label.

It then lists the production outputs on the glyph's row near its x (death or
unknown markers, special events). Read-only: imports detector internals and
never modifies config, templates or artifacts.
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
from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.review.event_lane import select_static_samples
from splatoon3_ai_coach.review.lane_glyphs import (
    ROW_SLOTS,
    _candidate,
    _peaks,
    _window_std,
    accept_lane_match,
    compute_row_responses,
    cursor_band,
    detect_lane_glyph_candidates,
    glyph_image,
    lane_origin,
    load_lane_templates,
    score_lane_glyph,
)
from splatoon3_ai_coach.review.models import ReviewTimelineDataset

SCREEN_WIDTH = 1920


def main() -> int:
    """Trace every reviewed confirmed glyph and write ``misses.md`` / ``.json``."""
    args = _parse_args()
    labels = yaml.safe_load(args.labels.read_text("utf-8"))
    if labels.get("review_status") != "reviewed":
        raise SystemExit(f"{args.labels}: review_status is not 'reviewed'")
    config = load_config(default_config_path()).review
    traces: list[dict[str, Any]] = []
    cache: dict[str, tuple[ReviewTimelineDataset, list[Any]]] = {}
    for window in labels["windows"]:
        path = window["source_artifact"]
        if path not in cache:
            dataset = ReviewTimelineDataset.model_validate_json(
                Path(path).read_text("utf-8")
            )
            frames = [
                _frame_responses(s, config)
                for s in select_static_samples(dataset.samples, config.static_frame_count)
            ]
            cache[path] = (dataset, frames)
        dataset, frames = cache[path]
        lo = window["x_range"][0] + args.edge_px
        hi = window["x_range"][1] - args.edge_px
        for glyph in window["glyphs"]:
            if glyph["status"] != "confirmed_death" or glyph.get("edge"):
                continue
            if lo <= glyph["x"] <= hi:
                traces.append(
                    _trace(window, glyph, dataset, frames, config, args.match_px)
                )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "misses.json").write_text(json.dumps(traces, indent=2), encoding="utf-8")
    (args.out / "misses.md").write_text(render_markdown(traces), encoding="utf-8")
    print(
        f"{len(traces)} glyphs, {sum(not t['hit'] for t in traces)} missed -> {args.out}"
    )
    return 0


def _frame_responses(sample: Any, config: ReviewTimelineConfig) -> dict[str, Any]:
    """Row responses and pre/post echo candidates of one production frame."""
    image = cv2.imread(sample.image_path)
    templates = load_lane_templates(
        config.special_template_dir,
        image.shape[0],
        config.event_glyph_representation,
        tuple(config.special_template_scales),
    )
    rows = compute_row_responses(image, config, templates, sample.cursor.x)
    raw = _raw_candidates(image, sample.sample_id, config, rows)
    return {
        "sample": sample,
        "image": image,
        "rows": rows,
        "raw": raw,
        "kept": detect_lane_glyph_candidates(image, sample.sample_id, config, rows),
    }


def _raw_candidates(
    image: np.ndarray, sample_id: str, config: ReviewTimelineConfig, rows: list[Any]
) -> list[Any]:
    """``detect_lane_glyph_candidates`` without the final echo suppression."""
    x1, y1, x2, y2 = lane_origin(image, config)
    lane = image[y1:y2, x1:x2]
    gray = glyph_image(lane, "grayscale")
    out = []
    for row in rows:
        tracks = list(row.tracks.values())
        combined, owner = _combined(row)
        radius = _radius(tracks)
        for center in _peaks(combined, config.special_discovery_floor, radius):
            track = tracks[int(owner[center])]
            if _window_std(gray, track, center) >= config.event_glyph_min_std:
                out.append(
                    _candidate(
                        sample_id, row, lane, track, center, float(combined[center])
                    )
                )
    return out


def _combined(row: Any) -> tuple[np.ndarray, np.ndarray]:
    stacked = np.stack([t.score for t in row.tracks.values()])
    return stacked.max(axis=0), stacked.argmax(axis=0)


def _radius(tracks: list[Any]) -> int:
    widths = [int(t.width[t.width > 0].min()) for t in tracks if (t.width > 0).any()]
    return max(3, int(0.6 * min(widths))) if widths else 3


def _trace(
    window: dict[str, Any],
    glyph: dict[str, Any],
    dataset: ReviewTimelineDataset,
    frames: list[dict[str, Any]],
    config: ReviewTimelineConfig,
    tol: float,
) -> dict[str, Any]:
    outputs = _production_outputs(dataset, glyph, tol)
    return {
        "window_id": window["window_id"],
        "recording": window["recording"],
        "row": glyph["row"],
        "x": glyph["x"],
        "obscured": bool(glyph.get("obscured")),
        "notes": glyph.get("notes", ""),
        "hit": any(o["kind"] == "death" for o in outputs),
        "outputs": outputs,
        "frames": [_frame_stage(f, glyph, config, tol) for f in frames],
    }


def _frame_stage(
    frame: dict[str, Any], glyph: dict[str, Any], config: ReviewTimelineConfig, tol: float
) -> dict[str, Any]:
    """Where the glyph stops in one frame."""
    sample, rows = frame["sample"], frame["rows"]
    row = rows[glyph["row"]]
    c = int(round(glyph["x"] - row.lane_x))
    out: dict[str, Any] = {"sample_id": sample.sample_id, "cursor_x": sample.cursor.x}
    band = cursor_band(sample.cursor.x, row.lane_x, row.frame_width, config)
    if band is not None and band[0] - tol <= c <= band[1] + tol:
        return out | {"stage": "cursor"}
    combined, owner = _combined(row)
    ids = list(row.tracks)
    lo, hi = max(0, c - int(tol)), min(len(combined), c + int(tol) + 1)
    local = int(lo + combined[lo:hi].argmax()) if hi > lo else c
    death = row.tracks.get("death")
    out |= {
        "local_best": ids[int(owner[local])],
        "local_score": round(float(combined[local]), 3),
        "death_score": round(float(death.score[lo:hi].max()), 3)
        if death is not None
        else None,
    }
    if combined[local] < config.special_discovery_floor:
        return out | {"stage": "below_floor"}
    near = [
        k
        for k in frame["raw"]
        if k.row_id == glyph["row"] and abs(k.center_x - glyph["x"]) <= tol
    ]
    if not near:
        peaks = _peaks(
            combined, config.special_discovery_floor, _radius(list(row.tracks.values()))
        )
        dominant = [
            p for p in peaks if abs(p - local) < _radius(list(row.tracks.values()))
        ]
        if dominant:
            p = dominant[0]
            return out | {
                "stage": "nms",
                "by": ids[int(owner[p])],
                "by_x": p + row.lane_x,
                "by_score": round(float(combined[p]), 3),
            }
        return out | {"stage": "low_std"}
    candidate = min(near, key=lambda k: abs(k.center_x - glyph["x"]))
    if not any(k is candidate for k in frame["kept"]):
        return out | {"stage": "echo", **_suppressor(candidate, frame["raw"])}
    score = score_lane_glyph(candidate, row)
    return out | {
        "stage": "scored",
        "best": score.best_id,
        "score": round(score.score, 3),
        "runner_up": score.runner_up_id,
        "margin": round(score.margin, 3),
        "label": accept_lane_match(score, config),
    }


def _suppressor(candidate: Any, raw: list[Any]) -> dict[str, Any]:
    strongest = max(
        (
            o
            for o in raw
            if o is not candidate
            and abs(o.row_id - candidate.row_id) <= 1
            and abs(o.center_x - candidate.center_x) <= o.bbox[2]
        ),
        key=lambda o: o.discovery_score,
        default=None,
    )
    info = {"discovery_score": round(candidate.discovery_score, 3)}
    if strongest is not None:
        info |= {
            "by_row": strongest.row_id,
            "by_x": round(strongest.center_x, 1),
            "by_score": round(strongest.discovery_score, 3),
        }
    return info


def _production_outputs(
    dataset: ReviewTimelineDataset, glyph: dict[str, Any], tol: float
) -> list[dict[str, Any]]:
    """Persisted markers and special events on the glyph's row near its x."""
    out = []
    for m in dataset.match_history_events:
        x = m.x_norm * SCREEN_WIDTH
        if (
            m.player_slot_id
            and _row_of(m.player_slot_id) == glyph["row"]
            and abs(x - glyph["x"]) <= tol
        ):
            out.append({"kind": m.kind, "id": m.marker_id, "x": round(x, 1)})
    for e in dataset.special_events:
        if e.row_id == glyph["row"] and abs(e.x_norm * SCREEN_WIDTH - glyph["x"]) <= tol:
            out.append(
                {
                    "kind": "special",
                    "id": e.special_id,
                    "template": e.template_id,
                    "score": round(e.score, 3),
                    "x": round(e.x_norm * SCREEN_WIDTH, 1),
                }
            )
    return out


def _row_of(slot: str) -> int:
    return ROW_SLOTS.index(slot) if slot in ROW_SLOTS else -1


def render_markdown(traces: list[dict[str, Any]]) -> str:
    """Per-glyph stage table, misses first."""
    lines = ["# Reviewed death glyphs through the frozen lane detector", ""]
    for t in sorted(traces, key=lambda t: (t["hit"], t["window_id"], t["row"])):
        tag = "HIT" if t["hit"] else "MISS"
        lines.append(
            f"## {tag} {t['window_id']} {t['recording']} row {t['row']} x {t['x']}"
        )
        if t["notes"]:
            lines.append(f"_{t['notes']}_")
        lines.append(f"- production outputs: `{t['outputs'] or 'none'}`")
        for f in t["frames"]:
            detail = {k: v for k, v in f.items() if k not in ("sample_id", "cursor_x")}
            lines.append(f"- {f['sample_id']} (cursor {f['cursor_x']}): `{detail}`")
        lines.append("")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("labels", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--match-px", type=float, default=8.0)
    parser.add_argument("--edge-px", type=float, default=6.0)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
