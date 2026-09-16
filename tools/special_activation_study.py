#!/usr/bin/env python3
"""Observe-only special-activation study (checklist D) — ``candidates`` mode.

Reads ``vision_manifest.json`` and the already-dumped ``debug_snapshots/`` of
analyzed runs, finds visible-to-visible ``fill_fraction`` drops, and emits
candidate records plus review frame strips under
``analysis/special_gauge_survey/activation_study/``.

This tool observes only. It does not touch the pipeline, emit events, or write
anything back into a run directory.

Candidate generation is deliberately **not** smarter than the study definition:
a candidate is any pair of consecutive samples where both are ``visible`` and
``fill_fraction`` fell by at least ``--min-drop``. Candidates are never dropped
for being near a ``DEATH``, for ``ready`` being false, or for otherwise not
"looking like" an activation. The later analysis exists to discover what those
signals mean, so the raw candidate population has to reach it unfiltered.

Death proximity is also **not** recorded here, for a second reason: these
records drive the manual review pass, and showing the labeler how close a
candidate sits to a death would bias the very judgment the study is measuring.
Continuous death distance belongs to the ``trajectories`` and ``score`` modes.

The drop threshold is a parameter rather than a constant so the eventual sweep
reuses this exact candidate-generation logic.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
from loguru import logger
from pydantic import BaseModel, Field

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.paths import PROJECT_ROOT, resolve_config_path

DETECTOR_NAME = "special_gauge"
MANIFEST_NAME = "vision_manifest.json"

# Broad by design: the study must see sub-0.5 drops, since en-flounder_heights
# produces zero candidates at 0.5 while showing plausible drops at 0.38-0.43.
DEFAULT_MIN_DROP = 0.15
DEFAULT_PRE_SECONDS = 2.0
DEFAULT_POST_SECONDS = 3.0

# Each strip tile stacks a downscaled context frame over a magnified crop of
# the gauge ROI. At full-frame scale the dial is a few pixels across and a
# reviewer cannot actually see what it is doing.
CONTEXT_TILE_HEIGHT = 180
GAUGE_TILE_HEIGHT = 160


class GaugeSample(BaseModel):
    """One ``special_gauge`` reading at one cadence frame, as persisted."""

    timestamp: float
    frame_path: str | None = None
    visible: bool = False
    fill_fraction: float | None = None
    ready: bool = False
    dial_score: float = 0.0
    lit_sector_fraction: float = 0.0
    charged_score: float = 0.0
    press_score: float = 0.0
    ready_prompt_score: float = 0.0
    confidence: float = 0.0


class Candidate(BaseModel):
    """A visible-to-visible ``fill_fraction`` drop at or over the threshold.

    A candidate is an unjudged observation. It is not an activation, and
    nothing here asserts that a special was used.
    """

    run: str
    index: int
    timestamp: float
    prev_timestamp: float
    gap_seconds: float
    delta_fill: float
    previous: GaugeSample
    current: GaugeSample
    strip_path: str | None = None


class RunCandidates(BaseModel):
    """Candidate population for one analyzed run."""

    run: str
    language: str | None = None
    duration_seconds: float = 0.0
    sample_count: int = 0
    visible_sample_count: int = 0
    candidates: list[Candidate] = Field(default_factory=list)


class CandidateReport(BaseModel):
    """Full ``candidates`` output across every surveyed run."""

    mode: Literal["candidates"] = "candidates"
    min_drop: float
    pre_seconds: float
    post_seconds: float
    runs: list[RunCandidates] = Field(default_factory=list)


def find_run_dirs(analysis_dir: Path, names: list[str] | None) -> list[Path]:
    """Analyzed run directories to survey, or the explicitly named subset."""
    if names:
        return [analysis_dir / name for name in names]
    return sorted(path.parent for path in analysis_dir.glob(f"*/{MANIFEST_NAME}"))


def load_gauge_samples(manifest: dict[str, Any]) -> list[GaugeSample]:
    """Every ``special_gauge`` reading in the manifest, ordered by timestamp."""
    samples: list[GaugeSample] = []
    for frame in manifest.get("frame_results", []):
        timestamp = frame.get("timestamp")
        if timestamp is None:
            continue
        for detection in frame.get("detections", []):
            if detection.get("detector_name") != DETECTOR_NAME:
                continue
            samples.append(
                _sample(
                    detection.get("reading", {}),
                    timestamp=float(timestamp),
                    frame_path=frame.get("frame_path"),
                    confidence=float(detection.get("confidence", 0.0)),
                )
            )
    samples.sort(key=lambda sample: sample.timestamp)
    return samples


def _sample(
    reading: dict[str, Any],
    *,
    timestamp: float,
    frame_path: str | None,
    confidence: float,
) -> GaugeSample:
    """Build a sample from a persisted reading, preserving absent fill."""
    fill = reading.get("fill_fraction")
    return GaugeSample(
        timestamp=timestamp,
        frame_path=frame_path,
        visible=bool(reading.get("visible", False)),
        fill_fraction=None if fill is None else float(fill),
        ready=bool(reading.get("ready", False)),
        dial_score=float(reading.get("dial_score", 0.0)),
        lit_sector_fraction=float(reading.get("lit_sector_fraction", 0.0)),
        charged_score=float(reading.get("charged_score", 0.0)),
        press_score=float(reading.get("press_score", 0.0)),
        ready_prompt_score=float(reading.get("ready_prompt_score", 0.0)),
        confidence=confidence,
    )


def find_candidates(
    samples: list[GaugeSample],
    *,
    run: str,
    min_drop: float,
) -> list[Candidate]:
    """Consecutive visible-to-visible drops of at least ``min_drop``.

    "Consecutive" means adjacent in the cadence sample sequence; invisible
    samples break the pair rather than being skipped over, because a drop
    measured across an invisibility gap is not a measured drop.
    """
    candidates: list[Candidate] = []
    for previous, current in zip(samples, samples[1:], strict=False):
        if not (previous.visible and current.visible):
            continue
        if previous.fill_fraction is None or current.fill_fraction is None:
            continue
        delta = previous.fill_fraction - current.fill_fraction
        if delta < min_drop:
            continue
        candidates.append(
            Candidate(
                run=run,
                index=len(candidates),
                timestamp=current.timestamp,
                prev_timestamp=previous.timestamp,
                gap_seconds=round(current.timestamp - previous.timestamp, 3),
                delta_fill=round(delta, 4),
                previous=previous,
                current=current,
            )
        )
    return candidates


def strip_samples(
    samples: list[GaugeSample],
    candidate: Candidate,
    *,
    pre_seconds: float,
    post_seconds: float,
) -> list[GaugeSample]:
    """Samples spanning the review window around a candidate."""
    start = candidate.prev_timestamp - pre_seconds
    end = candidate.timestamp + post_seconds
    return [
        sample
        for sample in samples
        if start <= sample.timestamp <= end and sample.frame_path
    ]


def _gauge_row(
    image: np.ndarray,
    roi: tuple[float, float, float, float],
    width: int,
) -> np.ndarray:
    """Magnified gauge-ROI crop, centered on a row of the given width."""
    height, full_width = image.shape[:2]
    x0, y0, x1, y1 = roi
    crop = image[
        int(y0 * height) : int(y1 * height),
        int(x0 * full_width) : int(x1 * full_width),
    ]
    row = np.zeros((GAUGE_TILE_HEIGHT, width, 3), dtype=np.uint8)
    if crop.size == 0:
        return row
    scale = GAUGE_TILE_HEIGHT / crop.shape[0]
    gauge = cv2.resize(
        crop,
        (max(1, int(crop.shape[1] * scale)), GAUGE_TILE_HEIGHT),
        interpolation=cv2.INTER_NEAREST,
    )
    gauge_width = min(gauge.shape[1], width)
    left = (width - gauge_width) // 2
    row[:, left : left + gauge_width] = gauge[:, :gauge_width]
    return row


def _tile(
    run_dir: Path,
    sample: GaugeSample,
    *,
    roi: tuple[float, float, float, float],
    highlight: bool,
) -> np.ndarray | None:
    """One labeled review tile: context frame over a magnified gauge crop."""
    if sample.frame_path is None:
        return None
    image = cv2.imread(str(run_dir / sample.frame_path))
    if image is None:
        logger.warning("unreadable frame {}", sample.frame_path)
        return None
    scale = CONTEXT_TILE_HEIGHT / image.shape[0]
    context = cv2.resize(image, (int(image.shape[1] * scale), CONTEXT_TILE_HEIGHT))
    tile = cv2.vconcat([context, _gauge_row(image, roi, context.shape[1])])
    fill = "--" if sample.fill_fraction is None else f"{sample.fill_fraction:.2f}"
    label = (
        f"{sample.timestamp:.1f}s f={fill} v={int(sample.visible)} r={int(sample.ready)}"
    )
    cv2.rectangle(tile, (0, 0), (tile.shape[1] - 1, 18), (0, 0, 0), -1)
    cv2.putText(tile, label, (3, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    if highlight:
        cv2.rectangle(
            tile, (0, 0), (tile.shape[1] - 1, tile.shape[0] - 1), (0, 215, 255), 3
        )
    return tile


def write_strip(
    run_dir: Path,
    samples: list[GaugeSample],
    candidate: Candidate,
    out_path: Path,
    *,
    roi: tuple[float, float, float, float],
) -> bool:
    """Write the horizontal review strip; the drop pair is outlined."""
    boundary = {candidate.prev_timestamp, candidate.timestamp}
    tiles = [
        tile
        for tile in (
            _tile(run_dir, sample, roi=roi, highlight=sample.timestamp in boundary)
            for sample in samples
        )
        if tile is not None
    ]
    if not tiles:
        return False
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), cv2.hconcat(tiles))
    return True


def survey_run(
    run_dir: Path,
    *,
    min_drop: float,
    pre_seconds: float,
    post_seconds: float,
    out_dir: Path,
    write_strips: bool,
    roi: tuple[float, float, float, float],
) -> RunCandidates | None:
    """Candidate population for one run, writing its review strips."""
    manifest_path = run_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        logger.warning("no manifest in {}", run_dir.name)
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    samples = load_gauge_samples(manifest)
    candidates = find_candidates(samples, run=run_dir.name, min_drop=min_drop)
    for candidate in candidates:
        if not write_strips:
            continue
        window = strip_samples(
            samples, candidate, pre_seconds=pre_seconds, post_seconds=post_seconds
        )
        strip = out_dir / "strips" / run_dir.name / f"cand{candidate.index:02d}.jpg"
        if write_strip(run_dir, window, candidate, strip, roi=roi):
            candidate.strip_path = str(strip.relative_to(out_dir))
    return RunCandidates(
        run=run_dir.name,
        language=manifest.get("analysis", {}).get("language"),
        duration_seconds=round(samples[-1].timestamp, 3) if samples else 0.0,
        sample_count=len(samples),
        visible_sample_count=sum(1 for sample in samples if sample.visible),
        candidates=candidates,
    )


def gauge_roi(config_path: Path | None) -> tuple[float, float, float, float]:
    """Gauge ROI for strip crops, read from config rather than hardcoded."""
    config = load_config(resolve_config_path(config_path))
    return tuple(config.vision.special_gauge.roi)  # type: ignore[return-value]


def run_candidates_mode(args: argparse.Namespace) -> None:
    """Survey every requested run and write ``candidates.json``."""
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    roi = gauge_roi(Path(args.config) if args.config else None)
    report = CandidateReport(
        min_drop=args.min_drop,
        pre_seconds=args.pre,
        post_seconds=args.post,
    )
    for run_dir in find_run_dirs(Path(args.analysis_dir), args.run):
        surveyed = survey_run(
            run_dir,
            min_drop=args.min_drop,
            pre_seconds=args.pre,
            post_seconds=args.post,
            out_dir=out_dir,
            write_strips=not args.no_strips,
            roi=roi,
        )
        if surveyed is None:
            continue
        report.runs.append(surveyed)
        logger.info(
            "{}: {} candidates over {} samples ({} visible)",
            surveyed.run,
            len(surveyed.candidates),
            surveyed.sample_count,
            surveyed.visible_sample_count,
        )
    out_path = out_dir / "candidates.json"
    out_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    total = sum(len(run.candidates) for run in report.runs)
    logger.info(
        "wrote {} ({} candidates, {} runs, min_drop={})",
        out_path,
        total,
        len(report.runs),
        args.min_drop,
    )


def build_parser() -> argparse.ArgumentParser:
    """CLI for the study tool."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    candidates = sub.add_parser(
        "candidates", help="find visible-to-visible fill_fraction drops"
    )
    candidates.add_argument("--analysis-dir", default=str(PROJECT_ROOT / "analysis"))
    candidates.add_argument(
        "--out",
        default=str(
            PROJECT_ROOT / "analysis" / "special_gauge_survey" / "activation_study"
        ),
    )
    candidates.add_argument(
        "--run", action="append", help="run directory name (repeatable; default all)"
    )
    candidates.add_argument(
        "--config", help="config YAML for the gauge ROI (default: project default)"
    )
    candidates.add_argument("--min-drop", type=float, default=DEFAULT_MIN_DROP)
    candidates.add_argument("--pre", type=float, default=DEFAULT_PRE_SECONDS)
    candidates.add_argument("--post", type=float, default=DEFAULT_POST_SECONDS)
    candidates.add_argument(
        "--no-strips", action="store_true", help="skip review strip rendering"
    )
    candidates.set_defaults(func=run_candidates_mode)
    return parser


def main() -> None:
    """Entry point."""
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
