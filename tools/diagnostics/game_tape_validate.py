#!/usr/bin/env python3
"""Held-out validation of the frozen "GAME!" tape rule (match-end evidence).

Pre-registered in ``analysis/game_tape_probe/VALIDATION.md``. Commands::

    # freeze the held-out list (run once, then commit heldout.json)
    python tools/diagnostics/game_tape_validate.py select
    # scan the held-out videos; write detections, grids and onset strips
    python tools/diagnostics/game_tape_validate.py run [--jobs 3]
    # onset pages, the grid list and a blank truth.csv
    python tools/diagnostics/game_tape_validate.py sheets
    # after truth.csv is filled from the sheets: apply the pass criteria
    python tools/diagnostics/game_tape_validate.py report

The rule, thresholds and mask are frozen; this tool refuses to run if the
mask checksum differs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from game_tape_probe import SAMPLE_FPS, TapeLayout, frames, tape_features  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "analysis" / "game_tape_probe"
MASK = OUT_DIR / "band_mask.png"
MASK_SHA256 = "efa538023384bad7f2c1a9c4ceacbfeef8744537d74523533b89cb40567ca619"
MOVIES = Path.home() / "Movies"

FILL_MIN = 0.73
CONTRAST_MIN = 0.35
CONFIRM_FRAMES = 2
ONSET_TOLERANCE = 1.0
MIN_DURATION, MAX_DURATION = 100.0, 480.0
EXCLUDE = re.compile(r"review|preview|timeline|nocontest|results", re.IGNORECASE)
DEV = re.compile(r"^en-live-playerview-2026-10-1[12] ")
LATE_WINDOW = 10.0
GRID_SAMPLE, GRID_SEED = 20, 20261020
THUMB_EVERY = 4
THUMB_SIZE = (120, 68)
STRIP_SIZE = (240, 135)
STRIPS_PER_PAGE = 8


@dataclass(frozen=True)
class Run:
    """Consecutive frozen-rule positive frames."""

    start: float
    end: float
    frames: int


def positive(row: Mapping[str, float]) -> bool:
    """Frozen per-frame rule."""
    return row["fill"] >= FILL_MIN and row["contrast"] >= CONTRAST_MIN


def detect_runs(rows: Sequence[Mapping[str, float]]) -> list[Run]:
    """Runs of at least ``CONFIRM_FRAMES`` consecutive positive frames."""
    step = 1.0 / SAMPLE_FPS
    runs: list[list[float]] = []
    for row in rows:
        if not positive(row):
            continue
        if runs and abs(row["t"] - runs[-1][-1] - step) < 1e-6:
            runs[-1].append(row["t"])
        else:
            runs.append([row["t"]])
    return [Run(r[0], r[-1], len(r)) for r in runs if len(r) >= CONFIRM_FRAMES]


def language(name: str) -> str:
    """Language group from the file-name prefix only (pre-registered)."""
    if re.match(r"^en[-_]", name):
        return "en"
    if re.match(r"^(ja|jp)[-_]", name):
        return "ja"
    return "unknown"


def _duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def select(movies: Path, out: Path) -> int:
    """Write ``heldout.json``: every eligible recording, by the frozen rule."""
    if out.exists():
        raise SystemExit(f"{out} exists; the held-out list is frozen")
    rows = []
    for path in sorted(movies.iterdir()):
        if path.suffix.lower() not in (".mov", ".mp4", ".mkv"):
            continue
        if DEV.match(path.name) or EXCLUDE.search(path.name):
            continue
        duration = _duration(path)
        if MIN_DURATION <= duration <= MAX_DURATION:
            rows.append(
                {
                    "video": path.name,
                    "bytes": path.stat().st_size,
                    "duration": round(duration, 3),
                    "language": language(path.name),
                }
            )
    out.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    sys.stdout.write(f"{len(rows)} held-out videos -> {out}\n")
    return 0


def mask_sha256(path: Path) -> str:
    """Checksum of the frozen mask file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _scan_one(name: str, out_dir: Path) -> dict:
    """Scan one video; save a 2 s thumbnail grid and onset strips."""
    layout = TapeLayout.from_mask(cv2.imread(str(MASK), cv2.IMREAD_GRAYSCALE) > 0)
    path = MOVIES / name
    rows, thumbs = [], []
    for index, (t, image) in enumerate(frames(path)):
        features = tape_features(image, layout)
        rows.append(
            {
                "t": t,
                "fill": round(features.fill, 3),
                "contrast": round(features.contrast, 3),
            }
        )
        if index % THUMB_EVERY == 0:
            thumbs.append((t, cv2.resize(image, THUMB_SIZE)))
    runs = detect_runs(rows)
    stem = re.sub(r"[^A-Za-z0-9]+", "_", Path(name).stem).strip("_")
    _grid(thumbs, out_dir / "grids" / f"{stem}.jpg")
    for i, run in enumerate(runs):
        _strip(path, run.start, out_dir / "onsets" / f"{stem}__{i}.jpg")
    return {
        "video": name,
        "frames": len(rows),
        "runs": [run.__dict__ for run in runs],
        "rows": rows,
    }


