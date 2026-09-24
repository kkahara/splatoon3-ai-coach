#!/usr/bin/env python3
"""Observe-only special-activation study (checklist D).

Four modes, sharing one ``GaugeSample``/``Candidate`` foundation:

- ``candidates``: find visible-to-visible ``fill_fraction`` declines with no
  label to anchor on, and emit review frame strips. This is what production
  fusion would eventually run on. Death proximity is deliberately omitted
  from this output so raw-candidate review is not biased.
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
  parameter as the candidate-generation window being swept. Matching is
  one-to-one: one activation cannot legitimise several candidates, so extra
  candidates around a recovered activation stay unmatched and count as false
  candidates (``_match_one_to_one``).
- ``review-queue``: classify unmatched candidates on GT runs into
  ``extra`` / ``post_death`` / ``other`` and write a VMV review queue with
  attribution context (peak/trough/decline/span/nearest DEATH). Priority for
  Stage 2 is the ``other`` population. This is study labeling only — not a
  production ``SPECIAL_USED`` GameEvent.

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

Death proximity is also **not** recorded on ``candidates.json``, for a second
reason: those records drive an unbiased candidate-generation population.
Continuous death distance belongs to ``trajectories``, ``score``, and the
``review-queue`` attribution export (for VMV).

The drop threshold is a parameter rather than a constant so the eventual sweep
reuses this exact candidate-generation logic.

Merging is its own hazard in both directions, found by scoring the corrected
tool against the full 17-run GT rather than assumed:

- Under-merging real declines that share a merge window (e.g. two activations
  9s apart) into one candidate purely by trough proximity silently discarded
  whichever had the shallower decline — a real recall bug, not a modeling
  choice.
- Over-correcting that by comparing every new peak against the *prior
  candidate's trough* (a trough is, by construction, always far below any
  peak) made almost every peak identity change look like a "refill," which
  fragmented single smooth declines whose true peak ages out of the trailing
  window into many near-duplicate candidates (one 11s Kraken Royale decline
  produced 10 of them).

``_group_firings`` instead asks whether the *new peak itself* is a genuine
local rise compared to the immediately preceding measured sample
(``_is_rise``). A rise is real evidence of recharge; its absence means the
peak identity only shifted because an earlier, higher sample left the
trailing window, and the firing still belongs to the decline already in
progress. Verified against the full 56-activation GT: 0/56 unmatched, 36/56
match exactly one candidate, the remainder mostly two — that residual
duplication reflects genuine gauge read noise in specific spans, not a merge
defect (see ``CANDIDATE_REVIEW.md`` in the study output directory).
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
# when the new peak isn't itself a genuine rise. See _group_firings.
DEFAULT_MERGE_SECONDS = 8.0
# A peak sample only ~2 dial sectors (~1/21 fill each) above its immediately
# preceding measured sample is cadence/sensor jitter, not a real recharge.
# Empirically verified against the full 56-activation GT: 0.10 keeps every
# known-separate pair of declines separate (e.g. Triple Splashdown's two
# activations 12.5s apart) while collapsing single monotonic declines whose
# peak identity merely ages out of the trailing window (e.g. Kraken Royale's
# ~11s decline dropped from 10 spurious candidates to 1). 0.15 was tried and
# is too permissive — it wrongly re-merges the Triple Splashdown pair.
DEFAULT_REFILL_TOLERANCE = 0.10
DEFAULT_PRE_SECONDS = 2.0
DEFAULT_POST_SECONDS = 3.0
DEFAULT_ASSOC_PRE_SECONDS = 5.0
DEFAULT_ASSOC_POST_SECONDS = 10.0
# Peak 0–10s after a DEATH → post_death population (death-penalty confound).
POST_DEATH_WINDOW_SECONDS = 10.0

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


def _is_rise(measured: list[GaugeSample], index: int, *, refill_tolerance: float) -> bool:
    """Whether ``measured[index]`` is itself a genuine local rise.

    Compares the sample to the immediately preceding *measured* sample in
    time (not the current decline's trough). A peak sample that is a rise is
    real evidence the gauge recharged at that instant. A peak sample that is
    not a rise — its immediate predecessor was at or above it — only became
    "the peak" because an earlier, higher sample aged out of the trailing
    ``window_seconds`` lookback; it is still part of whatever decline was
    already in progress.
    """
    if index == 0:
        return True
    sample = measured[index]
    predecessor = measured[index - 1]
    return sample.fill_fraction - predecessor.fill_fraction > refill_tolerance  # type: ignore[operator]


def _group_firings(
    firings: list[tuple[GaugeSample, GaugeSample]],
    measured: list[GaugeSample],
    *,
    merge_seconds: float,
    refill_tolerance: float,
) -> list[list[tuple[GaugeSample, GaugeSample]]]:
    """Group firings into distinct declines, not just nearby troughs.

    Firings sharing the same originating peak are always one decline: they
    are the cascading cadence-sampled troughs on the way to one minimum.
    Firings with a *different* peak are the same decline unless the new peak
    is itself a genuine rise (see ``_is_rise``) — i.e. unless the gauge
    actually recharged at that instant, rather than the peak identity merely
    shifting because an earlier, higher sample aged out of the trailing
    window. Comparing the new peak against the *prior group's trough* (an
    earlier version of this function) was wrong: a trough is by construction
    far below any peak, so that comparison almost always looked like a
    "refill" and fragmented one smooth decline into many near-duplicate
    candidates — see the module-level notes on ``candidates`` mode.
    """
    index_by_timestamp = {sample.timestamp: i for i, sample in enumerate(measured)}
    groups: list[list[tuple[GaugeSample, GaugeSample]]] = []
    for firing in firings:
        peak, trough = firing
        if groups and groups[-1][-1][0].timestamp == peak.timestamp:
            groups[-1].append(firing)
            continue
        if groups:
            prior_trough = groups[-1][-1][1]
            gap = trough.timestamp - prior_trough.timestamp
            genuine_rise = _is_rise(
                measured,
                index_by_timestamp[peak.timestamp],
                refill_tolerance=refill_tolerance,
            )
            if gap <= merge_seconds and not genuine_rise:
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
        firings,
        measured,
        merge_seconds=merge_seconds,
        refill_tolerance=refill_tolerance,
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

    ``matched_candidate_count`` equals ``recovered_count`` by construction —
    matching is one-to-one, so a recovered activation consumes exactly one
    candidate (see ``_match_one_to_one``). Both are reported so that
    ``false_candidate_count = candidate_count - matched_candidate_count`` is
    readable without knowing that identity holds.

    ``multi_candidate_activation_count`` counts GT activations with more than
    one candidate in their association window, independent of which one the
    matching assigned. It is the transparency number for how much of the
    candidate population is extra candidates around real activations rather
    than candidates somewhere else entirely.
    """

    window_seconds: float
    min_decline: float
    special: str | None
    gt_count: int
    recovered_count: int
    recall: float | None
    candidate_count: int
    matched_candidate_count: int
    false_candidate_count: int
    multi_candidate_activation_count: int


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


