#!/usr/bin/env python3
"""Calibrate fixed left/right Splat Zones score digit ROIs from a full frame.

Writes study artifact ``analysis/score_survey/rois.yaml``. Supports:

- Interactive ``cv2.selectROI`` when HighGUI is available
- Non-interactive ``--left-pixels`` / ``--right-pixels`` for headless/CI

Normalized boxes are relative to the full 1920×1080 frame (not tight crops).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import yaml

from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.vision.roi import normalized_box_from_pixels

# Reuse HighGUI helpers from the generic ROI calibrator.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from roi_calibrate import (  # noqa: E402
    load_frame,
    select_pixel_roi,
)

DEFAULT_OUT = PROJECT_ROOT / "analysis" / "score_survey" / "rois.yaml"


def _parse_pixels(raw: str) -> tuple[int, int, int, int]:
    """Parse ``x1,y1,x2,y2`` pixel string."""
    parts = [p.strip() for p in raw.replace(" ", "").split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            f"expected x1,y1,x2,y2 got {raw!r}"
        )
    return tuple(int(p) for p in parts)  # type: ignore[return-value]


def main() -> int:
    """CLI entry: calibrate left/right score digit ROIs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        type=Path,
        help="Full-frame image or video path (1920×1080).",
    )
    parser.add_argument(
        "--time",
        type=float,
        default=None,
        help="Video seek time in seconds (default 0).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Output YAML (default {DEFAULT_OUT}).",
    )
    parser.add_argument(
        "--left-pixels",
        type=_parse_pixels,
        default=None,
        help="Non-interactive left digit box x1,y1,x2,y2.",
    )
    parser.add_argument(
        "--right-pixels",
        type=_parse_pixels,
        default=None,
        help="Non-interactive right digit box x1,y1,x2,y2.",
    )
    parser.add_argument(
        "--penalty-left-pixels",
        type=_parse_pixels,
        default=None,
        help="Optional left +N penalty box x1,y1,x2,y2.",
    )
    parser.add_argument(
        "--penalty-right-pixels",
        type=_parse_pixels,
        default=None,
        help="Optional right +N penalty box x1,y1,x2,y2.",
    )
    args = parser.parse_args()

    frame = load_frame(args.input, args.time)
    height, width = frame.shape[:2]

    if args.left_pixels is None or args.right_pixels is None:
        print("Select LEFT score digit ROI…")
        left_px = select_pixel_roi(frame, "score left digits")
        print("Select RIGHT score digit ROI…")
        right_px = select_pixel_roi(frame, "score right digits")
    else:
        left_px = args.left_pixels
        right_px = args.right_pixels

    left = normalized_box_from_pixels(*left_px, width, height)
    right = normalized_box_from_pixels(*right_px, width, height)

    payload: dict = {
        "frame_size": [width, height],
        "source": str(args.input),
        "source_time_s": args.time,
        "left_roi": list(left),
        "right_roi": list(right),
        "notes": (
            "Screen-position ROIs for SZ remaining counters. "
            "left/right = observation geometry only (not ally/opponent)."
        ),
    }

    if args.penalty_left_pixels is not None:
        payload["left_penalty_roi"] = list(
            normalized_box_from_pixels(*args.penalty_left_pixels, width, height)
        )
    if args.penalty_right_pixels is not None:
        payload["right_penalty_roi"] = list(
            normalized_box_from_pixels(*args.penalty_right_pixels, width, height)
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        yaml.safe_dump(payload, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    print(f"Wrote {args.out}")
    print(f"left_roi:  {list(left)}")
    print(f"right_roi: {list(right)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
