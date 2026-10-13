#!/usr/bin/env python3
"""Frozen death-template score by player species and team hue (read-only).

Two steps:

``sheet``
    Expected death positions come from Review roster lamps (alive -> dead
    brackets, ``review_row_hue_trace.expected_glyphs``), never from the glyph
    detector. For each one this records the **frozen** ``death.png`` track
    score (max over non-cursor static frames, as the detector computes it),
    the team hue of its row and its slot. It writes one contact sheet per
    recording (colour crops per slot, enlarged) and a labels file with
    ``species: null`` per slot. Species is fixed per player for a match, so
    one operator label per slot covers all of that slot's deaths. Labels are
    never inferred.

    Secondary, reported only: scores of the candidate templates in
    ``calibration/templates/review/variant_candidates/death_*.png``,
    computed with the same grayscale matching on the same window. They do
    not classify species and do not compete with the frozen template.

``analyze``
    Joins the operator labels and answers, from the frozen score only:
    squid vs octopus on non-blue rows; score vs team hue within each species;
    and how expected deaths below ``death_match_floor`` split by species and
    hue class.

Run with ``PYTHONPATH=tools/diagnostics``.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

import cv2
import numpy as np
import yaml
from review_extraction_misses import _frame_responses
from review_row_hue_trace import MATCH_PX, _death_score, expected_glyphs

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.review.event_lane import select_static_samples
from splatoon3_ai_coach.review.lane_glyphs import cursor_band, lane_origin
from splatoon3_ai_coach.review.models import ReviewTimelineDataset

CANDIDATE_DIR = Path("calibration/templates/review/variant_candidates")
BLUE_VIOLET = (115.0, 140.0)
CROPS_PER_SLOT = 4
CROP_HALF = 22
ZOOM = 3


def main() -> int:
    """Dispatch ``sheet`` or ``analyze``."""
    args = _parse_args()
    config = load_config(default_config_path()).review
    args.out.mkdir(parents=True, exist_ok=True)
    if args.command == "sheet":
        return _sheet(args.imports, config, args.out)
    return _analyze(args.out, config)


def _sheet(imports: list[Path], config: ReviewTimelineConfig, out: Path) -> int:
    candidates = _candidate_templates()
    glyphs: list[dict[str, Any]] = []
    labels: dict[str, dict[str, Any]] = {}
    for import_dir in imports:
        items, frame = _trace(import_dir, config, candidates)
        glyphs += items
        labels[import_dir.name] = {
            slot: {"species": None}
            for slot in sorted({g["slot"] for g in items}, key=_slot_order)
        }
        _write_sheet(import_dir.name, items, frame, out / f"sheet_{import_dir.name}.png")
    (out / "death_scores.json").write_text(json.dumps(glyphs, indent=2) + "\n", "utf-8")
    labels_path = out / "species_labels.yaml"
    if not labels_path.exists():
        header = (
            "# Operator labels: species of each slot's player for that match.\n"
            "# Values: squid | octopus | unclear. Leave null if no clear crop.\n"
        )
        labels_path.write_text(header + yaml.safe_dump(labels, sort_keys=False), "utf-8")
    print(f"{len(glyphs)} expected deaths; sheets and labels in {out}")
    return 0


def _trace(
    import_dir: Path, config: ReviewTimelineConfig, candidates: dict[str, np.ndarray]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ds = ReviewTimelineDataset.model_validate_json(
        (import_dir / "review_timeline.json").read_text("utf-8")
    )
    hues = {"top": ds.team_identity.top_color, "bottom": ds.team_identity.bottom_color}
    frames = [
        _frame_responses(s, config)
        for s in select_static_samples(ds.samples, config.static_frame_count)
    ]
    out = []
    for glyph in expected_glyphs(ds):
        clear = [f for f in frames if not _in_cursor(f, glyph, config)]
        if not clear:
            continue
        scores = [_death_score(f["rows"][glyph["row"]], glyph["x"]) for f in clear]
        team = glyph["slot"].split("_")[0]
        out.append(
            glyph
            | {
                "recording": import_dir.name,
                "team_row": team,
                "team_hue": hues[team],
                "frozen_death_score": max(s for s in scores if s is not None),
                "candidate_scores": _candidate_scores(
                    clear[0], glyph, config, candidates
                ),
                "crop_frame": clear[0]["sample"].sample_id,
            }
        )
    return out, {f["sample"].sample_id: f for f in frames}


def _in_cursor(frame: dict[str, Any], glyph: dict[str, Any], config: Any) -> bool:
    row = frame["rows"][glyph["row"]]
    band = cursor_band(frame["sample"].cursor.x, row.lane_x, row.frame_width, config)
    c = glyph["x"] - row.lane_x
    return band is not None and band[0] - MATCH_PX <= c <= band[1] + MATCH_PX


def _window(frame: dict[str, Any], glyph: dict[str, Any], config: Any) -> np.ndarray:
    image = frame["image"]
    x1, y1, x2, y2 = lane_origin(image, config)
    cy = int(y1 + config.event_lane_row_centers[glyph["row"]] * (y2 - y1))
    cx = int(glyph["x"])
    return image[cy - CROP_HALF : cy + CROP_HALF, cx - CROP_HALF : cx + CROP_HALF]


def _candidate_templates() -> dict[str, np.ndarray]:
    return {
        p.stem: cv2.cvtColor(cv2.imread(str(p)), cv2.COLOR_BGR2GRAY)
        for p in sorted(CANDIDATE_DIR.glob("death_*.png"))
    }


def _candidate_scores(
    frame: dict[str, Any], glyph: dict[str, Any], config: Any, templates: dict
) -> dict[str, float]:
    gray = cv2.cvtColor(_window(frame, glyph, config), cv2.COLOR_BGR2GRAY)
    out = {}
    for name, tpl in templates.items():
        if gray.shape[0] < tpl.shape[0] or gray.shape[1] < tpl.shape[1]:
            continue
        res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
        out[name] = round(float(res.max()), 3)
    return out


def _write_sheet(
    name: str, items: list[dict[str, Any]], frames: dict[str, Any], path: Path
) -> None:
    by_slot: dict[str, list] = defaultdict(list)
    for g in items:
        by_slot[g["slot"]].append(g)
    size = 2 * CROP_HALF * ZOOM
    rows = []
    config = load_config(default_config_path()).review
    for slot in sorted(by_slot, key=_slot_order):
        tiles = []
        for g in by_slot[slot][:CROPS_PER_SLOT]:
            crop = _window(frames[g["crop_frame"]], g, config)
            tiles.append(cv2.resize(crop, (size, size), interpolation=cv2.INTER_NEAREST))
        while len(tiles) < CROPS_PER_SLOT:
            tiles.append(np.zeros((size, size, 3), np.uint8))
        label = np.zeros((size, 160, 3), np.uint8)
        cv2.putText(
            label, slot, (8, size // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2
        )
        rows.append(np.hstack([label, *tiles]))
    title = np.zeros((40, rows[0].shape[1], 3), np.uint8)
    cv2.putText(title, name, (8, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    cv2.imwrite(str(path), np.vstack([title, *rows]))


def _slot_order(slot: str) -> tuple[int, int]:
    team, n = slot.split("_")
    return (0 if team == "top" else 1, int(n))


def _analyze(out: Path, config: ReviewTimelineConfig) -> int:
    glyphs = json.loads((out / "death_scores.json").read_text("utf-8"))
    labels = yaml.safe_load((out / "species_labels.yaml").read_text("utf-8"))
    floor = config.death_match_floor
    for g in glyphs:
        g["species"] = (labels.get(g["recording"], {}).get(g["slot"]) or {}).get(
            "species"
        )
        g["hue_class"] = _hue_class(g["team_hue"])
    groups: dict[str, list[float]] = defaultdict(list)
    below: dict[str, int] = defaultdict(int)
    for g in glyphs:
        key = f"{g['species']}/{g['hue_class']}"
        groups[key].append(g["frozen_death_score"])
        below[key] += g["frozen_death_score"] < floor
    summary = {
        k: {
            "n": len(v),
            "median": round(median(v), 3),
            "q25": round(float(np.percentile(v, 25)), 3),
            "q75": round(float(np.percentile(v, 75)), 3),
            "below_floor": below[k],
        }
        for k, v in sorted(groups.items())
    }
    (out / "species_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    lines = [
        "| species / hue class | n | median | q25 | q75 | below 0.60 |",
        "|---|---|---|---|---|---|",
    ]
    for k, s in summary.items():
        cells = [s["n"], s["median"], s["q25"], s["q75"], s["below_floor"]]
        lines.append(f"| {k} | " + " | ".join(str(c) for c in cells) + " |")
    (out / "species_summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


def _hue_class(hue: float | None) -> str:
    if hue is None:
        return "unknown"
    return "blue_violet" if BLUE_VIOLET[0] <= hue <= BLUE_VIOLET[1] else "other"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["sheet", "analyze"])
    parser.add_argument("imports", type=Path, nargs="*")
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
