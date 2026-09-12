#!/usr/bin/env python3
"""Preview stage-map ROI coverage (R01…) on a still or video frame.

Draws the same overlay as production map-ink diagnostics: white region boxes,
cyan union outline, and HSV ink classes inside the union. Use after editing
``configs/stage_maps/<stage_id>/default.yaml``.

Examples::

    python tools/map_ink_roi_preview.py --stage museum_dalfonsino path/to/frame.jpg
    python tools/map_ink_roi_preview.py --stage wahoo_world --time 42.0 clip.mov
    python tools/map_ink_roi_preview.py --geometry configs/stage_maps/museum_dalfonsino/default.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Sibling helpers (file picker + frame load).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from roi_calibrate import load_frame, pick_input_path  # noqa: E402

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.vision.map_ink import (
    MapInkClassifier,
    analyze_map_ink,
    write_map_ink_diagnostic,
)
from splatoon3_ai_coach.vision.stage_maps import (
    load_stage_map_geometry,
    resolve_stage_map_geometry,
)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO / "analysis" / "map_ink_validation" / "debug_map_ink"


def build_parser() -> argparse.ArgumentParser:
    """CLI for map-ink ROI coverage preview."""
    parser = argparse.ArgumentParser(
        description=(
            "Render stage-map sampling ROIs (white boxes) and their union "
            "(cyan) on a frame, matching map-ink diagnostic style."
        )
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=None,
        help="Still image or video (omit to open a file picker)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--stage",
        help="Stage id under configs/stage_maps/<stage>/default.yaml",
    )
    group.add_argument(
        "--geometry",
        type=Path,
        help="Explicit stage geometry YAML path",
    )
    parser.add_argument(
        "--mode",
        default=None,
        help="Optional battle_mode_id override when using --stage",
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
        help="Output image path (default under analysis/map_ink_validation/debug_map_ink/)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="App config YAML (default: configs/default.yaml)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    try:
        path = args.input if args.input is not None else pick_input_path()
        if not path.is_file():
            print(f"input not found: {path}", file=sys.stderr)
            return 1

        config = load_config(args.config or default_config_path())
        if args.geometry is not None:
            geometry = load_stage_map_geometry(args.geometry)
        else:
            geometry = resolve_stage_map_geometry(
                config.vision.map_ink.geometry_dir,
                stage_id=args.stage,
                battle_mode_id=args.mode,
            )
            if geometry is None:
                print(
                    f"no geometry pack for stage_id={args.stage!r} "
                    f"(looked under {config.vision.map_ink.geometry_dir})",
                    file=sys.stderr,
                )
                return 1

        image = load_frame(path, args.time)
        classifier = MapInkClassifier(config.vision.map_ink)
        observation = analyze_map_ink(
            image,
            geometry,
            classifier,
            video_time=float(args.time or 0.0),
            battle_mode_id=args.mode or geometry.battle_mode_id,
            evidence_ids=[f"preview:{path.name}"],
        )

        if args.output is not None:
            out_path = args.output
            out_dir = out_path.parent
            stem = out_path.stem
        else:
            out_dir = DEFAULT_OUT_DIR
            stem = f"{geometry.stage_id}_preview"

        written = write_map_ink_diagnostic(
            out_dir,
            image=image,
            observation=observation,
            geometry=geometry,
            classifier=classifier,
            stem=stem,
        )
        region_ids = ", ".join(r.id for r in geometry.regions)
        print(f"Stage: {geometry.stage_id}")
        print(f"Regions: {region_ids}")
        print(f"Union pixels: {observation.total_sample_pixels}")
        print(f"Wrote: {written}")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
