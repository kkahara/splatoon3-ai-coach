#!/usr/bin/env python3
"""Roster-lamp transition timing against death glyphs (interval-censored).

Each Review frame shows the roster at its cursor position, so a lamp change is
only known to lie between the last frame showing one state and the first
showing the other. For every death marker this finds the same-slot
alive->dead bracket ``(x_a, x_b]`` around it and reports the signed offset
``lamp transition - glyph`` as an interval in match-forward seconds:
``[(x_a - g), (x_b - g)] * seconds_per_px`` (positive = lamp later).

Reported per recording, team row and slot: bracket widths, how many intervals
contain 0, the offset values consistent with the most intervals, interval
midpoint statistics, and outliers. Dead->alive brackets after each death give
interval-censored death-state durations. Read-only.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any

from splatoon3_ai_coach.review.lane_glyphs import ROW_SLOTS
from splatoon3_ai_coach.review.models import ReviewTimelineDataset

SCREEN_WIDTH = 1920
MAX_BRACKET_PX = 20
SEARCH_PX = 40
SWEEP = [round(-3 + 0.05 * i, 2) for i in range(121)]


def main() -> int:
    """Measure every recording and write ``roster_timing.md`` / ``.json``."""
    args = _parse_args()
    rows: list[dict[str, Any]] = []
    for import_dir in args.imports:
        ds = ReviewTimelineDataset.model_validate_json(
            (import_dir / "review_timeline.json").read_text("utf-8")
        )
        rows += measure(ds, ds.source_recording_id or import_dir.name)
    groups = _groups(rows)
    summary = {name: summarize(items) for name, items in groups.items()}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "roster_timing.json").write_text(
        json.dumps({"summary": summary, "deaths": rows}, indent=2), "utf-8"
    )
    (args.out / "roster_timing.md").write_text(render(summary, rows), "utf-8")
    print(json.dumps(summary["all"], indent=2))
    return 0


def lamp_states(ds: ReviewTimelineDataset) -> dict[str, list[tuple[float, str]]]:
    """Per slot: (cursor x, state) with frames at one x merged; mixed -> unknown."""
    by_x: dict[str, dict[float, set[str]]] = defaultdict(lambda: defaultdict(set))
    for s in ds.samples:
        if s.cursor.x is None or s.clock_status == "implausible":
            continue
        for o in s.slots:
            by_x[o.slot_id][float(s.cursor.x)].add(o.state.value)
    out: dict[str, list[tuple[float, str]]] = {}
    for slot, xs in by_x.items():
        seq = []
        for x in sorted(xs):
            known = xs[x] - {"unknown"}
            seq.append((x, known.pop() if len(known) == 1 else "unknown"))
        out[slot] = seq
    return out


def brackets(
    seq: list[tuple[float, str]], before: str, after: str
) -> list[tuple[float, float]]:
    """``(x_a, x_b)`` where state goes ``before`` -> ``after`` between known frames."""
    known = [(x, s) for x, s in seq if s != "unknown"]
    return [
        (a[0], b[0])
        for a, b in zip(known, known[1:], strict=False)
        if a[1] == before and b[1] == after and b[0] - a[0] <= MAX_BRACKET_PX
    ]


def measure(ds: ReviewTimelineDataset, rid: str) -> list[dict[str, Any]]:
    """One row per death marker with its lamp brackets."""
    axis = ds.axis_calibration
    if axis is None:
        return []
    spp = abs(axis.slope) / SCREEN_WIDTH
    lamps = lamp_states(ds)
    rows = []
    for m in ds.match_history_events:
        if m.kind != "death" or m.player_slot_id not in ROW_SLOTS:
            continue
        g = m.x_norm * SCREEN_WIDTH
        seq = lamps.get(m.player_slot_id, [])
        down = [
            b
            for b in brackets(seq, "alive", "dead")
            if abs((b[0] + b[1]) / 2 - g) <= SEARCH_PX
        ]
        down = min(down, key=lambda b: abs((b[0] + b[1]) / 2 - g), default=None)
        row: dict[str, Any] = {
            "recording": rid,
            "slot": m.player_slot_id,
            "team": m.player_slot_id.split("_")[0],
            "marker_id": m.marker_id,
            "glyph_x": round(g, 1),
            "elapsed": round(m.elapsed_estimate, 2),
            "seconds_per_px": round(spp, 4),
        }
        if down is not None:
            row["down_px"] = list(down)
            row["offset_s"] = [
                round((down[0] - g) * spp, 3),
                round((down[1] - g) * spp, 3),
            ]
            row["width_s"] = round((down[1] - down[0]) * spp, 3)
            row.update(_recovery(seq, down, spp))
        rows.append(row)
    return rows


def _recovery(
    seq: list[tuple[float, str]], down: tuple[float, float], spp: float
) -> dict:
    """Interval-censored dead duration from the death bracket to the first recovery."""
    up = [b for b in brackets(seq, "dead", "alive") if b[0] >= down[1]]
    if not up:
        return {}
    first = min(up)
    if any(s == "alive" for x, s in seq if down[1] <= x <= first[0]):
        return {}
    return {
        "dead_seconds": [
            round((first[0] - down[1]) * spp, 2),
            round((first[1] - down[0]) * spp, 2),
        ]
    }


def _groups(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        for key in (
            "all",
            f"rec:{r['recording']}",
            f"team:{r['team']}",
            f"slot:{r['slot']}",
        ):
            groups[key].append(r)
    return dict(groups)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Interval statistics of the lamp-minus-glyph offset."""
    iv = [r["offset_s"] for r in rows if "offset_s" in r]
    if not iv:
        return {"markers": len(rows), "bracketed": 0}
    counts = [sum(lo <= d <= hi for lo, hi in iv) for d in SWEEP]
    best = max(counts)
    consistent = [d for d, c in zip(SWEEP, counts, strict=True) if c == best]
    mids = [(lo + hi) / 2 for lo, hi in iv]
    dead = [r["dead_seconds"] for r in rows if "dead_seconds" in r]
    return {
        "markers": len(rows),
        "bracketed": len(iv),
        "contain_zero": sum(lo <= 0 <= hi for lo, hi in iv),
        "max_consistent": best,
        "consistent_offset_s": [consistent[0], consistent[-1]],
        "midpoint_mean_s": round(mean(mids), 3),
        "midpoint_median_s": round(median(mids), 3),
        "bracket_width_median_s": round(median(hi - lo for lo, hi in iv), 2),
        "outside_by_s": sorted(
            round(lo if lo > 0 else hi, 2) for lo, hi in iv if not lo <= 0 <= hi
        ),
        "dead_seconds_median_bounds": [
            round(median(d[0] for d in dead), 2),
            round(median(d[1] for d in dead), 2),
        ]
        if dead
        else None,
    }


