#!/usr/bin/env python3
"""Roster state of all eight slots at each Review special (synchronisation test).

For every accepted special event the eight roster lamps are queried at the
glyph centre. Each slot's lamp is read from the nearest known frames on both
sides; the slot is ``unknown`` when they disagree or when any lamp-transition
bracket of that slot lies within ``X`` seconds of the special
(``reason = near_roster_transition``), or when no frame lies within
``SEARCH_PX`` on a side (``no_lamp_frames``). Rows are tagged with the
stratification categories used for manual review, and the sweep over ``X``
shows how many slot states each window leaves unknown and how the remaining
lamp states compare with event-lane order (a nearby death glyph before /
after the special glyph). ``--window`` only selects the reported table; the
reliability window is decided from the sweep and the manual sheets.

Writes ``sync.md`` / ``sync.json`` and, with ``--sheets``, roster crops before
and after each sampled special for manual inspection. Read-only validation:
nothing here is coaching evidence.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from review_roster_timing import SCREEN_WIDTH, SEARCH_PX, lamp_states

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.review.lane_glyphs import ROW_SLOTS
from splatoon3_ai_coach.review.models import ReviewTimelineDataset, ReviewTimelineSample

SLOTS = [f"{row}_{i}" for row in ("top", "bottom") for i in range(1, 5)]
WINDOWS_S = [0.0, 0.25, 0.5, 1.0, 1.5, 2.0]
NEAR_DEATH_S = 3.0
STACK_PX = 8.0
PER_CATEGORY = 3
ORDER_TIE_PX = 2.0
OBSCURING = {"killer_wail_5.1", "big_bubbler"}


def main() -> int:
    """Query lamps at every special and write the validation table."""
    args = _parse_args()
    rows: list[dict[str, Any]] = []
    datasets: dict[str, ReviewTimelineDataset] = {}
    for import_dir in args.imports:
        ds = ReviewTimelineDataset.model_validate_json(
            (import_dir / "review_timeline.json").read_text("utf-8")
        )
        rid = ds.source_recording_id or import_dir.name
        datasets[rid] = ds
        rows += recording_rows(ds, rid, args.window)
    sweep = {str(w): _sweep(datasets, w) for w in WINDOWS_S}
    sample = stratified_sample(rows)
    args.out.mkdir(parents=True, exist_ok=True)
    payload = {
        "window_s": args.window,
        "sweep": sweep,
        "sample": sample,
        "specials": rows,
    }
    (args.out / "sync.json").write_text(json.dumps(payload, indent=2), "utf-8")
    (args.out / "sync.md").write_text(render(rows, sweep, sample, args.window), "utf-8")
    if args.sheets:
        write_sheets(datasets, rows, sample, args.out / "sheets")
    print(json.dumps(sweep, indent=2))
    return 0


def recording_rows(
    ds: ReviewTimelineDataset, rid: str, window: float
) -> list[dict[str, Any]]:
    """One validation row per accepted special event."""
    axis = ds.axis_calibration
    if axis is None:
        return []
    spp = abs(axis.slope) / SCREEN_WIDTH
    lamps = lamp_states(ds)
    deaths = [
        (m.player_slot_id, m.x_norm * SCREEN_WIDTH)
        for m in ds.match_history_events
        if m.kind == "death" and m.player_slot_id in ROW_SLOTS
    ]
    rows = []
    for event in ds.special_events:
        if event.special_id == "unknown":
            continue
        g = event.x_norm * SCREEN_WIDTH
        states = {s: slot_state(lamps.get(s, []), g, window / spp) for s in SLOTS}
        row = {
            "recording": rid,
            "special_id": event.special_id,
            "slot": event.slot_id,
            "glyph_x": round(g, 1),
            "elapsed": round(event.elapsed_estimate, 2),
            "slots": states,
            "top_alive": _count(states, "top"),
            "bottom_alive": _count(states, "bottom"),
            "user_alive": states[event.slot_id]["state"],
            "nearest_transition_s": _nearest(states, spp),
            "categories": categories(
                event.special_id, event.elapsed_estimate, g, deaths, spp
            ),
            "order_checks": order_checks(g, deaths, spp),
        }
        rows.append(row)
    return rows


def order_checks(
    g: float, deaths: list[tuple[str, float]], spp: float
) -> list[dict[str, Any]]:
    """Lane-order truth for slots with a death glyph within ``NEAR_DEATH_S``.

    A death glyph before the special means that slot is dead at the special
    (death states last ~7 s); one after means it is still alive. Glyphs within
    ``ORDER_TIE_PX`` of the special are ``ambiguous`` (glyph localisation).
    """
    checks = []
    for slot, x in deaths:
        dt = (x - g) * spp
        if abs(dt) > NEAR_DEATH_S:
            continue
        if abs(x - g) <= ORDER_TIE_PX:
            expected = "ambiguous"
        else:
            expected = "dead" if x < g else "alive"
        checks.append({"slot": slot, "death_dt_s": round(dt, 2), "expected": expected})
    return checks


def slot_state(
    seq: list[tuple[float, str]], g: float, window_px: float
) -> dict[str, Any]:
    """Lamp state at ``g`` with its reason and nearest transition distance (px)."""
    known = [(x, s) for x, s in seq if s != "unknown"]
    before = [p for p in known if p[0] <= g and g - p[0] <= SEARCH_PX]
    after = [p for p in known if p[0] > g and p[0] - g <= SEARCH_PX]
    transitions = [
        (a[0], b[0]) for a, b in zip(known, known[1:], strict=False) if a[1] != b[1]
    ]
    dist = min((_gap(t, g) for t in transitions), default=None)
    out: dict[str, Any] = {"transition_px": None if dist is None else round(dist, 1)}
    if not before or not after:
        return out | {"state": "unknown", "reason": "no_lamp_frames"}
    prev, nxt = before[-1][1], after[0][1]
    if prev != nxt or (dist is not None and dist <= window_px):
        return out | {"state": "unknown", "reason": "near_roster_transition"}
    return out | {"state": prev, "reason": None}


def _gap(bracket: tuple[float, float], g: float) -> float:
    a, b = bracket
    return 0.0 if a < g <= b else min(abs(g - a), abs(g - b))


def _count(states: dict[str, dict[str, Any]], team: str) -> int | None:
    team_states = [v["state"] for k, v in states.items() if k.startswith(team)]
    if "unknown" in team_states:
        return None
    return sum(s == "alive" for s in team_states)


def _nearest(states: dict[str, dict[str, Any]], spp: float) -> float | None:
    dists = [
        v["transition_px"] for v in states.values() if v["transition_px"] is not None
    ]
    return round(min(dists) * spp, 2) if dists else None


def categories(
    special_id: str,
    elapsed: float,
    g: float,
    deaths: list[tuple[str, float]],
    spp: float,
) -> list[str]:
    """Stratification tags for manual review (match-forward death offsets)."""
    offsets = [(x - g) * spp for _, x in deaths]
    near = [d for d in offsets if abs(d) <= NEAR_DEATH_S]
    tags = []
    if not near:
        tags.append("far_from_death")
    if any(0 < d <= NEAR_DEATH_S for d in offsets):
        tags.append("before_death")
    if any(-NEAR_DEATH_S <= d < 0 for d in offsets):
        tags.append("after_death")
    if any(abs(x - g) <= STACK_PX for _, x in deaths):
        tags.append("stacked_with_death")
    if len(near) >= 2:
        tags.append("simultaneous_deaths")
    if special_id in OBSCURING and near:
        tags.append("obscuring_special_near_death")
    if elapsed < 0:
        tags.append("overtime")
    return tags


def _sweep(datasets: dict[str, ReviewTimelineDataset], window: float) -> dict[str, Any]:
    rows = [r for rid, ds in datasets.items() for r in recording_rows(ds, rid, window)]
    states = Counter(v["state"] for r in rows for v in r["slots"].values())
    reasons = Counter(
        v["reason"] for r in rows for v in r["slots"].values() if v["reason"]
    )
    return {
        "specials": len(rows),
        "slot_states": dict(states),
        "unknown_reasons": dict(reasons),
        "fully_known": sum(
            r["top_alive"] is not None and r["bottom_alive"] is not None for r in rows
        ),
        "user_dead": sum(r["user_alive"] == "dead" for r in rows),
        "lane_order": _order_summary(rows),
    }


def _order_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Lamp state at the special vs lane-order truth (non-ambiguous checks)."""
    counts: Counter[str] = Counter()
    wrong = []
    for r in rows:
        for c in r["order_checks"]:
            if c["expected"] == "ambiguous":
                counts["ambiguous"] += 1
                continue
            lamp = r["slots"][c["slot"]]["state"]
            if lamp == "unknown":
                counts["lamp_unknown"] += 1
            elif lamp == c["expected"]:
                counts["agree"] += 1
            else:
                counts["disagree"] += 1
                wrong.append(f"{_key(r)} {c['slot']} death_dt {c['death_dt_s']}")
    return dict(counts) | {"disagreements": wrong}


