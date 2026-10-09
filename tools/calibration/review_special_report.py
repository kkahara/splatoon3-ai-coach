#!/usr/bin/env python3
"""Calibration report for Review event-lane special and death detection.

Evaluates the production detector over confirmed labels for a grid of
representation, template scale set, cursor exclusion width, timestamp anchor
and acceptance thresholds. Recordings are split into tuning and held-out sets
by recording (never by frame). Choices are made on tuning recordings only;
held-out recordings confirm them. Validation thresholds are set by a human
from the held-out section, never derived mechanically here.

Each recording directory holds a ``review timeline-import`` output
(``review_timeline.json``) and a labels file (default ``sheet/labels.yaml``,
see ``review_special_sheet.py``). Only entries with ``status: confirmed`` and
``kind`` ``special`` / ``death`` are ground truth.

Matching is slot-aware: a prediction matches a label of the same kind and
slot within ``--match-window`` seconds (greedy by time difference).
"""

from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
import yaml

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.review.event_lane import (
    LaneObservation,
    calibrate_axis,
    events_from_observations,
    observe_frames,
    reaccept,
    select_static_samples,
)
from splatoon3_ai_coach.review.lane_glyphs import REPRESENTATIONS, UNKNOWN_ID
from splatoon3_ai_coach.review.models import (
    AxisCalibrationEvidence,
    ReviewTimelineDataset,
    ReviewTimelineSample,
)

SCALE_SETS = ((1.0,), (0.95, 1.0, 1.05), (0.9, 0.95, 1.0, 1.05, 1.1))
CURSOR_WIDTHS = (0.002, 0.004, 0.006)
ANCHORS = ("center", "left", "right")
SPECIAL_FLOORS = (0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8)
DEATH_FLOORS = (0.5, 0.55, 0.6, 0.65, 0.7, 0.75)
MARGINS = (0.02, 0.05, 0.1, 0.15)
WITHIN_NOISE_F1 = 0.02


@dataclass(frozen=True)
class Label:
    """One confirmed lane event."""

    kind: str
    slot: str
    special_id: str | None
    elapsed: float


@dataclass(frozen=True)
class Prediction:
    """One emitted detector event reduced to what matching needs."""

    kind: str
    slot: str
    special_id: str | None
    elapsed: float
    obscured: bool


@dataclass
class Recording:
    """Inputs for one labelled recording."""

    recording_id: str
    selected: list[ReviewTimelineSample]
    images: dict[str, np.ndarray]
    axis: AxisCalibrationEvidence | None
    labels: list[Label]


@dataclass
class Metrics:
    """Slot-aware detection metrics for one event kind."""

    tp: int = 0
    fp: int = 0
    fn: int = 0
    type_errors: int = 0
    slot_hits: int = 0
    slot_trials: int = 0
    abs_errors: list[float] = field(default_factory=list)
    within_2s: int = 0
    predicted: int = 0
    unknown: int = 0
    obscured: int = 0
    recovered: int = 0
    recordings: int = 0

    def add(self, other: Metrics) -> None:
        """Accumulate another recording's counts."""
        for name, value in asdict(other).items():
            if isinstance(value, list):
                getattr(self, name).extend(value)
            else:
                setattr(self, name, getattr(self, name) + value)

    def summary(self) -> dict[str, float | int | None]:
        """Report fields (rates are ``None`` when undefined)."""
        labels = self.tp + self.fn
        precision = self.tp / (self.tp + self.fp) if self.tp + self.fp else None
        recall = self.tp / labels if labels else None
        f1 = (
            2 * precision * recall / (precision + recall) if precision and recall else 0.0
        )
        per = max(1, self.recordings)
        return {
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "precision": _round(precision),
            "recall": _round(recall),
            "f1": _round(f1),
            "events_per_match": _round(self.predicted / per),
            "false_events_per_match": _round(self.fp / per),
            "slot_accuracy": _round(
                self.slot_hits / self.slot_trials if self.slot_trials else None
            ),
            "type_accuracy": _round(
                self.tp / (self.tp + self.type_errors)
                if self.tp + self.type_errors
                else None
            ),
            "timestamp_mae": _round(
                float(np.mean(self.abs_errors)) if self.abs_errors else None
            ),
            "recall_within_2s": _round(self.within_2s / labels if labels else None),
            "unknown_retained": self.unknown,
            "obscured": self.obscured,
            "recovered": self.recovered,
        }


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