def render(summary: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """Markdown summary plus outliers."""
    lines = [
        "# Roster-lamp alive->dead timing vs death glyphs",
        "",
        "Offset = lamp transition - glyph center, match-forward seconds"
        " (+ = lamp later).",
        "",
        "| Group | Markers | Bracketed | Contain 0 | Max consistent "
        "| Consistent offsets (s) | Midpoint mean / median (s) | Bracket width (s) "
        "| Dead duration median bounds (s) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, s in summary.items():
        if not s.get("bracketed"):
            lines.append(f"| {name} | {s['markers']} | 0 | | | | | | |")
            continue
        lines.append(
            f"| {name} | {s['markers']} | {s['bracketed']} | {s['contain_zero']} "
            f"| {s['max_consistent']} | {s['consistent_offset_s']} "
            f"| {s['midpoint_mean_s']} / {s['midpoint_median_s']} "
            f"| {s['bracket_width_median_s']} | {s['dead_seconds_median_bounds']} |"
        )
    lines += ["", "## Deaths whose bracket excludes 0 or with no bracket", ""]
    for r in rows:
        iv = r.get("offset_s")
        if iv is None or not iv[0] <= 0 <= iv[1]:
            lines.append(
                f"- {r['recording']} {r['slot']} {r['marker_id']} x {r['glyph_x']} "
                f"(clock {r['elapsed']}): offset {iv if iv else 'no bracket'}"
            )
    return "\n".join(lines) + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("imports", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
