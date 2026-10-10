#!/usr/bin/env python3
"""Roster-transition boundary cases at Review specials: sheets, labels, scoring.

The reliability window X is an output of reviewed visual truth, not a tuned
value. ``sheet`` builds the boundary corpus and review material; ``score``
turns reviewed labels into per-distance outcomes and the empirical window;
``gate`` runs the pre-registered held-out gate (recheck merged over raw
labels, timing / non-timing failures, lag / lead direction, bias test,
PASS / FAIL).

Corpus (one case = one special x one slot):

- ``flip``: slot state known at X = 0 but ``unknown`` at ``--flip-window``
  (default 0.5 s) in ``review_special_roster_sync.py``.
- ``respawn_supplement``: every other dead->alive lamp bracket whose nearer
  edge lies within ``--respawn-within`` s of a special (including brackets
  that contain the special). The event lane has no respawn glyph, so these
  can only be judged from the roster frames.

Each sheet shows the event lane around the special (cyan tick = special glyph
centre, red tick = this slot's death glyph, guide lines = this slot's lane
row) and the slot's roster icon in every frame spanning the transition and the
special, ordered match-forward, with a divider at the special glyph centre.
The pipeline's lamp reading is deliberately not shown.

The reviewer answers one question per case: at the special glyph centre, is
the slot demonstrably ``alive``, demonstrably ``dead``, or ``indeterminate``?
``basis`` records how: ``lane_death_order`` / ``roster_frames`` / ``both``.

Run from the repo root with ``PYTHONPATH=tools/diagnostics``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml
from review_roster_timing import SCREEN_WIDTH, lamp_states
from review_special_roster_sync import SLOTS, recording_rows

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.review.lane_glyphs import ROW_SLOTS
from splatoon3_ai_coach.review.models import ReviewTimelineDataset, ReviewTimelineSample

LANE_HALF_PX = 90
LANE_SCALE = 2.0
ICON_SCALE = 2.5
AT_PX = 0.5
COMPACT_JPEG_QUALITY = 80
STATIC_MIN_PX = 160
DEATH_NEAR_S = 3.0
STATES = ("alive", "dead", "indeterminate")
BASES = ("lane_death_order", "roster_frames", "both")
BINS_S = [0.0, 0.1, 0.2, 0.3, 0.5, 1.0]


def main() -> int:
    """Dispatch ``sheet``, ``recheck``, ``score`` or ``gate``."""
    args = _parse_args()
    return args.func(args)


# --- corpus ---------------------------------------------------------------


def build_cases(
    datasets: dict[str, ReviewTimelineDataset],
    flip_window: float,
    respawn_within: float,
    id_prefix: str = "B",
) -> list[dict[str, Any]]:
    """Flip cases plus the respawn supplement, in recording / x order."""
    cases: list[dict[str, Any]] = []
    for rid, ds in datasets.items():
        axis = ds.axis_calibration
        if axis is None:
            continue
        spp = abs(axis.slope) / SCREEN_WIDTH
        lamps = lamp_states(ds)
        deaths = _death_glyphs(ds)
        at0 = recording_rows(ds, rid, 0.0)
        atx = recording_rows(ds, rid, flip_window)
        for r0, rx in zip(at0, atx, strict=True):
            for slot in SLOTS:
                case = _case(
                    rid, r0, rx, slot, lamps.get(slot, []), deaths, spp, respawn_within
                )
                if case is not None:
                    cases.append(case)
    for number, case in enumerate(cases, start=1):
        case["case_id"] = f"{id_prefix}{number:02d}"
    return cases


def _case(
    rid: str,
    r0: dict[str, Any],
    rx: dict[str, Any],
    slot: str,
    seq: list[tuple[float, str]],
    deaths: list[tuple[str, float]],
    spp: float,
    respawn_within: float,
) -> dict[str, Any] | None:
    g = r0["glyph_x"]
    state0 = r0["slots"][slot]["state"]
    flip = state0 != "unknown" and rx["slots"][slot]["state"] == "unknown"
    bracket = _nearest_transition(seq, g)
    if bracket is None:
        return None
    a, b, before, after = bracket
    distance_s = (0.0 if a < g <= b else min(abs(g - a), abs(g - b))) * spp
    respawn = (before, after) == ("dead", "alive") and distance_s <= respawn_within
    if not flip and not respawn:
        return None
    death = [x for s, x in deaths if s == slot and abs(x - g) * spp <= DEATH_NEAR_S]
    return {
        "recording": rid,
        "special": f"{r0['special_id']}@{g:.0f}",
        "special_slot": r0["slot"],
        "special_clock": r0["elapsed"],
        "glyph_x": g,
        "slot": slot,
        "corpus": "flip" if flip else "respawn_supplement",
        "transition_type": f"{before}_to_{after}",
        "transition_bracket_px": [a, b],
        "transition_bracket_s_relative_to_special": [
            round((a - g) * spp, 2),
            round((b - g) * spp, 2),
        ],
        "distance_to_transition_s": round(distance_s, 2),
        "lane_death_glyph_dt_s": round((death[0] - g) * spp, 2) if death else None,
        "lane_death_glyph_x": death[0] if death else None,
        "pipeline_state_x0": state0,
        "seconds_per_px": spp,
    }


def _nearest_transition(
    seq: list[tuple[float, str]], g: float
) -> tuple[float, float, str, str] | None:
    known = [(x, s) for x, s in seq if s != "unknown"]
    pairs = [
        (p[0], q[0], p[1], q[1])
        for p, q in zip(known, known[1:], strict=False)
        if p[1] != q[1]
    ]

    def gap(t: tuple[float, float, str, str]) -> float:
        return 0.0 if t[0] < g <= t[1] else min(abs(g - t[0]), abs(g - t[1]))

    return min(pairs, key=gap, default=None)


def _death_glyphs(ds: ReviewTimelineDataset) -> list[tuple[str, float]]:
    return [
        (m.player_slot_id, m.x_norm * SCREEN_WIDTH)
        for m in ds.match_history_events
        if m.kind == "death" and m.player_slot_id in ROW_SLOTS
    ]


# --- sheet ----------------------------------------------------------------


def cmd_sheet(args: argparse.Namespace) -> int:
    """Write ``cases.json``, ``draft_labels.yaml`` and one image per case.

    ``--compact`` writes BEFORE / AT / AFTER JPEGs to ``sheets_compact/``.
    """
    config = load_config(default_config_path()).review
    datasets = _load(args.imports)
    cases = build_cases(datasets, args.flip_window, args.respawn_within, args.id_prefix)
    out: Path = args.out
    folder = out / ("sheets_compact" if args.compact else "sheets")
    folder.mkdir(parents=True, exist_ok=True)
    for case in cases:
        ds = datasets[case["recording"]]
        if args.compact:
            image = render_compact(ds, case, config)
            path = folder / f"{case['case_id']}.jpg"
            cv2.imwrite(
                str(path), image, [cv2.IMWRITE_JPEG_QUALITY, COMPACT_JPEG_QUALITY]
            )
        else:
            cv2.imwrite(
                str(folder / f"{case['case_id']}.png"), render_case(ds, case, config)
            )
    (out / "cases.json").write_text(json.dumps(cases, indent=2), "utf-8")
    (out / "draft_labels.yaml").write_text(_draft_yaml(cases, args), "utf-8")
    counts = Counter((c["corpus"], c["transition_type"]) for c in cases)
    print(json.dumps({f"{k[0]}/{k[1]}": v for k, v in counts.items()}, indent=2))
    return 0


def _draft_yaml(cases: list[dict[str, Any]], args: argparse.Namespace) -> str:
    entries = [
        {
            "case_id": c["case_id"],
            "recording": c["recording"],
            "special": c["special"],
            "special_clock": c["special_clock"],
            "slot": c["slot"],
            "corpus": c["corpus"],
            "transition_type": c["transition_type"],
            "transition_bracket_s_relative_to_special": c[
                "transition_bracket_s_relative_to_special"
            ],
            "lane_death_glyph_dt_s": c["lane_death_glyph_dt_s"],
            "reviewed_state_at_special": None,
            "basis": None,
            "reviewer_note": "",
        }
        for c in cases
    ]
    header = {
        "status": "draft",
        "question": "At the special glyph centre, is this slot demonstrably alive, "
        "demonstrably dead, or indeterminate?",
        "allowed_states": list(STATES),
        "allowed_basis": list(BASES),
        "flip_window_s": args.flip_window,
        "respawn_within_s": args.respawn_within,
    }
    return yaml.safe_dump(header | {"cases": entries}, sort_keys=False, width=100)


def render_case(
    ds: ReviewTimelineDataset, case: dict[str, Any], config: ReviewTimelineConfig
) -> np.ndarray:
    """Header, lane strip with ticks, and the slot icon across the boundary."""
    g = case["glyph_x"]
    frames = _frames(ds)
    tiles = [_lane_strip(ds, frames, case, config)]
    a, b = case["transition_bracket_px"]
    lo, hi = min(a, g), max(b, g)
    xs = sorted(frames)
    before = [x for x in xs if x < lo][-1:]
    after = [x for x in xs if x > hi][:1]
    span = before + [x for x in xs if lo <= x <= hi] + after
    divided = False
    for x in span:
        if not divided and x > g:
            tiles.append(_divider("---- special glyph centre ----", tiles[0].shape[1]))
            divided = True
        tiles.append(_icon_tile(frames[x], x, case, config))
    if not divided:
        tiles.append(_divider("---- special glyph centre ----", tiles[0].shape[1]))
    width = max(t.shape[1] for t in tiles)
    tiles = [_pad(t, width) for t in tiles]
    title = (
        f"{case['case_id']}  {case['recording']}\n"
        f"{case['special']} by {case['special_slot']}, clock {case['special_clock']}\n"
        f"SLOT UNDER REVIEW: {case['slot']}"
    )
    return np.vstack([_bar(title, width), *tiles])


def _frames(ds: ReviewTimelineDataset) -> dict[float, ReviewTimelineSample]:
    frames: dict[float, ReviewTimelineSample] = {}
    for s in ds.samples:
        if s.cursor.x is not None and s.clock_status != "implausible":
            frames.setdefault(float(s.cursor.x), s)
    return frames


def _lane_strip(
    ds: ReviewTimelineDataset,
    frames: dict[float, ReviewTimelineSample],
    case: dict[str, Any],
    config: ReviewTimelineConfig,
) -> np.ndarray:
    g = case["glyph_x"]
    far = [x for x in frames if abs(x - g) >= STATIC_MIN_PX]
    source = frames[min(far, key=lambda x: abs(abs(x - g) - 300))] if far else None
    if source is None:
        return _bar("no cursor-free frame for the lane", 400)
    image = cv2.imread(source.image_path)
    h, _ = image.shape[:2]
    _, y1n, _, y2n = config.event_lane_roi
    y1, y2 = int(h * y1n), int(h * y2n)
    x1 = int(max(0, g - LANE_HALF_PX))
    lane = image[y1:y2, x1 : int(g + LANE_HALF_PX)].copy()
    row = ROW_SLOTS.index(case["slot"])
    center = config.event_lane_row_centers[row] * (y2 - y1)
    half = 2.5 * config.event_lane_row_half_height * (y2 - y1)
    for y in (int(center - half), int(center + half)):
        cv2.line(lane, (0, y), (lane.shape[1], y), (200, 200, 200), 1)
    _tick(lane, g - x1, (255, 255, 0))
    if case["lane_death_glyph_x"] is not None:
        _tick(lane, case["lane_death_glyph_x"] - x1, (0, 0, 255))
    lane = cv2.resize(
        lane, None, fx=LANE_SCALE, fy=LANE_SCALE, interpolation=cv2.INTER_NEAREST
    )
    label = (
        "lane: cyan tick = special centre\n"
        "red tick = this slot's death glyph\n"
        "grey lines = this slot's lane row"
    )
    return np.vstack([_bar(label, lane.shape[1]), lane])


def _tick(lane: np.ndarray, x: float, color: tuple[int, int, int]) -> None:
    xi = int(round(x))
    cv2.line(lane, (xi, 0), (xi, 6), color, 1)
    cv2.line(lane, (xi, lane.shape[0] - 7), (xi, lane.shape[0] - 1), color, 1)


def _icon_tile(
    sample: ReviewTimelineSample,
    x: float,
    case: dict[str, Any],
    config: ReviewTimelineConfig,
) -> np.ndarray:
    image = cv2.imread(sample.image_path)
    row_crop, (sx1, sx2) = _team_row(image, x, case["slot"], config)
    icon = cv2.resize(
        row_crop[:, sx1:sx2],
        None,
        fx=ICON_SCALE,
        fy=ICON_SCALE,
        interpolation=cv2.INTER_NEAREST,
    )
    context = row_crop.copy()
    cv2.rectangle(context, (sx1, 0), (sx2 - 1, context.shape[0] - 1), (0, 255, 255), 1)
    height = max(icon.shape[0], context.shape[0])
    body = np.hstack(
        [
            _pad_h(icon, height),
            np.zeros((height, 8, 3), np.uint8),
            _pad_h(context, height),
        ]
    )
    dt = (x - case["glyph_x"]) * case["seconds_per_px"]
    dx = x - case["glyph_x"]
    side = "AT" if abs(dx) <= AT_PX else "BEFORE" if dx < 0 else "AFTER"
    label = f"{side} special: " + (
        f"dx {x - case['glyph_x']:+.1f} px ({dt:+.2f} s) clock {sample.elapsed_seconds}"
    )
    return np.vstack([_bar(label, body.shape[1]), body])


def _team_row(
    image: np.ndarray, cursor_x: float, slot: str, config: ReviewTimelineConfig
) -> tuple[np.ndarray, tuple[int, int]]:
    height, width = image.shape[:2]
    slot_w = width * config.roster_row_width_fraction / 4
    center = float(np.clip(cursor_x, 2 * slot_w, width - 2 * slot_w))
    team, index = slot.split("_")
    band = config.roster_top_band if team == "top" else config.roster_bottom_band
    left = int(round(center - 2 * slot_w))
    row = image[
        int(height * band[0]) : int(height * band[1]),
        left : int(round(center + 2 * slot_w)),
    ]
    sx1 = int(round((int(index) - 1) * slot_w))
    return row, (sx1, int(round(sx1 + slot_w)))


def _bar(text: str, width: int) -> np.ndarray:
    lines = text.split("\n")
    bar = np.zeros((20 * len(lines) + 4, max(width, 10), 3), np.uint8)
    for i, line in enumerate(lines):
        cv2.putText(
            bar,
            line,
            (4, 16 + 20 * i),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (255, 255, 255),
            1,
        )
    return bar


def _divider(text: str, width: int) -> np.ndarray:
    bar = np.full((22, max(width, 10), 3), (0, 90, 140), np.uint8)
    cv2.putText(bar, text, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    return bar


def _pad(tile: np.ndarray, width: int) -> np.ndarray:
    return cv2.copyMakeBorder(tile, 0, 4, 0, width - tile.shape[1], cv2.BORDER_CONSTANT)


def _pad_h(tile: np.ndarray, height: int) -> np.ndarray:
    return cv2.copyMakeBorder(tile, 0, height - tile.shape[0], 0, 0, cv2.BORDER_CONSTANT)


# --- recheck --------------------------------------------------------------


AT_FRAME_RULE = (
    "Label the slot in the yellow box at the special. (1) If an AT frame is shown "
    "(|dx| <= 0.5 px), its icon decides: full-colour icon = alive, X over the icon = "
    "dead. (2) Otherwise, if BEFORE and AFTER show the same state, label that state. "
    "(3) Otherwise, or if the deciding icon is illegible, blank or a disconnected "
    "silhouette: indeterminate. Alive and dead both appearing on the sheet is not by "
    "itself grounds for indeterminate."
)


def recheck_ids(
    cases: dict[str, dict[str, Any]],
    labels: dict[str, Any],
    known: dict[str, str] | None = None,
) -> list[str]:
    """Cases whose X = 0 state the label contradicts or does not support.

    Pre-registered known failures are left out, so the set equals the raw
    timing failures of ``gate``.
    """
    rows = [_score_row(cases[e["case_id"]], e) for e in labels["cases"]]
    return [r["case_id"] for r in rows if classify_failure(r, known or {}) == "timing"]


def cmd_recheck(args: argparse.Namespace) -> int:
    """Focused sheets for reviewed cases that conflict with the frames.

    ``--compact`` renders the same BEFORE / AT / AFTER JPEGs as the labelling
    sheets and states the AT-frame rule; case selection is identical.
    """
    config = load_config(default_config_path()).review
    datasets = _load(args.imports)
    cases = {c["case_id"]: c for c in json.loads(args.cases.read_text("utf-8"))}
    labels = yaml.safe_load(args.labels.read_text("utf-8"))
    known = read_known_failures(args.known_failures) if args.known_failures else {}
    ids = recheck_ids(cases, labels, known)
    out: Path = args.out
    plan = [{"sheet_id": i, "case_id": i, "role": "target"} for i in ids]
    if args.decoy_seed is not None:
        plan = decoy_plan(cases, labels, ids, args.decoy_seed)
        key = {"seed": args.decoy_seed, "labels": str(args.labels), "sheets": plan}
        out.mkdir(parents=True, exist_ok=True)
        (out / "recheck_key.json").write_text(json.dumps(key, indent=2), "utf-8")
    (out / "sheets").mkdir(parents=True, exist_ok=True)
    for item in plan:
        case = cases[item["case_id"]] | {"case_id": item["sheet_id"]}
        ds = datasets[case["recording"]]
        if args.compact:
            cv2.imwrite(
                str(out / "sheets" / f"{item['sheet_id']}.jpg"),
                render_compact(ds, case, config),
                [cv2.IMWRITE_JPEG_QUALITY, COMPACT_JPEG_QUALITY],
            )
        else:
            image = render_recheck(ds, case, config)
            cv2.imwrite(str(out / "sheets" / f"{item['sheet_id']}.png"), image)
    entries = [
        {"case_id": p["sheet_id"], "reviewed_state_at_special": None, "reviewer_note": ""}
        for p in plan
    ]
    question = (
        AT_FRAME_RULE
        if args.compact
        else "Earlier frame and later frame bracket the special. At the "
        "special, is this slot demonstrably alive, demonstrably dead, or "
        "indeterminate?"
    )
    header = {"status": "draft", "question": question, "replaces": str(args.labels)}
    text = yaml.safe_dump(header | {"cases": entries}, sort_keys=False, width=100)
    (out / "recheck_labels.yaml").write_text(text, "utf-8")
    print(f"{len(plan)} recheck sheets ({len(ids)} targets)")
    return 0


def decoy_plan(
    cases: dict[str, dict[str, Any]],
    labels: dict[str, Any],
    targets: list[str],
    seed: int,
) -> list[dict[str, Any]]:
    """Targets mixed with as many agreeing X = 0-known decoys, under neutral IDs.

    Decoys are drawn with ``seed`` from cases whose raw label equals the X = 0
    state; the combined list is shuffled and renamed ``R01``, ``R02``, ...
    """
    rows = [_score_row(cases[e["case_id"]], e) for e in labels["cases"]]
    pool = sorted(
        r["case_id"]
        for r in rows
        if r["outcome"] == "correct" and r["case_id"] not in targets
    )
    rng = random.Random(seed)
    decoys = rng.sample(pool, min(len(targets), len(pool)))
    mixed = [(i, "target") for i in targets] + [(i, "decoy") for i in decoys]
    rng.shuffle(mixed)
    return [
        {"sheet_id": f"R{n:02d}", "case_id": case_id, "role": role}
        for n, (case_id, role) in enumerate(mixed, start=1)
    ]


def apply_recheck_key(
    recheck: dict[str, dict[str, Any]], key: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Neutral sheet IDs back to case IDs: target entries and decoy entries."""
    sheets = {s["sheet_id"]: s for s in key["sheets"]}
    if set(sheets) != set(recheck):
        diff = sorted(set(sheets) ^ set(recheck))
        raise SystemExit(f"recheck labels and key differ: {', '.join(diff)}")
    targets: dict[str, dict[str, Any]] = {}
    decoys: list[dict[str, Any]] = []
    for sheet_id, entry in recheck.items():
        item = sheets[sheet_id]
        mapped = entry | {"case_id": item["case_id"], "sheet_id": sheet_id}
        if item["role"] == "target":
            targets[item["case_id"]] = mapped
        else:
            decoys.append(mapped)
    return targets, decoys


