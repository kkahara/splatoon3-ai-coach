#!/usr/bin/env python3
"""Study the frame-level Splat Zones ownership reading.

The study intentionally stops at ``ZoneControlReading``. It does not fuse
time, assign events, or modify coaching evidence.

Subcommands:

- ``extract``: pull full frames from Splat Zones videos. Runs with an existing
  ``vision_manifest.json`` are sampled deterministically from its fused state
  (regular in-match cadence, death/respawn, map open, penalty); runs without a
  manifest use a fixed cadence.
- ``read``: run ``read_zone_control`` over every labeled frame.
- ``compare``: confusion matrix, per-state precision/recall, exact accuracy,
  neutral false-positive rate, unknown/coverage rate, failure-mode and
  language/stage slices.

Ground truth JSON is a list of objects with ``frame_id``, ``path`` (relative to
the project root), ``state`` (``neutral`` / ``ally_control`` /
``opponent_control`` / ``unknown``), ``run``, ``language``, ``stage`` and
``tags`` (failure-mode cells such as ``penalty``, ``occlusion``, ``death``).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import cv2
from loguru import logger

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.paths import PROJECT_ROOT, default_config_path
from splatoon3_ai_coach.vision.zone_control import read_zone_control

STUDY_DIR = PROJECT_ROOT / "analysis" / "zone_control_survey"
DEFAULT_FRAMES = STUDY_DIR / "frames"
DEFAULT_GT = STUDY_DIR / "gt" / "gt.json"
DEFAULT_READINGS = STUDY_DIR / "readings.json"
DEFAULT_METRICS = STUDY_DIR / "metrics.json"
STATES = ("neutral", "ally_control", "opponent_control", "unknown")
OWNERSHIP = ("ally_control", "opponent_control")

# Calibration matrix: EN/JA, several stages and team-colour pairs.
CALIBRATION_RUNS: list[dict[str, str | None]] = [
    {"run": "en_barnicle", "video": "en-barnicle_and_dime_2026-09-14 21-13-09.mov",
     "language": "en", "stage": "barnacle_and_dime", "analysis": None},
    {"run": "en_hagglefish", "video": "en-hagglefish_market_2026-09-15 20-18-11.mov",
     "language": "en", "stage": "hagglefish_market", "analysis": None},
    {"run": "en_marlin", "video": "en-marlin_airport_2026-09-04 21-46-12.mov",
     "language": "en", "stage": "marlin_airport", "analysis": None},
    {"run": "en_mincemeat", "video": "en-2026-09-21 23-13-21.mov",
     "language": "en", "stage": "mincemeat_metalworks",
     "analysis": "en-2026-09-21 23-13-21"},
    {"run": "ja_crab", "video": "ja_brinewater_springs_crab_tank_2026-09-16 19-49-36.mp4",
     "language": "ja", "stage": "brinewater_springs",
     "analysis": "ja_brinewater_springs_crab_tank_2026-09-16 19-49-36"},
    {"run": "ja_sumper",
     "video": "ja_brinewater_springs_sumper_chump_2026-09-16 20-13-03.mov",
     "language": "ja", "stage": "brinewater_springs",
     "analysis": "ja_brinewater_springs_sumper_chump_2026-09-16 20-13-03"},
    {"run": "ja_kraken", "video": "ja_mahi_mahi_kraken_2026-09-16 20-18-04.mov",
     "language": "ja", "stage": "mahi_mahi_resort",
     "analysis": "ja_mahi_mahi_kraken_2026-09-16 20-18-04"},
    {"run": "ja_splattercolor",
     "video": "ja_mahi_mahi_resort_splattercolor_screen_2026-09-16 20-31-24.mov",
     "language": "ja", "stage": "mahi_mahi_resort",
     "analysis": "ja_mahi_mahi_resort_splattercolor_screen_2026-09-16 20-31-24"},
]
# Held-out matrix: videos never inspected while choosing ROIs/thresholds.
HOLDOUT_RUNS: list[dict[str, str | None]] = [
    {"run": "en_2248", "video": "en-2026-09-21 22-48-34.mov",
     "language": "en", "stage": "unlabeled_en_2248", "analysis": None},
    {"run": "en_2255", "video": "en-2026-09-21 22-55-33.mov",
     "language": "en", "stage": "unlabeled_en_2255", "analysis": None},
    {"run": "en_0707", "video": "en-2026-07-07 23-23-43.mov",
     "language": "en", "stage": "unlabeled_en_0707", "analysis": None},
    {"run": "en_0705", "video": "en-2026-07-05 21-30-18.mov",
     "language": "en", "stage": "unlabeled_en_0705", "analysis": None},
    {"run": "ja_inkstrike",
     "video": "ja_brinewater_springs_triple_inkstrike_2026-09-16 20-06-30.mov",
     "language": "ja", "stage": "brinewater_springs",
     "analysis": "ja_brinewater_springs_triple_inkstrike_2026-09-16 20-06-30"},
    {"run": "ja_splashdown",
     "video": "ja_brinewater_springs_triple_splashdown_2026-09-16 20-27-13.mov",
     "language": "ja", "stage": "brinewater_springs",
     "analysis": "ja_brinewater_springs_triple_splashdown_2026-09-16 20-27-13"},
    {"run": "ja_reef", "video": "ja_mahi_mahi_reef_slider_2026-09-16 20-00-03.mov",
     "language": "ja", "stage": "mahi_mahi_resort",
     "analysis": "ja_mahi_mahi_reef_slider_2026-09-16 20-00-03"},
    {"run": "ja_stamp",
     "video": "ja_mahi_mahi_resort_ultra_stamp_2026-09-16 19-49-36.mp4",
     "language": "ja", "stage": "mahi_mahi_resort",
     "analysis": "ja_mahi_mahi_resort_ultra_stamp_2026-09-16 19-49-36"},
]
# set name -> (runs, regular step seconds, event cells per run)
SAMPLE_SETS: dict[str, tuple[list[dict[str, str | None]], float, int]] = {
    "calibration": (CALIBRATION_RUNS, 12.0, 3),
    "holdout": (HOLDOUT_RUNS, 20.0, 2),
}


def _interval_starts(
    snapshots: list[dict[str, Any]], key: str, value: Any
) -> list[float]:
    """Return timestamps where ``snapshot[key] == value`` begins."""
    starts: list[float] = []
    previous = None
    for snap in snapshots:
        current = snap.get(key) == value
        if current and not previous:
            starts.append(float(snap["timestamp"]))
        previous = current
    return starts


def select_times(
    snapshots: list[dict[str, Any]],
    step_s: float = 12.0,
    event_cells: int = 3,
) -> list[tuple[float, str]]:
    """Pick deterministic study times from fused state snapshots."""
    in_match = [s for s in snapshots if s.get("match_phase") == "in_match"]
    if not in_match:
        return []
    start, end = float(in_match[0]["timestamp"]), float(in_match[-1]["timestamp"])
    picks: list[tuple[float, str]] = []
    t = start + 4.0
    while t < end - 2.0:
        picks.append((round(t, 1), "regular"))
        t += step_s
    cells = [
        ("player_lifecycle", "dead", 1.0, "death"),
        ("player_lifecycle", "countdown", 0.5, "respawn_countdown"),
        ("player_lifecycle", "respawned", 0.0, "respawn"),
        ("map_overlay_present", True, 0.5, "map_open"),
    ]
    for key, value, offset, cell in cells:
        starts = [x for x in _interval_starts(in_match, key, value) if x + offset < end]
        for x in starts[:event_cells]:
            picks.append((round(x + offset, 1), cell))
    return sorted(set(picks))


def _video_duration(path: Path) -> float:
    """Return a video's duration in seconds."""
    cap = cv2.VideoCapture(str(path))
    try:
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        return float(frames / fps)
    finally:
        cap.release()


