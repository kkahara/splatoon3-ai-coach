#!/usr/bin/env python3
"""Observe-only Splat Zones score reading study (Stage 1).

Empirical question: can the existing timer digit primitive reliably observe
the two fixed SZ remaining counters?

Subcommands:

- ``extract``: pull deliberately selected full frames from SZ videos
- ``read``: crop left/right ROIs, segment digits, match_glyph → readings.json
- ``compare``: side-level GT metrics vs gt.json

Screen geometry only (left/right). No ally/opponent, fusion, or GameEvent.
Reader delegates to ``vision.score`` (promoted after Stage 1 REPORT green).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml
from loguru import logger
from pydantic import BaseModel

from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.models import ScoreReading, ScoreSideReading
from splatoon3_ai_coach.vision.roi import crop_roi
from splatoon3_ai_coach.vision.score import read_score_frame
from splatoon3_ai_coach.vision.templates import load_templates

STUDY_DIR = PROJECT_ROOT / "analysis" / "score_survey"
DEFAULT_ROIS = STUDY_DIR / "rois.yaml"
DEFAULT_READINGS = STUDY_DIR / "readings.json"
DEFAULT_GT = STUDY_DIR / "gt" / "gt.json"
DEFAULT_TEMPLATES = PROJECT_ROOT / "calibration" / "templates"

# Deliberate sample matrix covering Stage 1 cells (EN + JA).
EXTRACT_PLAN: list[dict[str, Any]] = [
    # EN barnicle — opening, mid, asymmetric countdown
    {
        "run": "en_barnicle",
        "video": "en-barnicle_and_dime_2026-09-14 21-13-09.mov",
        "language": "en",
        "times": [
            (20.5, "100/100"),
            (25.5, "100/100_repeat"),
            (45.5, "early_2digit"),
            (65.5, "2digit_vs_2digit"),
            (90.5, "2digit_vs_2digit_low"),
            (105.5, "2digit_vs_1digit"),
        ],
    },
    # EN hagglefish — 3-digit vs 2-digit, late 1-digit
    {
        "run": "en_hagglefish",
        "video": "en-hagglefish_market_2026-09-15 20-18-11.mov",
        "language": "en",
        "times": [
            (34.0, "100/100"),
            (49.0, "3digit_vs_2digit"),
            (64.0, "early_countdown"),
            (89.0, "mid_match"),
            (134.0, "mid_late"),
            (189.0, "1digit_vs_2digit"),
        ],
    },
    # EN marlin — alternate team colors
    {
        "run": "en_marlin",
        "video": "en-marlin_airport_2026-09-04 21-46-12.mov",
        "language": "en",
        "times": [
            (17.0, "100/100"),
            (32.0, "99ish/100"),
            (82.0, "2digit_vs_2digit"),
            (107.0, "2digit_vs_2digit_alt"),
        ],
    },
    # JA kraken
    {
        "run": "ja_kraken",
        "video": "ja_mahi_mahi_kraken_2026-09-16 20-18-04.mov",
        "language": "ja",
        "times": [
            (19.0, "100/100"),
            (34.0, "99/99ish"),
            (54.0, "mid"),
            (99.0, "mid_late"),
            (149.0, "late_both_2digit"),
        ],
    },
    # JA crab tank — includes late asymmetric
    {
        "run": "ja_crab",
        "video": "ja_brinewater_springs_crab_tank_2026-09-16 19-49-36.mp4",
        "language": "ja",
        "times": [
            (20.9, "100/100"),
            (45.9, "early"),
            (75.9, "mid"),
            (155.9, "late"),
            (200.9, "late_asymmetric"),
        ],
    },
]


class FrameRecord(BaseModel):
    """One sample frame plus its reading and metadata."""

    frame_id: str
    path: str
    run: str
    language: str
    time_s: float
    cell: str
    reading: ScoreReading


class GroundTruthSide(BaseModel):
    """Manual GT for one counter."""

    value: int | None = None
    visible: bool = True


class GroundTruthFrame(BaseModel):
    """Manual GT for one sample frame."""

    left: GroundTruthSide
    right: GroundTruthSide
    left_penalty: int | None = None
    right_penalty: int | None = None
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


def read_frame(
    image: np.ndarray,
    rois: dict[str, Any],
    templates: dict[str, list[np.ndarray]],
    *,
    match_threshold: float = 0.55,
) -> ScoreReading:
    """Produce a dual-counter reading for one full frame (delegates to vision.score)."""
    return read_score_frame(
        image,
        left_roi=tuple(rois["left_roi"]),
        right_roi=tuple(rois["right_roi"]),
        templates=templates,
        match_threshold=match_threshold,
        left_penalty_roi=(
            tuple(rois["left_penalty_roi"]) if rois.get("left_penalty_roi") else None
        ),
        right_penalty_roi=(
            tuple(rois["right_penalty_roi"]) if rois.get("right_penalty_roi") else None
        ),
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
            name = f"t{time_s:07.1f}.png".replace(" ", "0")
            # Normalize: t0020.5.png style
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
            logger.info("wrote {} ({}x{}) cell={}", dest, frame.shape[1], frame.shape[0], cell)

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

    records: list[FrameRecord] = []
    for item in manifest:
        frame_path = STUDY_DIR / item["path"]
        image = cv2.imread(str(frame_path))
        if image is None:
            logger.error("could not read {}", frame_path)
            return 1
        reading = read_frame(
            image,
            rois,
            templates,
            match_threshold=float(args.match_threshold),
        )
        # Debug: annotate ROIs on a top-band crop.
        debug_stem = item["frame_id"].replace("/", "__")
        if debug_stem.endswith(".png"):
            debug_stem = debug_stem[: -len(".png")]
        _write_debug(image, rois, reading, debug_root / f"{debug_stem}.png")
        records.append(
            FrameRecord(
                frame_id=item["frame_id"],
                path=item["path"],
                run=item["run"],
                language=item["language"],
                time_s=float(item["time_s"]),
                cell=item["cell"],
                reading=reading,
            )
        )
        logger.info(
            "{} L={} R={} conf={:.3f}",
            item["frame_id"],
            reading.left.value,
            reading.right.value,
            reading.confidence,
        )

    payload = {
        "rois": str(Path(args.rois)),
        "match_threshold": float(args.match_threshold),
        "frames": [r.model_dump() for r in records],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    logger.info("wrote {} ({} frames)", out, len(records))
    return 0


def _write_debug(
    image: np.ndarray,
    rois: dict[str, Any],
    reading: ScoreReading,
    path: Path,
) -> None:
    """Save annotated top HUD strip for review."""
    from splatoon3_ai_coach.vision.roi import pixel_box_from_normalized

    h, w = image.shape[:2]
    band = image[0 : min(h, 240), :].copy()
    for name, key, side in (
        ("L", "left_roi", reading.left),
        ("R", "right_roi", reading.right),
    ):
        x1, y1, x2, y2 = pixel_box_from_normalized(tuple(rois[key]), w, h)
        cv2.rectangle(band, (x1, y1), (x2, y2), (0, 255, 255), 2)
        label = f"{name}={side.value if side.visible else '?'}"
        cv2.putText(
            band,
            label,
            (x1, max(y1 - 6, 14)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
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
    pen_ok = 0
    pen_n = 0
    failures: list[dict[str, Any]] = []

    for frame in readings["frames"]:
        fid = frame["frame_id"]
        if fid not in gt_frames:
            logger.warning("no GT for {}", fid)
            continue
        gt = gt_frames[fid]
        reading = ScoreReading.model_validate(frame["reading"])
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
                    "pred_l_vis": reading.left.visible,
                    "gt_l_vis": gt.left.visible,
                    "pred_r_vis": reading.right.visible,
                    "gt_r_vis": gt.right.visible,
                    "cell": frame.get("cell"),
                    "language": frame.get("language"),
                }
            )

        # Visibility: predicted visible flag vs GT visible
        for pred_side, gt_side in (
            (reading.left, gt.left),
            (reading.right, gt.right),
        ):
            vis_n += 1
            if pred_side.visible == gt_side.visible:
                vis_ok += 1

        for pred_pen, gt_pen in (
            (reading.left_penalty, gt.left_penalty),
            (reading.right_penalty, gt.right_penalty),
        ):
            if gt_pen is None and pred_pen is None:
                continue
            pen_n += 1
            if pred_pen == gt_pen:
                pen_ok += 1

    metrics = {
        "n_frames": n,
        "left_exact_accuracy": left_exact / n if n else 0.0,
        "right_exact_accuracy": right_exact / n if n else 0.0,
        "both_sides_exact_accuracy": both_exact / n if n else 0.0,
        "visibility_accuracy": vis_ok / vis_n if vis_n else 0.0,
        "optional_penalty_accuracy": pen_ok / pen_n if pen_n else None,
        "left_exact": left_exact,
        "right_exact": right_exact,
        "both_exact": both_exact,
        "visibility_ok": vis_ok,
        "visibility_n": vis_n,
        "penalty_ok": pen_ok,
        "penalty_n": pen_n,
        "failures": failures,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(
        f"frames={n}  left={metrics['left_exact_accuracy']:.3f}  "
        f"right={metrics['right_exact_accuracy']:.3f}  "
        f"both={metrics['both_sides_exact_accuracy']:.3f}  "
        f"vis={metrics['visibility_accuracy']:.3f}  "
        f"penalty={metrics['optional_penalty_accuracy']}"
    )
    print(f"wrote {out}")
    return 0


def _side_exact(pred: ScoreSideReading, gt: GroundTruthSide) -> bool:
    """Exact-value match when GT says visible; both invisible otherwise."""
    if not gt.visible:
        return not pred.visible
    return pred.visible and pred.value == gt.value


def build_parser() -> argparse.ArgumentParser:
    """CLI parser with extract / read / compare subcommands."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ext = sub.add_parser("extract", help="Extract sample frames from Movies/")
    p_ext.add_argument("--movies-dir", type=Path, default=_movies_dir())
    p_ext.add_argument("--out", type=Path, default=STUDY_DIR / "samples")
    p_ext.set_defaults(func=cmd_extract)

    p_read = sub.add_parser("read", help="Run study reader on samples")
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
