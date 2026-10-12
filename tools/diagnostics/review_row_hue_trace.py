#!/usr/bin/env python3
"""Where team-row death glyphs disappear in the frozen lane detector, by team hue.

Read-only diagnosis of the bottom-row death-glyph misses (blue / violet bottom
teams). Expected glyph positions come from Review roster lamps, independent of
the glyph detector: each slot's alive -> dead lamp bracket ``(x_a, x_b]`` (at
most ``MAX_BRACKET_PX`` wide) marks one death; its midpoint is the expected x.

For every expected glyph and every production static frame this replays the
frozen stages (``review_extraction_misses._frame_stage``: cursor, below_floor,
nms / low_std, echo, scored) and records raw evidence at the expected window:

- production death-template score (grayscale, as configured);
- grayscale window std (the ``event_glyph_min_std`` statistic);
- glyph pixel colour (saturated pixels): hue, value, grayscale level, and the
  grayscale contrast against the lane background;
- counterfactual, diagnostic only: the death score under the existing
  ``hsv_value`` representation. Not a proposed setting.

Nothing in config, templates, detector code or artifacts is modified. Run with
``PYTHONPATH=tools/diagnostics``.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any

import cv2
import numpy as np
from review_extraction_misses import _combined, _frame_responses, _radius

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.review.event_lane import select_static_samples
from splatoon3_ai_coach.review.lane_glyphs import (
    ROW_SLOTS,
    _peaks,
    _window_std,
    accept_lane_match,
    compute_row_responses,
    cursor_band,
    glyph_image,
    lane_origin,
    load_lane_templates,
    score_lane_glyph,
)
from splatoon3_ai_coach.review.models import ReviewTimelineDataset
from splatoon3_ai_coach.review.slot_state import lamp_states

SCREEN_WIDTH = 1920
MAX_BRACKET_PX = 20
MATCH_PX = 10.0
COUNTERFACTUAL = "hsv_value"
STAGE_RANK = {
    "cursor": 0,
    "below_floor": 1,
    "low_std": 2,
    "nms": 2,
    "echo": 3,
    "scored_unknown": 4,
    "scored_death": 5,
}


def main() -> int:
    """Trace every recording and write ``row_hue_trace.{json,md}``."""
    args = _parse_args()
    config = load_config(default_config_path()).review
    glyphs: list[dict[str, Any]] = []
    for import_dir in args.imports:
        glyphs += trace_recording(import_dir, config)
    summary = summarize(glyphs)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "row_hue_trace.json").write_text(
        json.dumps({"summary": summary, "glyphs": glyphs}, indent=2) + "\n", "utf-8"
    )
    (args.out / "row_hue_trace.md").write_text(render(summary), "utf-8")
    write_crops(args.imports, config, args.out / "bottom_row_crops.png")
    print(json.dumps(summary["groups"], indent=2))
    return 0


def expected_glyphs(ds: ReviewTimelineDataset) -> list[dict[str, Any]]:
    """One expected death glyph per narrow alive -> dead lamp bracket."""
    out = []
    for slot, seq in lamp_states(ds).items():
        if slot not in ROW_SLOTS:
            continue
        known = [(x, s) for x, s in seq if s != "unknown"]
        for (xa, sa), (xb, sb) in zip(known, known[1:], strict=False):
            if sa == "alive" and sb == "dead" and xb - xa <= MAX_BRACKET_PX:
                out.append(
                    {"slot": slot, "row": ROW_SLOTS.index(slot), "x": (xa + xb) / 2}
                )
    return out


def trace_recording(import_dir: Path, config: ReviewTimelineConfig) -> list[dict]:
    """Stage trace and pixel evidence for every expected glyph of one import."""
    ds = ReviewTimelineDataset.model_validate_json(
        (import_dir / "review_timeline.json").read_text("utf-8")
    )
    hues = {
        "top": ds.team_identity.top_color,
        "bottom": ds.team_identity.bottom_color,
    }
    frames = [
        _frame_responses(s, config)
        for s in select_static_samples(ds.samples, config.static_frame_count)
    ]
    for f in frames:
        x1, y1, x2, y2 = lane_origin(f["image"], config)
        f["gray"] = glyph_image(f["image"][y1:y2, x1:x2], "grayscale")
    counter = [_counterfactual_rows(f, config) for f in frames]
    out = []
    for glyph in expected_glyphs(ds):
        team = glyph["slot"].split("_")[0]
        per_frame = [
            _frame_evidence(f, cf, glyph, config)
            for f, cf in zip(frames, counter, strict=True)
        ]
        out.append(
            glyph
            | {
                "recording": import_dir.name,
                "team_row": team,
                "team_hue": hues[team],
                "production_hit": _production_hit(ds, glyph),
                "furthest_stage": max(
                    (p["stage"] for p in per_frame), key=lambda s: STAGE_RANK[s]
                ),
                "frames": per_frame,
            }
        )
    return out


def _counterfactual_rows(frame: dict[str, Any], config: ReviewTimelineConfig) -> list:
    image = frame["image"]
    templates = load_lane_templates(
        config.special_template_dir,
        image.shape[0],
        COUNTERFACTUAL,
        tuple(config.special_template_scales),
    )
    return compute_row_responses(
        image, config, templates, frame["sample"].cursor.x, representation=COUNTERFACTUAL
    )


def _frame_evidence(
    frame: dict[str, Any],
    cf_rows: list,
    glyph: dict[str, Any],
    config: ReviewTimelineConfig,
) -> dict[str, Any]:
    out = _stage(frame, glyph, config)
    if out["stage"] != "cursor":
        out |= _pixels(frame["image"], glyph, config)
        out["cf_death_score"] = _death_score(cf_rows[glyph["row"]], glyph["x"])
    return out


def _stage(
    frame: dict[str, Any], glyph: dict[str, Any], config: ReviewTimelineConfig
) -> dict[str, Any]:
    """Production stages in order; the first one that drops the glyph."""
    sample, row = frame["sample"], frame["rows"][glyph["row"]]
    c = int(round(glyph["x"] - row.lane_x))
    band = cursor_band(sample.cursor.x, row.lane_x, row.frame_width, config)
    if band is not None and band[0] - MATCH_PX <= c <= band[1] + MATCH_PX:
        return {"stage": "cursor"}
    tracks = list(row.tracks.values())
    combined, owner = _combined(row)
    lo, hi = max(0, c - int(MATCH_PX)), min(len(combined), c + int(MATCH_PX) + 1)
    out: dict[str, Any] = {
        "death_score": _death_score(row, glyph["x"]),
        "combined_score": round(float(combined[lo:hi].max()), 3),
    }
    if out["combined_score"] < config.special_discovery_floor:
        return out | {"stage": "below_floor"}
    radius = _radius(tracks)
    peaks = [
        p
        for p in _peaks(combined, config.special_discovery_floor, radius)
        if lo <= p < hi
    ]
    if not peaks:
        return out | {"stage": "nms"}
    peak = max(peaks, key=lambda p: combined[p])
    track = tracks[int(owner[peak])]
    out["peak_track"] = list(row.tracks)[int(owner[peak])]
    out["window_std"] = round(_window_std(frame["gray"], track, peak), 4)
    if out["window_std"] < config.event_glyph_min_std:
        return out | {"stage": "low_std"}
    kept = [
        k for k in frame["kept"] if k.row_id == glyph["row"] and k.lane_center == peak
    ]
    if not kept:
        return out | {"stage": "echo"}
    score = score_lane_glyph(kept[0], row)
    label = accept_lane_match(score, config)
    return out | {
        "stage": "scored_death" if label == "death" else "scored_unknown",
        "best": score.best_id,
        "score": round(score.score, 3),
        "margin": round(score.margin, 3),
    }


def _death_score(row: Any, x: float) -> float | None:
    death = row.tracks.get("death")
    if death is None:
        return None
    c = int(round(x - row.lane_x))
    lo, hi = max(0, c - int(MATCH_PX)), c + int(MATCH_PX) + 1
    return round(float(death.score[lo:hi].max()), 3)


def _pixels(
    image: np.ndarray, glyph: dict[str, Any], config: ReviewTimelineConfig
) -> dict[str, Any]:
    """Colour and grayscale contrast in an icon-sized window at the expected x."""
    x1, y1, x2, y2 = lane_origin(image, config)
    lane_h = y2 - y1
    cy = y1 + config.event_lane_row_centers[glyph["row"]] * lane_h
    half = int(round(0.05 * lane_h))
    xs = slice(int(glyph["x"] - half), int(glyph["x"] + half))
    window = image[int(cy - half) : int(cy + half), xs]
    if window.size == 0:
        return {}
    hsv = cv2.cvtColor(window, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(window, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    sat = hsv[..., 1] > 100
    bg = hsv[..., 1] < 50
    out: dict[str, Any] = {"gray_std": round(float(gray.std()), 4)}
    if sat.sum() >= 10:
        out |= {
            "glyph_hue": float(np.median(hsv[..., 0][sat])),
            "glyph_value": round(float(np.median(hsv[..., 2][sat])) / 255.0, 3),
            "glyph_gray": round(float(np.median(gray[sat])), 3),
        }
        if bg.sum() >= 10:
            out["gray_contrast"] = round(
                out["glyph_gray"] - float(np.median(gray[bg])), 3
            )
    return out


def _production_hit(ds: ReviewTimelineDataset, glyph: dict[str, Any]) -> bool:
    return any(
        m.kind == "death"
        and m.player_slot_id == glyph["slot"]
        and abs(m.x_norm * SCREEN_WIDTH - glyph["x"]) <= MATCH_PX
        for m in ds.match_history_events
    )


def write_crops(imports: list[Path], config: ReviewTimelineConfig, path: Path) -> None:
    """Per recording: bottom-row lane strip in colour above its grayscale."""
    strips = []
    for import_dir in imports:
        ds = ReviewTimelineDataset.model_validate_json(
            (import_dir / "review_timeline.json").read_text("utf-8")
        )
        sample = select_static_samples(ds.samples, config.static_frame_count)[0]
        image = cv2.imread(sample.image_path)
        x1, y1, x2, y2 = lane_origin(image, config)
        lane_h = y2 - y1
        top = y1 + int((config.event_lane_row_centers[4] - 0.05) * lane_h)
        strip = image[top:y2, x1 : x1 + 600]
        gray = cv2.cvtColor(cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        label = np.zeros((24, strip.shape[1], 3), np.uint8)
        text = f"{import_dir.name} bottom hue {ds.team_identity.bottom_color}"
        cv2.putText(label, text, (4, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,) * 3, 1)
        strips += [label, strip, gray]
    cv2.imwrite(str(path), np.vstack(strips))


def _group(g: dict[str, Any]) -> str:
    hue = g["team_hue"]
    band = "hue_120_136" if hue is not None and 120 <= hue <= 136 else "other_hue"
    return f"{g['team_row']}/{band}"


def summarize(glyphs: list[dict[str, Any]]) -> dict[str, Any]:
    """Per group and per recording/row: hits, furthest stages, raw evidence."""
    groups: dict[str, list] = defaultdict(list)
    per_rec: dict[str, list] = defaultdict(list)
    for g in glyphs:
        groups[_group(g)].append(g)
        per_rec[f"{g['recording']} {g['team_row']} hue {g['team_hue']}"].append(g)
    return {
        "groups": {k: _stats(v) for k, v in sorted(groups.items())},
        "recordings": {k: _stats(v) for k, v in sorted(per_rec.items())},
    }


def _stats(items: list[dict[str, Any]]) -> dict[str, Any]:
    frames = [f for g in items for f in g["frames"] if f["stage"] != "cursor"]

    def med(key: str) -> float | None:
        vals = [f[key] for f in frames if f.get(key) is not None]
        return round(median(vals), 3) if vals else None

    return {
        "expected": len(items),
        "production_hit": sum(g["production_hit"] for g in items),
        "furthest_stage": dict(Counter(g["furthest_stage"] for g in items)),
        "frame_stage": dict(Counter(f["stage"] for f in frames)),
        "median_death_score": med("death_score"),
        "median_combined_score": med("combined_score"),
        "median_peak_window_std": med("window_std"),
        "peak_track": dict(Counter(f.get("peak_track") for f in frames)),
        "median_cf_hsv_value_death_score": med("cf_death_score"),
        "median_gray_std": med("gray_std"),
        "median_glyph_hue": med("glyph_hue"),
        "median_glyph_gray": med("glyph_gray"),
        "median_gray_contrast": med("gray_contrast"),
    }


def render(summary: dict[str, Any]) -> str:
    """Markdown tables for groups and recordings."""
    cols = [
        "expected",
        "production_hit",
        "median_death_score",
        "median_combined_score",
        "median_peak_window_std",
        "median_cf_hsv_value_death_score",
        "median_glyph_hue",
        "median_glyph_gray",
        "median_gray_contrast",
        "furthest_stage",
    ]
    lines = ["# Team-row death glyphs by hue (frozen detector, read-only)", ""]
    for title, block in (("Groups", "groups"), ("Recordings", "recordings")):
        lines += [f"## {title}", "", "| key | " + " | ".join(cols) + " |"]
        lines.append("|---" * (len(cols) + 1) + "|")
        for key, s in summary[block].items():
            lines.append(f"| {key} | " + " | ".join(str(s[c]) for c in cols) + " |")
        lines.append("")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("imports", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
