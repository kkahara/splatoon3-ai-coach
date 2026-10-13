#!/usr/bin/env python3
"""Does each Review timeline match its declared gameplay video? (read-only)

For every Review import ``i`` and gameplay analysis ``j`` this maps Review
death glyph remaining-clock values into gameplay ``j``'s video time with
``j``'s own frozen calibration midpoint (``video = c_mid - r``), then scores
the share of gameplay roster-X onsets that have a glyph within ``TOL_S`` on
the same HUD side. Scores are reported at shift 0 and at the best shift in
``[-MAX_SHIFT_S, MAX_SHIFT_S]``. Diagnostic only: nothing is fitted back into
the alignment method, and a high off-diagonal score is evidence about pair
identity, never a re-pairing.

Run with ``PYTHONPATH=tools/diagnostics``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
from review_gameplay_alignment import _remaining, roster_x_onsets

from splatoon3_ai_coach.alignment.review_alignment import align_pair, load_pair
from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.paths import default_config_path

TOL_S = 1.0
MAX_SHIFT_S = 30.0
STEP_S = 0.1


def main() -> int:
    """Score every Review x gameplay combination and write the matrix."""
    args = _parse_args()
    config = load_config(default_config_path())
    labels = [r.name for r, _ in args.pair]
    reviews = {r.name: _review_glyphs(r, g, config) for r, g in args.pair}
    games = {r.name: _gameplay(r, g, config) for r, g in args.pair}
    rows = []
    for i in labels:
        for j in labels:
            rows.append({"review": i, "gameplay": j} | _score(reviews[i], games[j]))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "identity.json").write_text(json.dumps(rows, indent=2) + "\n", "utf-8")
    (args.out / "identity.md").write_text(_render(rows, labels), "utf-8")
    print(_render(rows, labels))
    return 0


def _review_glyphs(review: Path, gameplay: Path, config: Any) -> dict[str, Any]:
    """Review death glyph remaining values per HUD side, from the declared pair."""
    ds, *_ = load_pair(review, gameplay)
    ally = ds.team_identity.ally_row
    out: dict[str, list[float]] = {"ally": [], "opponent": []}
    for m in ds.match_history_events:
        if m.kind != "death" or m.team_row not in ("top", "bottom") or ally is None:
            continue
        r = _remaining(ds, m)
        if r is not None:
            out["ally" if m.team_row == ally else "opponent"].append(r)
    return out


def _gameplay(review: Path, gameplay: Path, config: Any) -> dict[str, Any]:
    """Own-pair calibration midpoint and roster-X onset midpoints per side."""
    ds, manifest, mode, _, _ = load_pair(review, gameplay)
    c_lo, c_hi = align_pair(ds, manifest, mode, config).calibration.c_interval
    onsets, _ = roster_x_onsets(manifest, config.alignment.max_onset_bracket_seconds)
    out: dict[str, Any] = {"c_mid": (c_lo + c_hi) / 2, "ally": [], "opponent": []}
    for team, (a, b) in onsets:
        out[team].append((a + b) / 2)
    return out


def _score(review: dict[str, Any], game: dict[str, Any]) -> dict[str, Any]:
    shifts = np.arange(-MAX_SHIFT_S, MAX_SHIFT_S + STEP_S / 2, STEP_S)
    hits = np.zeros_like(shifts)
    total = 0
    for side in ("ally", "opponent"):
        glyph_t = np.array([game["c_mid"] - r for r in review[side]])
        onset_t = np.array(game[side])
        total += len(onset_t)
        if not len(glyph_t) or not len(onset_t):
            continue
        for k, s in enumerate(shifts):
            d = np.abs(onset_t[:, None] - (glyph_t[None, :] + s)).min(axis=1)
            hits[k] += int((d <= TOL_S).sum())
    zero = int(np.argmin(np.abs(shifts)))
    best = int(np.argmax(hits))

    def share(h: float) -> float | None:
        return round(float(h) / total, 3) if total else None

    return {
        "onsets": total,
        "glyphs": len(review["ally"]) + len(review["opponent"]),
        "share_at_0": share(hits[zero]),
        "best_shift_s": round(float(shifts[best]), 1),
        "share_at_best": share(hits[best]),
    }


def _render(rows: list[dict[str, Any]], labels: list[str]) -> str:
    lines = [
        "# Review x gameplay identity (read-only)",
        "",
        f"Share of roster-X onsets with a same-side Review death glyph within "
        f"{TOL_S} s. Cell: share at shift 0 / best share @ best shift.",
        "",
        "| Review \\ gameplay | " + " | ".join(labels) + " |",
        "|---" * (len(labels) + 1) + "|",
    ]
    by = {(r["review"], r["gameplay"]): r for r in rows}
    for i in labels:
        cells = []
        for j in labels:
            r = by[(i, j)]
            cell = f"{r['share_at_0']} / {r['share_at_best']} @ {r['best_shift_s']:+.1f}"
            cells.append(f"**{cell}**" if i == j else cell)
        lines.append(f"| {i} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pair", nargs=2, type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
