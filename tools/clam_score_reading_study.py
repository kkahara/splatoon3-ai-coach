#!/usr/bin/env python3
"""Observe-only Clam Blitz scoreboard Stage 1 study.

Authoritative measurement path (do NOT use production ScoreDetector):

    Clam video → study-local reader → left/right points (+ polarity) → GT → REPORT

Subcommands:

- ``confirm``: require clam_blitz + usable in_match (writes/validates runs.json)
- ``extract``: deliberate full frames from the two locked videos
- ``read``: study-local digit reader with winning polarity per side
- ``compare``: exact-value + visibility vs gt.json; value-range / digit-width stats

Screen geometry only (left/right). No ally/opponent, fusion, or GameEvent.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
import yaml
from loguru import logger
from pydantic import BaseModel, Field

from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.types import NormalizedBox
from splatoon3_ai_coach.vision.models import ScoreSideReading
from splatoon3_ai_coach.vision.roi import crop_roi, pixel_box_from_normalized
from splatoon3_ai_coach.vision.score import (
    _candidate_rank,
    _digit_pairs_from_seg,
    _segment_score_roi,
)
from splatoon3_ai_coach.vision.templates import load_templates

from splatoon3_ai_coach.vision.timer import segment_timer_roi

STUDY_DIR = PROJECT_ROOT / "analysis" / "clam_blitz_score_survey"
DEFAULT_ROIS = STUDY_DIR / "rois.yaml"
DEFAULT_READINGS = STUDY_DIR / "readings.json"
DEFAULT_GT = STUDY_DIR / "gt" / "gt.json"
DEFAULT_RUNS = STUDY_DIR / "runs.json"
DEFAULT_TEMPLATES = PROJECT_ROOT / "calibration" / "templates"

Polarity = Literal["normal", "inverted", "white_on_color", "none"]

# Deliberate matrix from scrubbing confirmed in_match (Remaining countdown).
EXTRACT_PLAN: list[dict[str, Any]] = [
    {
        "run": "en_humpback",
        "video": "2026-09-04 21-39-09.mov",
        "language": "en",
        "times": [
            (9.0, "opening_low"),
            (29.0, "opening_repeat"),
            (89.0, "2digit_vs_3digit"),
            (169.0, "2digit_vs_2digit"),
            (249.0, "late_one_sided"),
            (269.0, "late_both_2digit"),
        ],
    },
    {
        "run": "ja_hammerhead",
        "video": "2026-09-05 09-34-24.mov",
        "language": "ja",
        "times": [
            (23.5, "opening_low"),
            (83.5, "3digit_vs_2digit"),
            (103.5, "mid_asymmetric"),
            (223.5, "late_asymmetric"),
            (303.5, "late_one_sided"),
            (323.5, "late_both"),
        ],
    },
]


class StudySideReading(BaseModel):
    """One counter observation with winning polarity evidence."""

    value: int | None = None
    digit_scores: list[float] = Field(default_factory=list)
    visible: bool = False
    polarity: Polarity = "none"


class StudyFrameReading(BaseModel):
    """Dual-counter study reading (not production ScoreReading)."""

    battle_mode_id: str = "clam_blitz"
    left: StudySideReading
    right: StudySideReading
    confidence: float = 0.0


class GroundTruthSide(BaseModel):
    """Manual GT for one counter."""

    value: int | None = None
    visible: bool = True


class GroundTruthFrame(BaseModel):
    """Manual GT for one sample frame."""

    left: GroundTruthSide
    right: GroundTruthSide
    cell: str | None = None
    language: str | None = None
    notes: str | None = None


def _movies_dir() -> Path:
    return Path.home() / "Movies"


def _load_rois(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid rois.yaml: {path}")
    for key in ("left_roi", "right_roi"):
        if key not in data:
            raise ValueError(f"rois.yaml missing {key}")
    return data


def _white_on_color_seg(crop: np.ndarray):
    """Segment bright/white digits on saturated team-colored pods."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 0, 170), (180, 90, 255))
    return segment_timer_roi(cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR), min_area=25)