def decoy_check(
    decoys: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Recheck consistency on decoys (raw label agreed with X = 0); never scored."""
    by_id = {r["case_id"]: r for r in rows}
    items = [
        {
            "case_id": d["case_id"],
            "sheet_id": d["sheet_id"],
            "raw_reviewed": by_id[d["case_id"]]["raw_reviewed"],
            "recheck_reviewed": d["reviewed_state_at_special"],
        }
        for d in sorted(decoys, key=lambda d: d["case_id"])
    ]
    changed = [i["case_id"] for i in items if i["raw_reviewed"] != i["recheck_reviewed"]]
    return {"decoys": len(items), "changed": changed, "items": items}


def render_compact(
    ds: ReviewTimelineDataset, case: dict[str, Any], config: ReviewTimelineConfig
) -> np.ndarray:
    """Nearest BEFORE / AT / AFTER frames of the target slot, nothing else.

    AT is a frame within ``AT_PX`` of the special glyph centre (glyph
    localisation); it is shown whenever one exists.
    """
    g = case["glyph_x"]
    frames = _frames(ds)
    at = [x for x in frames if abs(x - g) <= AT_PX]
    at_x = min(at, key=lambda x: abs(x - g)) if at else None
    before = max((x for x in frames if x < g and x != at_x), default=None)
    after = min((x for x in frames if x > g and x != at_x), default=None)
    tiles = []
    for name, x in (("BEFORE", before), ("AT", at_x), ("AFTER", after)):
        if x is None:
            if name != "AT":
                tiles.append(_bar(f"{name} the special: no frame", 300))
            continue
        tiles.append(_icon_tile(frames[x], x, case, config))
    width = max(t.shape[1] for t in tiles)
    title = f"{case['case_id']}  slot {case['slot']} (yellow box)\ntime runs downward"
    return np.vstack([_bar(title, width), *(_pad(t, width) for t in tiles)])


def render_recheck(
    ds: ReviewTimelineDataset, case: dict[str, Any], config: ReviewTimelineConfig
) -> np.ndarray:
    """The slot in the last frame before and the first frame after the special."""
    g = case["glyph_x"]
    frames = _frames(ds)
    earlier = max((x for x in frames if x <= g), default=None)
    later = min((x for x in frames if x > g), default=None)
    tiles = []
    for name, x in (
        ("EARLIER (before the special)", earlier),
        ("LATER (after the special)", later),
    ):
        if x is None:
            tiles.append(_bar(f"{name}: no frame", 400))
            continue
        tile = _icon_tile(frames[x], x, case, config)
        tiles.append(np.vstack([_divider(name, tile.shape[1]), tile]))
    width = max(t.shape[1] for t in tiles)
    title = (
        f"{case['case_id']} re-check  {case['recording']}\n"
        f"special {case['special']} by {case['special_slot']}\n"
        f"SLOT UNDER REVIEW: {case['slot']} (yellow box)\n"
        "time runs downward: EARLIER frame, special, LATER frame"
    )
    special = _divider("---- special glyph centre ----", width)
    body = [_pad(tiles[0], width), special, _pad(tiles[1], width)]
    return np.vstack([_bar(title, width), *body])


# --- score ----------------------------------------------------------------


def cmd_score(args: argparse.Namespace) -> int:
    """Compare reviewed truth with the X = 0 reading by distance to transition."""
    labels = yaml.safe_load(args.labels.read_text("utf-8"))
    if labels.get("status") != "reviewed" and not args.allow_draft:
        raise SystemExit("labels are not marked status: reviewed (use --allow-draft)")
    cases = {c["case_id"]: c for c in json.loads(args.cases.read_text("utf-8"))}
    rows = [_score_row(cases[e["case_id"]], e) for e in labels["cases"]]
    result = {"bins": _bins(rows), "window": _window(rows), "cases": rows}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "boundary_score.json").write_text(json.dumps(result, indent=2), "utf-8")
    (args.out / "boundary_score.md").write_text(_score_md(result), "utf-8")
    print(json.dumps({"bins": result["bins"], "window": result["window"]}, indent=2))
    return 0


def _score_row(case: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    reviewed = entry.get("reviewed_state_at_special")
    if reviewed is not None and reviewed not in STATES:
        raise SystemExit(f"{entry['case_id']}: invalid state {reviewed!r}")
    pipeline = case["pipeline_state_x0"]
    if reviewed is None:
        outcome = "unreviewed"
    elif reviewed == "indeterminate":
        outcome = "indeterminate"
    elif pipeline == "unknown":
        outcome = "pipeline_unknown"
    else:
        outcome = "correct" if pipeline == reviewed else "wrong"
    return {
        "case_id": case["case_id"],
        "transition_type": case["transition_type"],
        "distance_s": case["distance_to_transition_s"],
        "reviewed": reviewed,
        "basis": entry.get("basis"),
        "pipeline_x0": pipeline,
        "outcome": outcome,
    }


def _bins(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    edges = [*BINS_S, float("inf")]
    for lo, hi in zip(edges, edges[1:], strict=False):
        inside = [r for r in rows if lo <= r["distance_s"] < hi]
        for kind in ("alive_to_dead", "dead_to_alive"):
            subset = [r for r in inside if r["transition_type"] == kind]
            if subset:
                out.append(
                    {"distance_s": [lo, hi], "transition_type": kind}
                    | dict(Counter(r["outcome"] for r in subset))
                )
    return out


def _window(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Largest distance at which Review fails to support the X = 0 state."""
    reviewed = [r for r in rows if r["outcome"] != "unreviewed"]
    bad = [r for r in reviewed if r["outcome"] in ("wrong", "indeterminate")]
    return {
        "reviewed": len(reviewed),
        "max_distance_wrong_s": max(
            (r["distance_s"] for r in bad if r["outcome"] == "wrong"), default=None
        ),
        "max_distance_indeterminate_s": max(
            (r["distance_s"] for r in bad if r["outcome"] == "indeterminate"),
            default=None,
        ),
        "min_distance_reviewed_s": min((r["distance_s"] for r in reviewed), default=None),
        "empirical_x_s": max((r["distance_s"] for r in bad), default=0.0),
    }


def _score_md(result: dict[str, Any]) -> str:
    w = result["window"]
    lines = [
        "# Boundary-case scoring (reviewed truth vs X = 0 lamp reading)",
        "",
        f"Reviewed cases: {w['reviewed']}. Empirical X (largest distance with a "
        f"wrong or indeterminate case): {w['empirical_x_s']} s. Wrong up to "
        f"{w['max_distance_wrong_s']} s; indeterminate up to "
        f"{w['max_distance_indeterminate_s']} s; closest reviewed case "
        f"{w['min_distance_reviewed_s']} s.",
        "",
        "| Distance to transition (s) | Transition | Outcomes |",
        "|---|---|---|",
    ]
    for b in result["bins"]:
        outcomes = {
            k: v for k, v in b.items() if k not in ("distance_s", "transition_type")
        }
        lines.append(f"| {b['distance_s']} | {b['transition_type']} | {outcomes} |")
    lines += [
        "",
        "| Case | Transition | Distance (s) | Reviewed | Basis | X=0 | Outcome |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['case_id']} | {r['transition_type']} | {r['distance_s']} "
        f"| {r['reviewed']} | {r['basis']} | {r['pipeline_x0']} | {r['outcome']} |"
        for r in result["cases"]
    ]
    return "\n".join(lines) + "\n"


# --- gate -----------------------------------------------------------------

BIAS_MIN_FAILURES = 3
BIAS_SHARE = (4, 5)
DIRECTIONS = ("lag", "lead", "none")


def cmd_gate(args: argparse.Namespace) -> int:
    """Pre-registered held-out gate: merge recheck, classify, verdict, persist."""
    batches = [_read_batch(Path(c), Path(lab)) for c, lab in args.batch]
    recheck = read_recheck(args.recheck) if args.recheck else {}
    decoys: list[dict[str, Any]] = []
    if args.recheck_key:
        key = json.loads(args.recheck_key.read_text("utf-8"))
        recheck, decoys = apply_recheck_key(recheck, key)
    known = read_known_failures(args.known_failures)
    result = run_gate(batches, recheck, known)
    check_recheck_coverage(result)
    if args.recheck_key:
        result["decoy_check"] = decoy_check(decoys, result["cases"])
    result["provenance"] = _provenance(args)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "gate.json").write_text(json.dumps(result, indent=2), "utf-8")
    (args.out / "gate.md").write_text(gate_md(result), "utf-8")
    print(json.dumps(result["verdict"], indent=2))
    return 0


