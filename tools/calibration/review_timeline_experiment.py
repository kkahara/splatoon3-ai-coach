#!/usr/bin/env python3
"""One-factor Review timeline experiments (scale, cursor, anchor, overtime fit).

Re-imports stored frames per variant and reports, per recording, axis-fit
accuracy, death timestamps vs roster-lamp bounds (independent of axis and
anchor), special timestamp shift vs baseline, event-gate metrics and the GO
gate. Protocol: ``analysis/review_validation/experiments/PROTOCOL.md``.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from statistics import mean
from typing import Any

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.review.clock import is_overtime_display
from splatoon3_ai_coach.review.event_lane import calibrate_axis
from splatoon3_ai_coach.review.models import ReviewTimelineDataset
from splatoon3_ai_coach.review.timeline_pipeline import import_timeline
from splatoon3_ai_coach.review.validation import load_labels, validate_artifact

RECORDINGS = {
    "21-49-20": "tuning",
    "21-23-30": "tuning",
    "21-56-52": "held_out",
    "21-41-07": "held_out",
}
VARIANTS: list[tuple[str, str, dict[str, Any]]] = [
    ("baseline", "baseline", {}),
    ("scale", "scale_0.95-1.05", {"special_template_scales": [0.95, 1.0, 1.05]}),
    (
        "scale",
        "scale_0.90-1.10",
        {"special_template_scales": [0.9, 0.95, 1.0, 1.05, 1.1]},
    ),
    ("cursor", "cursor_0.002", {"cursor_exclusion_half_width": 0.002}),
    ("cursor", "cursor_0.006", {"cursor_exclusion_half_width": 0.006}),
    ("anchor", "anchor_left", {"event_timestamp_anchor": "left"}),
    ("anchor", "anchor_right", {"event_timestamp_anchor": "right"}),
    ("overtime", "overtime_fit", {"axis_include_overtime": True}),
]
AXIS_INLIER_SECONDS = 5.0
REFERENCE_MAX_GAP = 5.0
MATCH_SECONDS = 3.0


def main() -> int:
    """Run every variant on every recording and write report.json / report.md."""
    args = _parse_args()
    base = load_config(default_config_path()).review
    go_labels = load_labels(args.go_labels)
    results: dict[str, dict[str, Any]] = {}
    for rid in RECORDINGS:
        source = args.imports / rid
        runs = {
            name: _import(source, base.model_copy(update=update))
            for _, name, update in VARIANTS
        }
        references = lamp_references(runs["baseline"])
        labels = load_labels(args.labels_root / rid / "gate_labels.yaml")
        results[rid] = {
            name: _metrics(
                runs[name],
                runs["baseline"],
                references,
                labels,
                go_labels if rid == "21-23-30" else None,
                base.model_copy(update=update),
            )
            for _, name, update in VARIANTS
        }
        print(f"{rid}: {len(references)} lamp-bounded deaths")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(results, indent=2) + "\n", "utf-8")
    (args.out / "report.md").write_text(render(results), "utf-8")
    return 0


def _import(source: Path, config: ReviewTimelineConfig) -> ReviewTimelineDataset:
    with tempfile.TemporaryDirectory() as tmp:
        return import_timeline(
            source / "timeline_samples",
            config=config,
            manifest_path=source / "timeline_samples_manifest.yaml",
            output_dir=Path(tmp),
        )


def _metrics(
    ds: ReviewTimelineDataset,
    base: ReviewTimelineDataset,
    references: list[dict[str, Any]],
    labels: Any,
    go_labels: Any,
    config: ReviewTimelineConfig,
) -> dict[str, Any]:
    gate = validate_artifact(ds, labels)
    out: dict[str, Any] = {
        "axis": axis_metrics(ds, config),
        "deaths": death_metrics(ds, references),
        "special_shift": special_shift(ds, base),
        "special_checks_pass": all(
            v for k, v in gate.checks.items() if k.startswith("special")
        ),
        "gate": {
            k: getattr(gate.metrics, k)
            for k in (
                "special_candidate_precision",
                "special_candidate_recall",
                "special_type_accuracy",
                "special_false_claims",
                "death_slot_accuracy",
            )
        },
        "unknown_specials": sum(e.special_id == "unknown" for e in ds.special_events),
    }
    if go_labels is not None:
        out["go"] = validate_artifact(ds, go_labels).go
    return out


def axis_metrics(
    ds: ReviewTimelineDataset, config: ReviewTimelineConfig
) -> dict[str, Any]:
    """In-sample, cross-validated regulation and overtime residuals."""
    axis = calibrate_axis(ds.samples, config)
    if axis is None:
        return {"fitted": False}
    regulation = [
        s for s in ds.samples if _x(s) is not None and (s.elapsed_seconds or -1) >= 0
    ]
    overtime = [
        s
        for s in ds.samples
        if _x(s) is not None
        and s.elapsed_seconds is not None
        and is_overtime_display(s.displayed_clock)
    ]
    folds = []
    for parity in (0, 1):
        train = [s for s in ds.samples if s.capture_index % 2 == parity]
        test = [s for s in regulation if s.capture_index % 2 != parity]
        fold_axis = calibrate_axis(train, config)
        if fold_axis is not None:
            folds.extend(_residuals(fold_axis, test))
    return {
        "fitted": True,
        "slope": axis.slope,
        "intercept": axis.intercept,
        "points": axis.point_count,
        "in_sample_mae": axis.mean_absolute_error_seconds,
        "regulation_cv_mae": _inlier_mean(folds),
        "overtime_mae": _inlier_mean(_residuals(axis, overtime)),
        "overtime_samples": len(overtime),
    }


def _x(sample: Any) -> float | None:
    return sample.cursor.x_norm


def _residuals(axis: Any, samples: list[Any]) -> list[float]:
    return [
        abs(axis.slope * _x(s) + axis.intercept - float(s.elapsed_seconds))
        for s in samples
    ]


def _inlier_mean(values: list[float]) -> float | None:
    """Mean residual over reads within 5 s (digit misreads are not fit error)."""
    kept = [v for v in values if v <= AXIS_INLIER_SECONDS]
    return round(mean(kept), 4) if kept else None


def lamp_references(ds: ReviewTimelineDataset) -> list[dict[str, Any]]:
    """Per baseline death marker: [first_dead, last_alive] from roster lamps."""
    lamps: dict[str, list[tuple[int, str]]] = {}
    for state in ds.second_states:
        for slot in state.slots:
            if slot.state.value in ("alive", "dead"):
                lamps.setdefault(slot.slot_id, []).append(
                    (state.elapsed_second, slot.state.value)
                )
    references = []
    for marker in ds.match_history_events:
        if marker.kind != "death" or marker.player_slot_id is None:
            continue
        t = float(marker.elapsed_estimate)
        best = _nearest_transition(lamps.get(marker.player_slot_id, []), t)
        if best is not None:
            references.append(
                {"slot": marker.player_slot_id, "t0": t, "lo": best[0], "hi": best[1]}
            )
    return references


def _nearest_transition(lamps: list[tuple[int, str]], t: float) -> tuple[int, int] | None:
    ordered = sorted(lamps, reverse=True)
    pairs = [
        (lo, hi)
        for (hi, a), (lo, b) in zip(ordered, ordered[1:], strict=False)
        if a == "alive" and b == "dead" and hi - lo <= REFERENCE_MAX_GAP
    ]
    scored = [(max(lo - t, t - hi, 0.0), lo, hi) for lo, hi in pairs]
    scored = [s for s in scored if s[0] <= REFERENCE_MAX_GAP]
    return (min(scored)[1], min(scored)[2]) if scored else None


def death_metrics(
    ds: ReviewTimelineDataset, references: list[dict[str, Any]]
) -> dict[str, Any]:
    """Containment of death estimates in lamp intervals (independent reference)."""
    contained, outside, offsets, matched = 0, [], [], 0
    for ref in references:
        estimates = [
            float(m.elapsed_estimate)
            for m in ds.match_history_events
            if m.kind == "death"
            and m.player_slot_id == ref["slot"]
            and abs(float(m.elapsed_estimate) - ref["t0"]) <= MATCH_SECONDS
        ]
        if not estimates:
            continue
        t = min(estimates, key=lambda e: abs(e - ref["t0"]))
        matched += 1
        distance = max(ref["lo"] - t, t - ref["hi"], 0.0)
        contained += distance == 0
        outside.append(distance)
        offsets.append(t - (ref["lo"] + ref["hi"]) / 2)
    return {
        "references": len(references),
        "matched": matched,
        "contained": contained,
        "mean_outside_s": round(mean(outside), 4) if outside else None,
        "mean_offset_s": round(mean(offsets), 4) if offsets else None,
    }


def special_shift(
    ds: ReviewTimelineDataset, base: ReviewTimelineDataset
) -> dict[str, Any]:
    """Signed shift of matched specials vs baseline (no accuracy claim)."""
    shifts = []
    for event in base.special_events:
        same = [
            e.elapsed_estimate - event.elapsed_estimate
            for e in ds.special_events
            if e.slot_id == event.slot_id
            and abs(e.elapsed_estimate - event.elapsed_estimate) <= MATCH_SECONDS
        ]
        if same:
            shifts.append(min(same, key=abs))
    return {
        "matched": len(shifts),
        "of": len(base.special_events),
        "mean_shift_s": round(mean(shifts), 4) if shifts else None,
    }


def render(results: dict[str, dict[str, Any]]) -> str:
    """Per-factor, per-recording tables."""
    lines = ["# Review timeline one-factor experiments", "", "Protocol: PROTOCOL.md", ""]
    header = (
        "| Recording | Split | Variant | Axis pts | In-sample MAE | Reg CV MAE | OT MAE "
        "| Deaths in lamp bounds | Outside s | Offset s | Special shift s "
        "| Recall | Type | False claims | Unknown | Checks | GO |"
    )
    for factor in dict.fromkeys(f for f, _, _ in VARIANTS[1:]):
        names = ["baseline"] + [n for f, n, _ in VARIANTS if f == factor]
        lines += [f"## {factor}", "", header, "|" + "---|" * 17]
        for rid, split in RECORDINGS.items():
            for name in names:
                lines.append(_row(rid, split, name, results[rid][name]))
        lines.append("")
    return "\n".join(lines) + "\n"


def _row(rid: str, split: str, name: str, m: dict[str, Any]) -> str:
    a, d, g = m["axis"], m["deaths"], m["gate"]
    cells = [
        rid,
        split,
        name,
        a.get("points"),
        _f(a.get("in_sample_mae")),
        _f(a.get("regulation_cv_mae")),
        _f(a.get("overtime_mae")),
        f"{d['contained']}/{d['matched']} (of {d['references']})",
        _f(d["mean_outside_s"]),
        _f(d["mean_offset_s"]),
        _f(m["special_shift"]["mean_shift_s"]),
        _f(g["special_candidate_recall"]),
        _f(g["special_type_accuracy"]),
        g["special_false_claims"],
        m["unknown_specials"],
        "pass" if m["special_checks_pass"] else "FAIL",
        {True: "GO", False: "NO-GO"}.get(m.get("go"), "-"),
    ]
    return "| " + " | ".join(str(c) for c in cells) + " |"


def _f(value: Any) -> str:
    return "-" if value is None else f"{value:.3f}"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--imports", type=Path, default=Path("analysis/review_validation/gate")
    )
    parser.add_argument(
        "--labels-root", type=Path, default=Path("analysis/review_validation/specials")
    )
    parser.add_argument(
        "--go-labels",
        type=Path,
        default=Path("analysis/review_validation/go_gate/labels.yaml"),
    )
    parser.add_argument(
        "--out", type=Path, default=Path("analysis/review_validation/experiments")
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
