#!/usr/bin/env python3
"""Measure fused Splat Zones control state and transitions on real video.

Works on analysis directories produced by ``s3-coach analyze`` with
``zone_control`` enabled (their manifests hold raw ``ZoneControlReading``s).

Subcommands:

- ``extract``: one decode pass per run. Writes unannotated pod-band label
  sheets for every in-match cadence frame and re-runs the detector with each
  threshold variant (``detector_variants.json``).
- ``evaluate``: replays fusion + events for every variant with
  ``refuse_vision_manifest`` and compares against the labeled timeline.
- ``invariance``: strips zone readings, re-fuses, and checks that non-zone
  snapshot fields and GameEvents are unchanged.
- ``merge-gt``: builds ``gt_timeline.json`` from first-pass timeline labels
  (``gt/timeline_pass1``) overridden by full-resolution adjudication
  (``gt/adjudicated``).
- ``audit-evidence``: after ``s3-coach refuse`` + ``coach-inputs``, audits
  every DEATH_EPISODE unit: sample qualities, held-sample fact leaks,
  context transitions vs persisted events and labels, unknown bridging.

Ground truth (``--gt``) maps run name → list of ``[start_s, end_s, state]``
segments over cadence timestamps (inclusive). Uncovered in-match frames are
``unknown`` (HUD hidden: map, respawn, finish).

A labeled transition is a state change between consecutive labeled-known
frames at most ``VISIBLE_GAP_S`` apart; changes across longer unknown gaps
are *hidden* and reported separately (the contract forbids bridging them).
An event matches a labeled transition with the same target state when it
falls within ``[t - MATCH_BEFORE_S, t + MATCH_AFTER_S]``.
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from loguru import logger

from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import AppConfig, ZoneControlDetectorConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT, default_config_path
from splatoon3_ai_coach.media.video import VideoLoader
from splatoon3_ai_coach.media.vision_manifest import load_vision_manifest
from splatoon3_ai_coach.vision.models import (
    GameEventReason,
    VisionFrameResult,
    VisionManifest,
    ZoneControlReading,
)
from splatoon3_ai_coach.vision.pipeline import refuse_vision_manifest
from splatoon3_ai_coach.vision.zone_control import read_zone_control

STUDY_DIR = PROJECT_ROOT / "analysis" / "zone_control_survey"
DEFAULT_CONFIG = default_config_path()
VISIBLE_GAP_S = 2.0
MATCH_BEFORE_S = 0.5
MATCH_AFTER_S = 4.0
STALE_S = 4.0
BAND = (0.40, 0.115, 0.60, 0.21)
THUMB_W = 192
SHEET_COLS = 6
SHEET_ROWS = 20
POD_W = 30
POD_H = 24
TIMELINE_COLS = 30
TIMELINE_ROWS_PER_IMAGE = 7
AUDIT_KEYS = (
    "units", "with_block", "llm_view_zone", "with_transitions", "transitions",
    "direct", "not_in_events", "bridges_unknown", "transition_gt_match",
    "held_fact_leaks",
)
LABEL_CODES = {
    "A": "ally_control",
    "O": "opponent_control",
    "N": "neutral",
    "U": "unknown",
}

DETECTOR_VARIANTS: dict[str, dict[str, float]] = {
    "default": {},
    "loose": {
        "lit_min_fraction": 0.70,
        "dim_min_dark_fraction": 0.40,
        "dim_max_lit_fraction": 0.55,
    },
    "strict": {
        "lit_min_fraction": 0.90,
        "dim_min_dark_fraction": 0.55,
        "dim_max_lit_fraction": 0.40,
    },
    "no_edge_gate": {"min_edge_fraction": 0.0},
}
CONFIRM = (1, 2, 3)
HOLD = (1.0, 2.0, 3.0)
MIN_CONF = (0.55, 0.75)


@dataclass(frozen=True)
class Variant:
    detector: str
    confirm: int
    hold: float
    min_conf: float

    @property
    def name(self) -> str:
        return f"{self.detector}/c{self.confirm}/h{self.hold:g}/m{self.min_conf:g}"


def _zone_config(config: AppConfig, **update: Any) -> ZoneControlDetectorConfig:
    return config.vision.zone_control.model_copy(update=update)


def _thumb(image: np.ndarray) -> np.ndarray:
    h, w = image.shape[:2]
    x0, y0, x1, y1 = BAND
    crop = image[int(y0 * h) : int(y1 * h), int(x0 * w) : int(x1 * w)]
    scale = THUMB_W / crop.shape[1]
    return cv2.resize(crop, (THUMB_W, int(crop.shape[0] * scale)))


def _write_sheets(thumbs: list[tuple[int, float, np.ndarray]], out: Path) -> None:
    """Tile thumbnails with index + timestamp captions; no detector output."""
    out.mkdir(parents=True, exist_ok=True)
    per = SHEET_COLS * SHEET_ROWS
    for sheet_no in range(0, len(thumbs), per):
        cells = []
        for idx, t, img in thumbs[sheet_no : sheet_no + per]:
            cap = np.zeros((16, THUMB_W, 3), np.uint8)
            cv2.putText(cap, f"{idx} t{t:.1f}", (2, 12), cv2.FONT_HERSHEY_SIMPLEX,
                        0.4, (0, 255, 255), 1)
            cells.append(np.vstack([cap, img]))
        blank = np.zeros_like(cells[0])
        cells += [blank] * (per - len(cells))
        rows = [np.hstack(cells[r * SHEET_COLS : (r + 1) * SHEET_COLS])
                for r in range(SHEET_ROWS)]
        cv2.imwrite(str(out / f"s{sheet_no // per:02d}.png"), np.vstack(rows))


def _pod_column(image: np.ndarray) -> np.ndarray:
    """Left pod over right pod, shrunk to one timeline column."""
    h, w = image.shape[:2]
    pods = []
    for x0, x1 in ((0.41, 0.47), (0.53, 0.59)):
        crop = image[int(0.125 * h) : int(0.20 * h), int(x0 * w) : int(x1 * w)]
        pods.append(cv2.resize(crop, (POD_W, POD_H), interpolation=cv2.INTER_AREA))
    gap = np.full((2, POD_W, 3), 80, np.uint8)
    return np.vstack([pods[0], gap, pods[1]])


def _write_timeline(columns: list[np.ndarray], out: Path) -> None:
    """Rows of ``TIMELINE_COLS`` frames; every 10th frame index captioned."""
    rows = []
    for start in range(0, len(columns), TIMELINE_COLS):
        chunk = columns[start : start + TIMELINE_COLS]
        chunk += [np.zeros_like(columns[0])] * (TIMELINE_COLS - len(chunk))
        sep = np.full((columns[0].shape[0], 1, 3), 40, np.uint8)
        body = np.hstack([c for col in chunk for c in (col, sep)])
        cap = np.zeros((12, body.shape[1], 3), np.uint8)
        for k in range(0, TIMELINE_COLS, 5):
            cv2.putText(cap, str(start + k), (k * (POD_W + 1), 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.33, (0, 255, 255), 1)
        rows.append(np.vstack([cap, body, np.zeros((4, body.shape[1], 3), np.uint8)]))
    for part in range(0, len(rows), TIMELINE_ROWS_PER_IMAGE):
        name = f"timeline_{part // TIMELINE_ROWS_PER_IMAGE}.png"
        chunk = rows[part : part + TIMELINE_ROWS_PER_IMAGE]
        cv2.imwrite(str(out / name), np.vstack(chunk))


def _in_match_frames(manifest: VisionManifest) -> dict[int, VisionFrameResult]:
    phase = {s.timestamp: s.match_phase for s in manifest.state_snapshots}
    return {
        f.source_frame_index: f
        for f in manifest.frame_results
        if f.source == "cadence" and phase.get(f.timestamp) == "in_match"
    }


def extract(run_dir: Path, video: Path, config: AppConfig) -> None:
    """Decode once: label sheets + detector-variant readings per frame."""
    manifest = load_vision_manifest(run_dir)
    wanted = _in_match_frames(manifest)
    variants = {k: _zone_config(config, **v) for k, v in DETECTOR_VARIANTS.items()}
    readings: dict[str, dict[str, Any]] = {k: {} for k in variants}
    thumbs: list[tuple[int, float, np.ndarray]] = []
    columns: list[np.ndarray] = []
    mismatches = 0
    with VideoLoader(
        video, max_width=config.video.max_width, max_height=config.video.max_height
    ) as loader:
        for frame in loader.frames():
            result = wanted.get(frame.frame_index)
            if result is None:
                continue
            for name, zcfg in variants.items():
                readings[name][result.frame_id] = read_zone_control(
                    frame.image, zcfg
                ).model_dump(mode="json")
            stored = _stored_reading(result)
            if stored is not None and (
                stored.observed_state
                != readings["default"][result.frame_id]["observed_state"]
            ):
                mismatches += 1
            thumbs.append((len(thumbs), result.timestamp, _thumb(frame.image)))
            columns.append(_pod_column(frame.image))
    out = run_dir / "zone_study"
    out.mkdir(exist_ok=True)
    (out / "detector_variants.json").write_text(json.dumps(readings))
    (out / "frames.json").write_text(
        json.dumps([[i, t] for i, t, _ in thumbs])
    )
    _write_sheets(thumbs, out / "sheets")
    _write_timeline(columns, out)
    logger.info(
        "{}: {} in-match frames, {} default-vs-manifest mismatches",
        run_dir.name, len(thumbs), mismatches,
    )


def _stored_reading(frame: VisionFrameResult) -> ZoneControlReading | None:
    for item in frame.detections:
        if isinstance(item.reading, ZoneControlReading):
            return item.reading
    return None


def _swap_readings(
    manifest: VisionManifest, readings: dict[str, Any]
) -> VisionManifest:
    """Replace stored zone readings with a detector variant's readings."""
    frames = []
    for frame in manifest.frame_results:
        new = readings.get(frame.frame_id)
        detections = []
        for item in frame.detections:
            if isinstance(item.reading, ZoneControlReading) and new is not None:
                reading = ZoneControlReading.model_validate(new)
                item = item.model_copy(
                    update={"reading": reading, "confidence": reading.confidence}
                )
            detections.append(item)
        frames.append(frame.model_copy(update={"detections": detections}))
    return manifest.model_copy(update={"frame_results": frames})


