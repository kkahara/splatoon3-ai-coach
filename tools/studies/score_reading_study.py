#!/usr/bin/env python3
"""Observe-only Splat Zones score reading study (Stage 1).

Empirical question: can the existing timer digit primitive reliably observe
the two fixed SZ remaining counters?

Subcommands:

- ``extract``: pull deliberately selected full frames from SZ videos
  (``--set stage1`` main counters, ``--set penalty`` Stage 0 ``+N`` transitions)
- ``read``: crop left/right ROIs, segment digits, match_glyph → readings.json
- ``compare``: side-level GT metrics vs gt.json, plus penalty exact / presence /
  absence on frames whose GT has ``penalty_labeled``

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

# Stage 0 penalty matrix: +N appearing, counting down, clearing, faded/hidden.
PENALTY_EXTRACT_PLAN: list[dict[str, Any]] = [
    {
        "run": "ja_kraken",
        "video": "ja_mahi_mahi_kraken_2026-09-16 20-18-04.mov",
        "language": "ja",
        "times": [
            (33.5, "penalty_right_appears"),
            (49.0, "penalty_left_appears"),
            (53.0, "penalty_countdown"),
            (54.5, "penalty_faded_tick"),
            (62.0, "penalty_last_1"),
            (62.5, "penalty_cleared_main_ticks"),
            (75.0, "penalty_both_sides"),
            (119.5, "penalty_2digit_appears"),
            (122.5, "penalty_right_last_1"),
            (123.0, "penalty_right_cleared"),
        ],
    },
    {
        "run": "en_hagglefish",
        "video": "en-hagglefish_market_2026-09-15 20-18-11.mov",
        "language": "en",
        "times": [
            (138.0, "penalty_both_2digit"),
            (150.0, "penalty_last_1"),
            (150.5, "penalty_cleared_main_ticks"),
            (191.0, "penalty_large_appears"),
            (198.0, "penalty_hidden_by_animation"),
            (234.5, "penalty_last_1_late"),
        ],
    },
]

SAMPLE_SETS: dict[str, tuple[list[dict[str, Any]], Path]] = {
    "stage1": (EXTRACT_PLAN, STUDY_DIR / "samples"),
    "penalty": (PENALTY_EXTRACT_PLAN, STUDY_DIR / "samples_penalty"),
}


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
    # True when penalties were labeled: ``None`` then means "no +N on screen".
    penalty_labeled: bool = False
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
    """Extract deliberate full-frame sample PNGs + manifest for one sample set."""
    movies = Path(args.movies_dir)
    plan, default_out = SAMPLE_SETS[args.set]
    out_root = Path(args.out) if args.out else default_out
    out_root.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    id_prefix = "" if args.set == "stage1" else f"{args.set}/"

    for entry in plan:
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
            frame_id = f"{id_prefix}{run}/{name}"
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
                "wrote {} ({}x{}) cell={}", dest, frame.shape[1], frame.shape[0], cell
            )

    manifest_path = out_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    logger.info("manifest {} ({} frames)", manifest_path, len(manifest))
    return 0


def _load_manifests(sample_dirs: list[Path]) -> list[dict[str, Any]] | None:
    """Concatenate sample manifests; ``None`` if any is missing."""
    manifest: list[dict[str, Any]] = []
    for samples_root in sample_dirs:
        manifest_path = samples_root / "manifest.json"
        if not manifest_path.is_file():
            logger.error("missing {}; run extract first", manifest_path)
            return None
        manifest.extend(json.loads(manifest_path.read_text(encoding="utf-8")))
    return manifest


def cmd_read(args: argparse.Namespace) -> int:
    """Read all sample frames → readings.json + debug crops."""
    rois = _load_rois(Path(args.rois))
    templates = load_templates(Path(args.templates))
    sample_dirs = args.samples or [d for _, d in SAMPLE_SETS.values()]
    manifest = _load_manifests([Path(d) for d in sample_dirs])
    if manifest is None:
        return 1
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
            "{} L={} R={} LP={} RP={} conf={:.3f}",
            item["frame_id"],
            reading.left.value,
            reading.right.value,
            reading.left_penalty,
            reading.right_penalty,
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
    band = image[0 : min(h, 280), :].copy()
    boxes: list[tuple[str, str, str]] = [
        ("L", "left_roi", str(reading.left.value if reading.left.visible else "?")),
        ("R", "right_roi", str(reading.right.value if reading.right.visible else "?")),
    ]
    if rois.get("left_penalty_roi") and rois.get("right_penalty_roi"):
        boxes.append(("LP", "left_penalty_roi", str(reading.left_penalty)))
        boxes.append(("RP", "right_penalty_roi", str(reading.right_penalty)))
    for name, key, shown in boxes:
        x1, y1, x2, y2 = pixel_box_from_normalized(tuple(rois[key]), w, h)
        cv2.rectangle(band, (x1, y1), (x2, y2), (0, 255, 255), 2)
        label = f"{name}={shown}"
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
    pen: dict[str, Any] = {
        "sides": 0,
        "exact": 0,
        "present_gt": 0,
        "present_exact": 0,
        "absent_gt": 0,
        "absent_ok": 0,
        "missed": 0,
        "false_positive": 0,
        "wrong_value": 0,
        "failures": [],
    }
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

        if gt.penalty_labeled:
            _tally_penalty(fid, reading, gt, pen)

    metrics = {
        "n_frames": n,
        "left_exact_accuracy": left_exact / n if n else 0.0,
        "right_exact_accuracy": right_exact / n if n else 0.0,
        "both_sides_exact_accuracy": both_exact / n if n else 0.0,
        "visibility_accuracy": vis_ok / vis_n if vis_n else 0.0,
        "penalty_exact_accuracy": pen["exact"] / pen["sides"] if pen["sides"] else None,
        "penalty_present_exact_accuracy": (
            pen["present_exact"] / pen["present_gt"] if pen["present_gt"] else None
        ),
        "penalty_absent_accuracy": (
            pen["absent_ok"] / pen["absent_gt"] if pen["absent_gt"] else None
        ),
        "left_exact": left_exact,
        "right_exact": right_exact,
        "both_exact": both_exact,
        "visibility_ok": vis_ok,
        "visibility_n": vis_n,
        "penalty": pen,
        "failures": failures,
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
    print(
        f"penalty sides={pen['sides']}  exact={pen['exact']}  "
        f"present={pen['present_exact']}/{pen['present_gt']}  "
        f"absent={pen['absent_ok']}/{pen['absent_gt']}  missed={pen['missed']}  "
        f"false_positive={pen['false_positive']}  wrong_value={pen['wrong_value']}"
    )
    print(f"wrote {out}")
    return 0


def _tally_penalty(
    frame_id: str,
    reading: ScoreReading,
    gt: GroundTruthFrame,
    pen: dict[str, Any],
) -> None:
    """Per-side penalty exact / presence / absence tallies (GT = on-screen +N)."""
    for side, pred_pen, gt_pen in (
        ("left", reading.left_penalty, gt.left_penalty),
        ("right", reading.right_penalty, gt.right_penalty),
    ):
        pen["sides"] += 1
        if pred_pen == gt_pen:
            pen["exact"] += 1
        if gt_pen is None:
            pen["absent_gt"] += 1
            if pred_pen is None:
                pen["absent_ok"] += 1
            else:
                pen["false_positive"] += 1
        else:
            pen["present_gt"] += 1
            if pred_pen == gt_pen:
                pen["present_exact"] += 1
            elif pred_pen is None:
                pen["missed"] += 1
            else:
                pen["wrong_value"] += 1
        if pred_pen != gt_pen:
            pen["failures"].append(
                {"frame_id": frame_id, "side": side, "pred": pred_pen, "gt": gt_pen}
            )


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
    p_ext.add_argument("--set", choices=sorted(SAMPLE_SETS), default="stage1")
    p_ext.add_argument("--out", type=Path, default=None)
    p_ext.set_defaults(func=cmd_extract)

    p_read = sub.add_parser("read", help="Run study reader on samples")
    p_read.add_argument("--rois", type=Path, default=DEFAULT_ROIS)
    p_read.add_argument(
        "--samples",
        type=Path,
        action="append",
        default=None,
        help="Sample dir with manifest.json (repeatable; default: all sets).",
    )
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