def read_study_side(
    image: np.ndarray,
    roi_box: NormalizedBox,
    templates: dict[str, list[np.ndarray]],
    *,
    match_threshold: float,
) -> StudySideReading:
    """Study-local side reader; record winning polarity evidence.

    Tries normal, inverted, then white-on-color (filled team pods).
    """
    crop = crop_roi(image, roi_box)
    digit_templates = {k: v for k, v in templates.items() if k in "0123456789"}
    best: StudySideReading | None = None
    attempts: list[tuple[Polarity, object]] = [
        ("normal", lambda: _segment_score_roi(crop, invert=False)),
        ("inverted", lambda: _segment_score_roi(crop, invert=True)),
        ("white_on_color", lambda: _white_on_color_seg(crop)),
    ]
    for polarity, seg_fn in attempts:
        seg = seg_fn()
        pairs = _digit_pairs_from_seg(
            seg, digit_templates, match_threshold=match_threshold
        )
        if not pairs:
            continue
        symbols = [p[1] for p in pairs]
        scores = [p[2] for p in pairs]
        try:
            value = int("".join(symbols))
        except ValueError:
            continue
        # Remaining counters are 0–100; >100 is almost always a false prepend.
        if value > 100:
            continue
        candidate = StudySideReading(
            value=value,
            digit_scores=scores,
            visible=True,
            polarity=polarity,
        )
        rank_cand = ScoreSideReading(
            value=value, digit_scores=scores, visible=True
        )
        if best is None:
            best = candidate
            continue
        prev = ScoreSideReading(
            value=best.value,
            digit_scores=best.digit_scores,
            visible=True,
        )
        if _candidate_rank(rank_cand) > _candidate_rank(prev):
            best = candidate
    return best if best is not None else StudySideReading(visible=False, polarity="none")


def read_study_frame(
    image: np.ndarray,
    rois: dict[str, Any],
    templates: dict[str, list[np.ndarray]],
    *,
    match_threshold: float = 0.55,
) -> StudyFrameReading:
    """Dual-counter study reading with per-side polarity."""
    left = read_study_side(
        image,
        tuple(rois["left_roi"]),
        templates,
        match_threshold=match_threshold,
    )
    right = read_study_side(
        image,
        tuple(rois["right_roi"]),
        templates,
        match_threshold=match_threshold,
    )
    scores = list(left.digit_scores) + list(right.digit_scores)
    confidence = float(np.mean(scores)) if scores else 0.0
    return StudyFrameReading(
        battle_mode_id="clam_blitz",
        left=left,
        right=right,
        confidence=confidence,
    )


def _extract_frame(video: Path, time_s: float) -> np.ndarray:
    """Seek and decode one BGR frame at ``time_s``."""
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"could not open video: {video}")
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, time_s * 1000.0)
        ok, frame = cap.read()
        if not ok or frame is None:
            raise RuntimeError(f"failed to read t={time_s} from {video}")
        return frame
    finally:
        cap.release()


def cmd_confirm(args: argparse.Namespace) -> int:
    """Validate runs.json mode evidence; exit non-zero if unusable."""
    path = Path(args.runs)
    if not path.is_file():
        logger.error("missing {}; hard mode confirm required before ROI/extract", path)
        return 1
    data = json.loads(path.read_text(encoding="utf-8"))
    runs = data.get("runs") or []
    if len(runs) < 2:
        logger.error("need at least EN+JA runs in {}", path)
        return 1
    ok = True
    for run in runs:
        mode = run.get("battle_mode_id")
        t0, t1 = run.get("match_start"), run.get("match_end")
        if mode != "clam_blitz":
            logger.error("{}: battle_mode_id={!r} (want clam_blitz)", run.get("run"), mode)
            ok = False
        if t0 is None or t1 is None or float(t1) <= float(t0):
            logger.error("{}: unusable match interval [{}, {}]", run.get("run"), t0, t1)
            ok = False
        else:
            logger.info(
                "{} ok mode={} in_match=[{}, {}] lang={}",
                run.get("run"),
                mode,
                t0,
                t1,
                run.get("language"),
            )
    return 0 if ok else 1