def stratified_sample(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Up to ``PER_CATEGORY`` specials per tag, spread over recordings."""
    chosen: dict[str, str] = {}
    tags = sorted({t for r in rows for t in r["categories"]})
    for tag in tags:
        pool = [r for r in rows if tag in r["categories"]]
        pool.sort(
            key=lambda r: (
                sum(_key(r) == k for k in chosen),
                r["recording"],
                r["glyph_x"],
            )
        )
        picked = 0
        for r in _round_robin(pool):
            if picked == PER_CATEGORY:
                break
            if _key(r) not in chosen:
                chosen[_key(r)] = tag
                picked += 1
    return [{"key": k, "category": t} for k, t in chosen.items()]


def _round_robin(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_rec: dict[str, list[dict[str, Any]]] = {}
    for r in pool:
        by_rec.setdefault(r["recording"], []).append(r)
    out = []
    while any(by_rec.values()):
        for items in by_rec.values():
            if items:
                out.append(items.pop(len(items) // 2))
    return out


def _key(r: dict[str, Any]) -> str:
    return f"{r['recording']}:{r['slot']}:{r['special_id']}@{r['glyph_x']:.0f}"


def render(
    rows: list[dict[str, Any]],
    sweep: dict[str, Any],
    sample: list[dict[str, Any]],
    window: float,
) -> str:
    """Markdown: window sweep, sample list and the full validation table."""
    lines = [
        "# Review special <-> roster synchronisation",
        "",
        "## Reliability-window sweep",
        "",
        "Lane order: lamp state at the special vs the order of that slot's death",
        "glyph and the special glyph (death within 3 s; ties within 2 px skipped).",
        "",
        "| X (s) | Slot states alive / dead / unknown | Unknown reasons "
        "| Specials with both counts known | User slot dead "
        "| Lane order agree / disagree / lamp unknown |",
        "|---|---|---|---|---|---|",
    ]
    for w, s in sweep.items():
        st, lo = s["slot_states"], s["lane_order"]
        lines.append(
            f"| {w} | {st.get('alive', 0)} / {st.get('dead', 0)} "
            f"/ {st.get('unknown', 0)} | {s['unknown_reasons']} "
            f"| {s['fully_known']}/{s['specials']} | {s['user_dead']} "
            f"| {lo.get('agree', 0)} / {lo.get('disagree', 0)} "
            f"/ {lo.get('lamp_unknown', 0)} |"
        )
    lines += ["", "Lane-order disagreements per X:", ""]
    lines += [f"- X={w}: {s['lane_order']['disagreements']}" for w, s in sweep.items()]
    sampled = {s["key"]: s["category"] for s in sample}
    lines += [
        "",
        f"## Validation table (reporting window X = {window} s, not calibrated)",
        "",
        "Slot cells: A alive, D dead, ? unknown (t = near transition, n = no frames).",
        "",
        "| Special | Clock | Slot | top_1-4 | bottom_1-4 | Top alive | Bottom alive "
        "| Nearest transition (s) | Categories | Sample |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        cells = {k: _cell(v) for k, v in r["slots"].items()}
        top = "".join(cells[f"top_{i}"] for i in range(1, 5))
        bottom = "".join(cells[f"bottom_{i}"] for i in range(1, 5))
        lines.append(
            f"| {r['recording']} {r['special_id']}@{r['glyph_x']:.0f} | {r['elapsed']} "
            f"| {r['slot']} | {top} | {bottom} | {_na(r['top_alive'])} "
            f"| {_na(r['bottom_alive'])} | {_na(r['nearest_transition_s'])} "
            f"| {', '.join(r['categories'])} | {sampled.get(_key(r), '')} |"
        )
    return "\n".join(lines) + "\n"


def _cell(v: dict[str, Any]) -> str:
    if v["state"] == "alive":
        return "A"
    if v["state"] == "dead":
        return "D"
    return "?t" if v["reason"] == "near_roster_transition" else "?n"


def _na(value: object) -> str:
    return "-" if value is None else str(value)


def write_sheets(
    datasets: dict[str, ReviewTimelineDataset],
    rows: list[dict[str, Any]],
    sample: list[dict[str, Any]],
    out: Path,
) -> None:
    """Roster crops at the frames just before and after each sampled special."""
    config = load_config(default_config_path()).review
    out.mkdir(parents=True, exist_ok=True)
    by_key = {_key(r): r for r in rows}
    for number, item in enumerate(sample, start=1):
        r = by_key[item["key"]]
        ds = datasets[r["recording"]]
        tiles = []
        for sample_frame in _bracketing_frames(ds, r["glyph_x"]):
            dx = (sample_frame.cursor.x or 0) - r["glyph_x"]
            tiles.append(_frame_tile(sample_frame, dx, config))
        if not tiles:
            continue
        header = (
            f"S{number:02d} {item['category']} | {r['recording']} {r['slot']} "
            f"{r['special_id']} clock {r['elapsed']} "
            f"| computed top {_na(r['top_alive'])} bottom {_na(r['bottom_alive'])}"
        )
        width = max(t.shape[1] for t in tiles)
        tiles = [
            cv2.copyMakeBorder(t, 0, 6, 0, width - t.shape[1], cv2.BORDER_CONSTANT)
            for t in tiles
        ]
        sheet = np.vstack([_bar(header, width), *tiles])
        cv2.imwrite(str(out / f"S{number:02d}_{r['recording']}_{r['slot']}.png"), sheet)


def _bracketing_frames(ds: ReviewTimelineDataset, g: float) -> list[ReviewTimelineSample]:
    frames: dict[float, ReviewTimelineSample] = {}
    for s in ds.samples:
        if s.cursor.x is not None and s.clock_status != "implausible":
            frames.setdefault(float(s.cursor.x), s)
    before = sorted(x for x in frames if x <= g)[-2:]
    after = sorted(x for x in frames if x > g)[:2]
    return [frames[x] for x in before + after]


def _frame_tile(sample: ReviewTimelineSample, dx: float, config: Any) -> np.ndarray:
    image = cv2.imread(sample.image_path)
    h, w = image.shape[:2]
    x = int(sample.cursor.x or 0)
    crops = []
    for band in (config.roster_top_band, config.roster_bottom_band):
        crop = image[
            int(h * band[0]) : int(h * band[1]), max(0, x - 140) : min(w, x + 140)
        ]
        crops.append(
            cv2.resize(crop, None, fx=1.4, fy=1.4, interpolation=cv2.INTER_NEAREST)
        )
    width = max(c.shape[1] for c in crops)
    crops = [
        cv2.copyMakeBorder(c, 0, 0, 0, width - c.shape[1], cv2.BORDER_CONSTANT)
        for c in crops
    ]
    label = f"dx {dx:+.0f} px  clock {sample.elapsed_seconds}  (top row, then bottom row)"
    return np.vstack([_bar(label, width), crops[0], crops[1]])


def _bar(text: str, width: int) -> np.ndarray:
    bar = np.zeros((24, max(width, 10), 3), np.uint8)
    cv2.putText(bar, text, (4, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    return bar


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("imports", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--window",
        type=float,
        required=True,
        help="Reporting window X (s) for the table and sheets; not a calibrated value",
    )
    parser.add_argument("--sheets", action="store_true", help="Write manual-review crops")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