def _gt_timeline(
    segments: list[list[Any]], times: list[float]
) -> dict[float, str]:
    out = {t: "unknown" for t in times}
    for start, end, state in segments:
        for t in times:
            if start <= t <= end:
                out[t] = state
    return out


def _gt_transitions(gt: dict[float, str]) -> tuple[list[tuple], int]:
    """Visible labeled transitions and a count of hidden ones."""
    visible: list[tuple] = []
    hidden = 0
    prev: tuple[float, str] | None = None
    for t in sorted(gt):
        state = gt[t]
        if state == "unknown":
            continue
        if prev is not None and state != prev[1]:
            if t - prev[0] <= VISIBLE_GAP_S:
                visible.append((t, prev[1], state))
            else:
                hidden += 1
        prev = (t, state)
    return visible, hidden


@dataclass
class _Matching:
    latencies: list[float]
    false_count: int
    missed: int
    from_mismatches: int


def _match(events: list[tuple], gts: list[tuple]) -> _Matching:
    """Greedy one-to-one matching on target state within the match window.

    ``from_mismatches`` counts matched events whose from-state differs from
    the labeled from-state (e.g. a suppressed one-frame neutral reported as a
    direct ally↔opponent flip).
    """
    used: set[int] = set()
    result = _Matching([], 0, 0, 0)
    for et, efrom, eto in events:
        best = None
        for i, (gt_t, _, gto) in enumerate(gts):
            if i in used or gto != eto:
                continue
            if gt_t - MATCH_BEFORE_S <= et <= gt_t + MATCH_AFTER_S:
                best = i
                break
        if best is None:
            result.false_count += 1
        else:
            used.add(best)
            result.latencies.append(et - gts[best][0])
            result.from_mismatches += efrom != gts[best][1]
    result.missed = len(gts) - len(used)
    return result