def main() -> int:
    """Run the grid on tuning recordings, confirm the choice on held-out."""
    args = _parse_args()
    base = load_config(resolve_config_path(args.config)).review
    recordings = {
        path.name: load_recording(path, args.labels, base) for path in args.recordings
    }
    tuning = [recordings[r] for r in args.tuning]
    held_out = [recordings[r] for r in args.held_out]
    grid = run_grid(tuning, base, args.match_window)
    choice = choose(grid)
    held = evaluate(held_out, base, choice["config"], args.match_window)
    per_rep = {
        rep: choose([row for row in grid if row["config"]["representation"] == rep])
        for rep in REPRESENTATIONS
    }
    held_by_rep = {
        rep: evaluate(held_out, base, row["config"], args.match_window)
        for rep, row in per_rep.items()
    }
    report = {
        "special_gate_comparison": gate_comparison(
            "special", tuning, held_out, base, args.match_window
        ),
        "death_gate_comparison": gate_comparison(
            "death", tuning, held_out, base, args.match_window
        ),
        "tuning_recordings": args.tuning,
        "held_out_recordings": args.held_out,
        "match_window_seconds": args.match_window,
        "label_counts": {
            r.recording_id: _label_counts(r.labels) for r in recordings.values()
        },
        "axis": {
            r.recording_id: None if r.axis is None else r.axis.model_dump()
            for r in recordings.values()
        },
        "chosen": choice,
        "held_out": held,
        "best_per_representation": {
            rep: {"tuning": per_rep[rep], "held_out": held_by_rep[rep]}
            for rep in REPRESENTATIONS
        },
        "grid": grid,
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.out / "report.md").write_text(render_markdown(report), encoding="utf-8")
    print(f"Wrote {args.out / 'report.md'}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings", type=Path, nargs="+")
    parser.add_argument("--tuning", nargs="+", required=True)
    parser.add_argument("--held-out", nargs="+", required=True)
    parser.add_argument("--labels", default="sheet/labels.yaml")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--match-window", type=float, default=5.0)
    args = parser.parse_args()
    overlap = set(args.tuning) & set(args.held_out)
    if overlap:
        parser.error(f"recordings in both splits: {sorted(overlap)}")
    return args


def load_recording(
    directory: Path, labels_name: str, config: ReviewTimelineConfig
) -> Recording:
    """Import output, selected static frames, axis and confirmed labels."""
    dataset = ReviewTimelineDataset.model_validate_json(
        (directory / "review_timeline.json").read_text(encoding="utf-8")
    )
    selected = select_static_samples(dataset.samples, config.static_frame_count)
    images = {s.sample_id: cv2.imread(s.image_path) for s in selected}
    return Recording(
        recording_id=directory.name,
        selected=selected,
        images={k: v for k, v in images.items() if v is not None},
        axis=calibrate_axis(dataset.samples, config),
        labels=load_labels(directory / labels_name),
    )