def _candidate_distance_seconds(candidate: Candidate, t: float) -> float:
    """Temporal distance from a labeled press time to a candidate's onset.

    Measured from ``peak_time`` because the peak is where the decline starts
    and is therefore the physical correlate of the press. Measuring from the
    whole span instead would tie every candidate whose span happens to contain
    the label at zero distance, which is common once several candidates
    overlap one activation.
    """
    return abs(candidate.peak_time - t)


def _claim_one_to_one(
    activations: list[GTActivation],
    candidates_by_run: dict[str, list[Candidate]],
    *,
    assoc_pre: float,
    assoc_post: float,
) -> tuple[set[int], set[tuple[str, int]], int]:
    """Assign at most one candidate to each GT activation, and vice versa.

    Returns ``(claimed_activation_indices, claimed_(run, cand_index),
    multi_candidate_activations)``.

    One activation cannot legitimise N candidates. Counting every candidate
    that overlaps *any* association window as "matched" — what this mode did
    previously — understates the extra-candidate population exactly where it
    matters, because a broad association window routinely puts two to four
    candidates around one labeled press. Under one-to-one matching each
    activation may claim at most one candidate, each candidate may satisfy at
    most one activation, and every leftover candidate stays unmatched and
    counts as a false candidate.

    Pairs are assigned tightest-first (smallest distance from the labeled
    press to the candidate's peak; ties broken by greater decline, then by
    time), so the result does not depend on the order activations appear in
    the GT file. This is deliberately greedy rather than a global optimum:
    the study measures the evidence, it does not build the production
    matcher. Greedy can leave a second activation unrecovered when two real
    presses have only one candidate between them — that is a real candidate
    identity collapse and *should* surface as a recall loss, which is what a
    20s candidate-generation window does to Tacticooler.
    """
    pairs: list[tuple[float, float, float, float, int, int]] = []
    multi_candidate_activations = 0
    for activation_index, activation in enumerate(activations):
        overlapping = [
            candidate_index
            for candidate_index, candidate in enumerate(
                candidates_by_run[activation.run]
            )
            if _candidate_in_association_window(
                candidate, activation.t, assoc_pre, assoc_post
            )
        ]
        if len(overlapping) > 1:
            multi_candidate_activations += 1
        for candidate_index in overlapping:
            candidate = candidates_by_run[activation.run][candidate_index]
            pairs.append(
                (
                    _candidate_distance_seconds(candidate, activation.t),
                    -candidate.decline,
                    candidate.peak_time,
                    candidate.trough_time,
                    activation_index,
                    candidate_index,
                )
            )

    claimed_activations: set[int] = set()
    claimed_candidates: set[tuple[str, int]] = set()
    for *_sort_keys, activation_index, candidate_index in sorted(pairs):
        candidate_key = (activations[activation_index].run, candidate_index)
        if (
            activation_index in claimed_activations
            or candidate_key in claimed_candidates
        ):
            continue
        claimed_activations.add(activation_index)
        claimed_candidates.add(candidate_key)
    return claimed_activations, claimed_candidates, multi_candidate_activations