def _run_times(
    entry: dict[str, str | None],
    video: Path,
    step_s: float,
    event_cells: int,
) -> list[tuple[float, str]]:
    """Resolve study times for one extraction run."""
    if entry["analysis"]:
        manifest = PROJECT_ROOT / "analysis" / entry["analysis"] / "vision_manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8"))
        return select_times(data["state_snapshots"], step_s, event_cells)
    duration = _video_duration(video)
    times: list[tuple[float, str]] = []
    t = 20.0
    while t < duration - 25.0:
        times.append((round(t, 1), "regular"))
        t += step_s
    return times


def _grab(cap: cv2.VideoCapture, time_s: float) -> Any:
    """Seek and decode one frame."""
    cap.set(cv2.CAP_PROP_POS_MSEC, time_s * 1000.0)
    ok, frame = cap.read()
    return frame if ok else None


def extract_frames(
    movies_dir: Path,
    out_dir: Path,
    sample_set: str = "calibration",
) -> list[dict[str, Any]]:
    """Extract one study matrix and return manifest rows."""
    runs, step_s, event_cells = SAMPLE_SETS[sample_set]
    rows: list[dict[str, Any]] = []
    for entry in runs:
        video = movies_dir / str(entry["video"])
        if not video.is_file():
            logger.warning("missing video {}", video)
            continue
        run_dir = out_dir.resolve() / str(entry["run"])
        run_dir.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(str(video))
        try:
            for time_s, cell in _run_times(entry, video, step_s, event_cells):
                frame = _grab(cap, time_s)
                if frame is None:
                    continue
                path = run_dir / f"t{time_s:07.1f}.png"
                cv2.imwrite(str(path), frame)
                rows.append({
                    "frame_id": f"{entry['run']}/{path.name}",
                    "path": str(path.relative_to(PROJECT_ROOT)),
                    "run": entry["run"], "language": entry["language"],
                    "stage": entry["stage"], "video": entry["video"],
                    "time_s": time_s, "cell": cell,
                })
        finally:
            cap.release()
    return rows


