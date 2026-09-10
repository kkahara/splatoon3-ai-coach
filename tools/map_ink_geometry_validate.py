#!/usr/bin/env python3
"""Offline multi-frame geometry validation for Manta Maria + Museum d'Alfonsino.

Validation-only. Discovers MAP_OVERLAY intervals from existing analysis manifests,
decodes candidate frames, attributes stage via Sanpo paint-map silhouette match
(not a production detector), diversity-selects 3–5 frames per stage, supplements
with manual PNGs, and writes diagnostics + report.

Does not modify classifier/HSV, scenarios, CoachInput, or MapObservationClock.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

logger.disable("splatoon3_ai_coach")

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.vision.map_ink import (
    MapInkClassifier,
    MapObservation,
    analyze_map_ink,
    write_map_ink_diagnostic,
)
from splatoon3_ai_coach.vision.stage_maps import resolve_stage_map_geometry

# ---------------------------------------------------------------------------
# Explicit knobs (do not bury thresholds in magic literals mid-logic)
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parents[1]
ANALYSIS_ROOT = REPO / "analysis"
MOVIES_ROOT = Path("/Users/kenjikahara/Movies")
PAINT_MAP_DIR = REPO / "calibration" / "templates" / "paint_map"
GEOMETRY_DIR = REPO / "configs" / "stage_maps"
OUT_ROOT = ANALYSIS_ROOT / "map_ink_validation"
DIAG_DIR = OUT_ROOT / "multi_frame" / "debug_map_ink"
MANUAL_ROOT = OUT_ROOT / "manual"
METRICS_PATH = OUT_ROOT / "metrics.json"
REPORT_PATH = OUT_ROOT / "VALIDATION_REPORT.md"

TARGET_STAGES: tuple[str, ...] = ("manta_maria", "museum_dalfonsino")

# Central panel used for silhouette attribution (avoids HUD corners).
PANEL_BOX = (0.22, 0.12, 0.78, 0.90)
EDGE_SIG_SIZE = (256, 320)
# Absolute edge-NCC floor for accepting attribution (Sanpo and/or *_game
# silhouette). Tuned so known *_game.png self-matches strongly and live
# Manta frames that top the distractor set clear the bar.
STAGE_MATCH_THRESHOLD = 0.15
# Best stage must beat the runner-up by at least this margin.
STAGE_MATCH_MARGIN = 0.03

MAX_CANDIDATES_PER_INTERVAL = 3
MAX_CANDIDATES_PER_VIDEO = 12
SELECT_PER_STAGE = 5
MIN_USEFUL_FRAMES = 3
NONE_SENTINEL = -1.0

SNAPSHOT_NAME_RE = re.compile(r"_(\d+\.\d+)\.jpe?g$", re.IGNORECASE)


@dataclass
class OverlayInterval:
    video_stem: str
    analysis_dir: Path
    video_path: Path | None
    start_time: float
    end_time: float
    interval_id: str


@dataclass
class CandidateFrame:
    stage_id: str | None
    source_type: str  # video | manual
    video_stem: str | None
    timestamp: float | None
    source_path: str
    overlay_start: float | None
    overlay_end: float | None
    interval_id: str | None
    image: np.ndarray
    manta_score: float = 0.0
    museum_score: float = 0.0
    best_stage: str | None = None
    best_score: float = 0.0
    distractor_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class SelectedResult:
    candidate: CandidateFrame
    observation: MapObservation
    diagnostic_path: str
    geometry_notes: list[str]


def main() -> int:
    """Run multi-frame geometry validation for the two target stages."""
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    DIAG_DIR.mkdir(parents=True, exist_ok=True)
    _ensure_manual_dirs()

    config = load_config(default_config_path())
    classifier = MapInkClassifier(config.vision.map_ink)
    sanpo_refs = _load_sanpo_edge_refs()
    if not all(s in sanpo_refs for s in TARGET_STAGES):
        raise SystemExit(f"Missing Sanpo paint maps for {TARGET_STAGES}")

    inventory = _discover_overlay_inventory()
    raw_candidates = _generate_timestamp_candidates(inventory)
    print(
        f"inventory: overlay_intervals={len(inventory)} "
        f"videos={len({i.video_stem for i in inventory})} "
        f"timestamp_candidates={len(raw_candidates)}"
    )

    decoded = _decode_candidates(raw_candidates)
    print(f"decoded frames: {len(decoded)}")

    attributed: dict[str, list[CandidateFrame]] = {s: [] for s in TARGET_STAGES}
    for cand in decoded:
        _attribute_stage(cand, sanpo_refs)
        if (
            cand.best_stage in TARGET_STAGES
            and cand.best_score >= STAGE_MATCH_THRESHOLD
            and _margin_ok(cand)
            and cand.best_stage is not None
        ):
            cand.stage_id = cand.best_stage
            attributed[cand.best_stage].append(cand)

    for stage, items in attributed.items():
        print(f"attributed {stage}: {len(items)}")

    selected: dict[str, list[CandidateFrame]] = {}
    for stage in TARGET_STAGES:
        video_picks = _diversity_select(attributed[stage], SELECT_PER_STAGE)
        manuals = _load_manual_frames(stage)
        # Prefer video diversity; append manuals (always include known *_game.png).
        combined = list(video_picks)
        for manual in manuals:
            if len(combined) >= SELECT_PER_STAGE and any(
                c.source_type == "video" for c in combined
            ):
                # Keep manuals as extras up to SELECT+2 for inspection.
                if len(combined) >= SELECT_PER_STAGE + 2:
                    break
            combined.append(manual)
        selected[stage] = combined
        print(
            f"selected {stage}: video={sum(1 for c in combined if c.source_type=='video')} "
            f"manual={sum(1 for c in combined if c.source_type=='manual')}"
        )

    results: dict[str, list[SelectedResult]] = {}
    metrics_payload: dict[str, object] = {
        "stage_match_threshold": STAGE_MATCH_THRESHOLD,
        "stage_match_margin": STAGE_MATCH_MARGIN,
        "stages": {},
    }
    for stage in TARGET_STAGES:
        geometry = resolve_stage_map_geometry(
            GEOMETRY_DIR, stage_id=stage, battle_mode_id=None
        )
        if geometry is None:
            raise SystemExit(f"Missing geometry pack for {stage}")
        stage_results: list[SelectedResult] = []
        for cand in selected[stage]:
            obs = analyze_map_ink(
                cand.image,
                geometry,
                classifier,
                video_time=float(cand.timestamp or 0.0),
                battle_mode_id=None,
            )
            stem = _diagnostic_stem(stage, cand)
            out = write_map_ink_diagnostic(
                DIAG_DIR,
                image=cand.image,
                observation=obs,
                geometry=geometry,
                classifier=classifier,
                stem=stem,
            )
            # Also persist raw frame for visual geometry review.
            raw_path = DIAG_DIR / f"{stem}_raw.jpg"
            cv2.imwrite(str(raw_path), cand.image)
            notes = _geometry_auto_notes(stage, geometry, cand.image, obs)
            stage_results.append(
                SelectedResult(
                    candidate=cand,
                    observation=obs,
                    diagnostic_path=str(out.relative_to(REPO)),
                    geometry_notes=notes,
                )
            )
        results[stage] = stage_results
        metrics_payload["stages"][stage] = _stage_metrics_block(
            stage, inventory, attributed[stage], stage_results
        )

    report = _render_report(inventory, attributed, results)
    REPORT_PATH.write_text(report, encoding="utf-8")
    METRICS_PATH.write_text(
        json.dumps(metrics_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {REPORT_PATH}")
    print(f"wrote {METRICS_PATH}")
    print(f"diagnostics under {DIAG_DIR}")
    return 0


def _ensure_manual_dirs() -> None:
    """Create manual PNG folders + README."""
    for stage in TARGET_STAGES:
        path = MANUAL_ROOT / stage
        path.mkdir(parents=True, exist_ok=True)
    readme = MANUAL_ROOT / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Manual map-open frames (supplemental)\n\n"
            "Drop extra gameplay map screenshots here when video yield is thin:\n\n"
            "- `manta_maria/*.png`\n"
            "- `museum_dalfonsino/*.png`\n\n"
            "Used only by `tools/map_ink_geometry_validate.py` for geometry "
            "consistency checks. Not a production data source.\n",
            encoding="utf-8",
        )


def _load_sanpo_edge_refs() -> dict[str, list[np.ndarray]]:
    """Load edge signatures per stage (Sanpo + optional ``*_game`` variants).

    Scoring takes the max over Sanpo and known gameplay silhouettes for that
    stage. Still validation-only attribution — not a production detector.
    """
    refs: dict[str, list[np.ndarray]] = {}
    for path in sorted(PAINT_MAP_DIR.glob("*.png")):
        image = cv2.imread(str(path))
        if image is None:
            continue
        stem = path.stem
        stage_id = stem[:-5] if stem.endswith("_game") else stem
        refs.setdefault(stage_id, []).append(_edge_signature(image))
    return refs


def _edge_signature(image: np.ndarray) -> np.ndarray:
    """Normalized edge map of the central map panel for silhouette NCC."""
    crop = _crop_norm(image, PANEL_BOX)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 40, 120)
    edges = cv2.resize(edges, EDGE_SIG_SIZE, interpolation=cv2.INTER_AREA)
    arr = edges.astype(np.float32)
    return (arr - arr.mean()) / (arr.std() + 1e-6)


def _crop_norm(image: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = box
    left, top = int(x1 * width), int(y1 * height)
    right, bottom = int(x2 * width), int(y2 * height)
    return image[top:bottom, left:right]


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    return float((a * b).mean())


def _discover_overlay_inventory() -> list[OverlayInterval]:
    """Collect MAP_OVERLAY intervals from analysis manifests with optional movies."""
    intervals: list[OverlayInterval] = []
    for analysis_dir in sorted(ANALYSIS_ROOT.iterdir()):
        if not analysis_dir.is_dir():
            continue
        manifest_path = analysis_dir / "vision_manifest.json"
        if not manifest_path.is_file():
            continue
        # Skip nested validation / variant dirs without standard stems.
        if analysis_dir.name.startswith("map_ink") or "player_count" in analysis_dir.name:
            continue
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Bad JSON {}", manifest_path)
            continue
        video_path = MOVIES_ROOT / f"{analysis_dir.name}.mov"
        video = video_path if video_path.is_file() else None
        events = data.get("game_events") or []
        for index, event in enumerate(events):
            if event.get("event_type") != "map_overlay":
                continue
            start = float(event.get("start_time", 0.0))
            end = float(event.get("end_time", start))
            if end < start:
                end = start
            intervals.append(
                OverlayInterval(
                    video_stem=analysis_dir.name,
                    analysis_dir=analysis_dir,
                    video_path=video,
                    start_time=start,
                    end_time=end,
                    interval_id=f"{analysis_dir.name}#{index}:{start:.3f}-{end:.3f}",
                )
            )
    return intervals


def _generate_timestamp_candidates(
    intervals: list[OverlayInterval],
) -> list[tuple[OverlayInterval, float]]:
    """Sparse times across intervals/videos (not consecutive cadence frames)."""
    by_video: dict[str, list[OverlayInterval]] = {}
    for item in intervals:
        by_video.setdefault(item.video_stem, []).append(item)

    candidates: list[tuple[OverlayInterval, float]] = []
    for _stem, items in sorted(by_video.items()):
        video_count = 0
        for interval in items:
            if video_count >= MAX_CANDIDATES_PER_VIDEO:
                break
            times = _sample_interval_times(interval.start_time, interval.end_time)
            for t in times:
                if video_count >= MAX_CANDIDATES_PER_VIDEO:
                    break
                candidates.append((interval, t))
                video_count += 1
    return candidates


def _sample_interval_times(start: float, end: float) -> list[float]:
    """Mid / interior samples for an overlay interval."""
    duration = max(0.0, end - start)
    if duration < 0.05:
        return [start]
    if duration < 1.0:
        return [start + 0.5 * duration]
    # Long interval: up to 3 separated interior points.
    fracs = [0.25, 0.50, 0.75][:MAX_CANDIDATES_PER_INTERVAL]
    return [start + duration * f for f in fracs]


def _decode_candidates(
    candidates: list[tuple[OverlayInterval, float]],
) -> list[CandidateFrame]:
    """Decode via debug snapshot when possible, else OpenCV seek."""
    out: list[CandidateFrame] = []
    # Cache VideoCapture per stem.
    caps: dict[str, cv2.VideoCapture] = {}
    snap_index: dict[str, list[tuple[float, Path]]] = {}

    try:
        for interval, timestamp in candidates:
            image = _load_snapshot_near(interval.analysis_dir, timestamp, snap_index)
            source_path = ""
            if image is not None:
                source_path = str(
                    _nearest_snapshot_path(interval.analysis_dir, timestamp, snap_index)
                )
            elif interval.video_path is not None:
                image = _seek_frame(caps, interval.video_path, timestamp)
                source_path = str(interval.video_path)
            if image is None:
                continue
            out.append(
                CandidateFrame(
                    stage_id=None,
                    source_type="video",
                    video_stem=interval.video_stem,
                    timestamp=float(timestamp),
                    source_path=source_path,
                    overlay_start=interval.start_time,
                    overlay_end=interval.end_time,
                    interval_id=interval.interval_id,
                    image=image,
                )
            )
    finally:
        for cap in caps.values():
            cap.release()
    return out


def _load_snapshot_near(
    analysis_dir: Path,
    timestamp: float,
    cache: dict[str, list[tuple[float, Path]]],
) -> np.ndarray | None:
    path = _nearest_snapshot_path(analysis_dir, timestamp, cache)
    if path is None:
        return None
    # Accept only if within 0.6s of the requested overlay time.
    match = SNAPSHOT_NAME_RE.search(path.name)
    if match is None:
        return None
    snap_t = float(match.group(1))
    if abs(snap_t - timestamp) > 0.6:
        return None
    return cv2.imread(str(path))


def _nearest_snapshot_path(
    analysis_dir: Path,
    timestamp: float,
    cache: dict[str, list[tuple[float, Path]]],
) -> Path | None:
    key = str(analysis_dir)
    if key not in cache:
        snap_dir = analysis_dir / "debug_snapshots"
        entries: list[tuple[float, Path]] = []
        if snap_dir.is_dir():
            for path in snap_dir.glob("*.jpg"):
                match = SNAPSHOT_NAME_RE.search(path.name)
                if match:
                    entries.append((float(match.group(1)), path))
            entries.sort()
        cache[key] = entries
    entries = cache[key]
    if not entries:
        return None
    best = min(entries, key=lambda item: abs(item[0] - timestamp))
    return best[1]


def _seek_frame(
    caps: dict[str, cv2.VideoCapture],
    video_path: Path,
    timestamp: float,
) -> np.ndarray | None:
    key = str(video_path)
    if key not in caps:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            return None
        caps[key] = cap
    cap = caps[key]
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, timestamp) * 1000.0)
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    return frame


def _attribute_stage(
    cand: CandidateFrame, refs: dict[str, list[np.ndarray]]
) -> None:
    """Sanpo (+ optional gameplay) silhouette scores; validation-only."""
    sig = _edge_signature(cand.image)
    scores = {
        stage_id: max(_ncc(sig, ref) for ref in variants)
        for stage_id, variants in refs.items()
    }
    cand.distractor_scores = {
        k: float(v) for k, v in scores.items() if k not in TARGET_STAGES
    }
    cand.manta_score = float(scores.get("manta_maria", 0.0))
    cand.museum_score = float(scores.get("museum_dalfonsino", 0.0))
    best_stage = max(scores, key=scores.get)
    cand.best_stage = best_stage
    cand.best_score = float(scores[best_stage])


def _margin_ok(cand: CandidateFrame) -> bool:
    scores = {
        "manta_maria": cand.manta_score,
        "museum_dalfonsino": cand.museum_score,
        **cand.distractor_scores,
    }
    ordered = sorted(scores.values(), reverse=True)
    if len(ordered) < 2:
        return cand.best_score >= STAGE_MATCH_THRESHOLD
    return (ordered[0] - ordered[1]) >= STAGE_MATCH_MARGIN


def _feature_vector(obs: MapObservation) -> np.ndarray:
    def _v(value: float | None) -> float:
        return NONE_SENTINEL if value is None else float(value)

    return np.array(
        [
            _v(obs.ally_classified_fraction),
            _v(obs.opponent_classified_fraction),
            _v(obs.classified_fraction),
        ],
        dtype=np.float64,
    )


def _diversity_select(
    candidates: list[CandidateFrame],
    k: int,
) -> list[CandidateFrame]:
    """Greedy max-min selection preferring distinct intervals/videos + ink features."""
    if not candidates:
        return []
    # Need ink features — analyze lightly with geometry for selection only.
    config = load_config(default_config_path())
    classifier = MapInkClassifier(config.vision.map_ink)
    scored: list[tuple[CandidateFrame, np.ndarray, MapObservation]] = []
    for cand in candidates:
        assert cand.stage_id is not None
        geometry = resolve_stage_map_geometry(
            GEOMETRY_DIR, stage_id=cand.stage_id, battle_mode_id=None
        )
        if geometry is None:
            continue
        obs = analyze_map_ink(
            cand.image,
            geometry,
            classifier,
            video_time=float(cand.timestamp or 0.0),
            battle_mode_id=None,
        )
        scored.append((cand, _feature_vector(obs), obs))
    if not scored:
        return []

    # Seed: farthest from mean feature (more distinctive ink state).
    feats = np.stack([f for _, f, _ in scored])
    mean = feats.mean(axis=0)
    seed_idx = int(np.argmax(np.linalg.norm(feats - mean, axis=1)))
    selected_idx = [seed_idx]
    selected_keys = {
        (
            scored[seed_idx][0].video_stem,
            scored[seed_idx][0].interval_id,
        )
    }

    while len(selected_idx) < min(k, len(scored)):
        best_i = None
        best_score = -1.0
        for index, (cand, feat, _) in enumerate(scored):
            if index in selected_idx:
                continue
            # Distance to nearest already-selected in feature space.
            dist = min(
                float(np.linalg.norm(feat - scored[j][1])) for j in selected_idx
            )
            key = (cand.video_stem, cand.interval_id)
            # Bonus for new interval/video.
            bonus = 0.35 if key not in selected_keys else 0.0
            # Mild penalty if same video already heavily used.
            same_video = sum(
                1
                for j in selected_idx
                if scored[j][0].video_stem == cand.video_stem
            )
            penalty = 0.08 * same_video
            score = dist + bonus - penalty
            if score > best_score:
                best_score = score
                best_i = index
        if best_i is None:
            break
        selected_idx.append(best_i)
        selected_keys.add(
            (scored[best_i][0].video_stem, scored[best_i][0].interval_id)
        )

    return [scored[i][0] for i in selected_idx]


def _load_manual_frames(stage_id: str) -> list[CandidateFrame]:
    """Load known *_game.png plus manual/<stage>/*.png."""
    paths: list[Path] = []
    game = PAINT_MAP_DIR / f"{stage_id}_game.png"
    if game.is_file():
        paths.append(game)
    manual_dir = MANUAL_ROOT / stage_id
    if manual_dir.is_dir():
        paths.extend(sorted(manual_dir.glob("*.png")))
        paths.extend(sorted(manual_dir.glob("*.jpg")))
    out: list[CandidateFrame] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        image = cv2.imread(str(path))
        if image is None:
            continue
        out.append(
            CandidateFrame(
                stage_id=stage_id,
                source_type="manual",
                video_stem=None,
                timestamp=None,
                source_path=str(path.relative_to(REPO)),
                overlay_start=None,
                overlay_end=None,
                interval_id=None,
                image=image,
                best_stage=stage_id,
                best_score=1.0,
            )
        )
    return out


def _diagnostic_stem(stage: str, cand: CandidateFrame) -> str:
    if cand.source_type == "manual":
        name = Path(cand.source_path).stem
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        return f"{stage}_manual_{safe}"
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", cand.video_stem or "unknown")
    t = cand.timestamp if cand.timestamp is not None else 0.0
    return f"{stage}_{stem}_{t:08.3f}"


def _geometry_auto_notes(
    stage: str,
    geometry,
    image: np.ndarray,
    obs: MapObservation,
) -> list[str]:
    """Heuristic notes for report (human still judges overlays)."""
    notes: list[str] = []
    height, width = image.shape[:2]
    from splatoon3_ai_coach.vision.map_ink import _union_region_mask

    union = _union_region_mask(height, width, geometry.regions)
    # Soft background proxy: low edge density.
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 120)
    dens = cv2.blur(edges.astype(np.float32), (31, 31))
    soft = (dens < 5) & (gray > 60)
    soft_frac = float(np.count_nonzero(union & soft)) / max(1, int(union.sum()))
    notes.append(f"soft_bg_in_union≈{soft_frac:.2f}")
    notes.append(f"classified_fraction={obs.classified_fraction}")

    if stage == "manta_maria":
        r01 = next((r for r in geometry.regions if r.id == "R01"), None)
        if r01 is not None:
            notes.append(f"R01_roi={list(r01.roi)}")
            # Strip just above R01 top: if high ink-like green/magenta exists there,
            # flag possible top-deck miss (heuristic only).
            y1 = r01.roi[1]
            band = _crop_norm(image, (r01.roi[0], max(0.0, y1 - 0.06), r01.roi[2], y1))
            notes.append(_inkish_band_note("above_R01", band))
    if stage == "museum_dalfonsino":
        # Far-right of map panel relative to union max-x.
        ys, xs = np.where(union)
        if len(xs):
            union_x2 = xs.max() / float(width)
            notes.append(f"union_max_x={union_x2:.3f}")
            right_band = _crop_norm(image, (min(0.95, union_x2), 0.35, 0.82, 0.70))
            # Band just outside union on the right.
            outside = _crop_norm(
                image, (min(0.98, union_x2 + 0.01), 0.35, min(0.99, union_x2 + 0.08), 0.70)
            )
            notes.append(_inkish_band_note("inside_near_right", right_band))
            notes.append(_inkish_band_note("outside_right_of_union", outside))
    return notes


def _inkish_band_note(label: str, band: np.ndarray) -> str:
    if band.size == 0:
        return f"{label}:empty"
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    green = ((h >= 40) & (h <= 95) & (s >= 70) & (v >= 70)).mean()
    magenta = ((h >= 120) & (h <= 170) & (s >= 70) & (v >= 70)).mean()
    return f"{label}:greenish={green:.2f},magentish={magenta:.2f}"


def _stage_metrics_block(
    stage: str,
    inventory: list[OverlayInterval],
    attributed: list[CandidateFrame],
    results: list[SelectedResult],
) -> dict:
    video_results = [r for r in results if r.candidate.source_type == "video"]
    manual_results = [r for r in results if r.candidate.source_type == "manual"]
    frames = []
    for item in results:
        c = item.candidate
        o = item.observation
        frames.append(
            {
                "stage": stage,
                "source_type": c.source_type,
                "video_stem": c.video_stem,
                "timestamp": c.timestamp,
                "source_path": c.source_path,
                "overlay_interval": (
                    [c.overlay_start, c.overlay_end]
                    if c.overlay_start is not None
                    else None
                ),
                "interval_id": c.interval_id,
                "manta_score": c.manta_score,
                "museum_score": c.museum_score,
                "best_stage": c.best_stage,
                "best_score": c.best_score,
                "total_union_pixels": o.total_sample_pixels,
                "classified_pixels": o.classified_pixels,
                "unclassified_pixels": o.unclassified_pixels,
                "classified_fraction": o.classified_fraction,
                "ally_classified_fraction": o.ally_classified_fraction,
                "opponent_classified_fraction": o.opponent_classified_fraction,
                "ally_ink_pixels": o.ally_ink_pixels,
                "opponent_ink_pixels": o.opponent_ink_pixels,
                "diagnostic_path": item.diagnostic_path,
                "geometry_notes": item.geometry_notes,
            }
        )
    n_video = len(video_results)
    verdict = "INSUFFICIENT_FRAMES" if n_video + len(manual_results) < MIN_USEFUL_FRAMES else "PENDING_VISUAL"
    return {
        "overlay_intervals_total": len(inventory),
        "attributed_video_candidates": len(attributed),
        "selected_video_frames": n_video,
        "selected_manual_frames": len(manual_results),
        "insufficient_video_candidates": n_video < MIN_USEFUL_FRAMES,
        "verdict_placeholder": verdict,
        "frames": frames,
    }


def _render_report(
    inventory: list[OverlayInterval],
    attributed: dict[str, list[CandidateFrame]],
    results: dict[str, list[SelectedResult]],
) -> str:
    lines: list[str] = []
    lines.append("# Map ink multi-frame geometry validation\n")
    lines.append("Stages: `manta_maria`, `museum_dalfonsino`.")
    lines.append("Attribution: Sanpo paint-map edge NCC (validation-only).")
    lines.append(
        f"Thresholds: `STAGE_MATCH_THRESHOLD={STAGE_MATCH_THRESHOLD}`, "
        f"`STAGE_MATCH_MARGIN={STAGE_MATCH_MARGIN}`.\n"
    )
    lines.append(
        f"Overlay intervals discovered: **{len(inventory)}** across "
        f"**{len({i.video_stem for i in inventory})}** analysis folders.\n"
    )
    lines.append(
        "Classifier/HSV unchanged. Geometry edits only if visual review shows "
        "a **repeatable** spatial defect. Low `classified_fraction` with "
        "grill/grey unclassified is acceptable.\n"
    )

    overall: list[str] = []
    for stage in TARGET_STAGES:
        stage_results = results[stage]
        video_n = sum(1 for r in stage_results if r.candidate.source_type == "video")
        manual_n = sum(1 for r in stage_results if r.candidate.source_type == "manual")
        title = "Manta Maria" if stage == "manta_maria" else "Museum d'Alfonsino"
        lines.append(f"## {title}\n")
        lines.append("### Candidate inventory\n")
        lines.append(f"- MAP_OVERLAY intervals (global pool): {len(inventory)}")
        lines.append(f"- Stage-attributed video candidates: {len(attributed[stage])}")
        lines.append(f"- Selected video frames: {video_n}")
        lines.append(f"- Selected / included manual frames: {manual_n}")
        if video_n < MIN_USEFUL_FRAMES:
            lines.append("- **insufficient video candidates**")
        lines.append("")
        lines.append("### Per-frame table\n")
        lines.append(
            "| source | time | interval | attr | cls | ally | opp | notes |"
        )
        lines.append("|---|---:|---|---:|---:|---:|---:|---|")
        for item in stage_results:
            c = item.candidate
            o = item.observation
            src = (
                c.video_stem
                if c.source_type == "video"
                else Path(c.source_path).name
            )
            t = f"{c.timestamp:.1f}" if c.timestamp is not None else "—"
            interval = (
                f"{c.overlay_start:.1f}-{c.overlay_end:.1f}"
                if c.overlay_start is not None
                else "manual"
            )
            attr = f"{c.best_score:.3f}" if c.source_type == "video" else "manual"
            cls = _fmt(o.classified_fraction)
            ally = _fmt(o.ally_classified_fraction)
            opp = _fmt(o.opponent_classified_fraction)
            note = "; ".join(item.geometry_notes[:3])
            lines.append(
                f"| {src} | {t} | {interval} | {attr} | {cls} | {ally} | {opp} | {note} |"
            )
        lines.append("")
        lines.append("### Geometry review\n")
        if stage == "manta_maria":
            lines.append(
                "> Does Manta R01 at `y=0.10` repeatedly miss paintable top-deck area?\n"
            )
            lines.append(
                "**Conclusion (auto draft — confirm on overlays):** "
                f"{_draft_manta_conclusion(stage_results)}\n"
            )
        else:
            lines.append(
                "> Is the far-right paintable wing consistently inside the geometry union?\n"
            )
            lines.append(
                "**Conclusion (auto draft — confirm on overlays):** "
                f"{_draft_museum_conclusion(stage_results)}\n"
            )
        verdict = _draft_verdict(stage_results, video_n)
        overall.append(verdict)
        lines.append(f"**Stage verdict:** `{verdict}`\n")
        lines.append(f"Diagnostics: `{DIAG_DIR.relative_to(REPO)}/`\n")

    lines.append("## Aggregate\n")
    lines.append(f"- manta_maria: `{overall[0]}`")
    lines.append(f"- museum_dalfonsino: `{overall[1]}`")
    if all(v == "CONSISTENT" for v in overall):
        lines.append("- overall: `CONSISTENT`")
    elif any(v == "INSUFFICIENT_FRAMES" for v in overall):
        lines.append("- overall: `INSUFFICIENT_FRAMES` (see stage sections)")
    else:
        lines.append("- overall: `INCONSISTENT` or mixed — see stage sections")
    lines.append("")
    lines.append("## Freeze / next architecture (not implemented)\n")
    lines.append(
        "If both stages pass visual consistency, geometry is frozen for this phase.\n\n"
        "Next (separate task):\n\n"
        "```text\n"
        "MAP_OVERLAY interval\n"
        "    → sparse MapObservations\n"
        "    → MapObservationClock.at(t, max_gap_seconds=...)\n"
        "    → scenario / CoachInput evidence\n"
        "```\n\n"
        "Mirror `PlayerCountClock.at(..., max_gap_seconds=...)`. "
        "Maximum age is mandatory.\n"
    )
    return "\n".join(lines) + "\n"


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _draft_manta_conclusion(results: list[SelectedResult]) -> str:
    hits = 0
    for item in results:
        for note in item.geometry_notes:
            if note.startswith("above_R01:") and (
                "greenish=0." in note or "magentish=0." in note
            ):
                # Parse fractions.
                green = _parse_frac(note, "greenish=")
                magenta = _parse_frac(note, "magentish=")
                if max(green, magenta) >= 0.12:
                    hits += 1
    if len(results) < MIN_USEFUL_FRAMES:
        return "inconclusive (too few frames)"
    if hits >= max(2, len(results) // 2):
        return "repeatable defect suspected (ink-like pixels above R01) — inspect overlays"
    return "no repeatable defect suggested by band heuristic — confirm on overlays"


def _draft_museum_conclusion(results: list[SelectedResult]) -> str:
    hits = 0
    for item in results:
        outside = 0.0
        for note in item.geometry_notes:
            if note.startswith("outside_right_of_union:"):
                outside = max(
                    _parse_frac(note, "greenish="),
                    _parse_frac(note, "magentish="),
                )
        if outside >= 0.12:
            hits += 1
    if len(results) < MIN_USEFUL_FRAMES:
        return "inconclusive (too few frames)"
    if hits >= max(2, len(results) // 2):
        return "repeatable defect suspected (ink-like pixels right of union) — inspect overlays"
    return "no repeatable defect suggested by band heuristic — confirm on overlays"


def _parse_frac(note: str, key: str) -> float:
    try:
        part = note.split(key, 1)[1]
        return float(part.split(",")[0].split(")")[0])
    except (IndexError, ValueError):
        return 0.0


def _draft_verdict(results: list[SelectedResult], video_n: int) -> str:
    if len(results) < MIN_USEFUL_FRAMES:
        return "INSUFFICIENT_FRAMES"
    # Trends sanity: do ally/opponent fractions vary across frames?
    allies = [
        r.observation.ally_classified_fraction
        for r in results
        if r.observation.ally_classified_fraction is not None
    ]
    if len(allies) >= 2 and (max(allies) - min(allies)) < 0.02:
        # Not inconsistent by itself — may be similar paint states.
        pass
    if video_n < MIN_USEFUL_FRAMES:
        return "INSUFFICIENT_FRAMES"
    return "PENDING_VISUAL"


if __name__ == "__main__":
    raise SystemExit(main())
