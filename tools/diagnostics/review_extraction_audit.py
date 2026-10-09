#!/usr/bin/env python3
"""Audit Review event-lane death markers against reviewed glyph labels.

Reads the labels written over ``review_extraction_sheet.py``'s template and the
``match_history_events`` of each referenced ``review_timeline.json``. Within
each labelled window (minus ``--edge-px`` at both ends; labels flagged ``edge``
are excluded) every death marker is classified against the labels:

* ``hit``: same-row ``confirmed_death`` glyph within ``--match-px``;
* ``duplicate``: a second marker on an already matched glyph;
* ``wrong_row``: nearest confirmed glyph within ``--match-px`` is on another row;
* ``fp_not_death`` / ``fp_ambiguous``: same-row glyph labelled as such;
* ``fp_unmatched``: no labelled glyph nearby.

Recall counts confirmed glyphs with a same-row marker. Slot attribution compares
each confirmed glyph's row slot with lamp transitions in the window (validation
only; lamps never decide labels). Marker-vs-glyph x offset is reported as a
downstream timing figure. Labels must have ``review_status: reviewed`` unless
``--allow-draft`` is given, in which case the report is marked preliminary.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from splatoon3_ai_coach.review.lane_glyphs import ROW_SLOTS
from splatoon3_ai_coach.review.models import ReviewTimelineDataset

SCREEN_WIDTH = 1920


def main() -> int:
    """Run the audit and write ``audit.md`` / ``audit.json``."""
    args = _parse_args()
    labels = yaml.safe_load(args.labels.read_text("utf-8"))
    draft = labels.get("review_status") != "reviewed"
    if draft and not args.allow_draft:
        raise SystemExit(
            f"{args.labels}: review_status is not 'reviewed' (use --allow-draft)"
        )
    datasets: dict[str, ReviewTimelineDataset] = {}
    windows = []
    for window in labels["windows"]:
        if not window.get("complete"):
            continue
        if args.recordings and window["recording"] not in args.recordings:
            continue
        path = Path(window["source_artifact"])
        if args.artifact_root is not None:
            path = args.artifact_root / window["recording"] / "review_timeline.json"
        key = str(path)
        if key not in datasets:
            datasets[key] = ReviewTimelineDataset.model_validate_json(
                path.read_text("utf-8")
            )
        windows.append(audit_window(window, datasets[key], args))
    summary = summarize(windows)
    args.out.mkdir(parents=True, exist_ok=True)
    payload = {
        "preliminary": draft,
        "artifact_root": str(args.artifact_root) if args.artifact_root else None,
        "labels": str(args.labels),
        "summary": summary,
        "windows": windows,
        "settings": {
            "match_px": args.match_px,
            "edge_px": args.edge_px,
            "lamp_px": args.lamp_px,
        },
    }
    (args.out / "audit.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (args.out / "audit.md").write_text(render_markdown(payload), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def audit_window(
    window: dict[str, Any], dataset: ReviewTimelineDataset, args: argparse.Namespace
) -> dict[str, Any]:
    """Classify markers and glyphs of one labelled window."""
    lo, hi = window["x_range"][0] + args.edge_px, window["x_range"][1] - args.edge_px
    glyphs = [
        dict(g, gid=i)
        for i, g in enumerate(window["glyphs"])
        if not g.get("edge") and lo <= g["x"] <= hi
    ]
    markers = [
        {
            "marker_id": m.marker_id,
            "row": ROW_SLOTS.index(m.player_slot_id),
            "slot": m.player_slot_id,
            "x": round(m.x_norm * SCREEN_WIDTH, 1),
        }
        for m in dataset.match_history_events
        if m.kind == "death"
        and m.player_slot_id in ROW_SLOTS
        and lo <= m.x_norm * SCREEN_WIDTH <= hi
    ]
    matched: dict[int, str] = {}
    for marker in sorted(markers, key=lambda m: m["x"]):
        marker.update(_classify(marker, glyphs, matched, args.match_px))
    confirmed = [g for g in glyphs if g["status"] == "confirmed_death"]
    for glyph in confirmed:
        glyph["marker_id"] = matched.get(glyph["gid"])
        glyph["lamp"] = _lamp_agreement(glyph, window["lamp_transitions"], args.lamp_px)
    return {
        "window_id": window["window_id"],
        "recording": window["recording"],
        "category": window["category"],
        "audited_x": [lo, hi],
        "markers": markers,
        "confirmed": confirmed,
        "ambiguous_with_marker": _ambiguous_hits(glyphs, markers),
        "lamps_without_glyph": _lamps_without_glyph(window, lo, hi, args),
    }


def _classify(
    marker: dict[str, Any],
    glyphs: list[dict[str, Any]],
    matched: dict[int, str],
    tol: float,
) -> dict[str, Any]:
    near = [g for g in glyphs if abs(g["x"] - marker["x"]) <= tol]
    same = sorted(
        (g for g in near if g["row"] == marker["row"]),
        key=lambda g: (g["status"] != "confirmed_death", abs(g["x"] - marker["x"])),
    )
    if same and same[0]["status"] == "confirmed_death":
        glyph = same[0]
        if glyph["gid"] in matched:
            return {"outcome": "duplicate", "glyph_x": glyph["x"]}
        matched[glyph["gid"]] = marker["marker_id"]
        return {
            "outcome": "hit",
            "glyph_x": glyph["x"],
            "dx": round(marker["x"] - glyph["x"], 1),
        }
    if same:
        kind = (
            "fp_not_death" if same[0]["status"] == "not_death_glyph" else "fp_ambiguous"
        )
        return {"outcome": kind, "glyph_x": same[0]["x"], "identity": same[0]["identity"]}
    other = [g for g in near if g["status"] == "confirmed_death"]
    if other:
        glyph = min(
            other,
            key=lambda g: (abs(g["row"] - marker["row"]), abs(g["x"] - marker["x"])),
        )
        return {"outcome": "wrong_row", "glyph_row": glyph["row"], "glyph_x": glyph["x"]}
    return {"outcome": "fp_unmatched"}


def _lamp_agreement(
    glyph: dict[str, Any], lamps: list[dict[str, Any]], tol: float
) -> str:
    """``agree`` / ``other_slot`` / ``none`` for lamp changes near the glyph."""
    near = [
        t
        for t in lamps
        if abs((t["last_alive_x"] + t["first_dead_x"]) / 2 - glyph["x"]) <= tol
    ]
    if any(t["slot"] == ROW_SLOTS[glyph["row"]] for t in near):
        return "agree"
    same_team = [
        t
        for t in near
        if t["slot"].split("_")[0] == ROW_SLOTS[glyph["row"]].split("_")[0]
    ]
    return "other_slot" if same_team else "none"


def _ambiguous_hits(
    glyphs: list[dict[str, Any]], markers: list[dict[str, Any]]
) -> list[dict]:
    return [
        {
            "row": g["row"],
            "x": g["x"],
            "marked": any(
                m.get("glyph_x") == g["x"] and m["row"] == g["row"] for m in markers
            ),
        }
        for g in glyphs
        if g["status"] == "ambiguous"
    ]


def _lamps_without_glyph(
    window: dict[str, Any],
    lo: float,
    hi: float,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    # Edge-excluded glyphs still explain a lamp change; they only leave recall.
    confirmed = [g for g in window["glyphs"] if g["status"] == "confirmed_death"]
    out = []
    for t in window["lamp_transitions"]:
        x = (t["last_alive_x"] + t["first_dead_x"]) / 2
        row = ROW_SLOTS.index(t["slot"])
        if lo <= x <= hi and not any(
            g["row"] == row and abs(g["x"] - x) <= args.lamp_px for g in confirmed
        ):
            out.append({"slot": t["slot"], "x": x})
    return out


def summarize(windows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts; recall split by team row."""
    outcomes = Counter(m["outcome"] for w in windows for m in w["markers"])
    confirmed = [g for w in windows for g in w["confirmed"]]
    recall = {}
    for team, rows in (("top", range(4)), ("bottom", range(4, 8)), ("all", range(8))):
        pool = [g for g in confirmed if g["row"] in rows]
        recall[team] = {
            "found": sum(g["marker_id"] is not None for g in pool),
            "labelled": len(pool),
        }
    dxs = [m["dx"] for w in windows for m in w["markers"] if m["outcome"] == "hit"]
    ambiguous = [a for w in windows for a in w["ambiguous_with_marker"]]
    return {
        "windows": len(windows),
        "recall": recall,
        "markers": dict(outcomes),
        "slot_lamp_agreement": dict(Counter(g["lamp"] for g in confirmed)),
        "ambiguous": {
            "labelled": len(ambiguous),
            "with_marker": sum(a["marked"] for a in ambiguous),
        },
        "lamp_changes_without_confirmed_glyph": sum(
            len(w["lamps_without_glyph"]) for w in windows
        ),
        "hit_dx_px": {
            "n": len(dxs),
            "mean": round(sum(dxs) / len(dxs), 2) if dxs else None,
            "max_abs": max((abs(d) for d in dxs), default=None),
        },
    }