def _read_batch(
    cases_path: Path, labels_path: Path
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    cases = json.loads(cases_path.read_text("utf-8"))
    labels = yaml.safe_load(labels_path.read_text("utf-8"))
    return str(cases_path.parent), cases, labels


def read_recheck(path: Path) -> dict[str, dict[str, Any]]:
    """Reviewed recheck entries by case ID; rejects drafts and malformed rows."""
    data = yaml.safe_load(path.read_text("utf-8"))
    if not isinstance(data, dict) or data.get("status") != "reviewed":
        raise SystemExit(f"{path}: recheck labels are not marked status: reviewed")
    out: dict[str, dict[str, Any]] = {}
    for entry in data.get("cases") or []:
        case_id = entry.get("case_id")
        if case_id in out:
            raise SystemExit(f"{path}: duplicate recheck case {case_id}")
        if entry.get("reviewed_state_at_special") not in STATES:
            raise SystemExit(f"{path}: {case_id} needs alive / dead / indeterminate")
        if not str(entry.get("reviewer_note") or "").strip():
            raise SystemExit(f"{path}: {case_id} needs a basis note")
        out[case_id] = entry
    if not out:
        raise SystemExit(f"{path}: no recheck cases")
    return out


def read_known_failures(path: Path) -> dict[str, str]:
    """Pre-registered non-timing failures: case ID -> documented cause."""
    data = yaml.safe_load(path.read_text("utf-8"))
    out: dict[str, str] = {}
    for entry in (data or {}).get("known_failures") or []:
        case_id, cause = entry.get("case_id"), entry.get("cause")
        if not case_id or not cause:
            raise SystemExit(f"{path}: known failure needs case_id and cause")
        if case_id in out:
            raise SystemExit(f"{path}: duplicate known failure {case_id}")
        out[case_id] = cause
    return out


def run_gate(
    batches: list[tuple[str, list[dict[str, Any]], dict[str, Any]]],
    recheck: dict[str, dict[str, Any]],
    known: dict[str, str],
) -> dict[str, Any]:
    """Merged rows, raw vs final summaries and the verdict (no file access)."""
    pairs = merge_inputs(batches)
    for ids, what in ((recheck, "recheck"), (known, "known failure")):
        unknown = sorted(set(ids) - set(pairs))
        if unknown:
            raise SystemExit(f"{what} IDs not in any batch: {', '.join(unknown)}")
    raw_rows = [gate_row(c, e, None, known) for c, e in pairs.values()]
    rows = [gate_row(c, e, recheck.get(i), known) for i, (c, e) in pairs.items()]
    return {
        "batches": [
            {"source": src, "cases": len(cases), "labels_status": labels.get("status")}
            for src, cases, labels in batches
        ],
        "rechecked_cases": sorted(recheck),
        "known_failures": known,
        "raw": _summary(raw_rows),
        "final": _summary(rows),
        "verdict": gate_verdict(rows),
        "cases": rows,
    }


def check_recheck_coverage(result: dict[str, Any]) -> None:
    """The recheck must cover exactly the raw timing failures, no more, no less."""
    required = sorted(result["raw"]["timing_failures"])
    if sorted(result["rechecked_cases"]) != required:
        raise SystemExit(
            f"recheck covers {result['rechecked_cases']}, raw timing failures "
            f"require {required}"
        )


def merge_inputs(
    batches: list[tuple[str, list[dict[str, Any]], dict[str, Any]]],
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    """Case ID -> (case, raw reviewed label entry), validated across batches."""
    pairs: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for src, cases, labels in batches:
        if labels.get("status") != "reviewed":
            raise SystemExit(f"{src}: labels are not marked status: reviewed")
        by_id = _unique({c["case_id"]: c for c in cases}, cases, src, "case")
        entries = labels.get("cases") or []
        label_by_id = _unique({e["case_id"]: e for e in entries}, entries, src, "label")
        if set(by_id) != set(label_by_id):
            diff = sorted(set(by_id) ^ set(label_by_id))
            raise SystemExit(f"{src}: cases and labels differ: {', '.join(diff)}")
        for case_id, case in by_id.items():
            if case_id in pairs:
                raise SystemExit(f"duplicate case {case_id} across batches")
            entry = label_by_id[case_id]
            if entry.get("reviewed_state_at_special") not in STATES:
                raise SystemExit(f"{src}: {case_id} has no reviewed state")
            pairs[case_id] = (case, entry)
    return pairs


def _unique(
    by_id: dict[str, Any], items: list[dict[str, Any]], src: str, what: str
) -> dict[str, Any]:
    if len(by_id) != len(items):
        dupes = [k for k, n in Counter(i["case_id"] for i in items).items() if n > 1]
        raise SystemExit(f"{src}: duplicate {what} IDs {', '.join(dupes)}")
    return by_id


def gate_row(
    case: dict[str, Any],
    raw_entry: dict[str, Any],
    recheck_entry: dict[str, Any] | None,
    known: dict[str, str],
) -> dict[str, Any]:
    """One merged, scored and classified case; the raw judgment is kept."""
    raw = raw_entry["reviewed_state_at_special"]
    recheck = recheck_entry["reviewed_state_at_special"] if recheck_entry else None
    final = recheck if recheck_entry else raw
    merged = recheck_entry if recheck_entry else raw_entry
    row = _score_row(
        case, {"basis": merged.get("basis"), "reviewed_state_at_special": final}
    )
    bracket = case["transition_bracket_s_relative_to_special"]
    kind = classify_failure(row, known)
    direction = (
        transition_direction(case["transition_type"], bracket, final, row["pipeline_x0"])
        if kind == "timing"
        else "none"
    )
    return row | {
        "recording": case["recording"],
        "slot": case["slot"],
        "corpus": case["corpus"],
        "transition_bracket_s": bracket,
        "in_bracket": case["distance_to_transition_s"] == 0,
        "raw_reviewed": raw,
        "recheck_reviewed": recheck,
        "label_source": "recheck" if recheck_entry else "raw",
        "recheck_note": recheck_entry.get("reviewer_note") if recheck_entry else None,
        "failure_kind": kind,
        "known_cause": known.get(case["case_id"]),
        "direction": direction,
    }


def classify_failure(row: dict[str, Any], known: dict[str, str]) -> str | None:
    """``timing``, ``non_timing`` (pre-registered only) or ``None``.

    A failure is an X = 0 state that review contradicts or cannot support.
    """
    if row["pipeline_x0"] == "unknown" or row["outcome"] not in (
        "wrong",
        "indeterminate",
    ):
        return None
    return "non_timing" if row["case_id"] in known else "timing"


def transition_direction(
    transition_type: str, bracket: list[float], reviewed: str, x0_state: str
) -> str:
    """``lag``: X = 0 still shows the before-state the label has left (bracket
    after the special); ``lead``: X = 0 already shows the after-state the label
    has not reached (bracket before the special); otherwise ``none``."""
    before, after = transition_type.split("_to_")
    start, end = bracket
    if start >= 0 and x0_state == before and reviewed == after:
        return "lag"
    if end < 0 and x0_state == after and reviewed == before:
        return "lead"
    return "none"


def bias_test(directions: list[str]) -> dict[str, Any]:
    """Systematic bias: >= 3 timing failures and >= 80% in one of lag / lead."""
    counts = {d: directions.count(d) for d in DIRECTIONS}
    n = len(directions)
    dominant = max(("lag", "lead"), key=lambda d: counts[d])
    num, den = BIAS_SHARE
    found = n >= BIAS_MIN_FAILURES and den * counts[dominant] >= num * n
    return {
        "timing_failures": n,
        "counts": counts,
        "min_failures": BIAS_MIN_FAILURES,
        "min_share": num / den,
        "dominant_direction": dominant if counts[dominant] else None,
        "dominant_share": round(counts[dominant] / n, 3) if n else None,
        "systematic_bias": found,
        "bias_direction": dominant if found else None,
    }


def gate_verdict(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pre-registered PASS / FAIL with the bias test and empirical-X check."""
    timing = [r for r in rows if r["failure_kind"] == "timing"]
    bias = bias_test([r["direction"] for r in timing])
    attributable = [r for r in rows if r["failure_kind"] != "non_timing"]
    determinable = [
        r
        for r in attributable
        if r["pipeline_x0"] != "unknown" and r["reviewed"] in ("alive", "dead")
    ]
    cond2 = [r["case_id"] for r in determinable if r["failure_kind"] == "timing"]
    passed = not timing and not cond2
    if passed:
        reason, action = "no_timing_failures", "freeze the validated timing configuration"
    elif bias["systematic_bias"]:
        reason = "systematic_bias"
        action = "no tuning on B / C; derive a correction, validate on new recordings"
    else:
        reason = "isolated_timing_failures"
        action = "implementation unchanged; diagnose the listed cases"
    return {
        "verdict": "PASS" if passed else "FAIL",
        "reason": reason,
        "action": action,
        "condition_1_timing_failures": [r["case_id"] for r in timing],
        "condition_2_determinable_cases": len(determinable),
        "condition_2_timing_failures": cond2,
        "condition_2_redundant": True,
        "empirical_x_timing_attributable": _window(attributable),
        "bias": bias,
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    timing = [r for r in rows if r["failure_kind"] == "timing"]
    return {
        "labels": dict(Counter(r["reviewed"] for r in rows)),
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "timing_failures": [r["case_id"] for r in timing],
        "non_timing_failures": [
            r["case_id"] for r in rows if r["failure_kind"] == "non_timing"
        ],
        "x0_unknown_in_bracket": sum(
            r["pipeline_x0"] == "unknown" and r["in_bracket"] for r in rows
        ),
        "x0_unknown_out_of_bracket": sum(
            r["pipeline_x0"] == "unknown" and not r["in_bracket"] for r in rows
        ),
        "directions": dict(Counter(r["direction"] for r in timing)),
        "window": _window(rows),
        "bins": _bins(rows),
    }


def _provenance(args: argparse.Namespace) -> dict[str, Any]:
    paths = [Path(p) for pair in args.batch for p in pair]
    optional = (args.recheck, args.recheck_key, args.known_failures)
    paths += [p for p in optional if p is not None]
    config = default_config_path()
    review = yaml.safe_load(config.read_text("utf-8"))["review"]
    head = _git("rev-parse", "HEAD")
    prereg = args.preregistration_sha
    known_rel = args.known_failures.resolve().relative_to(
        Path(_git("rev-parse", "--show-toplevel"))
    )
    if (
        _git("show", f"{prereg}:{known_rel}")
        != args.known_failures.read_text("utf-8").strip()
    ):
        raise SystemExit("known failures differ from the pre-registered commit")
    return {
        "preregistration_git_sha": _git("rev-parse", prereg),
        "gate_run_git_sha": head,
        "gate_run_tool_dirty": bool(
            _git("status", "--porcelain", "--", __file__, str(config))
        ),
        "inputs": {str(p): _sha256(p.read_bytes()) for p in paths},
        "config_path": str(config),
        "config_sha256": _sha256(config.read_bytes()),
        "review_config_sha256": _sha256(
            json.dumps(review, sort_keys=True).encode("utf-8")
        ),
    }


def _git(*argv: str) -> str:
    return subprocess.run(
        ["git", *argv], check=True, capture_output=True, text=True
    ).stdout.strip()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def gate_md(result: dict[str, Any]) -> str:
    """Human-readable gate report; every case is listed."""
    v, raw, final = result["verdict"], result["raw"], result["final"]
    bias, window = v["bias"], v["empirical_x_timing_attributable"]
    prov = result.get("provenance", {})
    lines = [
        "# SPECIAL_USE held-out gate (zero offset, X = 0)",
        "",
        f"**Verdict: {v['verdict']}** ({v['reason']}): {v['action']}.",
        "",
        f"- Pre-registration commit: `{prov.get('preregistration_git_sha')}`",
        f"- Gate run commit: `{prov.get('gate_run_git_sha')}` "
        f"(tool/config dirty: {prov.get('gate_run_tool_dirty')})",
        f"- Review config sha256: `{prov.get('review_config_sha256')}`",
        "- Batches: "
        + ", ".join(f"{b['source']} ({b['cases']})" for b in result["batches"]),
        f"- Rechecked cases ({len(result['rechecked_cases'])}): "
        + ", ".join(result["rechecked_cases"]),
        f"- Known non-timing failures: {result['known_failures']}",
        "",
        "| | Raw labels | Final (recheck merged) |",
        "|---|---|---|",
    ]
    for key in (
        "labels",
        "outcomes",
        "timing_failures",
        "non_timing_failures",
        "x0_unknown_in_bracket",
        "x0_unknown_out_of_bracket",
        "directions",
    ):
        lines.append(f"| {key} | {raw[key]} | {final[key]} |")
    lines.append(
        f"| empirical X (all cases) | {raw['window']['empirical_x_s']} s "
        f"| {final['window']['empirical_x_s']} s |"
    )
    lines += _verdict_md(v, bias, window)
    if "decoy_check" in result:
        d = result["decoy_check"]
        lines += [
            "",
            f"Decoy consistency (not scored): {d['decoys']} decoys, "
            f"{len(d['changed'])} relabelled {d['changed']}.",
        ]
    lines += _cases_md(result["cases"])
    return "\n".join(lines) + "\n"


def _verdict_md(
    v: dict[str, Any], bias: dict[str, Any], window: dict[str, Any]
) -> list[str]:
    return [
        "",
        "## Gate conditions",
        "",
        f"1. Timing failures (merged B + C): {len(v['condition_1_timing_failures'])} "
        f"{v['condition_1_timing_failures']}.",
        f"2. Timing failures among {v['condition_2_determinable_cases']} "
        "timing-attributable cases with a determinable label and an X = 0 state: "
        f"{len(v['condition_2_timing_failures'])} {v['condition_2_timing_failures']}. "
        "Redundant: these failures are a subset of condition 1, so condition 1 "
        "implies condition 2; no separate statistic.",
        f"   Timing-attributable empirical X: {window['empirical_x_s']} s "
        f"(wrong up to {window['max_distance_wrong_s']} s, indeterminate up to "
        f"{window['max_distance_indeterminate_s']} s).",
        "",
        f"Bias test: {bias['timing_failures']} timing failures, counts "
        f"{bias['counts']}; needs >= {bias['min_failures']} and >= "
        f"{bias['min_share']:.0%} in one direction; dominant "
        f"{bias['dominant_direction']} ({bias['dominant_share']}); systematic bias: "
        f"{bias['systematic_bias']}.",
    ]


def _cases_md(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "",
        "## All cases",
        "",
        "| Case | Recording | Slot | Transition | Bracket (s) | Raw | Recheck | Final "
        "| Source | X=0 | Outcome | Failure | Direction |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        failure = r["failure_kind"] or ""
        if r["known_cause"]:
            failure += f" ({r['known_cause']})"
        lines.append(
            f"| {r['case_id']} | {r['recording']} | {r['slot']} | {r['transition_type']} "
            f"| {r['transition_bracket_s']} | {r['raw_reviewed']} | "
            f"{r['recheck_reviewed'] or ''} | {r['reviewed']} | {r['label_source']} "
            f"| {r['pipeline_x0']} | {r['outcome']} | {failure} | {r['direction']} |"
        )
    return lines


# --- cli ------------------------------------------------------------------


def _load(imports: list[Path]) -> dict[str, ReviewTimelineDataset]:
    out = {}
    for import_dir in imports:
        ds = ReviewTimelineDataset.model_validate_json(
            (import_dir / "review_timeline.json").read_text("utf-8")
        )
        out[ds.source_recording_id or import_dir.name] = ds
    return out


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(required=True)
    sheet = sub.add_parser("sheet", help="Build the corpus, sheets and draft labels")
    sheet.add_argument("imports", type=Path, nargs="+")
    sheet.add_argument("--out", type=Path, required=True)
    sheet.add_argument("--flip-window", type=float, default=0.5)
    sheet.add_argument("--respawn-within", type=float, default=1.0)
    sheet.add_argument("--id-prefix", default="B", help="Case-id prefix per batch")
    sheet.add_argument(
        "--compact", action="store_true", help="BEFORE / AT / AFTER slot-only JPEG sheets"
    )
    sheet.set_defaults(func=cmd_sheet)
    recheck = sub.add_parser("recheck", help="Focused sheets for conflicting labels")
    recheck.add_argument("imports", type=Path, nargs="+")
    recheck.add_argument("--cases", type=Path, required=True)
    recheck.add_argument("--labels", type=Path, required=True)
    recheck.add_argument("--out", type=Path, required=True)
    recheck.add_argument(
        "--compact", action="store_true", help="BEFORE / AT / AFTER JPEG recheck sheets"
    )
    recheck.add_argument(
        "--known-failures", type=Path, help="Pre-registered non-timing failures to skip"
    )
    recheck.add_argument(
        "--decoy-seed",
        type=int,
        help="Mix in as many agreeing decoys under neutral IDs (writes recheck_key.json)",
    )
    recheck.set_defaults(func=cmd_recheck)
    score = sub.add_parser("score", help="Score reviewed labels")
    score.add_argument("--cases", type=Path, required=True)
    score.add_argument("--labels", type=Path, required=True)
    score.add_argument("--out", type=Path, required=True)
    score.add_argument("--allow-draft", action="store_true")
    score.set_defaults(func=cmd_score)
    gate = sub.add_parser("gate", help="Pre-registered held-out gate over batches")
    gate.add_argument(
        "--batch",
        nargs=2,
        action="append",
        required=True,
        metavar=("CASES_JSON", "LABELS_YAML"),
    )
    gate.add_argument(
        "--recheck", type=Path, help="Omit only when no case qualified for a recheck"
    )
    gate.add_argument(
        "--recheck-key", type=Path, help="recheck_key.json for decoy-mixed rechecks"
    )
    gate.add_argument("--known-failures", type=Path, required=True)
    gate.add_argument(
        "--preregistration-sha",
        required=True,
        help="Commit that recorded the gate criteria and known failures",
    )
    gate.add_argument("--out", type=Path, required=True)
    gate.set_defaults(func=cmd_gate)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
