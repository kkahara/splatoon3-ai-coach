#!/usr/bin/env python3
"""Stage 3.2 before/after validation of Splat Zones score fusion.

Compares two copies of the analysis library (``--before`` fused with Stage 3.1
rules, ``--after`` re-fused with ``s3-coach refuse`` under Stage 3.2) on the
plan's gates:

- no fused score in non-Splat-Zones matches (off-mode leakage)
- no spurious fused ``1`` / ``11`` (a low value with higher values within
  ±5 s on the same side — i.e. not the real endgame)
- no fused observed value above that side's running minimum
- events and non-score snapshot fields unchanged by the re-fuse

Plus a trajectory ground-truth check: fused values at labelled frames
(``analysis/score_survey/gt/gt.json``) for library runs, counted as exact /
withheld (``None``) / wrong.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from splatoon3_ai_coach.config.paths import PROJECT_ROOT

BEFORE = PROJECT_ROOT / "analysis" / "_rescore_v2"
AFTER = PROJECT_ROOT / "analysis" / "_rescore_v3_score"
GT = PROJECT_ROOT / "analysis" / "score_survey" / "gt" / "gt.json"
OUT = PROJECT_ROOT / "analysis" / "score_survey" / "STAGE_3_2_VALIDATION.md"
SIDES = (
    ("ally_remaining", "ally_score_quality"),
    ("opponent_remaining", "opponent_score_quality"),
)
SCORE_FIELDS = {
    "ally_remaining",
    "opponent_remaining",
    "ally_score_quality",
    "opponent_score_quality",
    "ally_penalty",
    "opponent_penalty",
    "ally_penalty_quality",
    "opponent_penalty_quality",
    "evidence_ids",
}
# gt.json run prefix → library analysis directory name.
GT_RUNS = {
    "ja_kraken": "ja_mahi_mahi_kraken_2026-09-16 20-18-04",
    "ja_crab": "ja_brinewater_springs_crab_tank_2026-09-16 19-49-36",
}


def _analyses(root: Path) -> dict[str, Path]:
    """Relative analysis path → directory for every manifest under ``root``."""
    return {
        str(p.parent.relative_to(root)): p.parent
        for p in sorted(root.glob("**/vision_manifest.json"))
    }


def _mode(analysis: Path) -> str | None:
    identity = analysis / "match_identity.json"
    if not identity.is_file():
        return None
    return json.loads(identity.read_text()).get("battle_mode_id")


def _in_match(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in manifest["state_snapshots"] if s.get("match_phase") == "in_match"]


def _spurious_low(values: list[tuple[float, int | None]]) -> int:
    """Fused 1/11 with a higher value on the same side within ±5 s."""
    count = 0
    for t, v in values:
        if v not in (1, 11):
            continue
        near = [w for (u, w) in values if w is not None and abs(u - t) <= 5.0]
        if any(w > v + 5 for w in near):
            count += 1
    return count


def _running_min_violations(values: list[tuple[float, int | None, str]]) -> int:
    """Observed values above the side's running minimum of observed values."""
    running = 101
    bad = 0
    for _, v, q in values:
        if v is None or q != "observed":
            continue
        if v > running:
            bad += 1
        running = min(running, v)
    return bad


def match_stats(manifest: dict[str, Any]) -> dict[str, int]:
    """Gate counts for one fused manifest."""
    snaps = _in_match(manifest)
    stats = Counter[str]()
    stats["in_match_snapshots"] = len(snaps)
    for value_key, quality_key in SIDES:
        series = [(s["timestamp"], s.get(value_key), s.get(quality_key)) for s in snaps]
        stats["side_values"] += sum(1 for _, v, _ in series if v is not None)
        stats["observed"] += sum(1 for _, _, q in series if q == "observed")
        stats["rejected"] += sum(1 for _, _, q in series if q == "rejected_implausible")
        stats["spurious_1_11"] += _spurious_low([(t, v) for t, v, _ in series])
        stats["above_running_min"] += _running_min_violations(series)
    return dict(stats)


def _strip_ids(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: v for k, v in e.items() if k != "evidence_ids"} for e in events]