def render_markdown(payload: dict[str, Any]) -> str:
    """Human-readable audit."""
    s = payload["summary"]
    head = ["# Event-lane extraction audit", ""]
    if payload["preliminary"]:
        head += [
            "**PRELIMINARY — labels are unreviewed drafts. Do not treat as results.**",
            "",
        ]
    head += [
        f"Labels: `{payload['labels']}`; settings: `{payload['settings']}`",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    for team, r in s["recall"].items():
        head.append(f"| recall ({team}) | {r['found']}/{r['labelled']} |")
    for key in (
        "markers",
        "slot_lamp_agreement",
        "ambiguous",
        "lamp_changes_without_confirmed_glyph",
        "hit_dx_px",
    ):
        head.append(f"| {key} | `{s[key]}` |")
    head += ["", "## Windows", ""]
    for w in payload["windows"]:
        head.append(
            f"### {w['window_id']} {w['recording']} {w['category']} x {w['audited_x']}"
        )
        for g in w["confirmed"]:
            head.append(
                f"- glyph row {g['row']} x {g['x']}: "
                f"marker {g['marker_id']}, lamp {g['lamp']}"
            )
        for m in w["markers"]:
            if m["outcome"] != "hit":
                head.append(
                    f"- marker {m['marker_id']} row {m['row']} x {m['x']}: {m['outcome']}"
                )
        for t in w["lamps_without_glyph"]:
            head.append(f"- lamp {t['slot']} x {t['x']}: no confirmed glyph")
        head.append("")
    return "\n".join(head)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("labels", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--match-px", type=float, default=8.0)
    parser.add_argument("--edge-px", type=float, default=6.0)
    parser.add_argument("--lamp-px", type=float, default=10.0)
    parser.add_argument("--allow-draft", action="store_true")
    parser.add_argument(
        "--artifact-root",
        type=Path,
        help="read <root>/<recording>/review_timeline.json instead of source_artifact",
    )
    parser.add_argument("--recordings", nargs="*", help="only these recordings")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