def cmd_extract(args: argparse.Namespace) -> int:
    """Extract deliberate full-frame sample PNGs + manifest."""
    movies = Path(args.movies_dir)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []

    for entry in EXTRACT_PLAN:
        run = entry["run"]
        video_path = movies / entry["video"]
        if not video_path.is_file():
            logger.error("missing video {}", video_path)
            return 1
        run_dir = out_root / run
        run_dir.mkdir(parents=True, exist_ok=True)
        for time_s, cell in entry["times"]:
            frame = _extract_frame(video_path, float(time_s))
            name = f"t{float(time_s):07.1f}.png"
            dest = run_dir / name
            cv2.imwrite(str(dest), frame)
            frame_id = f"{run}/{name}"
            manifest.append(
                {
                    "frame_id": frame_id,
                    "path": str(dest.relative_to(STUDY_DIR)),
                    "run": run,
                    "language": entry["language"],
                    "video": entry["video"],
                    "time_s": float(time_s),
                    "cell": cell,
                    "width": int(frame.shape[1]),
                    "height": int(frame.shape[0]),
                }
            )
            logger.info(
                "wrote {} ({}x{}) cell={}",
                dest,
                frame.shape[1],
                frame.shape[0],
                cell,
            )

    manifest_path = out_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    logger.info("manifest {} ({} frames)", manifest_path, len(manifest))
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    """Read all sample frames → readings.json + debug crops."""
    rois = _load_rois(Path(args.rois))
    templates = load_templates(Path(args.templates))
    samples_root = Path(args.samples)
    manifest_path = samples_root / "manifest.json"
    if not manifest_path.is_file():
        logger.error("missing {}; run extract first", manifest_path)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    debug_root = STUDY_DIR / "debug"
    debug_root.mkdir(parents=True, exist_ok=True)

    frames_out: list[dict[str, Any]] = []
    for item in manifest:
        frame_path = STUDY_DIR / item["path"]
        image = cv2.imread(str(frame_path))
        if image is None:
            logger.error("could not read {}", frame_path)
            return 1
        reading = read_study_frame(
            image,
            rois,
            templates,
            match_threshold=float(args.match_threshold),
        )
        debug_stem = item["frame_id"].replace("/", "__")
        if debug_stem.endswith(".png"):
            debug_stem = debug_stem[: -len(".png")]
        _write_debug(image, rois, reading, debug_root / f"{debug_stem}.png")
        frames_out.append(
            {
                "frame_id": item["frame_id"],
                "path": item["path"],
                "run": item["run"],
                "language": item["language"],
                "time_s": float(item["time_s"]),
                "cell": item["cell"],
                "reading": reading.model_dump(),
            }
        )
        logger.info(
            "{} L={}({}) R={}({}) conf={:.3f}",
            item["frame_id"],
            reading.left.value,
            reading.left.polarity,
            reading.right.value,
            reading.right.polarity,
            reading.confidence,
        )

    payload = {
        "authority": "study-local clam reader (not production ScoreDetector)",
        "rois": str(Path(args.rois)),
        "match_threshold": float(args.match_threshold),
        "frames": frames_out,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote {} ({} frames)", out, len(frames_out))
    return 0


def _write_debug(
    image: np.ndarray,
    rois: dict[str, Any],
    reading: StudyFrameReading,
    path: Path,
) -> None:
    """Save annotated top HUD strip for review."""
    h, w = image.shape[:2]
    band = image[0 : min(h, 240), :].copy()
    for name, key, side in (
        ("L", "left_roi", reading.left),
        ("R", "right_roi", reading.right),
    ):
        x1, y1, x2, y2 = pixel_box_from_normalized(tuple(rois[key]), w, h)
        cv2.rectangle(band, (x1, y1), (x2, y2), (0, 255, 255), 2)
        label = (
            f"{name}={side.value if side.visible else '?'} {side.polarity}"
        )
        cv2.putText(
            band,
            label,
            (x1, max(y1 - 6, 14)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), band)


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare readings.json against manual GT; print side-level metrics."""
    readings = json.loads(Path(args.readings).read_text(encoding="utf-8"))
    gt_raw = json.loads(Path(args.gt).read_text(encoding="utf-8"))
    gt_frames: dict[str, GroundTruthFrame] = {
        fid: GroundTruthFrame.model_validate(body)
        for fid, body in gt_raw.get("frames", gt_raw).items()
    }

    n = 0
    left_exact = 0
    right_exact = 0
    both_exact = 0
    vis_ok = 0
    vis_n = 0
    failures: list[dict[str, Any]] = []
    left_vals: list[int] = []
    right_vals: list[int] = []
    digit_widths: Counter[int] = Counter()
    polarity_counts: Counter[str] = Counter()

    for frame in readings["frames"]:
        fid = frame["frame_id"]
        reading = StudyFrameReading.model_validate(frame["reading"])
        for side in (reading.left, reading.right):
            polarity_counts[side.polarity] += 1
            if side.visible and side.value is not None:
                width = len(str(side.value))
                digit_widths[width] += 1
        if reading.left.visible and reading.left.value is not None:
            left_vals.append(int(reading.left.value))
        if reading.right.visible and reading.right.value is not None:
            right_vals.append(int(reading.right.value))

        if fid not in gt_frames:
            logger.warning("no GT for {}", fid)
            continue
        gt = gt_frames[fid]
        n += 1
        left_hit = _side_exact(reading.left, gt.left)
        right_hit = _side_exact(reading.right, gt.right)
        if left_hit:
            left_exact += 1
        if right_hit:
            right_exact += 1
        if left_hit and right_hit:
            both_exact += 1
        else:
            failures.append(
                {
                    "frame_id": fid,
                    "pred_left": reading.left.value,
                    "gt_left": gt.left.value,
                    "pred_right": reading.right.value,
                    "gt_right": gt.right.value,
                    "left_polarity": reading.left.polarity,
                    "right_polarity": reading.right.polarity,
                    "cell": frame.get("cell"),
                    "language": frame.get("language"),
                }
            )
        for pred_side, gt_side in (
            (reading.left, gt.left),
            (reading.right, gt.right),
        ):
            vis_n += 1
            if pred_side.visible == gt_side.visible:
                vis_ok += 1

    metrics = {
        "n_frames": n,
        "left_exact_accuracy": left_exact / n if n else 0.0,
        "right_exact_accuracy": right_exact / n if n else 0.0,
        "both_sides_exact_accuracy": both_exact / n if n else 0.0,
        "visibility_accuracy": vis_ok / vis_n if vis_n else 0.0,
        "left_exact": left_exact,
        "right_exact": right_exact,
        "both_exact": both_exact,
        "visibility_ok": vis_ok,
        "visibility_n": vis_n,
        "left_value_range": (
            [min(left_vals), max(left_vals)] if left_vals else None
        ),
        "right_value_range": (
            [min(right_vals), max(right_vals)] if right_vals else None
        ),
        "digit_width_counts": {str(k): int(v) for k, v in sorted(digit_widths.items())},
        "polarity_counts": dict(polarity_counts),
        "failures": failures,
        "authority": "study-local clam reader (not production ScoreDetector)",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(
        f"frames={n}  left={metrics['left_exact_accuracy']:.3f}  "
        f"right={metrics['right_exact_accuracy']:.3f}  "
        f"both={metrics['both_sides_exact_accuracy']:.3f}  "
        f"vis={metrics['visibility_accuracy']:.3f}"
    )
    print(f"wrote {out}")
    return 0


def _side_exact(pred: StudySideReading, gt: GroundTruthSide) -> bool:
    """Exact-value match when GT says visible; both invisible otherwise."""
    if not gt.visible:
        return not pred.visible
    return pred.visible and pred.value == gt.value


def build_parser() -> argparse.ArgumentParser:
    """CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_conf = sub.add_parser("confirm", help="Validate clam mode + match bounds")
    p_conf.add_argument("--runs", type=Path, default=DEFAULT_RUNS)
    p_conf.set_defaults(func=cmd_confirm)

    p_ext = sub.add_parser("extract", help="Extract sample frames from Movies/")
    p_ext.add_argument("--movies-dir", type=Path, default=_movies_dir())
    p_ext.add_argument("--out", type=Path, default=STUDY_DIR / "samples")
    p_ext.set_defaults(func=cmd_extract)

    p_read = sub.add_parser("read", help="Study-local reader on samples")
    p_read.add_argument("--rois", type=Path, default=DEFAULT_ROIS)
    p_read.add_argument("--samples", type=Path, default=STUDY_DIR / "samples")
    p_read.add_argument("--templates", type=Path, default=DEFAULT_TEMPLATES)
    p_read.add_argument("--match-threshold", type=float, default=0.55)
    p_read.add_argument("--out", type=Path, default=DEFAULT_READINGS)
    p_read.set_defaults(func=cmd_read)

    p_cmp = sub.add_parser("compare", help="Compare readings vs GT")
    p_cmp.add_argument("--readings", type=Path, default=DEFAULT_READINGS)
    p_cmp.add_argument("--gt", type=Path, default=DEFAULT_GT)
    p_cmp.add_argument("--out", type=Path, default=STUDY_DIR / "metrics.json")
    p_cmp.set_defaults(func=cmd_compare)

    return parser


def main() -> int:
    """Entry point."""
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