def _is_stale(t: float, state: str, gt: dict[float, str]) -> bool:
    """A wrong fused state that was the labeled state within ``STALE_S``."""
    return any(
        t - STALE_S <= u < t and gt[u] == state for u in gt
    )


def _short_segments(gts: list[tuple], gt: dict[float, str], confirm: int) -> int:
    """Labeled transitions whose new state lasts fewer than ``confirm`` frames."""
    times = sorted(gt)
    short = 0
    for t, _, to in gts:
        run = 0
        for u in times[times.index(t):]:
            if gt[u] != to:
                break
            run += 1
        short += run < confirm
    return short


def _run_metrics(
    manifest: VisionManifest, gt: dict[float, str], confirm: int
) -> dict[str, Any]:
    snaps = [s for s in manifest.state_snapshots if s.timestamp in gt]
    events = [
        (e.start_time, e.from_zone_control, e.to_zone_control)
        for e in manifest.game_events
        if e.reason is GameEventReason.ZONE_CONTROL_TRANSITION
    ]
    gts, hidden = _gt_transitions(gt)
    matching = _match(events, gts)
    known = [s for s in snaps if gt[s.timestamp] != "unknown"]
    asserted = [s for s in known if s.zone_control_state != "unknown"]
    neutral = [s for s in asserted if gt[s.timestamp] == "neutral"]
    wrong = [s for s in asserted if s.zone_control_state != gt[s.timestamp]]
    stale = [s for s in wrong if _is_stale(s.timestamp, s.zone_control_state, gt)]
    return {
        "stale_errors": len(stale),
        "wrong_errors": len(wrong) - len(stale),
        "wrong_observed": sum(
            s.zone_control_quality == "observed" for s in wrong if s not in stale
        ),
        "gt_short_transitions": _short_segments(gts, gt, confirm),
        "from_mismatches": matching.from_mismatches,
        "frames": len(snaps),
        "minutes": len(snaps) * 0.5 / 60.0,
        "gt_known": len(known),
        "asserted": len(asserted),
        "correct": sum(s.zone_control_state == gt[s.timestamp] for s in asserted),
        "neutral_asserted": len(neutral),
        "neutral_fp": sum(s.zone_control_state != "neutral" for s in neutral),
        "unknown": sum(s.zone_control_quality == "unknown" for s in snaps),
        "held": sum(s.zone_control_quality == "held" for s in snaps),
        "observed_while_hidden": sum(
            s.zone_control_quality == "observed" and gt[s.timestamp] == "unknown"
            for s in snaps
        ),
        "gt_transitions": len(gts),
        "gt_hidden_transitions": hidden,
        "gt_direct": sum(
            {a, b} == {"ally_control", "opponent_control"} for _, a, b in gts
        ),
        "events": len(events),
        "event_direct": sum(
            {a, b} == {"ally_control", "opponent_control"} for _, a, b in events
        ),
        "false_switches": matching.false_count,
        "missed": matching.missed,
        "latencies": matching.latencies,
    }


