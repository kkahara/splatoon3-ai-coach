#!/usr/bin/env python3
"""Observe-only special-activation study (checklist D).

Three modes, sharing one ``GaugeSample``/``Candidate`` foundation:

- ``candidates``: find visible-to-visible ``fill_fraction`` declines with no
  label to anchor on, and emit review frame strips. This is what production
  fusion would eventually run on.
- ``trajectories``: for every blind-labeled GT activation, dump the full
  windowed signal (visible/fill_fraction/ready/dial/charged/press scores,
  signed distance to nearest DEATH) around the *known* activation time. This
  window is deliberately generous and independently configurable from
  candidate generation, so a duration-based special's full
  disappearance/reappearance cycle is visible whole rather than cut off at
  whatever span the candidate rule searches.
- ``score``: a decline-threshold x candidate-generation-window sweep against
  the GT, broken down per special and in aggregate. Matching a candidate to a
  labeled activation uses a separate *activation-association window*
  (``--assoc-pre``/``--assoc-post``) — how far a candidate's peak/trough may
  sit from a label and still count as that activation — which is not the same
  parameter as the candidate-generation window being swept.

Reads ``vision_manifest.json`` and the already-dumped ``debug_snapshots/`` of
analyzed runs. Writes everything under
``analysis/special_gauge_survey/activation_study/``.

This tool observes only. It does not touch the pipeline, emit events, or write
anything back into a run directory.

A candidate is a **cumulative decline**: within a trailing window, a peak
``fill_fraction`` followed by a trough at least ``--min-decline`` below it.

The earlier single-step definition (one consecutive pair falling by a
threshold) was falsified by the blind pass. The gauge often drains
progressively over five to seven seconds rather than in one cadence step, so a
single-step rule recovered only 3 of 8 blind-labeled activations at 0.15 and 1
of 8 at 0.5. Peak-to-trough spans both observed shapes: the progressive drain
and the single-step fall. ``max_single_step`` is recorded per candidate so the
old shape stays measurable without a second code path.

Candidate generation is deliberately **not** smarter than the study definition.
Candidates are never dropped for being near a ``DEATH``, for ``ready`` being
false, for a weak ``dial_score``, or for otherwise not "looking like" an
activation. The later analysis exists to discover what those signals mean, so
the raw candidate population has to reach it unfiltered.

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

# Broad by design. All 8 blind-labeled activations show a 6s decline between
# 0.52 and 0.90, but the scoring phase still sweeps; this is a generous
# generation floor for the review population, not a tuned operating point.
DEFAULT_MIN_DECLINE = 0.30
DEFAULT_WINDOW_SECONDS = 6.0
# Firings this close together describe one decline, not several — but only
# when there was no genuine refill between them. See _group_firings.
DEFAULT_MERGE_SECONDS = 8.0
# A rise above the prior trough smaller than this is cadence/sensor jitter,
# not a real recharge; a rise above it means the peak change was a real
# refill, not just the earlier peak aging out of the trailing window.
DEFAULT_REFILL_TOLERANCE = 0.15
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
    """A peak-to-trough ``fill_fraction`` decline at or over the threshold.

    A candidate is an unjudged observation. It is not an activation, and
    nothing here asserts that a special was used.

    ``trajectory`` carries the surrounding samples so the shape can be
    reconstructed later without re-reading the manifest.
    """

    run: str
    index: int
    # First sample whose trailing window satisfies the threshold.
    onset_time: float
    decline: float
    peak_time: float
    peak_fill: float
    trough_time: float
    trough_fill: float
    # Peak to trough, which separates a progressive drain from a one-step fall.
    span_seconds: float
    max_single_step: float
    invisible_samples_in_span: int
    peak: GaugeSample
    trough: GaugeSample
    trajectory: list[GaugeSample] = Field(default_factory=list)
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
    rule: str = "cumulative_decline"
    min_decline: float
    window_seconds: float
    merge_seconds: float
    pre_seconds: float
    post_seconds: float
    runs: list[RunCandidates] = Field(default_factory=list)


class GTActivation(BaseModel):
    """One blind-labeled activation timestamp, joined with its run's special.

    Identity (``special``) is known because each GT run is a single-special
    recording (by filename, or by the earlier hand-transcribed blind pass).
    It is never inferred from the gauge signal.
    """

    run: str
    language: str | None = None
    special: str | None = None
    clock: str | None = None
    t: float


def load_gt_activations(gt_path: Path) -> list[GTActivation]:
    """Every blind-labeled activation across every run in the GT file."""
    data = json.loads(gt_path.read_text(encoding="utf-8"))
    activations: list[GTActivation] = []
    for run in data.get("runs", []):
        for entry in run.get("activations", []):
            activations.append(
                GTActivation(
                    run=run["run"],
                    language=run.get("language"),
                    special=run.get("special"),
                    clock=entry.get("clock"),
                    t=float(entry["t"]),
                )
            )
    return activations


def load_death_times(manifest: dict[str, Any]) -> list[float]:
    """Every ``DEATH`` ``GameEvent.start_time`` in the manifest, sorted.

    Independent evidence from fusion, not the ``special_gauge`` detector.
    """
    return sorted(
        float(event["start_time"])
        for event in manifest.get("game_events", [])
        if event.get("event_type") == "death"
    )


def nearest_death_signed_seconds(
    death_times: list[float], activation_time: float
) -> float | None:
    """Signed seconds from ``activation_time`` to the nearest DEATH.

    Positive means the death is after the activation; negative means before.
    ``None`` when the run has no DEATH events at all, which is itself a fact
    worth preserving rather than a missing value to default to zero.
    """
    if not death_times:
        return None
    nearest = min(death_times, key=lambda death: abs(death - activation_time))
    return round(nearest - activation_time, 3)


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


def _firings(
    measured: list[GaugeSample],
    *,
    min_decline: float,
    window_seconds: float,
) -> list[tuple[GaugeSample, GaugeSample]]:
    """Every sample whose trailing window holds a peak ``min_decline`` above it.

    Invisible samples are excluded from ``measured`` rather than treated as
    zero fill, so a decline is only ever measured between real observations.
    """
    fired: list[tuple[GaugeSample, GaugeSample]] = []
    for index, trough in enumerate(measured):
        peak = trough
        for prior in reversed(measured[:index]):
            if trough.timestamp - prior.timestamp > window_seconds:
                break
            if prior.fill_fraction > peak.fill_fraction:  # type: ignore[operator]
                peak = prior
        decline = peak.fill_fraction - trough.fill_fraction  # type: ignore[operator]
        if decline >= min_decline:
            fired.append((peak, trough))
    return fired


def _group_firings(
    firings: list[tuple[GaugeSample, GaugeSample]],
    *,
    merge_seconds: float,
    refill_tolerance: float,
) -> list[list[tuple[GaugeSample, GaugeSample]]]:
    """Group firings into distinct declines, not just nearby troughs.

    Firings sharing the same originating peak are always one decline: they
    are the cascading cadence-sampled troughs on the way to one minimum.
    Firings with a *different* peak are only the same decline when the peak
    identity changed merely because the earlier peak aged out of the trailing
    window — not because the gauge actually recharged. A rise from the prior
    group's trough to the new peak greater than ``refill_tolerance`` is a
    genuine refill and always starts a new candidate, regardless of how close
    in time it is to the last one. This is the fix for a defect where two
    real, separate declines within ``merge_seconds`` of each other collapsed
    into one candidate, silently discarding whichever had the shallower
    decline — see the module-level notes on ``candidates`` mode.
    """
    groups: list[list[tuple[GaugeSample, GaugeSample]]] = []
    for firing in firings:
        peak, trough = firing
        if groups and groups[-1][-1][0].timestamp == peak.timestamp:
            groups[-1].append(firing)
            continue
        if groups:
            prior_trough = groups[-1][-1][1]
            gap = trough.timestamp - prior_trough.timestamp
            refill = peak.fill_fraction - prior_trough.fill_fraction  # type: ignore[operator]
            if gap <= merge_seconds and refill <= refill_tolerance:
                groups[-1].append(firing)
                continue
        groups.append([firing])
    return groups


def find_candidates(
    samples: list[GaugeSample],
    *,
    run: str,
    min_decline: float,
    window_seconds: float,
    merge_seconds: float,
    refill_tolerance: float = DEFAULT_REFILL_TOLERANCE,
) -> list[Candidate]:
    """Peak-to-trough declines of at least ``min_decline`` within the window.

    Adjacent firings describing one decline are merged; the deepest trough in
    a merged group defines the candidate. See ``_group_firings`` for what
    "adjacent" means — trough proximity alone is not sufficient, since two
    genuinely separate declines can have troughs closer together than
    ``merge_seconds``.
    """
    measured = [
        sample
        for sample in samples
        if sample.visible and sample.fill_fraction is not None
    ]
    firings = _firings(measured, min_decline=min_decline, window_seconds=window_seconds)
    groups = _group_firings(
        firings, merge_seconds=merge_seconds, refill_tolerance=refill_tolerance
    )
    return [
        _candidate(group, samples, run=run, index=index)
        for index, group in enumerate(groups)
    ]


def _candidate(
    group: list[tuple[GaugeSample, GaugeSample]],
    samples: list[GaugeSample],
    *,
    run: str,
    index: int,
) -> Candidate:
    """Summarize one merged decline, keeping the evidence to redraw it."""
    peak, trough = max(
        group,
        key=lambda pair: pair[0].fill_fraction - pair[1].fill_fraction,  # type: ignore[operator]
    )
    span = [
        sample
        for sample in samples
        if peak.timestamp <= sample.timestamp <= trough.timestamp
    ]
    in_span = [s for s in span if s.visible and s.fill_fraction is not None]
    steps = [
        a.fill_fraction - b.fill_fraction  # type: ignore[operator]
        for a, b in zip(in_span, in_span[1:], strict=False)
    ]
    return Candidate(
        run=run,
        index=index,
        onset_time=group[0][1].timestamp,
        decline=round(peak.fill_fraction - trough.fill_fraction, 4),  # type: ignore[operator]
        peak_time=peak.timestamp,
        peak_fill=peak.fill_fraction,  # type: ignore[arg-type]
        trough_time=trough.timestamp,
        trough_fill=trough.fill_fraction,  # type: ignore[arg-type]
        span_seconds=round(trough.timestamp - peak.timestamp, 3),
        max_single_step=round(max(steps, default=0.0), 4),
        invisible_samples_in_span=sum(1 for s in span if not s.visible),
        peak=peak,
        trough=trough,
        trajectory=span,
    )


def strip_samples(
    samples: list[GaugeSample],
    candidate: Candidate,
    *,
    pre_seconds: float,
    post_seconds: float,
) -> list[GaugeSample]:
    """Samples spanning the review window around a candidate."""
    start = candidate.peak_time - pre_seconds
    end = candidate.trough_time + post_seconds
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
    """Write the horizontal review strip; the peak and trough are outlined."""
    boundary = {candidate.peak_time, candidate.trough_time}
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
    min_decline: float,
    window_seconds: float,
    merge_seconds: float,
    refill_tolerance: float,
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
    candidates = find_candidates(
        samples,
        run=run_dir.name,
        min_decline=min_decline,
        window_seconds=window_seconds,
        merge_seconds=merge_seconds,
        refill_tolerance=refill_tolerance,
    )
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
        min_decline=args.min_decline,
        window_seconds=args.window,
        merge_seconds=args.merge,
        pre_seconds=args.pre,
        post_seconds=args.post,
    )
    for run_dir in find_run_dirs(Path(args.analysis_dir), args.run):
        surveyed = survey_run(
            run_dir,
            min_decline=args.min_decline,
            window_seconds=args.window,
            merge_seconds=args.merge,
            refill_tolerance=args.refill_tolerance,
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
        "wrote {} ({} candidates, {} runs, min_decline={} over {}s)",
        out_path,
        total,
        len(report.runs),
        args.min_decline,
        args.window,
    )


class TrajectorySample(BaseModel):
    """One ``special_gauge`` observation, offset from a GT activation time."""

    timestamp: float
    offset_seconds: float
    visible: bool
    fill_fraction: float | None
    ready: bool
    dial_score: float
    charged_score: float
    press_score: float


class ActivationTrajectory(BaseModel):
    """The full observation window around one blind-labeled activation.

    This is Stage 1 evidence only. ``special`` and ``activation_time`` come
    from the GT file, not from the gauge signal — nothing here asserts that
    the samples "look like" that special, only what was actually observed
    around the labeled time.
    """

    run: str
    special: str | None
    language: str | None
    clock: str | None
    activation_time: float
    window_start: float
    window_end: float
    nearest_death_signed_seconds: float | None
    samples: list[TrajectorySample] = Field(default_factory=list)


class TrajectoryReport(BaseModel):
    """Full ``trajectories`` output across every GT-labeled activation."""

    mode: Literal["trajectories"] = "trajectories"
    gt_path: str
    pre_seconds: float
    post_seconds: float
    activations: list[ActivationTrajectory] = Field(default_factory=list)


def build_trajectory(
    activation: GTActivation,
    samples: list[GaugeSample],
    death_times: list[float],
    *,
    pre_seconds: float,
    post_seconds: float,
) -> ActivationTrajectory:
    """The generous observation window around one GT activation.

    Deliberately not truncated to the candidate-generation window: a
    duration-based special's disappearance/reappearance cycle should be
    visible whole, not cut off at whatever span the candidate rule searches.
    """
    start = activation.t - pre_seconds
    end = activation.t + post_seconds
    window = [sample for sample in samples if start <= sample.timestamp <= end]
    return ActivationTrajectory(
        run=activation.run,
        special=activation.special,
        language=activation.language,
        clock=activation.clock,
        activation_time=activation.t,
        window_start=start,
        window_end=end,
        nearest_death_signed_seconds=nearest_death_signed_seconds(
            death_times, activation.t
        ),
        samples=[
            TrajectorySample(
                timestamp=sample.timestamp,
                offset_seconds=round(sample.timestamp - activation.t, 3),
                visible=sample.visible,
                fill_fraction=sample.fill_fraction,
                ready=sample.ready,
                dial_score=sample.dial_score,
                charged_score=sample.charged_score,
                press_score=sample.press_score,
            )
            for sample in window
        ],
    )


def run_trajectories_mode(args: argparse.Namespace) -> None:
    """Dump the full observation window around every GT-labeled activation."""
    gt_path = Path(args.gt)
    activations = load_gt_activations(gt_path)
    analysis_dir = Path(args.analysis_dir)
    report = TrajectoryReport(
        gt_path=str(gt_path), pre_seconds=args.pre, post_seconds=args.post
    )
    samples_by_run: dict[str, list[GaugeSample]] = {}
    deaths_by_run: dict[str, list[float]] = {}
    for activation in activations:
        if activation.run not in samples_by_run:
            manifest_path = analysis_dir / activation.run / MANIFEST_NAME
            if not manifest_path.is_file():
                logger.warning("no manifest for GT run {}", activation.run)
                samples_by_run[activation.run] = []
                deaths_by_run[activation.run] = []
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            samples_by_run[activation.run] = load_gauge_samples(manifest)
            deaths_by_run[activation.run] = load_death_times(manifest)
        report.activations.append(
            build_trajectory(
                activation,
                samples_by_run[activation.run],
                deaths_by_run[activation.run],
                pre_seconds=args.pre,
                post_seconds=args.post,
            )
        )
    out_path = Path(args.out) / "trajectories.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    logger.info(
        "wrote {} ({} activations over [-{}s, +{}s] windows)",
        out_path,
        len(report.activations),
        args.pre,
        args.post,
    )


class ScoreCell(BaseModel):
    """Recall and false-candidate count at one (window, threshold) setting.

    ``special`` is ``None`` for the aggregate row across every GT run. This
    is a measurement, not a recommendation: a higher recall at a wider window
    is reported alongside its false-candidate count rather than treated as
    automatically better.
    """

    window_seconds: float
    min_decline: float
    special: str | None
    gt_count: int
    recovered_count: int
    recall: float | None
    candidate_count: int
    false_candidate_count: int


class ScoreReport(BaseModel):
    """Full ``score`` output: a decline-threshold x window sweep, per special."""

    mode: Literal["score"] = "score"
    gt_path: str
    assoc_pre_seconds: float
    assoc_post_seconds: float
    merge_seconds: float
    refill_tolerance: float
    window_values: list[float] = Field(default_factory=list)
    min_decline_values: list[float] = Field(default_factory=list)
    cells: list[ScoreCell] = Field(default_factory=list)


def _candidate_in_association_window(
    candidate: Candidate, t: float, assoc_pre: float, assoc_post: float
) -> bool:
    """Whether a candidate's peak-to-trough span overlaps the activation window.

    The activation-association window ``[t - assoc_pre, t + assoc_post]`` is a
    separate concept from the candidate-generation window swept elsewhere in
    this mode: it is how far from a *labeled* press time a candidate may sit
    and still be counted as the same activation, not how far ``find_candidates``
    searches when it has no label to anchor on. Overlap (not containment) is
    used because the labeled press time is a single instant that can fall
    slightly outside the measured peak/trough on either side — see the offsets
    recorded by ``trajectories`` mode for the observed range.
    """
    window_start = t - assoc_pre
    window_end = t + assoc_post
    return candidate.peak_time <= window_end and candidate.trough_time >= window_start


def _score_cell(
    *,
    window_seconds: float,
    min_decline: float,
    merge_seconds: float,
    refill_tolerance: float,
    special: str | None,
    activations: list[GTActivation],
    samples_by_run: dict[str, list[GaugeSample]],
    assoc_pre: float,
    assoc_post: float,
) -> ScoreCell:
    """Recall and false-candidate count for one sweep cell.

    ``activations`` is pre-filtered to the runs this cell covers (either one
    special's runs, or every GT run for the aggregate row).
    """
    relevant_runs = {activation.run for activation in activations}
    candidates_by_run = {
        run: find_candidates(
            samples_by_run[run],
            run=run,
            min_decline=min_decline,
            window_seconds=window_seconds,
            merge_seconds=merge_seconds,
            refill_tolerance=refill_tolerance,
        )
        for run in relevant_runs
    }
    def _in_window(candidate: Candidate, t: float) -> bool:
        return _candidate_in_association_window(candidate, t, assoc_pre, assoc_post)

    recovered = 0
    for activation in activations:
        if any(
            _in_window(candidate, activation.t)
            for candidate in candidates_by_run[activation.run]
        ):
            recovered += 1
    total_candidates = sum(len(c) for c in candidates_by_run.values())
    matched_candidates = sum(
        1
        for run, candidates in candidates_by_run.items()
        for candidate in candidates
        if any(
            _in_window(candidate, activation.t)
            for activation in activations
            if activation.run == run
        )
    )
    return ScoreCell(
        window_seconds=window_seconds,
        min_decline=min_decline,
        special=special,
        gt_count=len(activations),
        recovered_count=recovered,
        recall=round(recovered / len(activations), 4) if activations else None,
        candidate_count=total_candidates,
        false_candidate_count=total_candidates - matched_candidates,
    )


def run_score_mode(args: argparse.Namespace) -> None:
    """Sweep decline threshold x candidate-generation window against GT.

    Reports, per special and in aggregate, whether each GT activation is
    recoverable at each (window, threshold) setting, alongside how many
    candidates that setting generates in total — so a wider window's recall
    gain is never read without its false-candidate cost sitting next to it.
    """
    gt_path = Path(args.gt)
    activations = load_gt_activations(gt_path)
    analysis_dir = Path(args.analysis_dir)
    samples_by_run: dict[str, list[GaugeSample]] = {}
    for activation in activations:
        if activation.run in samples_by_run:
            continue
        manifest_path = analysis_dir / activation.run / MANIFEST_NAME
        if not manifest_path.is_file():
            logger.warning("no manifest for GT run {}", activation.run)
            samples_by_run[activation.run] = []
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        samples_by_run[activation.run] = load_gauge_samples(manifest)

    specials = sorted({a.special for a in activations if a.special is not None})
    report = ScoreReport(
        gt_path=str(gt_path),
        assoc_pre_seconds=args.assoc_pre,
        assoc_post_seconds=args.assoc_post,
        merge_seconds=args.merge,
        refill_tolerance=args.refill_tolerance,
        window_values=args.windows,
        min_decline_values=args.declines,
    )
    for window_seconds in args.windows:
        for min_decline in args.declines:
            report.cells.append(
                _score_cell(
                    window_seconds=window_seconds,
                    min_decline=min_decline,
                    merge_seconds=args.merge,
                    refill_tolerance=args.refill_tolerance,
                    special=None,
                    activations=activations,
                    samples_by_run=samples_by_run,
                    assoc_pre=args.assoc_pre,
                    assoc_post=args.assoc_post,
                )
            )
            for special in specials:
                report.cells.append(
                    _score_cell(
                        window_seconds=window_seconds,
                        min_decline=min_decline,
                        merge_seconds=args.merge,
                        refill_tolerance=args.refill_tolerance,
                        special=special,
                        activations=[a for a in activations if a.special == special],
                        samples_by_run=samples_by_run,
                        assoc_pre=args.assoc_pre,
                        assoc_post=args.assoc_post,
                    )
                )
    out_path = Path(args.out) / "score.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    logger.info(
        "wrote {} ({} cells over {} windows x {} thresholds, {} specials + aggregate)",
        out_path,
        len(report.cells),
        len(args.windows),
        len(args.declines),
        len(specials),
    )


def _float_list(raw: str) -> list[float]:
    """Parse a comma-separated CLI list of floats."""
    return [float(value) for value in raw.split(",")]


def build_parser() -> argparse.ArgumentParser:
    """CLI for the study tool."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    candidates = sub.add_parser(
        "candidates", help="find peak-to-trough fill_fraction declines"
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
    candidates.add_argument("--min-decline", type=float, default=DEFAULT_MIN_DECLINE)
    candidates.add_argument("--window", type=float, default=DEFAULT_WINDOW_SECONDS)
    candidates.add_argument("--merge", type=float, default=DEFAULT_MERGE_SECONDS)
    candidates.add_argument(
        "--refill-tolerance", type=float, default=DEFAULT_REFILL_TOLERANCE
    )
    candidates.add_argument("--pre", type=float, default=DEFAULT_PRE_SECONDS)
    candidates.add_argument("--post", type=float, default=DEFAULT_POST_SECONDS)
    candidates.add_argument(
        "--no-strips", action="store_true", help="skip review strip rendering"
    )
    candidates.set_defaults(func=run_candidates_mode)

    default_gt = str(
        PROJECT_ROOT
        / "analysis"
        / "special_gauge_survey"
        / "activation_study"
        / "gt"
        / "blind_activations.json"
    )
    default_out = str(
        PROJECT_ROOT / "analysis" / "special_gauge_survey" / "activation_study"
    )

    trajectories = sub.add_parser(
        "trajectories",
        help="dump the full observation window around every GT activation",
    )
    trajectories.add_argument("--analysis-dir", default=str(PROJECT_ROOT / "analysis"))
    trajectories.add_argument("--gt", default=default_gt)
    trajectories.add_argument("--out", default=default_out)
    # Generous by design: wide enough to see a duration-based special's full
    # disappearance/reappearance cycle, not the 6s candidate-generation span.
    trajectories.add_argument("--pre", type=float, default=3.0)
    trajectories.add_argument("--post", type=float, default=25.0)
    trajectories.set_defaults(func=run_trajectories_mode)

    score = sub.add_parser(
        "score",
        help="sweep decline threshold x candidate-generation window against GT",
    )
    score.add_argument("--analysis-dir", default=str(PROJECT_ROOT / "analysis"))
    score.add_argument("--gt", default=default_gt)
    score.add_argument("--out", default=default_out)
    score.add_argument(
        "--windows",
        type=_float_list,
        default=[4.0, 6.0, 10.0, 15.0, 20.0],
        help="comma-separated candidate-generation windows in seconds",
    )
    score.add_argument(
        "--declines",
        type=_float_list,
        default=[0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
        help="comma-separated decline thresholds",
    )
    score.add_argument("--merge", type=float, default=DEFAULT_MERGE_SECONDS)
    score.add_argument(
        "--refill-tolerance", type=float, default=DEFAULT_REFILL_TOLERANCE
    )
    # The activation-association window: how far a candidate's peak/trough may
    # sit from a *labeled* press time and still count as that activation. This
    # is deliberately a separate parameter from --windows (candidate
    # generation, which has no label to anchor on). Defaults reflect the
    # observed peak/trough offsets across the frozen 4-run GT (peak up to 3s
    # before or 4.5s after the label; trough up to 8.5s after), plus margin
    # for slower duration-based specials.
    score.add_argument("--assoc-pre", type=float, default=5.0)
    score.add_argument("--assoc-post", type=float, default=10.0)
    score.set_defaults(func=run_score_mode)
    return parser


def main() -> None:
    """Entry point."""
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