def read_samples(ground_truth_path: Path, output_path: Path, config_path: Path) -> int:
    """Read every labeled frame and write detector observations as JSON."""
    config = load_config(config_path).vision.zone_control
    labels = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for label in labels:
        image = cv2.imread(str(PROJECT_ROOT / label["path"]))
        if image is None:
            logger.warning("could not read {}", label["path"])
            continue
        reading = read_zone_control(image, config)
        rows.append({
            "frame_id": label["frame_id"],
            "state": reading.observed_state,
            "confidence": reading.confidence,
            "usable": reading.confidence >= config.min_usable_confidence,
            "left_signal": reading.left_signal,
            "right_signal": reading.right_signal,
        })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return len(rows)


def _effective_state(reading: dict[str, Any]) -> str:
    """Treat readings below the usable-confidence floor as unknown."""
    return reading["state"] if reading.get("usable", True) else "unknown"


def _precision_recall(confusion: dict[str, Counter[str]]) -> dict[str, Any]:
    """Per-state precision and recall from an expected→predicted confusion."""
    out: dict[str, Any] = {}
    for state in STATES[:3]:
        tp = confusion[state][state]
        predicted = sum(confusion[exp][state] for exp in confusion)
        expected = sum(confusion[state].values())
        out[state] = {
            "precision": tp / predicted if predicted else None,
            "recall": tp / expected if expected else None,
            "support": expected,
            "predicted": predicted,
        }
    return out


def _summary(pairs: list[tuple[dict[str, Any], str]]) -> dict[str, Any]:
    """Headline metrics for (label, predicted-state) pairs."""
    known = [(lab, pred) for lab, pred in pairs if lab["state"] != "unknown"]
    neutral = [pred for lab, pred in known if lab["state"] == "neutral"]
    owned = [pred for lab, pred in known if lab["state"] in OWNERSHIP]
    return {
        "samples": len(known),
        "exact": sum(lab["state"] == pred for lab, pred in known),
        "exact_accuracy": (
            sum(lab["state"] == pred for lab, pred in known) / len(known)
            if known else None
        ),
        "neutral_samples": len(neutral),
        "neutral_false_positive_rate": (
            sum(p in OWNERSHIP for p in neutral) / len(neutral) if neutral else None
        ),
        "ownership_swap_count": sum(
            lab["state"] in OWNERSHIP and pred in OWNERSHIP and pred != lab["state"]
            for lab, pred in known
        ),
        "ownership_as_neutral_count": sum(p == "neutral" for p in owned),
        "unknown_rate": (
            sum(pred == "unknown" for _, pred in known) / len(known) if known else None
        ),
        "coverage": (
            sum(pred != "unknown" for _, pred in known) / len(known) if known else None
        ),
    }