def load_labels(path: Path) -> list[Label]:
    """Confirmed special/death labels; refuses files with no confirmed entry."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("review_status", "reviewed") != "reviewed":
        raise SystemExit(
            f"{path}: review_status is {data['review_status']!r}; "
            "labels must be human-reviewed before they are ground truth"
        )
    events = data.get("events") or []
    confirmed = [
        Label(
            kind=str(e["kind"]),
            slot=str(e["slot"]),
            special_id=e.get("special_id"),
            elapsed=float(e["elapsed"]),
        )
        for e in events
        if e.get("status") == "confirmed" and e.get("kind") in {"special", "death"}
    ]
    if not confirmed and not any(e.get("status") == "confirmed" for e in events):
        raise SystemExit(f"{path}: no confirmed labels")
    return confirmed


def _label_counts(labels: list[Label]) -> dict[str, int]:
    return {
        "special": sum(1 for x in labels if x.kind == "special"),
        "death": sum(1 for x in labels if x.kind == "death"),
    }


def run_grid(
    recordings: list[Recording], base: ReviewTimelineConfig, window: float
) -> list[dict[str, object]]:
    """Every grid point scored on ``recordings`` (expensive responses cached)."""
    rows: list[dict[str, object]] = []
    for rep, scales, cursor in itertools.product(
        REPRESENTATIONS, SCALE_SETS, CURSOR_WIDTHS
    ):
        heavy = {
            "representation": rep,
            "scales": list(scales),
            "cursor_half_width": cursor,
        }
        observed = {
            r.recording_id: observe_frames(r.images, r.selected, _config(base, heavy))
            for r in recordings
        }
        for anchor in ANCHORS:
            config = {**heavy, "anchor": anchor}
            config.update(_best_thresholds(recordings, observed, base, config, window))
            metrics = score_config(recordings, observed, base, config, window)
            rows.append({"config": config, **metrics})
    return rows


def _best_thresholds(
    recordings: list[Recording],
    observed: dict[str, list[LaneObservation]],
    base: ReviewTimelineConfig,
    config: dict[str, object],
    window: float,
) -> dict[str, float]:
    """Special and death floor/margin tuned independently (other kind at base)."""
    out: dict[str, float] = {}
    for kind, floors in (("special", SPECIAL_FLOORS), ("death", DEATH_FLOORS)):
        scored = []
        for floor, margin in itertools.product(floors, MARGINS):
            trial = {**config, f"{kind}_floor": floor, f"{kind}_margin": margin}
            metrics = score_config(recordings, observed, base, trial, window)[kind]
            scored.append(
                (float(metrics["f1"] or 0), -float(metrics["fp"] or 0), floor, margin)
            )
        _, _, floor, margin = max(scored)
        out[f"{kind}_floor"] = floor
        out[f"{kind}_margin"] = margin
    return out


def _config(
    base: ReviewTimelineConfig, config: dict[str, object]
) -> ReviewTimelineConfig:
    update: dict[str, object] = {
        "event_glyph_representation": config["representation"],
        "special_template_scales": tuple(config["scales"]),  # type: ignore[arg-type]
        "cursor_exclusion_half_width": config["cursor_half_width"],
    }
    optional = {
        "anchor": "event_timestamp_anchor",
        "special_floor": "special_match_floor",
        "special_margin": "special_match_margin",
        "death_floor": "death_match_floor",
        "death_margin": "death_match_margin",
    }
    update.update(
        {target: config[key] for key, target in optional.items() if key in config}
    )
    return base.model_copy(update=update)


def score_config(
    recordings: list[Recording],
    observed: dict[str, list[LaneObservation]],
    base: ReviewTimelineConfig,
    config: dict[str, object],
    window: float,
) -> dict[str, dict[str, float | int | None]]:
    """Summed special and death metrics over ``recordings``."""
    cfg = _config(base, config)
    totals = {"special": Metrics(), "death": Metrics()}
    for recording in recordings:
        predictions = predict(
            recording, reaccept(observed[recording.recording_id], cfg), cfg
        )
        for kind, metrics in totals.items():
            metrics.add(match(predictions, recording.labels, kind, window))
    return {kind: metrics.summary() for kind, metrics in totals.items()}


def evaluate(
    recordings: list[Recording],
    base: ReviewTimelineConfig,
    config: dict[str, object],
    window: float,
) -> dict[str, object]:
    """Metrics for one configuration, overall and per recording."""
    cfg = _config(base, config)
    observed = {
        r.recording_id: observe_frames(r.images, r.selected, cfg) for r in recordings
    }
    per_recording = {
        r.recording_id: score_config([r], observed, base, config, window)
        for r in recordings
    }
    return {
        "overall": score_config(recordings, observed, base, config, window),
        "per_recording": per_recording,
    }


def predict(
    recording: Recording,
    observations: list[LaneObservation],
    config: ReviewTimelineConfig,
) -> list[Prediction]:
    """Production events for one recording as matchable predictions."""
    if recording.axis is None:
        return []
    markers, specials = events_from_observations(
        observations, recording.images, recording.selected, recording.axis, config
    )
    out = [
        Prediction(
            "special",
            e.slot_id,
            e.special_id,
            e.elapsed_estimate,
            bool(e.cursor_obscured_sample_ids),
        )
        for e in specials
    ]
    out.extend(
        Prediction(
            "death" if m.kind == "death" else UNKNOWN_ID,
            m.player_slot_id or "",
            None,
            m.elapsed_estimate if m.elapsed_estimate is not None else -1.0,
            bool(m.cursor_obscured_sample_ids),
        )
        for m in markers
    )
    return out


def match(
    predictions: list[Prediction], labels: list[Label], kind: str, window: float
) -> Metrics:
    """Slot-aware greedy matching for one event kind."""
    metrics = Metrics(recordings=1)
    preds = [p for p in predictions if p.kind == kind and p.special_id != UNKNOWN_ID]
    metrics.unknown = sum(
        1
        for p in predictions
        if (p.kind == kind and p.special_id == UNKNOWN_ID)
        or (kind == "death" and p.kind == UNKNOWN_ID)
    )
    gold = [x for x in labels if x.kind == kind]
    metrics.predicted = len(preds)
    metrics.obscured = sum(1 for p in preds if p.obscured)
    pairs = sorted(
        (abs(p.elapsed - g.elapsed), i, j)
        for i, p in enumerate(preds)
        for j, g in enumerate(gold)
        if p.slot == g.slot and abs(p.elapsed - g.elapsed) <= window
    )
    used_p: set[int] = set()
    used_g: set[int] = set()
    for diff, i, j in pairs:
        if i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        pred, gold_event = preds[i], gold[j]
        if kind == "special" and pred.special_id != gold_event.special_id:
            metrics.type_errors += 1
            continue
        metrics.tp += 1
        metrics.abs_errors.append(diff)
        metrics.within_2s += int(diff <= 2.0)
        metrics.recovered += int(pred.obscured)
    metrics.fp = len(preds) - metrics.tp
    metrics.fn = len(gold) - metrics.tp
    _slot_accuracy(metrics, preds, gold, window)
    return metrics


def _slot_accuracy(
    metrics: Metrics, preds: list[Prediction], gold: list[Label], window: float
) -> None:
    """Among time-matched pairs (slot ignored), how often the slot agrees."""
    pairs = sorted(
        (abs(p.elapsed - g.elapsed), i, j)
        for i, p in enumerate(preds)
        for j, g in enumerate(gold)
        if abs(p.elapsed - g.elapsed) <= min(window, 2.0)
    )
    used_p: set[int] = set()
    used_g: set[int] = set()
    for _, i, j in pairs:
        if i in used_p or j in used_g:
            continue
        used_p.add(i)
        used_g.add(j)
        metrics.slot_trials += 1
        metrics.slot_hits += int(preds[i].slot == gold[j].slot)


HEAD_TO_HEAD = {
    "special": ((0.65, 0.05), (0.5, 0.15)),
    "death": ((0.6, 0.05), (0.7, 0.15)),
}
GATE_FLOORS = {"special": SPECIAL_FLOORS, "death": DEATH_FLOORS}


def gate_comparison(
    kind: str,
    tuning: list[Recording],
    held_out: list[Recording],
    base: ReviewTimelineConfig,
    window: float,
) -> list[dict[str, object]]:
    """One kind's floor x margin under each representation.

    Scales, cursor width, anchor and the other kind's gate stay at ``base``
    so only this kind's acceptance gate varies. Reported per split and per
    recording.
    """
    rows: list[dict[str, object]] = []
    everything = [*tuning, *held_out]
    for rep in REPRESENTATIONS:
        heavy = {
            "representation": rep,
            "scales": list(base.special_template_scales),
            "cursor_half_width": base.cursor_exclusion_half_width,
            "anchor": base.event_timestamp_anchor,
            "special_floor": base.special_match_floor,
            "special_margin": base.special_match_margin,
            "death_floor": base.death_match_floor,
            "death_margin": base.death_match_margin,
        }
        observed = {
            r.recording_id: observe_frames(r.images, r.selected, _config(base, heavy))
            for r in everything
        }
        for floor, margin in itertools.product(GATE_FLOORS[kind], MARGINS):
            config = {**heavy, f"{kind}_floor": floor, f"{kind}_margin": margin}
            rows.append(
                {
                    "representation": rep,
                    "floor": floor,
                    "margin": margin,
                    "tuning": score_config(tuning, observed, base, config, window),
                    "held_out": score_config(held_out, observed, base, config, window),
                    "per_recording": {
                        r.recording_id: score_config([r], observed, base, config, window)
                        for r in everything
                    },
                }
            )
    return rows


def choose(grid: list[dict[str, object]]) -> dict[str, object]:
    """Best tuning row: special F1 + death F1, then fewer false events.

    Representation ties within ``WITHIN_NOISE_F1`` resolve to whiteness, then
    to the smallest scale set.
    """

    def total(row: dict[str, object]) -> float:
        return float(row["special"]["f1"] or 0) + float(row["death"]["f1"] or 0)  # type: ignore[index]

    best = max(total(row) for row in grid)
    near = [row for row in grid if total(row) >= best - WITHIN_NOISE_F1]
    return min(
        near,
        key=lambda row: (
            row["config"]["representation"] != "whiteness",  # type: ignore[index]
            len(row["config"]["scales"]),  # type: ignore[index]
            -total(row),
            float(row["special"]["false_events_per_match"] or 0)  # type: ignore[index]
            + float(row["death"]["false_events_per_match"] or 0),  # type: ignore[index]
        ),
    )


def render_markdown(report: dict[str, object]) -> str:
    """Human-readable summary; ``report.json`` keeps the full grid."""
    lines = [
        "# Review event-lane calibration report",
        "",
        f"Tuning: {', '.join(report['tuning_recordings'])}  ",  # type: ignore[arg-type]
        f"Held-out: {', '.join(report['held_out_recordings'])}  ",  # type: ignore[arg-type]
        f"Match window: {report['match_window_seconds']} s (slot-aware)",
        "",
        "## Chosen on tuning",
        "",
        "```json",
        json.dumps(report["chosen"]["config"], indent=2),  # type: ignore[index]
        "```",
        "",
    ]
    lines += _gate_sections(report, "special")
    lines += _gate_sections(report, "death")
    lines += _metrics_table("Tuning", report["chosen"])  # type: ignore[arg-type]
    lines += _metrics_table("Held-out", report["held_out"]["overall"])  # type: ignore[index]
    lines += ["## Best per representation (tuning choice, held-out check)", ""]
    for rep, row in report["best_per_representation"].items():  # type: ignore[union-attr]
        held = row["held_out"]["overall"]
        lines.append(
            f"- **{rep}**: tuning special F1 {row['tuning']['special']['f1']}, "
            f"death F1 {row['tuning']['death']['f1']}; held-out special F1 "
            f"{held['special']['f1']}, death F1 {held['death']['f1']}"
        )
    lines += [
        "",
        "Validation thresholds (`min_special_*`) are set by hand from the",
        "held-out section with a written rationale, not copied from tuning.",
        "",
    ]
    return "\n".join(lines)


def _gate_sections(report: dict[str, object], kind: str) -> list[str]:
    """Head-to-head and full grids for one kind's acceptance gate."""
    rows = report[f"{kind}_gate_comparison"]  # type: ignore[index]
    recordings = [*report["tuning_recordings"], *report["held_out_recordings"]]  # type: ignore[misc]
    tuning = set(report["tuning_recordings"])  # type: ignore[arg-type]
    title = kind.capitalize()
    lines = [
        f"## {title} gate: head-to-head (other settings at config values)",
        "",
        "| repr | gate | recording | split | TP | FP | FN | precision | recall |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        if (row["floor"], row["margin"]) not in HEAD_TO_HEAD[kind]:
            continue
        gate = f"{row['floor']:.2f}/{row['margin']:.2f}"
        cells = [
            (r, "tuning" if r in tuning else "held-out", row["per_recording"][r][kind])
            for r in recordings
        ]
        cells += [
            ("ALL", "tuning", row["tuning"][kind]),
            ("ALL", "held-out", row["held_out"][kind]),
        ]
        for rid, split, m in cells:
            lines.append(
                f"| {row['representation']} | {gate} | {rid} | {split} | {m['tp']} | "
                f"{m['fp']} | {m['fn']} | {m['precision']} | {m['recall']} |"
            )
    lines += [
        "",
        f"## {title} gate: full grid (FP / FN / precision / recall)",
        "",
        "| repr | floor | margin | tuning FP | tuning FN | tuning P | tuning R "
        "| held FP | held FN | held P | held R |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        t, h = row["tuning"][kind], row["held_out"][kind]
        lines.append(
            f"| {row['representation']} | {row['floor']} | {row['margin']} | "
            f"{t['fp']} | {t['fn']} | {t['precision']} | {t['recall']} | "
            f"{h['fp']} | {h['fn']} | {h['precision']} | {h['recall']} |"
        )
    return [*lines, ""]


def _metrics_table(title: str, metrics: dict[str, dict[str, object]]) -> list[str]:
    keys = list(metrics["special"].keys())
    lines = [f"### {title}", "", "| metric | special | death |", "|---|---|---|"]
    lines += [
        f"| {key} | {metrics['special'][key]} | {metrics['death'][key]} |" for key in keys
    ]
    return [*lines, ""]


if __name__ == "__main__":
    raise SystemExit(main())
