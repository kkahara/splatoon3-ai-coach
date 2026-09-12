#!/usr/bin/env python3
"""Visualize normalized ROI boxes on an image or video frame.

Coordinates → overlay (geometry only). Pair with ``roi_calibrate.py`` which
does interactive selection → coordinates. Does not write production YAML and
does not run map-ink classification (see ``map_ink_roi_preview.py`` for that).

Examples::

    python tools/roi_visualize.py map.png --roi '[0.44,0.06,0.73,0.28]' --name R01
    python tools/roi_visualize.py map.png \\
      --roi R01='[0.44,0.06,0.73,0.28]' \\
      --roi R02='[0.50,0.20,0.80,0.40]'
    python tools/roi_visualize.py frame.jpg --stage inkblot_art_academy
    python tools/roi_visualize.py frame.jpg --geometry configs/stage_maps/inkblot_art_academy/default.yaml
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

import cv2
import numpy as np

# Sibling helpers (file picker + frame load).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from roi_calibrate import load_frame, pick_input_path  # noqa: E402

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.types import NormalizedBox
from splatoon3_ai_coach.vision.roi import pixel_box_from_normalized
from splatoon3_ai_coach.vision.stage_maps import (
    load_stage_map_geometry,
    resolve_stage_map_geometry,
)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO / "analysis" / "roi_visualize"

# BGR colors cycling for multi-ROI boxes.
_ROI_COLORS: tuple[tuple[int, int, int], ...] = (
    (0, 165, 255),  # orange
    (0, 255, 255),  # yellow
    (255, 180, 0),  # sky
    (180, 105, 255),  # pink
    (0, 255, 128),  # green
    (255, 128, 0),  # blue-ish
)
_UNION_COLOR = (255, 220, 0)  # cyan-ish (BGR)


def parse_normalized_box(text: str) -> NormalizedBox:
    """Parse ``[x1,y1,x2,y2]`` (JSON-ish / Python list) into a NormalizedBox."""
    raw = text.strip()
    try:
        value = ast.literal_eval(raw)
    except (SyntaxError, ValueError) as exc:
        raise ValueError(
            f"invalid ROI box {text!r}; expected [x1, y1, x2, y2]"
        ) from exc
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"invalid ROI box {text!r}; expected four numbers")
    try:
        box = tuple(float(v) for v in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid ROI box {text!r}; expected four numbers") from exc
    return _validate_box(box)  # type: ignore[arg-type]


def _validate_box(box: tuple[float, float, float, float]) -> NormalizedBox:
    """Require coordinates in ``[0, 1]`` with positive area."""
    x1, y1, x2, y2 = box
    if not all(0.0 <= v <= 1.0 for v in box):
        raise ValueError(f"ROI coordinates must be in [0, 1]: {box}")
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"ROI must have positive area: {box}")
    return (x1, y1, x2, y2)


_NAMED_ROI = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z0-9_\-]*)\s*=\s*(?P<body>.+)$"
)


def parse_roi_arg(text: str) -> tuple[str | None, NormalizedBox]:
    """Parse ``NAME=[...]`` or bare ``[...]`` into ``(name_or_None, box)``."""
    raw = text.strip()
    match = _NAMED_ROI.match(raw)
    if match:
        return match.group("name"), parse_normalized_box(match.group("body"))
    return None, parse_normalized_box(raw)


def collect_cli_rois(
    roi_args: list[str],
    *,
    name: str | None = None,
) -> list[tuple[str, NormalizedBox]]:
    """Turn repeatable ``--roi`` strings into named boxes.

    ``--name`` applies only when there is exactly one unnamed box.
    Unnamed boxes become ``roi``, ``roi2``, …
    """
    parsed = [parse_roi_arg(item) for item in roi_args]
    unnamed = [box for label, box in parsed if label is None]
    if name is not None:
        if len(parsed) != 1 or parsed[0][0] is not None:
            raise ValueError(
                "--name only applies with a single unnamed --roi '[x1,y1,x2,y2]'"
            )
        return [(name, parsed[0][1])]

    result: list[tuple[str, NormalizedBox]] = []
    unnamed_index = 0
    for label, box in parsed:
        if label is not None:
            result.append((label, box))
            continue
        unnamed_index += 1
        auto = "roi" if unnamed_index == 1 else f"roi{unnamed_index}"
        result.append((auto, box))
    _ = unnamed  # kept for clarity / future diagnostics
    return result


def render_rois(
    image: np.ndarray,
    regions: list[tuple[str, NormalizedBox]],
    *,
    draw_union: bool = True,
) -> np.ndarray:
    """Draw labeled ROI rectangles (and optional union outline) on a copy."""
    if not regions:
        raise ValueError("no regions to draw")
    canvas = image.copy()
    height, width = canvas.shape[:2]

    if draw_union and len(regions) >= 2:
        mask = np.zeros((height, width), dtype=np.uint8)
        for _, box in regions:
            left, top, right, bottom = pixel_box_from_normalized(box, width, height)
            left = max(0, min(width, left))
            right = max(0, min(width, right))
            top = max(0, min(height, top))
            bottom = max(0, min(height, bottom))
            if right > left and bottom > top:
                mask[top:bottom, left:right] = 255
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(canvas, contours, -1, _UNION_COLOR, 2)

    for index, (label, box) in enumerate(regions):
        color = _ROI_COLORS[index % len(_ROI_COLORS)]
        left, top, right, bottom = pixel_box_from_normalized(box, width, height)
        left = max(0, min(width, left))
        right = max(0, min(width, right))
        top = max(0, min(height, top))
        bottom = max(0, min(height, bottom))
        cv2.rectangle(canvas, (left, top), (right, bottom), color, 2)
        text_y = max(14, top - 6)
        cv2.putText(
            canvas,
            label,
            (left + 4, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 0, 0),
            3,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            label,
            (left + 4, text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            1,
            cv2.LINE_AA,
        )
    return canvas


def build_parser() -> argparse.ArgumentParser:
    """CLI parser for ROI visualization."""
    parser = argparse.ArgumentParser(
        description=(
            "Draw normalized [x1,y1,x2,y2] ROI boxes on an image/video frame. "
            "Geometry only — no map-ink classification."
        )
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=None,
        help="Still image or video (omit to open a file picker)",
    )
    parser.add_argument(
        "--roi",
        action="append",
        default=[],
        help=(
            "Normalized box: '[x1,y1,x2,y2]' or 'NAME=[x1,y1,x2,y2]'. "
            "Repeatable."
        ),
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Label for a single unnamed --roi (e.g. R01)",
    )
    stage_group = parser.add_mutually_exclusive_group()
    stage_group.add_argument(
        "--stage",
        default=None,
        help="Load regions from configs/stage_maps/<stage>/default.yaml",
    )
    stage_group.add_argument(
        "--geometry",
        type=Path,
        default=None,
        help="Load regions from an explicit stage geometry YAML",
    )
    parser.add_argument(
        "--mode",
        default=None,
        help="Optional battle_mode_id when using --stage",
    )
    parser.add_argument(
        "--time",
        type=float,
        default=None,
        help="Video seek time in seconds (default 0 for video)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help=f"Output image path (default under {DEFAULT_OUT_DIR}/)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="App config YAML when resolving --stage (default: configs/default.yaml)",
    )
    return parser


def _regions_from_stage(
    *,
    stage: str | None,
    geometry_path: Path | None,
    mode: str | None,
    config_path: Path | None,
) -> list[tuple[str, NormalizedBox]]:
    """Load stage-map regions as ``(id, roi)`` pairs."""
    if geometry_path is not None:
        geometry = load_stage_map_geometry(geometry_path)
    else:
        assert stage is not None
        config = load_config(config_path or default_config_path())
        geometry = resolve_stage_map_geometry(
            config.vision.map_ink.geometry_dir,
            stage_id=stage,
            battle_mode_id=mode,
        )
        if geometry is None:
            raise ValueError(
                f"no geometry pack for stage_id={stage!r} "
                f"(looked under {config.vision.map_ink.geometry_dir})"
            )
    return [(region.id, region.roi) for region in geometry.regions]


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    try:
        if not args.roi and args.stage is None and args.geometry is None:
            raise ValueError(
                "provide at least one --roi and/or --stage / --geometry"
            )

        path = args.input if args.input is not None else pick_input_path()
        if not path.is_file():
            print(f"input not found: {path}", file=sys.stderr)
            return 1

        regions: list[tuple[str, NormalizedBox]] = []
        if args.stage is not None or args.geometry is not None:
            regions.extend(
                _regions_from_stage(
                    stage=args.stage,
                    geometry_path=args.geometry,
                    mode=args.mode,
                    config_path=args.config,
                )
            )
        if args.roi:
            regions.extend(collect_cli_rois(args.roi, name=args.name))
        elif args.name is not None:
            raise ValueError("--name requires a single unnamed --roi")

        image = load_frame(path, args.time)
        canvas = render_rois(image, regions)

        if args.output is not None:
            out_path = args.output
        else:
            DEFAULT_OUT_DIR.mkdir(parents=True, exist_ok=True)
            stem = path.stem
            if args.stage:
                stem = f"{args.stage}_{stem}"
            elif args.geometry is not None:
                stem = f"{args.geometry.stem}_{stem}"
            out_path = DEFAULT_OUT_DIR / f"{stem}_rois.jpg"

        out_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(out_path), canvas):
            raise ValueError(f"could not write output image: {out_path}")

        print(f"Image: {image.shape[1]}x{image.shape[0]}")
        print(f"Regions ({len(regions)}):")
        for label, box in regions:
            print(f"  {label}: [{box[0]:.6f}, {box[1]:.6f}, {box[2]:.6f}, {box[3]:.6f}]")
        print(f"Wrote: {out_path}")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