def _slices(pairs: list[tuple[dict[str, Any], str]], key: str) -> dict[str, Any]:
    """Summary metrics grouped by a label field or by tag membership."""
    groups: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    for lab, pred in pairs:
        values = lab.get("tags", []) if key == "tags" else [lab.get(key, "?")]
        for value in values:
            groups[str(value)].append((lab, pred))
    return {name: _summary(items) for name, items in sorted(groups.items())}


def compare_samples(readings_path: Path, ground_truth_path: Path) -> dict[str, Any]:
    """Compare detector states with manually labeled frame states."""
    readings = {
        row["frame_id"]: row
        for row in json.loads(readings_path.read_text(encoding="utf-8"))
    }
    labels = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    pairs = [
        (lab, _effective_state(readings[lab["frame_id"]]))
        for lab in labels
        if lab["frame_id"] in readings
    ]
    confusion: dict[str, Counter[str]] = {s: Counter() for s in STATES[:3]}
    for lab, pred in pairs:
        if lab["state"] in confusion:
            confusion[lab["state"]][pred] += 1
    return {
        **_summary(pairs),
        "labeled_unknown_excluded": sum(lab["state"] == "unknown" for lab, _ in pairs),
        "labeled_unknown_asserted": sum(
            lab["state"] == "unknown" and pred != "unknown" for lab, pred in pairs
        ),
        "confusion_expected_to_predicted": {
            exp: {pred: confusion[exp][pred] for pred in STATES} for exp in confusion
        },
        "per_state": _precision_recall(confusion),
        "by_language": _slices(pairs, "language"),
        "by_stage": _slices(pairs, "stage"),
        "by_failure_mode": _slices(pairs, "tags"),
        "mismatches": [
            {"frame_id": lab["frame_id"], "expected": lab["state"], "predicted": pred,
             "tags": lab.get("tags", [])}
            for lab, pred in pairs
            if lab["state"] != "unknown" and pred != lab["state"]
        ],
    }


def main() -> int:
    """Run the requested study operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=default_config_path())
    sub = parser.add_subparsers(dest="command", required=True)
    extract = sub.add_parser("extract")
    extract.add_argument("--movies-dir", type=Path, default=Path.home() / "Movies")
    extract.add_argument("--out", type=Path, default=DEFAULT_FRAMES)
    extract.add_argument("--set", choices=sorted(SAMPLE_SETS), default="calibration")
    read = sub.add_parser("read")
    read.add_argument("--gt", type=Path, default=DEFAULT_GT)
    read.add_argument("--out", type=Path, default=DEFAULT_READINGS)
    compare = sub.add_parser("compare")
    compare.add_argument("--readings", type=Path, default=DEFAULT_READINGS)
    compare.add_argument("--gt", type=Path, default=DEFAULT_GT)
    compare.add_argument("--out", type=Path, default=DEFAULT_METRICS)
    args = parser.parse_args()
    if args.command == "extract":
        rows = extract_frames(args.movies_dir, args.out, args.set)
        (args.out / "manifest.json").write_text(json.dumps(rows, indent=1) + "\n")
        logger.info("extracted {} frames", len(rows))
        return 0
    if args.command == "read":
        logger.info("read {} samples", read_samples(args.gt, args.out, args.config))
        return 0
    result = compare_samples(args.readings, args.gt)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    logger.info(
        "exact={} neutral_fp={} unknown={}",
        result["exact_accuracy"], result["neutral_false_positive_rate"],
        result["unknown_rate"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
