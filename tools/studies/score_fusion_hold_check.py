#!/usr/bin/env python3
"""Offline Stage 3.1 hold validation against Stage 2 trajectories.

Replays frozen JSONL through ``fuse_score_sides_at`` (same rules as production
fusion). Does not modify ScoreDetector or invent values.

Reports separately:

- unchanged hold-candidate gaps (≤ hold window): recovery success
- changed hold-candidate gaps: stale-value exposure median/max
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from typing import Any

from loguru import logger

from splatoon3_ai_coach.config.models import ScoreDetectorConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.models import ScoreReading, ScoreSideReading
from splatoon3_ai_coach.vision.state import _ScoreSideMemory, fuse_score_sides_at

TRAJ_DIR = PROJECT_ROOT / "analysis" / "score_survey" / "trajectory_study"
DEFAULT_HOLD = 2.0


def _side(value: int | None, visible: bool) -> ScoreSideReading:
    return ScoreSideReading(
        value=value,
        digit_scores=[0.9] if visible and value is not None else [],
        visible=visible,
    )


def _reading_from_sample(sample: dict[str, Any]) -> ScoreReading:
    left = sample["left"]
    right = sample["right"]
    return ScoreReading(
        left=_side(left.get("value"), bool(left.get("visible"))),
        right=_side(right.get("value"), bool(right.get("visible"))),
        confidence=float(sample.get("confidence") or 0.0),
    )


def replay_run(
    samples: list[dict[str, Any]],
    *,
    max_hold_seconds: float,
    min_usable_confidence: float,
) -> list[dict[str, Any]]:
    """Return fused per-timestamp ally/opponent series."""
    ally = _ScoreSideMemory()
    opp = _ScoreSideMemory()
    out: list[dict[str, Any]] = []
    for sample in samples:
        reading = _reading_from_sample(sample)
        usable = float(sample.get("confidence") or 0.0) >= min_usable_confidence
        a, aq, o, oq, _, ally, opp = fuse_score_sides_at(
            timestamp=float(sample["t"]),
            match_phase="in_match",
            reading=reading,
            reading_usable=usable,
            evidence_id=f"t{sample['t']}",
            max_hold_seconds=max_hold_seconds,
            ally_mem=ally,
            opponent_mem=opp,
        )
        out.append(
            {
                "t": float(sample["t"]),
                "ally_remaining": a,
                "ally_score_quality": aq,
                "opponent_remaining": o,
                "opponent_score_quality": oq,
                "raw_left": sample["left"].get("value")
                if sample["left"].get("visible")
                else None,
                "raw_right": sample["right"].get("value")
                if sample["right"].get("visible")
                else None,
            }
        )
    return out


def _fused_at(fused: list[dict[str, Any]], t: float) -> dict[str, Any] | None:
    """Exact timestamp match, else nearest sample."""
    if not fused:
        return None
    for row in fused:
        if abs(float(row["t"]) - t) < 1e-9:
            return row
    return min(fused, key=lambda row: abs(float(row["t"]) - t))


def _count_invented(fused: list[dict[str, Any]]) -> int:
    """Held values must equal the prior observed value — never mid-gap invents."""
    last_obs_ally: int | None = None
    last_obs_opp: int | None = None
    invented = 0
    for row in fused:
        if row["ally_score_quality"] == "observed":
            last_obs_ally = row["ally_remaining"]
        elif row["ally_score_quality"] == "held":
            if row["ally_remaining"] != last_obs_ally:
                invented += 1
        if row["opponent_score_quality"] == "observed":
            last_obs_opp = row["opponent_remaining"]
        elif row["opponent_score_quality"] == "held":
            if row["opponent_remaining"] != last_obs_opp:
                invented += 1
    return invented


def evaluate_gaps(
    fused: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    *,
    max_hold_seconds: float,
) -> dict[str, Any]:
    """Score hold-candidate gaps against fused series."""
    within = [
        g
        for g in gaps
        if g.get("hold_candidate")
        and float(g["gap_seconds"]) <= max_hold_seconds + 1e-9
    ]
    unchanged = [g for g in within if not g.get("changed_during_gap")]
    changed = [g for g in within if g.get("changed_during_gap")]

    recovered = 0
    for g in unchanged:
        mid = (float(g["gap_start"]) + float(g["gap_end"])) / 2.0
        # Prefer a sample strictly inside the invisible stretch.
        interior = [
            row
            for row in fused
            if float(g["gap_start"]) - 1e-9
            <= float(row["t"])
            <= float(g["gap_end"]) + 1e-9
            and row["raw_left"] is None
            and row["raw_right"] is None
        ]
        f = interior[len(interior) // 2] if interior else _fused_at(fused, mid)
        if f is None:
            continue
        ok_left = True
        ok_right = True
        if g.get("last_visible_left") is not None:
            ok_left = (
                f["ally_remaining"] == g["last_visible_left"]
                and f["ally_score_quality"] == "held"
            )
        if g.get("last_visible_right") is not None:
            ok_right = (
                f["opponent_remaining"] == g["last_visible_right"]
                and f["opponent_score_quality"] == "held"
            )
        if ok_left and ok_right:
            recovered += 1

    stale_exposures: list[float] = []
    for g in changed:
        start = float(g["gap_start"])
        end = float(g["gap_end"])
        first_left = g.get("first_visible_left")
        first_right = g.get("first_visible_right")
        last_left = g.get("last_visible_left")
        last_right = g.get("last_visible_right")
        exposure_end = end
        for row in fused:
            t = float(row["t"])
            if t < start - 1e-9:
                continue
            if t > end + 1e-9:
                break
            # Stale ends when fusion no longer shows the pre-gap value
            # (unknown clear, or observed new value on a changed side).
            left_changed = (
                last_left is not None
                and first_left is not None
                and last_left != first_left
            )
            right_changed = (
                last_right is not None
                and first_right is not None
                and last_right != first_right
            )
            left_done = True
            right_done = True
            if left_changed:
                left_done = not (
                    row["ally_remaining"] == last_left
                    and row["ally_score_quality"] in {"held", "observed"}
                )
            if right_changed:
                right_done = not (
                    row["opponent_remaining"] == last_right
                    and row["opponent_score_quality"] in {"held", "observed"}
                )
            if left_done and right_done:
                exposure_end = t
                break
        stale_exposures.append(max(0.0, round(exposure_end - start, 3)))

    return {
        "hold_seconds": max_hold_seconds,
        "hold_candidate_gaps_within_hold": len(within),
        "unchanged": len(unchanged),
        "changed": len(changed),
        "unchanged_hold_recovery": recovered,
        "unchanged_hold_recovery_rate": (
            recovered / len(unchanged) if unchanged else None
        ),
        "changed_stale_exposure_seconds": stale_exposures,
        "changed_stale_exposure_median": (
            float(median(stale_exposures)) if stale_exposures else None
        ),
        "changed_stale_exposure_max": (
            max(stale_exposures) if stale_exposures else None
        ),
        "invented_intermediate_count": _count_invented(fused),
    }


def main() -> int:
    """CLI entry."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traj-dir", type=Path, default=TRAJ_DIR)
    parser.add_argument("--hold-seconds", type=float, default=DEFAULT_HOLD)
    parser.add_argument(
        "--out",
        type=Path,
        default=TRAJ_DIR / "hold_validation_2s.json",
    )
    args = parser.parse_args()

    gaps_path = args.traj_dir / "visibility_gaps.json"
    traj_root = args.traj_dir / "trajectories"
    if not gaps_path.is_file() or not traj_root.is_dir():
        logger.error("missing Stage 2 artifacts under {}", args.traj_dir)
        return 1

    score_cfg = ScoreDetectorConfig()
    all_gaps = json.loads(gaps_path.read_text(encoding="utf-8"))
    per_run: dict[str, Any] = {}
    exposures: list[float] = []

    for jsonl in sorted(traj_root.glob("*.jsonl")):
        run = jsonl.stem
        samples = [
            json.loads(line)
            for line in jsonl.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        fused = replay_run(
            samples,
            max_hold_seconds=float(args.hold_seconds),
            min_usable_confidence=score_cfg.min_usable_confidence,
        )
        run_gaps = [g for g in all_gaps if g.get("run") == run]
        metrics = evaluate_gaps(
            fused, run_gaps, max_hold_seconds=float(args.hold_seconds)
        )
        exposures.extend(metrics["changed_stale_exposure_seconds"])
        per_run[run] = {
            k: v
            for k, v in metrics.items()
            if k != "changed_stale_exposure_seconds"
        }
        logger.info(
            "{}: within={} unchanged={} changed={} recovered={} "
            "stale_med={} stale_max={} invented={}",
            run,
            metrics["hold_candidate_gaps_within_hold"],
            metrics["unchanged"],
            metrics["changed"],
            metrics["unchanged_hold_recovery"],
            metrics["changed_stale_exposure_median"],
            metrics["changed_stale_exposure_max"],
            metrics["invented_intermediate_count"],
        )

    unchanged_total = sum(int(m["unchanged"]) for m in per_run.values())
    changed_total = sum(int(m["changed"]) for m in per_run.values())
    recovered_total = sum(int(m["unchanged_hold_recovery"]) for m in per_run.values())
    invented_total = sum(int(m["invented_intermediate_count"]) for m in per_run.values())

    summary = {
        "hold_seconds": float(args.hold_seconds),
        "hold_candidate_gaps_within_hold": unchanged_total + changed_total,
        "unchanged": unchanged_total,
        "changed": changed_total,
        "unchanged_hold_recovery": recovered_total,
        "unchanged_hold_recovery_rate": (
            recovered_total / unchanged_total if unchanged_total else None
        ),
        "changed_stale_exposure_median": (
            float(median(exposures)) if exposures else None
        ),
        "changed_stale_exposure_max": max(exposures) if exposures else None,
        "invented_intermediate_count": invented_total,
        "per_run": per_run,
        "notes": (
            "Hold retains last observation only. Changed-gap stale exposure is "
            "expected while within the hold window; it is not a fusion bug. "
            "Gap duration uses Stage 2 gap_seconds; hold age is measured from "
            "last observation (may be ~0.5s longer than gap_seconds at 2 FPS)."
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    md = args.traj_dir / "HOLD_VALIDATION.md"
    lines = [
        "# Stage 3.1 hold validation (offline)",
        "",
        f"Hold window: **{args.hold_seconds}s** (provisional production default).",
        "",
        "Replay: Stage 2 `trajectories/*.jsonl` → `fuse_score_sides_at` "
        f"(min_usable_confidence={score_cfg.min_usable_confidence}).",
        "",
        "## Aggregate",
        "",
        f"- hold_candidate gaps with duration ≤ {args.hold_seconds}s: "
        f"**{summary['hold_candidate_gaps_within_hold']}**",
        f"  - unchanged: **{unchanged_total}**",
        f"  - changed: **{changed_total}**",
        f"- unchanged → successful hold recovery: "
        f"**{recovered_total}/{unchanged_total}**"
        + (
            f" ({100.0 * recovered_total / unchanged_total:.1f}%)"
            if unchanged_total
            else ""
        ),
        f"- changed-gap stale exposure: median="
        f"**{summary['changed_stale_exposure_median']}**s "
        f"max=**{summary['changed_stale_exposure_max']}**s",
        f"- invented intermediate values during holds: **{invented_total}**",
        "",
        "Stale exposure on changed gaps means fusion temporarily surfaces the "
        "last observed value — it must not invent intermediates.",
        "",
        f"Machine-readable: `{args.out.name}`",
        "",
    ]
    md.write_text("\n".join(lines), encoding="utf-8")
    logger.info("wrote {} and {}", args.out, md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