def _match_one_to_one(
    activations: list[GTActivation],
    candidates_by_run: dict[str, list[Candidate]],
    *,
    assoc_pre: float,
    assoc_post: float,
) -> tuple[int, int, int]:
    """Counts from ``_claim_one_to_one`` for score cells."""
    claimed_activations, claimed_candidates, multi = _claim_one_to_one(
        activations,
        candidates_by_run,
        assoc_pre=assoc_pre,
        assoc_post=assoc_post,
    )
    return len(claimed_activations), len(claimed_candidates), multi


def _is_post_death_candidate(
    nearest_death_signed: float | None, *, window_seconds: float
) -> bool:
    """Whether the candidate peak falls in the post-death confound window.

    ``nearest_death_signed`` is ``death - peak`` (see
    ``nearest_death_signed_seconds``). A value in ``[-window, 0]`` means the
    peak is 0–``window`` seconds *after* the nearest DEATH — the death-penalty
    gauge drop becoming visible after the death UI clears.
    """
    if nearest_death_signed is None:
        return False
    return -window_seconds <= nearest_death_signed <= 0.0


class ReviewQueueItem(BaseModel):
    """One unmatched candidate for VMV attribution review.

    ``population`` is the Stage 1 split: ``extra`` (near a recovered GT
    activation), ``post_death`` (peak 0–10s after DEATH), or ``other`` (the
    key unresolved set). Label meanings in VMV: SPECIAL_USED means a special
    was actually consumed — not that the gauge dropped or a candidate fired.
    """

    run: str
    special: str | None = None
    candidate_index: int
    population: Literal["extra", "post_death", "other"]
    peak_time: float
    trough_time: float
    decline: float
    span_seconds: float
    max_single_step: float
    peak_fill: float
    trough_fill: float
    nearest_death_signed_seconds: float | None = None
    manifest_path: str
    strip_path: str | None = None