def _label(image: np.ndarray, text: str) -> np.ndarray:
    image = image.copy()
    cv2.putText(image, text, (2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)
    return image


def _grid(thumbs: Sequence[tuple[float, np.ndarray]], out: Path, cols: int = 12) -> None:
    """Whole-video contact sheet, one thumbnail every 2 s."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tiles = [_label(img, f"{t:.0f}") for t, img in thumbs]
    blank = np.zeros_like(tiles[0]) if tiles else np.zeros((68, 120, 3), np.uint8)
    tiles += [blank] * (-len(tiles) % cols)
    rows = [np.hstack(tiles[i : i + cols]) for i in range(0, len(tiles), cols)]
    cv2.imwrite(str(out), np.vstack(rows) if rows else blank)


def _strip(path: Path, onset: float, out: Path) -> None:
    """Frames from onset -2.0 to +0.5 s (0.5 s apart) for onset verification."""
    out.parent.mkdir(parents=True, exist_ok=True)
    start = max(0.0, onset - 2.0)
    tiles = [
        _label(cv2.resize(img, STRIP_SIZE), f"{t:.1f}")
        for t, img in frames(path, start, 3.0)
    ][:6]
    cv2.imwrite(str(out), np.hstack(tiles))


def run(out_dir: Path, jobs: int) -> int:
    """Scan every held-out video (resumable) and write ``detections.json``."""
    if mask_sha256(MASK) != MASK_SHA256:
        raise SystemExit("mask checksum differs from the frozen value")
    heldout = json.loads((out_dir / "heldout.json").read_text(encoding="utf-8"))
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    todo = [h["video"] for h in heldout if not (raw_dir / f"{h['video']}.json").exists()]
    with ProcessPoolExecutor(max_workers=jobs) as pool:
        for result in pool.map(_scan_one, todo, [out_dir] * len(todo)):
            (raw_dir / f"{result['video']}.json").write_text(
                json.dumps(result), encoding="utf-8"
            )
            sys.stdout.write(f"{result['video']}: runs {result['runs']}\n")
            sys.stdout.flush()
    detections = []
    for h in heldout:
        result = json.loads((raw_dir / f"{h['video']}.json").read_text(encoding="utf-8"))
        detections.append({**h, "frames": result["frames"], "runs": result["runs"]})
    (out_dir / "detections.json").write_text(
        json.dumps(detections, indent=1) + "\n", "utf-8"
    )
    return 0


def grid_sample(detections: Sequence[Mapping]) -> list[str]:
    """Videos whose whole-video grid is inspected: every video without a run,
    plus a seeded sample of ``GRID_SAMPLE`` videos with a run."""
    import random

    with_run = sorted(d["video"] for d in detections if d["runs"])
    without = sorted(d["video"] for d in detections if not d["runs"])
    sample = random.Random(GRID_SEED).sample(with_run, min(GRID_SAMPLE, len(with_run)))
    return without + sorted(sample)


def _match(start: float, onsets: Sequence[float]) -> tuple[float | None, bool]:
    """True onset a run belongs to, and whether it started on time."""
    for onset in onsets:
        if onset - 0.5 - 1e-6 <= start <= onset + LATE_WINDOW:
            return onset, start <= onset + ONSET_TOLERANCE + 1e-6
    return None, False


def evaluate(
    detections: Sequence[Mapping], truth: Mapping[str, Mapping[str, str]]
) -> dict:
    """Apply the pre-registered criteria to detections and truth labels."""
    keys = ("endings", "detected", "on_time")
    groups: dict[str, dict[str, int]] = {}
    false_runs, missed = [], []
    for det in detections:
        group = groups.setdefault(det["language"], dict.fromkeys(keys, 0))
        onsets = [
            float(x) for x in truth[det["video"]]["true_onsets"].split(";") if x.strip()
        ]
        matched: dict[float, bool] = {}
        for run_doc in det["runs"]:
            onset, on_time = _match(run_doc["start"], onsets)
            if onset is None:
                false_runs.append((det["video"], run_doc["start"]))
            elif onset not in matched:
                matched[onset] = on_time
        group["endings"] += len(onsets)
        group["detected"] += len(matched)
        group["on_time"] += sum(matched.values())
        missed += [(det["video"], o) for o in onsets if o not in matched]
    total = {k: sum(g[k] for g in groups.values()) for k in keys}
    recall = {lang: _ratio(g["detected"], g["endings"]) for lang, g in groups.items()}
    overall = _ratio(total["detected"], total["endings"])
    on_time = _ratio(total["on_time"], total["detected"])
    group_ok = all(
        rate is None or rate >= 0.95 or groups[lang]["endings"] < 10
        for lang, rate in recall.items()
    )
    passed = (
        not false_runs
        and overall is not None
        and overall >= 0.95
        and group_ok
        and on_time is not None
        and on_time >= 0.95
    )
    return {
        "groups": groups,
        "total": total,
        "recall": recall,
        "recall_overall": overall,
        "on_time_rate": on_time,
        "false_runs": false_runs,
        "missed": missed,
        "passed": passed,
    }


def sheets(out_dir: Path) -> int:
    """Onset pages, the grid list and a blank ``truth.csv`` for labeling."""
    detections = json.loads((out_dir / "detections.json").read_text(encoding="utf-8"))
    strips = sorted((out_dir / "onsets").glob("*.jpg"))
    pages = out_dir / "pages"
    pages.mkdir(exist_ok=True)
    for page, i in enumerate(range(0, len(strips), STRIPS_PER_PAGE)):
        rows = [
            _label(cv2.imread(str(p)), p.stem) for p in strips[i : i + STRIPS_PER_PAGE]
        ]
        width = max(r.shape[1] for r in rows)
        rows = [np.pad(r, ((0, 0), (0, width - r.shape[1]), (0, 0))) for r in rows]
        cv2.imwrite(str(pages / f"onsets_{page:02d}.jpg"), np.vstack(rows))
    grid = set(grid_sample(detections))
    (out_dir / "grid_list.txt").write_text(
        "\n".join(sorted(grid)) + "\n", encoding="utf-8"
    )
    truth = out_dir / "truth.csv"
    if not truth.exists():
        with truth.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                ["video", "language", "runs", "grid_checked", "true_onsets", "notes"]
            )
            for d in detections:
                runs = ";".join(f"{r['start']}" for r in d["runs"])
                checked = "Y" if d["video"] in grid else "N"
                writer.writerow([d["video"], d["language"], runs, checked, "", ""])
    sys.stdout.write(f"{len(strips)} onset strips; {len(grid)} grids to inspect\n")
    return 0


def _ratio(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def report(out_dir: Path) -> int:
    """Evaluate ``detections.json`` against ``truth.csv``; write ``RESULT.json``."""
    detections = json.loads((out_dir / "detections.json").read_text(encoding="utf-8"))
    with (out_dir / "truth.csv").open(newline="", encoding="utf-8") as fh:
        truth = {row["video"]: row for row in csv.DictReader(fh)}
    missing = [d["video"] for d in detections if d["video"] not in truth]
    if missing:
        raise SystemExit(f"truth.csv lacks {len(missing)} videos, e.g. {missing[0]}")
    result = evaluate(detections, truth)
    (out_dir / "RESULT.json").write_text(json.dumps(result, indent=1) + "\n", "utf-8")
    sys.stdout.write(json.dumps(result, indent=1) + "\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("select", "run", "sheets", "report"))
    parser.add_argument("--out", type=Path, default=OUT_DIR / "heldout")
    parser.add_argument("--jobs", type=int, default=3)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    if args.command == "select":
        return select(MOVIES, args.out / "heldout.json")
    if args.command == "run":
        return run(args.out, args.jobs)
    if args.command == "sheets":
        return sheets(args.out)
    return report(args.out)


if __name__ == "__main__":
    raise SystemExit(main())