def _aggregate(per_run: dict[str, dict[str, Any]]) -> dict[str, Any]:
    keys = [k for k in next(iter(per_run.values())) if k != "latencies"]
    total = {k: sum(r[k] for r in per_run.values()) for k in keys}
    lat = [x for r in per_run.values() for x in r["latencies"]]
    return {
        **total,
        "state_accuracy": total["correct"] / max(total["asserted"], 1),
        "coverage": total["asserted"] / max(total["gt_known"], 1),
        "neutral_fp_rate": total["neutral_fp"] / max(total["neutral_asserted"], 1),
        "unknown_share": total["unknown"] / max(total["frames"], 1),
        "held_share": total["held"] / max(total["frames"], 1),
        "false_switches_per_min": total["false_switches"] / max(total["minutes"], 1e-9),
        "latency_median": statistics.median(lat) if lat else None,
        "latency_mean": statistics.fmean(lat) if lat else None,
        "latency_max": max(lat) if lat else None,
    }


def evaluate(
    run_dirs: list[Path], gt_path: Path, config: AppConfig, out: Path
) -> None:
    """Evaluate every detector × fusion variant against the labeled timeline."""
    gt_all = json.loads(gt_path.read_text())
    loaded = []
    for run_dir in run_dirs:
        manifest = load_vision_manifest(run_dir)
        study = run_dir / "zone_study"
        times = [t for _, t in json.loads((study / "frames.json").read_text())]
        det = json.loads((study / "detector_variants.json").read_text())
        gt = _gt_timeline(gt_all[run_dir.name], times)
        loaded.append((run_dir.name, manifest, gt, det))
    rows = []
    for dname, confirm, hold, min_conf in itertools.product(
        DETECTOR_VARIANTS, CONFIRM, HOLD, MIN_CONF
    ):
        variant = Variant(dname, confirm, hold, min_conf)
        cfg = config.model_copy(deep=True)
        cfg.vision.zone_control = _zone_config(
            config, **DETECTOR_VARIANTS[dname], confirm_readings=confirm,
            hold_seconds=hold, min_usable_confidence=min_conf,
        )
        per_run = {}
        for name, manifest, gt, det in loaded:
            refused = refuse_vision_manifest(_swap_readings(manifest, det[dname]), cfg)
            per_run[name] = _run_metrics(refused, gt, confirm)
        rows.append({"variant": variant.name, **_aggregate(per_run),
                     "per_run": {k: {x: y for x, y in v.items() if x != "latencies"}
                                 for k, v in per_run.items()}})
        logger.info("{} acc={:.3f} fp/min={:.2f} missed={}", variant.name,
                    rows[-1]["state_accuracy"], rows[-1]["false_switches_per_min"],
                    rows[-1]["missed"])
    out.write_text(json.dumps(rows, indent=1))