class ReviewQueueReport(BaseModel):
    """VMV review queue for unmatched candidates on GT runs."""

    mode: Literal["review_queue"] = "review_queue"
    gt_path: str
    min_decline: float
    window_seconds: float
    merge_seconds: float
    refill_tolerance: float
    assoc_pre_seconds: float
    assoc_post_seconds: float
    post_death_window_seconds: float
    review_priority: Literal["other"] = "other"
    note: str = (
        "Attribution review only. SPECIAL_USED in VMV means a special was "
        "actually consumed — not a production GameEvent. Prioritize population "
        "'other' before post_death / extra."
    )
    counts: dict[str, int] = Field(default_factory=dict)
    items: list[ReviewQueueItem] = Field(default_factory=list)


def _strip_path_if_present(out_dir: Path, run: str, index: int) -> str | None:
    """Existing review strip path, if a previous ``candidates`` run wrote one."""
    path = out_dir / "strips" / run / f"cand{index:02d}.jpg"
    return str(path) if path.is_file() else None


def run_review_queue_mode(args: argparse.Namespace) -> None:
    """Classify unmatched GT-run candidates for VMV attribution review."""
    gt_path = Path(args.gt)
    activations = load_gt_activations(gt_path)
    analysis_dir = Path(args.analysis_dir)
    out_dir = Path(args.out)
    special_by_run = {
        activation.run: activation.special for activation in activations
    }
    samples_by_run: dict[str, list[GaugeSample]] = {}
    deaths_by_run: dict[str, list[float]] = {}
    manifest_path_by_run: dict[str, Path] = {}
    for activation in activations:
        if activation.run in samples_by_run:
            continue
        manifest_path = analysis_dir / activation.run / MANIFEST_NAME
        manifest_path_by_run[activation.run] = manifest_path
        if not manifest_path.is_file():
            logger.warning("no manifest for GT run {}", activation.run)
            samples_by_run[activation.run] = []
            deaths_by_run[activation.run] = []
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        samples_by_run[activation.run] = load_gauge_samples(manifest)
        deaths_by_run[activation.run] = load_death_times(manifest)

    candidates_by_run = {
        run: find_candidates(
            samples,
            run=run,
            min_decline=args.min_decline,
            window_seconds=args.window,
            merge_seconds=args.merge,
            refill_tolerance=args.refill_tolerance,
        )
        for run, samples in samples_by_run.items()
    }
    _claimed_acts, claimed_candidates, _multi = _claim_one_to_one(
        activations,
        candidates_by_run,
        assoc_pre=args.assoc_pre,
        assoc_post=args.assoc_post,
    )

    items: list[ReviewQueueItem] = []
    counts = {"extra": 0, "post_death": 0, "other": 0, "assigned": 0}
    counts["assigned"] = len(claimed_candidates)
    for run, candidates in candidates_by_run.items():
        run_acts = [a for a in activations if a.run == run]
        deaths = deaths_by_run.get(run, [])
        manifest_path = manifest_path_by_run[run]
        for candidate in candidates:
            key = (run, candidate.index)
            if key in claimed_candidates:
                continue
            in_window = any(
                _candidate_in_association_window(
                    candidate, activation.t, args.assoc_pre, args.assoc_post
                )
                for activation in run_acts
            )
            nearest = nearest_death_signed_seconds(deaths, candidate.peak_time)
            if in_window:
                population: Literal["extra", "post_death", "other"] = "extra"
            elif _is_post_death_candidate(
                nearest, window_seconds=args.post_death_window
            ):
                population = "post_death"
            else:
                population = "other"
            counts[population] += 1
            items.append(
                ReviewQueueItem(
                    run=run,
                    special=special_by_run.get(run),
                    candidate_index=candidate.index,
                    population=population,
                    peak_time=candidate.peak_time,
                    trough_time=candidate.trough_time,
                    decline=candidate.decline,
                    span_seconds=candidate.span_seconds,
                    max_single_step=candidate.max_single_step,
                    peak_fill=candidate.peak_fill,
                    trough_fill=candidate.trough_fill,
                    nearest_death_signed_seconds=nearest,
                    manifest_path=str(manifest_path),
                    strip_path=_strip_path_if_present(
                        out_dir, run, candidate.index
                    ),
                )
            )

    items.sort(key=lambda item: (item.population != "other", item.run, item.peak_time))
    report = ReviewQueueReport(
        gt_path=str(gt_path),
        min_decline=args.min_decline,
        window_seconds=args.window,
        merge_seconds=args.merge,
        refill_tolerance=args.refill_tolerance,
        assoc_pre_seconds=args.assoc_pre,
        assoc_post_seconds=args.assoc_post,
        post_death_window_seconds=args.post_death_window,
        counts=counts,
        items=items,
    )
    out_path = out_dir / "review_queue.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    logger.info(
        "wrote {} (extra={} post_death={} other={} assigned={}; review priority=other)",
        out_path,
        counts["extra"],
        counts["post_death"],
        counts["other"],
        counts["assigned"],
    )


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
    recovered, matched_candidates, multi_candidate_activations = _match_one_to_one(
        activations,
        candidates_by_run,
        assoc_pre=assoc_pre,
        assoc_post=assoc_post,
    )
    total_candidates = sum(len(c) for c in candidates_by_run.values())
    return ScoreCell(
        window_seconds=window_seconds,
        min_decline=min_decline,
        special=special,
        gt_count=len(activations),
        recovered_count=recovered,
        recall=round(recovered / len(activations), 4) if activations else None,
        candidate_count=total_candidates,
        matched_candidate_count=matched_candidates,
        false_candidate_count=total_candidates - matched_candidates,
        multi_candidate_activation_count=multi_candidate_activations,
    )


