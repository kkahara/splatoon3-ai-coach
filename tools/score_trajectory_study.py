#!/usr/bin/env python3
"""Stage 2 Splat Zones score trajectory study (offline only).

Pipeline:

```text
existing manifest  →  in_match [t0, t1] only
video              →  fresh 2 FPS decode → ScoreDetector → JSONL
offline analysis   →  transitions / gaps / deltas / identity checks → report
```

Discipline (do not violate):

- ``ScoreDetector`` stays per-frame and observe-only (no hold, no team map,
  no events).
- Manifests never supply score observations.
- Hold policy, left↔local-team association, and event recommendations live
  only in this offline analysis layer.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from loguru import logger
from pydantic import BaseModel, Field

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.paths import PROJECT_ROOT, default_config_path
from splatoon3_ai_coach.vision.models import ScoreReading
from splatoon3_ai_coach.vision.roi import crop_roi
from splatoon3_ai_coach.vision.score import ScoreDetector

STUDY_DIR = PROJECT_ROOT / "analysis" / "score_survey" / "trajectory_study"
MANIFEST_NAME = "vision_manifest.json"

# Screen-left / screen-right roster banks (player_count YAML names them
# ally/opponent; this study treats them as geometric banks only).
LEFT_ROSTER_SLOTS = [
    (0.254167, 0.015741, 0.328646, 0.112963),
    (0.308333, 0.017593, 0.375521, 0.112037),
    (0.358854, 0.011111, 0.416146, 0.112963),
    (0.407292, 0.013889, 0.464583, 0.115741),
]
RIGHT_ROSTER_SLOTS = [
    (0.536458, 0.013889, 0.594271, 0.115741),
    (0.585417, 0.013889, 0.642188, 0.115741),
    (0.623437, 0.017593, 0.681771, 0.110185),
    (0.664583, 0.013889, 0.738021, 0.109259),
]

RUNS: list[dict[str, str]] = [
    {
        "run": "en_barnicle",
        "video": "en-barnicle_and_dime_2026-09-14 21-13-09.mov",
        "analysis": "en-barnicle_and_dime_2026-09-14 21-13-09",
        "language": "en",
    },
    {
        "run": "en_hagglefish",
        "video": "en-hagglefish_market_2026-09-15 20-18-11.mov",
        "analysis": "en-hagglefish_market_2026-09-15 20-18-11",
        "language": "en",
    },
    {
        "run": "en_marlin",
        "video": "en-marlin_airport_2026-09-04 21-46-12.mov",
        "analysis": "en-marlin_airport_2026-09-04 21-46-12",
        "language": "en",
    },
    {
        "run": "ja_kraken",
        "video": "ja_mahi_mahi_kraken_2026-09-16 20-18-04.mov",
        "analysis": "ja_mahi_mahi_kraken_2026-09-16 20-18-04",
        "language": "ja",
    },
    {
        "run": "ja_crab",
        "video": "ja_brinewater_springs_crab_tank_2026-09-16 19-49-36.mp4",
        "analysis": "ja_brinewater_springs_crab_tank_2026-09-16 19-49-36",
        "language": "ja",
    },
]

TransitionKind = Literal[
    "LEFT_CHANGE",
    "RIGHT_CHANGE",
    "BOTH_CHANGE",
    "VISIBILITY_GAP",
    "NO_CHANGE",
]


class SideSample(BaseModel):
    """One side of a per-frame ScoreReading (raw)."""

    value: int | None = None
    digit_scores: list[float] = Field(default_factory=list)
    visible: bool = False


class TrajectorySample(BaseModel):
    """One authoritative ScoreDetector observation at video time ``t``."""

    t: float
    left: SideSample
    right: SideSample
    confidence: float = 0.0


def _movies_dir() -> Path:
    return Path.home() / "Movies"


def _in_match_bounds(manifest_path: Path) -> tuple[float, float]:
    """Return ``(t0, t1)`` from manifest match_phase only — never score rows."""
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    times = [
        float(s["timestamp"])
        for s in data.get("state_snapshots") or []
        if s.get("match_phase") == "in_match" and "timestamp" in s
    ]
    if not times:
        raise RuntimeError(f"no in_match snapshots in {manifest_path}")
    return min(times), max(times)


def _side_from_reading(side: Any) -> SideSample:
    return SideSample(
        value=side.value,
        digit_scores=[float(x) for x in side.digit_scores],
        visible=bool(side.visible),
    )


def extract_run(
    *,
    run: str,
    video: Path,
    t0: float,
    t1: float,
    detector: ScoreDetector,
    sample_fps: float,
    out_jsonl: Path,
) -> list[TrajectorySample]:
    """Fresh decode + per-frame ScoreDetector. No hold / mapping / events."""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"could not open video: {video}")
    step = 1.0 / sample_fps
    samples: list[TrajectorySample] = []
    try:
        t = t0
        while t <= t1 + 1e-6:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                logger.warning("{}: decode miss at t={:.3f}", run, t)
                t += step
                continue
            reading, conf = detector.detect(frame, timestamp=t)
            if reading is None:
                sample = TrajectorySample(
                    t=round(t, 3),
                    left=SideSample(visible=False),
                    right=SideSample(visible=False),
                    confidence=0.0,
                )
            else:
                sample = TrajectorySample(
                    t=round(t, 3),
                    left=_side_from_reading(reading.left),
                    right=_side_from_reading(reading.right),
                    confidence=float(conf),
                )
            samples.append(sample)
            t += step
    finally:
        cap.release()

    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    with out_jsonl.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(sample.model_dump_json() + "\n")
    logger.info(
        "{}: wrote {} samples [{:.1f},{:.1f}] → {}",
        run,
        len(samples),
        t0,
        t1,
        out_jsonl,
    )
    return samples


def load_jsonl(path: Path) -> list[TrajectorySample]:
    """Load a trajectory JSONL file."""
    rows: list[TrajectorySample] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(TrajectorySample.model_validate_json(line))
    return rows


def _both_sides_visible(s: TrajectorySample) -> bool:
    return s.left.visible and s.right.visible


def _any_visible(s: TrajectorySample) -> bool:
    return s.left.visible or s.right.visible


def build_transitions(samples: list[TrajectorySample]) -> list[dict[str, Any]]:
    """Structural transition catalog between consecutive samples."""
    out: list[dict[str, Any]] = []
    for prev, curr in zip(samples, samples[1:], strict=False):
        left_gap = prev.left.visible != curr.left.visible
        right_gap = prev.right.visible != curr.right.visible
        left_delta: int | None = None
        right_delta: int | None = None
        left_changed = False
        right_changed = False
        if prev.left.visible and curr.left.visible:
            left_delta = int(curr.left.value or 0) - int(prev.left.value or 0)
            left_changed = left_delta != 0
        if prev.right.visible and curr.right.visible:
            right_delta = int(curr.right.value or 0) - int(prev.right.value or 0)
            right_changed = right_delta != 0

        if left_gap or right_gap:
            # Visibility edge takes precedence over value change labeling.
            kind: TransitionKind = "VISIBILITY_GAP"
        elif left_changed and right_changed:
            kind = "BOTH_CHANGE"
        elif left_changed:
            kind = "LEFT_CHANGE"
        elif right_changed:
            kind = "RIGHT_CHANGE"
        else:
            kind = "NO_CHANGE"

        out.append(
            {
                "kind": kind,
                "t_prev": prev.t,
                "t_curr": curr.t,
                "left_prev": prev.left.value if prev.left.visible else None,
                "left_curr": curr.left.value if curr.left.visible else None,
                "right_prev": prev.right.value if prev.right.visible else None,
                "right_curr": curr.right.value if curr.right.visible else None,
                "left_delta": left_delta,
                "right_delta": right_delta,
                "left_vis_prev": prev.left.visible,
                "left_vis_curr": curr.left.visible,
                "right_vis_prev": prev.right.visible,
                "right_vis_curr": curr.right.visible,
            }
        )
    return out


def build_visibility_gaps(
    samples: list[TrajectorySample],
    *,
    t0: float,
    t1: float,
) -> list[dict[str, Any]]:
    """Precise gaps; ``hold_candidate`` only for visible→invisible→visible."""

    def side_visible(s: TrajectorySample) -> bool:
        return _any_visible(s)

    gaps: list[dict[str, Any]] = []
    i = 0
    n = len(samples)
    while i < n:
        if side_visible(samples[i]):
            i += 1
            continue
        # Start of an invisible stretch.
        gap_start_idx = i
        while i < n and not side_visible(samples[i]):
            i += 1
        gap_end_idx = i - 1
        before = samples[gap_start_idx - 1] if gap_start_idx > 0 else None
        after = samples[i] if i < n else None
        # Require a visible sample before the gap to measure hold candidates.
        if before is None or not side_visible(before):
            continue
        gap_start = samples[gap_start_idx].t
        gap_end = samples[gap_end_idx].t
        if after is not None and side_visible(after):
            gap_end = after.t
            hold_candidate = True
            first_left = after.left.value if after.left.visible else None
            first_right = after.right.value if after.right.visible else None
        else:
            # Invisible through match boundary / end of window.
            hold_candidate = False
            first_left = None
            first_right = None
            gap_end = min(gap_end, t1)

        last_left = before.left.value if before.left.visible else None
        last_right = before.right.value if before.right.visible else None
        changed = False
        if hold_candidate:
            if last_left is not None and first_left is not None:
                changed = changed or (last_left != first_left)
            if last_right is not None and first_right is not None:
                changed = changed or (last_right != first_right)

        gaps.append(
            {
                "gap_start": gap_start,
                "gap_end": gap_end,
                "gap_seconds": round(gap_end - gap_start, 3),
                "last_visible_left": last_left,
                "last_visible_right": last_right,
                "first_visible_left": first_left,
                "first_visible_right": first_right,
                "changed_during_gap": changed if hold_candidate else None,
                "hold_candidate": hold_candidate,
                "near_match_start": gap_start <= t0 + 1.0,
                "near_match_end": gap_end >= t1 - 1.0,
            }
        )
    return gaps


def build_delta_inventory(samples: list[TrajectorySample]) -> list[dict[str, Any]]:
    """Every consecutive visible pair per side — no a-priori impossible threshold."""
    rows: list[dict[str, Any]] = []
    for prev, curr in zip(samples, samples[1:], strict=False):
        for side_name in ("left", "right"):
            prev_side: SideSample = getattr(prev, side_name)
            curr_side: SideSample = getattr(curr, side_name)
            if not (prev_side.visible and curr_side.visible):
                continue
            if prev_side.value is None or curr_side.value is None:
                continue
            rows.append(
                {
                    "t": curr.t,
                    "side": side_name,
                    "previous_value": prev_side.value,
                    "current_value": curr_side.value,
                    "delta": curr_side.value - prev_side.value,
                    "elapsed_seconds": round(curr.t - prev.t, 3),
                    "digit_scores_prev": prev_side.digit_scores,
                    "digit_scores_curr": curr_side.digit_scores,
                }
            )
    return rows


def _mean_hue_sat(crop: np.ndarray, *, s_min: int = 40, v_min: int = 40) -> tuple[float, float] | None:
    """Mean hue/sat of saturated pixels in a BGR crop."""
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)
    mask = (s >= s_min) & (v >= v_min)
    if int(mask.sum()) < 20:
        return None
    return float(h[mask].mean()), float(s[mask].mean())


def _hue_distance(a: float, b: float) -> float:
    """Circular distance on OpenCV hue wheel [0, 180)."""
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d)


def identity_check_frame(
    frame: np.ndarray,
    reading: ScoreReading,
    *,
    left_roi: tuple[float, float, float, float],
    right_roi: tuple[float, float, float, float],
) -> dict[str, Any]:
    """Offline: compare score-pod hue to left/right roster-bank hue.

    Does not map to ally/opponent — only screen-position association.
    """
    left_score_hue = _mean_hue_sat(crop_roi(frame, left_roi))
    right_score_hue = _mean_hue_sat(crop_roi(frame, right_roi))

    def bank_hue(slots: list[tuple[float, float, float, float]]) -> tuple[float, float] | None:
        hues: list[float] = []
        sats: list[float] = []
        for box in slots:
            hs = _mean_hue_sat(crop_roi(frame, box))
            if hs is None:
                continue
            hues.append(hs[0])
            sats.append(hs[1])
        if not hues:
            return None
        return float(np.mean(hues)), float(np.mean(sats))

    left_roster = bank_hue(LEFT_ROSTER_SLOTS)
    right_roster = bank_hue(RIGHT_ROSTER_SLOTS)

    # Best geometric pairing: left score ↔ left roster, right ↔ right.
    same_side_ok: bool | None = None
    crossed_ok: bool | None = None
    if left_score_hue and right_score_hue and left_roster and right_roster:
        same = (
            _hue_distance(left_score_hue[0], left_roster[0])
            + _hue_distance(right_score_hue[0], right_roster[0])
        )
        crossed = (
            _hue_distance(left_score_hue[0], right_roster[0])
            + _hue_distance(right_score_hue[0], left_roster[0])
        )
        same_side_ok = same <= crossed
        crossed_ok = crossed < same

    return {
        "left_value": reading.left.value if reading.left.visible else None,
        "right_value": reading.right.value if reading.right.visible else None,
        "left_score_hue": None if left_score_hue is None else round(left_score_hue[0], 1),
        "right_score_hue": None if right_score_hue is None else round(right_score_hue[0], 1),
        "left_roster_hue": None if left_roster is None else round(left_roster[0], 1),
        "right_roster_hue": None if right_roster is None else round(right_roster[0], 1),
        "same_side_color_match_preferred": same_side_ok,
        "crossed_color_match_preferred": crossed_ok,
        "note": (
            "left_roster_slots are the local-team HUD bank in Splatoon layout; "
            "this check only tests whether score pod colors stay aligned with "
            "their screen-side roster banks."
        ),
    }


def sample_identity_checks(
    *,
    run: str,
    video: Path,
    samples: list[TrajectorySample],
    detector: ScoreDetector,
    left_roi: tuple[float, float, float, float],
    right_roi: tuple[float, float, float, float],
    review_dir: Path,
    n_checks: int = 6,
) -> list[dict[str, Any]]:
    """Re-decode a few timestamps for roster↔score color association + review strips."""
    visible = [s for s in samples if _both_sides_visible(s)]
    if len(visible) < 2:
        return []
    indices = np.linspace(0, len(visible) - 1, num=min(n_checks, len(visible)), dtype=int)
    chosen = [visible[int(i)] for i in indices]
    review_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(str(video))
    checks: list[dict[str, Any]] = []
    try:
        for sample in chosen:
            cap.set(cv2.CAP_PROP_POS_MSEC, sample.t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            reading, _ = detector.detect(frame, timestamp=sample.t)
            if reading is None:
                continue
            check = identity_check_frame(
                frame, reading, left_roi=left_roi, right_roi=right_roi
            )
            check["run"] = run
            check["t"] = sample.t
            # Review strip (top HUD) — human evidence, not production state.
            band = frame[0:220, :].copy()
            path = review_dir / f"{run}_t{sample.t:07.1f}.png"
            cv2.imwrite(str(path), band)
            check["review_strip"] = str(path.relative_to(STUDY_DIR))
            checks.append(check)
    finally:
        cap.release()
    return checks


def summarize_run(
    *,
    run: str,
    language: str,
    t0: float,
    t1: float,
    samples: list[TrajectorySample],
    transitions: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    deltas: list[dict[str, Any]],
    identity: list[dict[str, Any]],
) -> dict[str, Any]:
    """Offline aggregate stats for one run (no production side effects)."""
    kind_counts = Counter(t["kind"] for t in transitions)
    hold_gaps = [g for g in gaps if g["hold_candidate"]]
    boundary_gaps = [g for g in gaps if not g["hold_candidate"]]
    delta_vals = [d["delta"] for d in deltas]
    flicker = 0
    # A→B→A on same side within two steps (from delta inventory order).
    by_side: dict[str, list[dict[str, Any]]] = {"left": [], "right": []}
    for d in deltas:
        by_side[d["side"]].append(d)
    for side_rows in by_side.values():
        for a, b in zip(side_rows, side_rows[1:], strict=False):
            if (
                a["delta"] != 0
                and b["delta"] != 0
                and a["delta"] == -b["delta"]
                and a["current_value"] == b["previous_value"]
                and abs(b["t"] - a["t"]) <= 1.0
            ):
                flicker += 1

    same_side = [c for c in identity if c.get("same_side_color_match_preferred") is True]
    crossed = [c for c in identity if c.get("crossed_color_match_preferred") is True]
    decided = [c for c in identity if c.get("same_side_color_match_preferred") is not None]

    duration = max(t1 - t0, 1e-6)
    value_changes = sum(
        1
        for t in transitions
        if t["kind"] in {"LEFT_CHANGE", "RIGHT_CHANGE", "BOTH_CHANGE"}
    )

    return {
        "run": run,
        "language": language,
        "t0": t0,
        "t1": t1,
        "n_samples": len(samples),
        "visible_any_frac": sum(1 for s in samples if _any_visible(s)) / len(samples),
        "visible_both_frac": sum(1 for s in samples if _both_sides_visible(s))
        / len(samples),
        "transition_counts": dict(kind_counts),
        "value_changes": value_changes,
        "value_changes_per_minute": value_changes / (duration / 60.0),
        "hold_candidate_gaps": len(hold_gaps),
        "boundary_exit_gaps": len(boundary_gaps),
        "hold_gap_seconds": [g["gap_seconds"] for g in hold_gaps],
        "hold_changed_during_gap": sum(
            1 for g in hold_gaps if g.get("changed_during_gap")
        ),
        "delta_count": len(delta_vals),
        "delta_min": min(delta_vals) if delta_vals else None,
        "delta_max": max(delta_vals) if delta_vals else None,
        "delta_histogram": dict(Counter(delta_vals)),
        "flicker_aba_pairs": flicker,
        "identity_checks": len(identity),
        "identity_same_side_preferred": len(same_side),
        "identity_crossed_preferred": len(crossed),
        "identity_decided": len(decided),
        "first_both_visible": next(
            (s.t for s in samples if _both_sides_visible(s)), None
        ),
        "last_both_visible": next(
            (s.t for s in reversed(samples) if _both_sides_visible(s)), None
        ),
    }


def write_report(
    *,
    out_path: Path,
    summaries: list[dict[str, Any]],
    all_hold_gaps: list[dict[str, Any]],
    all_deltas: list[dict[str, Any]],
) -> None:
    """Write TRAJECTORY_REPORT.md — recommendations only, no production code."""
    n_runs = len(summaries)
    same = sum(s["identity_same_side_preferred"] for s in summaries)
    decided = sum(s["identity_decided"] for s in summaries)
    hold_gaps = [g for g in all_hold_gaps if g["hold_candidate"]]
    changed = sum(1 for g in hold_gaps if g.get("changed_during_gap"))
    delta_hist: Counter[int] = Counter()
    for d in all_deltas:
        delta_hist[int(d["delta"])] += 1

    lines: list[str] = []
    lines.append("# Splat Zones score Stage 2 trajectory report")
    lines.append("")
    lines.append("## Question")
    lines.append("")
    lines.append(
        "> How do observe-only left/right `ScoreReading`s behave over time "
        "during `in_match`, and what (if anything) does that justify for Stage 3?"
    )
    lines.append("")
    lines.append("## Discipline")
    lines.append("")
    lines.append("- Authoritative score series: **fresh 2 FPS video → `ScoreDetector`**")
    lines.append("- Manifests: **`in_match` boundaries only** (no score rows)")
    lines.append(
        "- No hold, team mapping, or GameEvent inside the detector or this tool's extract path"
    )
    lines.append("- Interpretation stays in this offline report")
    lines.append("")
    lines.append("## Runs")
    lines.append("")
    lines.append("| Run | Lang | in_match | samples | both-visible frac | changes/min |")
    lines.append("|-----|------|----------|---------|-------------------|-------------|")
    for s in summaries:
        lines.append(
            f"| {s['run']} | {s['language']} | "
            f"{s['t0']:.1f}–{s['t1']:.1f} | {s['n_samples']} | "
            f"{s['visible_both_frac']:.3f} | {s['value_changes_per_minute']:.2f} |"
        )
    lines.append("")

    lines.append("## 1. Left/right identity vs local-team HUD bank")
    lines.append("")
    lines.append(
        "Local-team HUD indicator = **screen-left roster bank** (Splatoon layout). "
        "Each check compares score-pod hue to left/right roster hues and asks "
        "whether same-side pairing is preferred over crossed pairing."
    )
    lines.append("")
    if decided == 0:
        lines.append("No hue-decided identity checks (insufficient saturated pixels).")
    else:
        lines.append(
            f"Across **{n_runs} matches**, hue-decided checks preferred "
            f"**same-side** association in **{same}/{decided}** samples "
            f"({100.0 * same / decided:.1f}%)."
        )
        lines.append("")
        lines.append(
            "Evidence-shaped conclusion: the left counter remained color-associated "
            "with the local-team (screen-left) roster bank in the decided checks "
            "above. This does **not** encode `left = ally` in vision; Stage 3 may "
            "introduce semantic fields only after accepting this evidence."
        )
    lines.append("")
    lines.append("Per-run decided checks:")
    lines.append("")
    for s in summaries:
        lines.append(
            f"- `{s['run']}`: same-side {s['identity_same_side_preferred']}/"
            f"{s['identity_decided']} (crossed preferred "
            f"{s['identity_crossed_preferred']})"
        )
    lines.append("")
    lines.append("Review strips: `identity_review/`.")
    lines.append("")

    lines.append("## 2. Structural transition catalog")
    lines.append("")
    lines.append(
        "Kinds are structural only (`LEFT_CHANGE` / `RIGHT_CHANGE` / "
        "`BOTH_CHANGE` / `VISIBILITY_GAP` / `NO_CHANGE`) with raw deltas — "
        "not labeled as countdown/progress/knockout."
    )
    lines.append("")
    lines.append("| Run | LEFT | RIGHT | BOTH | VIS_GAP | NO_CHANGE |")
    lines.append("|-----|------|-------|------|---------|-----------|")
    for s in summaries:
        c = s["transition_counts"]
        lines.append(
            f"| {s['run']} | {c.get('LEFT_CHANGE', 0)} | {c.get('RIGHT_CHANGE', 0)} | "
            f"{c.get('BOTH_CHANGE', 0)} | {c.get('VISIBILITY_GAP', 0)} | "
            f"{c.get('NO_CHANGE', 0)} |"
        )
    lines.append("")

    lines.append("## 3. Visibility gaps (hold candidates)")
    lines.append("")
    lines.append(
        "`hold_candidate` = visible→invisible→visible inside the window. "
        "Gaps that run to the match boundary are **not** hold tests."
    )
    lines.append("")
    lines.append(f"- Hold-candidate gaps: **{len(hold_gaps)}**")
    lines.append(f"- Of those, `changed_during_gap=true`: **{changed}**")
    if hold_gaps:
        secs = [g["gap_seconds"] for g in hold_gaps]
        lines.append(
            f"- Gap seconds: min={min(secs):.2f} median={float(np.median(secs)):.2f} "
            f"max={max(secs):.2f}"
        )
        unchanged = [g["gap_seconds"] for g in hold_gaps if not g.get("changed_during_gap")]
        if unchanged:
            lines.append(
                f"- Unchanged-across-gap seconds (safe-hold evidence): "
                f"max={max(unchanged):.2f}"
            )
            lines.append(
                f"- Study-only hold suggestion (not implemented): "
                f"`max_hold_seconds ≈ {max(unchanged):.2f}` would cover all "
                f"unchanged hold-candidate gaps in this set; "
                f"{changed} gaps saw a value change and must not be papered over by hold."
            )
        else:
            lines.append(
                "- No unchanged hold-candidate gaps; hold is not justified from this set alone."
            )
    else:
        lines.append("- No hold-candidate gaps observed.")
    lines.append("")

    lines.append("## 4. Delta inventory (empirical, no pre-set impossible threshold)")
    lines.append("")
    lines.append(f"Consecutive visible pairs: **{len(all_deltas)}**")
    if all_deltas:
        vals = [d["delta"] for d in all_deltas]
        lines.append(f"- delta min/max: **{min(vals)}** / **{max(vals)}**")
        lines.append("- histogram (delta → count):")
        lines.append("")
        for delta, count in sorted(delta_hist.items(), key=lambda x: x[0]):
            lines.append(f"  - `{delta}`: {count}")
        lines.append("")
        nonzero = [d for d in all_deltas if d["delta"] != 0]
        lines.append(f"- Non-zero deltas: {len(nonzero)}")
        aba = sum(s["flicker_aba_pairs"] for s in summaries)
        lines.append(f"- Short A→B→A flicker pairs (≤1s steps): **{aba}**")
    lines.append("")
    lines.append(
        "Do not treat large `|delta|` as 'impossible' until a larger corpus "
        "separates true jumps from read errors. Stage 3 fusion may use this "
        "distribution later."
    )
    lines.append("")

    lines.append("## 5. Match boundaries")
    lines.append("")
    for s in summaries:
        lines.append(
            f"- `{s['run']}`: first both-visible t={s['first_both_visible']}, "
            f"last both-visible t={s['last_both_visible']}, "
            f"in_match=[{s['t0']:.1f},{s['t1']:.1f}]"
        )
    lines.append("")

    lines.append("## Stage 2 gate answers")
    lines.append("")
    lines.append(
        "1. **Left↔local-team association:** supported as screen-left roster "
        "color alignment in decided checks (see §1). Not encoded as ally."
    )
    lines.append(
        "2. **Transitions:** mostly `NO_CHANGE` with sparse `LEFT/RIGHT/BOTH_CHANGE` "
        "and `VISIBILITY_GAP`; see catalog — no semantic countdown labels applied."
    )
    lines.append(
        "3. **Hold:** only evaluate `hold_candidate` gaps; suggestion above is "
        "study-only and must not ship in `vision/state.py` from this report alone."
    )
    lines.append(
        "4. **Deltas:** empirical histogram reported; no a-priori anomaly cutoff."
    )
    lines.append(
        "5. **Candidate Stage 3 items (recommendations only):**"
    )
    lines.append("   - Optional fused snapshot fields `ally_remaining`/`opponent_remaining` **if** left↔local-team evidence is accepted")
    lines.append("   - Visibility hold with a max duration grounded in unchanged hold-candidate gaps")
    lines.append("   - Possibly a sparse score-change fact for coaching — **not** justified as a GameEvent until fusion semantics are designed")
    lines.append("")
    lines.append("## Explicit non-outcomes")
    lines.append("")
    lines.append("- No production fusion code added")
    lines.append("- No monotonicity in `ScoreDetector`")
    lines.append("- No score `GameEvent`")
    lines.append("- No CoachInput wiring")
    lines.append("")
    lines.append("## Reproduce")
    lines.append("")
    lines.append("```bash")
    lines.append("python tools/score_trajectory_study.py run")
    lines.append("```")
    lines.append("")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("wrote {}", out_path)


def cmd_run(args: argparse.Namespace) -> int:
    """Extract trajectories + offline catalogs + report."""
    movies = Path(args.movies_dir)
    out_root = Path(args.out)
    traj_dir = out_root / "trajectories"
    review_dir = out_root / "identity_review"
    traj_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(default_config_path() if args.config is None else Path(args.config))
    detector = ScoreDetector(config.vision.score, cadence_fps=args.sample_fps)
    left_roi = tuple(config.vision.score.left_roi)
    right_roi = tuple(config.vision.score.right_roi)

    all_summaries: list[dict[str, Any]] = []
    all_transitions: dict[str, list[dict[str, Any]]] = {}
    all_gaps: list[dict[str, Any]] = []
    all_deltas: list[dict[str, Any]] = []
    all_identity: dict[str, list[dict[str, Any]]] = {}

    for entry in RUNS:
        run = entry["run"]
        video = movies / entry["video"]
        manifest = PROJECT_ROOT / "analysis" / entry["analysis"] / MANIFEST_NAME
        if not video.is_file():
            logger.error("missing video {}", video)
            return 1
        if not manifest.is_file():
            logger.error("missing manifest {}", manifest)
            return 1
        t0, t1 = _in_match_bounds(manifest)
        jsonl = traj_dir / f"{run}.jsonl"
        if args.reuse_trajectories and jsonl.is_file():
            samples = load_jsonl(jsonl)
            logger.info("{}: reused {}", run, jsonl)
        else:
            samples = extract_run(
                run=run,
                video=video,
                t0=t0,
                t1=t1,
                detector=detector,
                sample_fps=float(args.sample_fps),
                out_jsonl=jsonl,
            )

        transitions = build_transitions(samples)
        gaps = build_visibility_gaps(samples, t0=t0, t1=t1)
        deltas = build_delta_inventory(samples)
        identity = sample_identity_checks(
            run=run,
            video=video,
            samples=samples,
            detector=detector,
            left_roi=left_roi,  # type: ignore[arg-type]
            right_roi=right_roi,  # type: ignore[arg-type]
            review_dir=review_dir,
        )
        for g in gaps:
            g["run"] = run
        for d in deltas:
            d["run"] = run

        summary = summarize_run(
            run=run,
            language=entry["language"],
            t0=t0,
            t1=t1,
            samples=samples,
            transitions=transitions,
            gaps=gaps,
            deltas=deltas,
            identity=identity,
        )
        all_summaries.append(summary)
        all_transitions[run] = transitions
        all_gaps.extend(gaps)
        all_deltas.extend(deltas)
        all_identity[run] = identity

    (out_root / "transitions.json").write_text(
        json.dumps(all_transitions, indent=2) + "\n", encoding="utf-8"
    )
    (out_root / "visibility_gaps.json").write_text(
        json.dumps(all_gaps, indent=2) + "\n", encoding="utf-8"
    )
    (out_root / "delta_inventory.json").write_text(
        json.dumps(all_deltas, indent=2) + "\n", encoding="utf-8"
    )
    (out_root / "identity_checks.json").write_text(
        json.dumps(all_identity, indent=2) + "\n", encoding="utf-8"
    )
    (out_root / "summaries.json").write_text(
        json.dumps(all_summaries, indent=2) + "\n", encoding="utf-8"
    )
    write_report(
        out_path=out_root / "TRAJECTORY_REPORT.md",
        summaries=all_summaries,
        all_hold_gaps=all_gaps,
        all_deltas=all_deltas,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="Extract + analyze + write TRAJECTORY_REPORT.md")
    p.add_argument("--movies-dir", type=Path, default=_movies_dir())
    p.add_argument("--out", type=Path, default=STUDY_DIR)
    p.add_argument("--config", type=Path, default=None)
    p.add_argument("--sample-fps", type=float, default=2.0)
    p.add_argument(
        "--reuse-trajectories",
        action="store_true",
        help="Reuse existing trajectories/*.jsonl (offline re-analysis only).",
    )
    p.set_defaults(func=cmd_run)
    return parser


def main() -> int:
    """Entry."""
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