def _non_score_equal(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Events (ignoring evidence_ids) and non-score snapshot fields are identical.

    Event ``evidence_ids`` legitimately lose score reading ids when score
    fusion asserts nothing (non-Splat-Zones, rejected readings).
    """
    if _strip_ids(before["game_events"]) != _strip_ids(after["game_events"]):
        return False
    for a, b in zip(before["state_snapshots"], after["state_snapshots"], strict=True):
        keys = (set(a) | set(b)) - SCORE_FIELDS
        if any(a.get(k) != b.get(k) for k in keys):
            return False
    return True


def gt_check(root: Path) -> Counter[str]:
    """Fused values at GT frames: exact / withheld / wrong (per side)."""
    frames = json.loads(GT.read_text())["frames"]
    tally = Counter[str]()
    for frame_id, gt in frames.items():
        run = frame_id.removeprefix("penalty/").split("/")[0]
        if run not in GT_RUNS:
            continue
        t = float(frame_id.rsplit("/t", 1)[1].removesuffix(".png"))
        manifest = json.loads((root / GT_RUNS[run] / "vision_manifest.json").read_text())
        snap = min(manifest["state_snapshots"], key=lambda s: abs(s["timestamp"] - t))
        if abs(snap["timestamp"] - t) > 0.3 or snap.get("match_phase") != "in_match":
            tally["no_snapshot"] += 2
            continue
        for key, side in (("ally_remaining", "left"), ("opponent_remaining", "right")):
            fused = snap.get(key)
            if fused is None:
                tally["withheld"] += 1
            elif fused == gt[side]["value"]:
                tally["exact"] += 1
            else:
                tally["wrong"] += 1
    return tally


def build(before_root: Path, after_root: Path) -> dict[str, Any]:
    """Per-match before/after gate counts and totals."""
    rows: list[dict[str, Any]] = []
    for rel, after_dir in _analyses(after_root).items():
        before_dir = before_root / rel
        if not (before_dir / "vision_manifest.json").is_file():
            continue
        before = json.loads((before_dir / "vision_manifest.json").read_text())
        after = json.loads((after_dir / "vision_manifest.json").read_text())
        rows.append(
            {
                "analysis": rel,
                "mode": _mode(after_dir),
                "before": match_stats(before),
                "after": match_stats(after),
                "non_score_unchanged": _non_score_equal(before, after),
            }
        )
    return {
        "rows": rows,
        "gt_before": dict(gt_check(before_root)),
        "gt_after": dict(gt_check(after_root)),
    }


def _sum(rows: list[dict[str, Any]], which: str, key: str) -> int:
    return sum(r[which].get(key, 0) for r in rows)


def render(result: dict[str, Any]) -> str:
    """Markdown validation report."""
    rows = result["rows"]
    sz = [r for r in rows if r["mode"] == "splat_zones"]
    off = [r for r in rows if r["mode"] != "splat_zones"]
    lines = [
        "# Stage 3.2 score fusion validation (before vs after re-fuse)",
        "",
        "Generated by `tools/score_refuse_validate.py`. Before = `analysis/_rescore_v2`",
        "(Stage 3.1 fusion); after = `analysis/_rescore_v3_score` (copy re-fused with",
        "`refuse` under Stage 3.2). Stored detector readings are identical in both.",
        "",
        "## Gates",
        "",
        "| Gate | Before | After |",
        "|------|---:|---:|",
        _gate(f"Non-Splat-Zones side-values ({len(off)} matches)", off, "side_values"),
        _gate(
            f"Fused 1/11 below a nearby value ({len(sz)} SZ matches)", sz, "spurious_1_11"
        ),
        _gate("Observed values above running minimum", sz, "above_running_min"),
        _gate("Splat Zones side-values asserted", sz, "side_values"),
        _gate("Splat Zones values rejected as implausible", sz, "rejected"),
        f"| Matches with events / non-score state changed | — | "
        f"{sum(1 for r in rows if not r['non_score_unchanged'])} of {len(rows)} |",
        "",
        "Reading the gates:",
        "",
        "- *Spurious 1/11* is an upper bound: a fused 1/11 with a value > v+5 on",
        "  the same side within ±5 s. Residual after-flags were inspected: steady",
        "  countdowns (18→11 over 4.5 s, 7→1 over 3.5 s) near knockout / match end,",
        "  not contradicted later — consistent with real endgame values.",
        "- *Above running minimum* is 0 after by construction (the backward",
        "  retraction pass removes values contradicted by later observations); the",
        "  independent check is the trajectory ground truth below.",
        "- Event `evidence_ids` are ignored in the unchanged check: they drop score",
        "  reading ids when score fusion asserts nothing (non-Splat-Zones).",
        "",
        "## Trajectory ground truth (fused value at labelled frames)",
        "",
        f"Runs: {', '.join(f'`{v}`' for v in GT_RUNS.values())}. Per side.",
        "",
        "| | exact | withheld | wrong |",
        "|---|---:|---:|---:|",
    ]
    for label in ("before", "after"):
        gt = result[f"gt_{label}"]
        counts = " | ".join(str(gt.get(k, 0)) for k in ("exact", "withheld", "wrong"))
        lines.append(f"| {label} | {counts} |")
    lines += [
        "",
        "## Per match",
        "",
        "| Analysis | Mode | 1/11 before→after | "
        "above-min before→after | values before→after | rejected |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for r in rows:
        b, a = r["before"], r["after"]
        lines.append(
            f"| `{r['analysis'][:48]}` | {r['mode']} | "
            f"{b.get('spurious_1_11', 0)}→{a.get('spurious_1_11', 0)} | "
            f"{b.get('above_running_min', 0)}→{a.get('above_running_min', 0)} | "
            f"{b.get('side_values', 0)}→{a.get('side_values', 0)} | "
            f"{a.get('rejected', 0)} |"
        )
    return "\n".join(lines) + "\n"


def _gate(label: str, rows: list[dict[str, Any]], key: str) -> str:
    return f"| {label} | {_sum(rows, 'before', key)} | {_sum(rows, 'after', key)} |"


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, default=BEFORE)
    parser.add_argument("--after", type=Path, default=AFTER)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    result = build(args.before, args.after)
    args.out.write_text(render(result), encoding="utf-8")
    args.out.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