def run_score_mode(args: argparse.Namespace) -> None:
    """Sweep decline threshold x candidate-generation window against GT.

    Reports, per special and in aggregate, whether each GT activation is
    recoverable at each (window, threshold) setting, alongside how many
    candidates that setting generates in total — so a wider window's recall
    gain is never read without its false-candidate cost sitting next to it.
    Candidates are matched to activations one-to-one, and the count of
    activations carrying more than one candidate is reported separately, so
    extra candidates are visible instead of being absorbed by the activations
    they sit next to.
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
    score.add_argument("--assoc-pre", type=float, default=DEFAULT_ASSOC_PRE_SECONDS)
    score.add_argument("--assoc-post", type=float, default=DEFAULT_ASSOC_POST_SECONDS)
    score.set_defaults(func=run_score_mode)

    review = sub.add_parser(
        "review-queue",
        help="classify unmatched candidates for VMV attribution review",
    )
    review.add_argument("--analysis-dir", default=str(PROJECT_ROOT / "analysis"))
    review.add_argument("--gt", default=default_gt)
    review.add_argument("--out", default=default_out)
    review.add_argument("--min-decline", type=float, default=DEFAULT_MIN_DECLINE)
    review.add_argument("--window", type=float, default=DEFAULT_WINDOW_SECONDS)
    review.add_argument("--merge", type=float, default=DEFAULT_MERGE_SECONDS)
    review.add_argument(
        "--refill-tolerance", type=float, default=DEFAULT_REFILL_TOLERANCE
    )
    review.add_argument("--assoc-pre", type=float, default=DEFAULT_ASSOC_PRE_SECONDS)
    review.add_argument("--assoc-post", type=float, default=DEFAULT_ASSOC_POST_SECONDS)
    review.add_argument(
        "--post-death-window",
        type=float,
        default=POST_DEATH_WINDOW_SECONDS,
        help="seconds after DEATH that classify a peak as post_death",
    )
    review.set_defaults(func=run_review_queue_mode)
    return parser


def main() -> None:
    """Entry point."""
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