def _read_labels(path: Path) -> dict[int, str]:
    """Parse ``<index>[-<index>] <A|O|N|U>`` lines into per-frame states."""
    out: dict[int, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        span, code = line.split()
        lo, _, hi = span.partition("-")
        for i in range(int(lo), int(hi or lo) + 1):
            out[i] = LABEL_CODES[code]
    return out


def merge_gt(run_dirs: list[Path], gt_dir: Path, out: Path) -> None:
    """Merge first-pass timeline labels with adjudicated overrides."""
    merged: dict[str, list[list[Any]]] = {}
    for run_dir in run_dirs:
        frames = json.loads((run_dir / "zone_study" / "frames.json").read_text())
        labels = _read_labels(gt_dir / "timeline_pass1" / f"{run_dir.name}.txt")
        labels.update(_read_labels(gt_dir / "adjudicated" / f"{run_dir.name}.txt"))
        segments: list[list[Any]] = []
        for index, t in frames:
            state = labels.get(index, "unknown")
            if state == "unknown":
                continue
            if segments and segments[-1][2] == state and segments[-1][3] == index - 1:
                segments[-1][1], segments[-1][3] = t, index
            else:
                segments.append([t, t, state, index])
        merged[run_dir.name] = [s[:3] for s in segments]
        logger.info("{}: {} labeled segments", run_dir.name, len(segments))
    out.write_text(json.dumps(merged, indent=1))


def _bridges_unknown(snaps: list[Any], t: float, from_state: str) -> bool:
    """Whether the chain back from ``t`` hits unknown before ``from_state``."""
    for snap in reversed([s for s in snaps if s.timestamp < t]):
        if snap.zone_control_quality == "unknown":
            return True
        if snap.zone_control_quality == "observed":
            return snap.zone_control_state != from_state
    return True


def _check_sample(
    counts: dict[str, int], key: str, fact: str | None,
    sample: dict[str, Any] | None, gt: dict[float, str],
) -> None:
    """Tally a sample's quality and grade a derived fact against the labels."""
    quality = sample["quality"] if sample else "absent"
    counts[f"{key}_{quality}"] = counts.get(f"{key}_{quality}", 0) + 1
    if fact is None:
        return
    if quality != "observed":
        counts["held_fact_leaks"] = counts.get("held_fact_leaks", 0) + 1
        return
    truth = gt.get(sample["video_time"], "unknown")
    grade = "gt_unknown" if truth == "unknown" else (
        "correct" if truth == fact else "wrong"
    )
    counts[f"{key}_fact_{grade}"] = counts.get(f"{key}_fact_{grade}", 0) + 1


def _audit_unit(
    counts: dict[str, int], ci: dict[str, Any], view: dict[str, Any],
    snaps: list[Any], events: set[tuple], gt: dict[float, str],
) -> None:
    """Audit one DEATH_EPISODE coaching unit's zone evidence and facts."""
    zone = ci["primary_context"].get("zone_control")
    facts = ci.get("zone_control_facts") or {}
    counts["units"] += 1
    counts["with_block"] += zone is not None
    counts["llm_view_zone"] += view.get("zone_control") is not None
    if zone is None:
        return
    _check_sample(counts, "anchor", facts.get("control_at_death"),
                  zone.get("at_anchor"), gt)
    _check_sample(counts, "pre_death", facts.get("control_before_death"),
                  zone.get("pre_death"), gt)
    gts, _ = _gt_transitions(gt)
    for tr in zone["transitions"]:
        key = (tr["video_time"], tr["from_state"], tr["to_state"])
        counts["transitions"] += 1
        counts["direct"] += {key[1], key[2]} == {"ally_control", "opponent_control"}
        counts["not_in_events"] += key not in events
        counts["bridges_unknown"] += _bridges_unknown(snaps, key[0], key[1])
        counts["transition_gt_match"] += any(
            (gfrom, gto) == key[1:]
            and g - MATCH_BEFORE_S <= key[0] <= g + MATCH_AFTER_S
            for g, gfrom, gto in gts
        )
    counts["with_transitions"] += bool(zone["transitions"])


def audit_evidence(run_dirs: list[Path], gt_path: Path, out: Path) -> None:
    """Audit persisted ScenarioContext / CoachInput zone evidence per unit."""
    gt_all = json.loads(gt_path.read_text())
    report: dict[str, dict[str, int]] = {}
    for run_dir in run_dirs:
        manifest = load_vision_manifest(run_dir)
        times = [t for _, t in json.loads((run_dir / "zone_study" / "frames.json")
                                          .read_text())]
        gt = _gt_timeline(gt_all[run_dir.name], times)
        snaps = sorted(manifest.state_snapshots, key=lambda s: s.timestamp)
        events = {
            (e.start_time, e.from_zone_control, e.to_zone_control)
            for e in manifest.game_events
            if e.reason is GameEventReason.ZONE_CONTROL_TRANSITION
        }
        counts = dict.fromkeys(AUDIT_KEYS, 0)
        for path in sorted((run_dir / "coach_inputs").glob("*.coach_input.json")):
            view_path = path.with_name(path.name.replace("coach_input", "llm_view"))
            _audit_unit(counts, json.loads(path.read_text()),
                        json.loads(view_path.read_text()), snaps, events, gt)
        report[run_dir.name] = counts
        logger.info("{}: {}", run_dir.name, counts)
    out.write_text(json.dumps(report, indent=1))


def invariance(run_dir: Path, config: AppConfig) -> None:
    """Non-zone snapshot fields and GameEvents must not depend on zone readings."""
    manifest = load_vision_manifest(run_dir)
    stripped = manifest.model_copy(update={"frame_results": [
        f.model_copy(update={"detections": [
            d for d in f.detections if not isinstance(d.reading, ZoneControlReading)
        ]})
        for f in manifest.frame_results
    ]})
    with_zone = refuse_vision_manifest(manifest, config)
    without = refuse_vision_manifest(stripped, config)
    zone_fields = {"zone_control_state", "zone_control_quality", "evidence_ids"}

    def snap_key(s: Any) -> dict[str, Any]:
        return s.model_dump(mode="json", exclude=zone_fields)

    def events(m: VisionManifest) -> list[dict[str, Any]]:
        return [
            e.model_dump(mode="json", exclude={"evidence_ids", "event_id", "id"})
            for e in m.game_events
            if e.reason is not GameEventReason.ZONE_CONTROL_TRANSITION
        ]

    snaps_equal = [snap_key(a) for a in with_zone.state_snapshots] == [
        snap_key(b) for b in without.state_snapshots
    ]
    events_equal = events(with_zone) == events(without)
    stored_equal = events(with_zone) == events(manifest)
    logger.info(
        "{}: snapshots_equal={} non_zone_events_equal={} matches_stored={} "
        "non_zone_events={} zone_events={}",
        run_dir.name, snaps_equal, events_equal, stored_equal,
        len(events(with_zone)),
        len(with_zone.game_events) - len(events(with_zone)),
    )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("extract")
    ex.add_argument("run_dir", type=Path)
    ex.add_argument("video", type=Path)
    ev = sub.add_parser("evaluate")
    ev.add_argument("run_dirs", type=Path, nargs="+")
    ev.add_argument("--gt", type=Path, default=STUDY_DIR / "gt" / "gt_timeline.json")
    ev.add_argument("--out", type=Path, default=STUDY_DIR / "transition_metrics.json")
    inv = sub.add_parser("invariance")
    inv.add_argument("run_dirs", type=Path, nargs="+")
    mg = sub.add_parser("merge-gt")
    mg.add_argument("run_dirs", type=Path, nargs="+")
    mg.add_argument("--gt-dir", type=Path, default=STUDY_DIR / "gt")
    mg.add_argument("--out", type=Path, default=STUDY_DIR / "gt" / "gt_timeline.json")
    au = sub.add_parser("audit-evidence")
    au.add_argument("run_dirs", type=Path, nargs="+")
    au.add_argument("--gt", type=Path, default=STUDY_DIR / "gt" / "gt_timeline.json")
    au.add_argument("--out", type=Path, default=STUDY_DIR / "evidence_audit.json")
    args = parser.parse_args()
    if args.command == "merge-gt":
        merge_gt(args.run_dirs, args.gt_dir, args.out)
        return
    if args.command == "audit-evidence":
        audit_evidence(args.run_dirs, args.gt, args.out)
        return
    config = load_config(args.config)
    if args.command == "extract":
        extract(args.run_dir, args.video, config)
    elif args.command == "evaluate":
        evaluate(args.run_dirs, args.gt, config, args.out)
    else:
        for run_dir in args.run_dirs:
            invariance(run_dir, config)


if __name__ == "__main__":
    main()
