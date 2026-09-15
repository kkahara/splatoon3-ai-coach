#!/usr/bin/env python3
"""A/B paint %: stage polygon mask vs ROI-union fallback (same classifier).

Does not invent polygons. Requires ``stage_mask.yaml`` for the stage to compare.

Example::

    python tools/stage_mask_ab_compare.py --stage manta_maria \\
      analysis/map_ink_validation/stage_mask_calibrate/manta_maria_t0176.8.jpg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from roi_calibrate import load_frame, pick_input_path  # noqa: E402

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.vision.map_ink import (
    MapInkClassifier,
    analyze_map_ink,
    write_map_ink_diagnostic,
)
from splatoon3_ai_coach.vision.stage_maps import resolve_stage_map_geometry
from splatoon3_ai_coach.vision.stage_mask import resolve_stage_mask

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / "analysis" / "map_ink_validation" / "stage_mask_ab"


def _fmt_frac(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}"


def build_parser() -> argparse.ArgumentParser:
    """CLI for stage-mask vs ROI-union paint comparison."""
    parser = argparse.ArgumentParser(
        description=(
            "Compare classified paint fractions with stage_mask.yaml vs ROI union "
            "on the same frame and classifier."
        )
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=None,
        help="Still image or video (omit to open a file picker)",
    )
    parser.add_argument("--stage", required=True, help="Stage id under stage_maps/")
    parser.add_argument("--mode", default=None, help="Optional battle_mode_id")
    parser.add_argument("--time", type=float, default=None, help="Video seek seconds")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=f"Diagnostic output dir (default: {DEFAULT_OUT})",
    )
    parser.add_argument("--config", type=Path, default=None)
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
        geometry_dir = config.vision.map_ink.geometry_dir
        geometry = resolve_stage_map_geometry(
            geometry_dir, stage_id=args.stage, battle_mode_id=args.mode
        )
        if geometry is None:
            print(f"no geometry pack for stage_id={args.stage!r}", file=sys.stderr)
            return 1
        stage_mask = resolve_stage_mask(geometry_dir, stage_id=args.stage)
        if stage_mask is None:
            print(
                f"no stage_mask.yaml for {args.stage!r} under {geometry_dir} — "
                "calibrate first with tools/stage_mask_calibrate.py",
                file=sys.stderr,
            )
            return 1

        image = load_frame(path, args.time)
        classifier = MapInkClassifier(config.vision.map_ink)
        t = float(args.time or 0.0)
        obs_mask = analyze_map_ink(
            image,
            geometry,
            classifier,
            video_time=t,
            battle_mode_id=args.mode,
            stage_mask=stage_mask,
        )
        obs_roi = analyze_map_ink(
            image,
            geometry,
            classifier,
            video_time=t,
            battle_mode_id=args.mode,
            stage_mask=None,
        )

        out_dir = args.output_dir or DEFAULT_OUT
        write_map_ink_diagnostic(
            out_dir,
            image=image,
            observation=obs_mask,
            geometry=geometry,
            classifier=classifier,
            stem=f"{args.stage}_mask",
            stage_mask=stage_mask,
        )
        write_map_ink_diagnostic(
            out_dir,
            image=image,
            observation=obs_roi,
            geometry=geometry,
            classifier=classifier,
            stem=f"{args.stage}_roi_union",
            stage_mask=None,
        )

        print(f"Stage: {args.stage}")
        print(f"Frame: {path.name}")
        print("")
        print(
            f"{'source':<12} {'sample_px':>10} {'cls_px':>8} "
            f"{'cls_frac':>10} {'ally':>10} {'opp':>10}"
        )
        for label, obs in (("stage_mask", obs_mask), ("roi_union", obs_roi)):
            print(
                f"{label:<12} {obs.total_sample_pixels:10d} {obs.classified_pixels:8d} "
                f"{_fmt_frac(obs.classified_fraction):>10} "
                f"{_fmt_frac(obs.ally_classified_fraction):>10} "
                f"{_fmt_frac(obs.opponent_classified_fraction):>10}"
            )
        print("")
        print(f"Diagnostics: {out_dir / (args.stage + '_mask.jpg')}")
        print(f"             {out_dir / (args.stage + '_roi_union.jpg')}")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
