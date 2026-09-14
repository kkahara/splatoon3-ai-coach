#!/usr/bin/env python3
"""Interactive playable-stage polygon calibration (normalized vertices).

Click vertices on a map-overlay screenshot, close the polygon, preview the
mask, then optionally write ``configs/stage_maps/<stage_id>/stage_mask.yaml``.

Requires OpenCV HighGUI::

    pip install -e ".[display]"

Examples::

    python tools/stage_mask_calibrate.py --stage mahi_mahi_resort path/to/frame.jpg
    python tools/stage_mask_calibrate.py --stage mahi_mahi_resort --view \\
        configs/stage_maps/mahi_mahi_resort/stage_mask.yaml path/to/frame.jpg
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

# Sibling helpers (file picker + frame load + HighGUI probe patterns).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from roi_calibrate import load_frame, pick_input_path  # noqa: E402

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.vision.stage_mask import (
    STAGE_MASK_FILENAME,
    StageMaskConfig,
    load_stage_mask,
    render_stage_mask_overlay,
    write_stage_mask,
)

REPO = Path(__file__).resolve().parents[1]


def _require_highgui() -> None:
    """Exit if this OpenCV build cannot run interactive windows."""
    try:
        cv2.namedWindow("__stage_mask_probe__", cv2.WINDOW_NORMAL)
        cv2.destroyWindow("__stage_mask_probe__")
    except cv2.error as exc:
        print(
            f"OpenCV HighGUI is unavailable ({exc}). "
            'Install display support: pip install -e ".[display]"',
            file=sys.stderr,
        )
        raise SystemExit(2) from exc


def _draw_in_progress(
    image: np.ndarray,
    pixels: list[tuple[int, int]],
    *,
    closed: bool,
) -> np.ndarray:
    """Overlay clicked vertices / polyline on a copy of the frame."""
    canvas = image.copy()
    if pixels:
        pts = np.array(pixels, dtype=np.int32)
        if len(pts) >= 2:
            cv2.polylines(
                canvas,
                [pts],
                isClosed=closed,
                color=(0, 255, 255),
                thickness=2,
            )
        for index, (x, y) in enumerate(pixels):
            cv2.circle(canvas, (x, y), 4, (0, 165, 255), -1)
            cv2.putText(
                canvas,
                str(index),
                (x + 6, y - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
    help_lines = [
        "LMB: add vertex",
        "u: undo  c: close polygon  r: reset  Enter/s: save  Esc/q: quit",
    ]
    for i, line in enumerate(help_lines):
        y = 28 + i * 22
        cv2.putText(
            canvas, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 3, cv2.LINE_AA
        )
        cv2.putText(
            canvas,
            line,
            (12, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    return canvas


def calibrate_polygon(image: np.ndarray, window: str) -> list[tuple[float, float]]:
    """Interactive click-to-polygon; returns normalized vertices."""
    _require_highgui()
    height, width = image.shape[:2]
    state: dict[str, object] = {"pixels": [], "closed": False}

    def on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
        if state["closed"]:
            return
        if event == cv2.EVENT_LBUTTONDOWN:
            pixels = state["pixels"]
            assert isinstance(pixels, list)
            pixels.append((int(x), int(y)))

    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, on_mouse)
    try:
        while True:
            pixels = state["pixels"]
            assert isinstance(pixels, list)
            closed = bool(state["closed"])
            canvas = _draw_in_progress(image, pixels, closed=closed)
            if closed and len(pixels) >= 3:
                config = StageMaskConfig(
                    stage_id="preview",
                    polygon=[
                        (x / float(width), y / float(height)) for x, y in pixels
                    ],
                    erosion_pixels=0,
                )
                canvas, inside, outside = render_stage_mask_overlay(image, config)
                cv2.putText(
                    canvas,
                    f"preview inside={inside} outside={outside}",
                    (12, height - 16),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (255, 255, 255),
                    1,
                    cv2.LINE_AA,
                )
            cv2.imshow(window, canvas)
            key = cv2.waitKey(20) & 0xFF
            if key in (27, ord("q")):
                raise ValueError("calibration cancelled")
            if key == ord("u") and pixels:
                pixels.pop()
                state["closed"] = False
            if key == ord("r"):
                state["pixels"] = []
                state["closed"] = False
            if key == ord("c"):
                if len(pixels) < 3:
                    print("need at least 3 vertices to close", file=sys.stderr)
                else:
                    state["closed"] = True
            if key in (13, ord("s")):  # Enter or s
                if not closed or len(pixels) < 3:
                    print("close the polygon first (press c)", file=sys.stderr)
                    continue
                return [
                    (
                        round(x / float(width), 6),
                        round(y / float(height), 6),
                    )
                    for x, y in pixels
                ]
    finally:
        cv2.destroyAllWindows()


def build_parser() -> argparse.ArgumentParser:
    """CLI for stage-mask polygon calibration / view."""
    parser = argparse.ArgumentParser(
        description=(
            "Click a playable-stage polygon on a map frame and write "
            "normalized stage_mask.yaml. Requires opencv HighGUI."
        )
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=None,
        help="Still image or video path (omit to open a file picker)",
    )
    parser.add_argument(
        "--stage",
        required=True,
        help="Stage id (e.g. mahi_mahi_resort)",
    )
    parser.add_argument(
        "--view",
        type=Path,
        default=None,
        help="Load an existing stage_mask.yaml and write/show overlay only",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help=(
            f"YAML output path (default: configs/stage_maps/<stage>/"
            f"{STAGE_MASK_FILENAME})"
        ),
    )
    parser.add_argument(
        "--overlay",
        type=Path,
        default=None,
        help="Optional path for a semi-transparent mask overlay image",
    )
    parser.add_argument(
        "--erosion-pixels",
        type=int,
        default=0,
        help="Optional boundary erosion written into YAML (default 0)",
    )
    parser.add_argument(
        "--time",
        type=float,
        default=None,
        help="Video seek time in seconds (default 0 for video)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="App config YAML (default: configs/default.yaml)",
    )
    parser.add_argument(
        "--no-show",
        action="store_true",
        help="In --view mode, write overlay only (no window)",
    )
    return parser


def _default_yaml_path(stage_id: str, config_path: Path | None) -> Path:
    """Resolve default stage_mask.yaml under the configured geometry_dir."""
    app = load_config(config_path or default_config_path())
    geometry_dir = app.vision.map_ink.geometry_dir
    if geometry_dir is None:
        geometry_dir = REPO / "configs" / "stage_maps"
    return Path(geometry_dir) / stage_id / STAGE_MASK_FILENAME


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    try:
        path = args.input if args.input is not None else pick_input_path()
        if not path.is_file():
            print(f"input not found: {path}", file=sys.stderr)
            return 1
        image = load_frame(path, args.time)
        height, width = image.shape[:2]

        if args.view is not None:
            config = load_stage_mask(args.view)
            overlay, inside, outside = render_stage_mask_overlay(image, config)
            print(f"Stage: {config.stage_id}")
            print(f"Vertices: {len(config.polygon)}")
            print(f"Frame: {width}x{height}")
            print(f"Inside: {inside}  Outside: {outside}")
            if args.overlay is not None:
                args.overlay.parent.mkdir(parents=True, exist_ok=True)
                if not cv2.imwrite(str(args.overlay), overlay):
                    raise ValueError(f"could not write overlay: {args.overlay}")
                print(f"Wrote overlay: {args.overlay}")
            if not args.no_show:
                _require_highgui()
                window = f"Stage mask view — {args.view.name}"
                cv2.imshow(window, overlay)
                cv2.waitKey(0)
                cv2.destroyAllWindows()
            return 0

        window = f"Stage mask calibrate — {path.name}"
        polygon = calibrate_polygon(image, window)
        config = StageMaskConfig(
            stage_id=args.stage,
            polygon=polygon,
            erosion_pixels=int(args.erosion_pixels),
        )
        out_yaml = args.output or _default_yaml_path(args.stage, args.config)
        write_stage_mask(out_yaml, config)
        overlay, inside, outside = render_stage_mask_overlay(image, config)
        print(f"Stage: {config.stage_id}")
        print(f"Vertices: {len(config.polygon)}")
        print(f"Frame: {width}x{height}")
        print(f"Inside: {inside}  Outside: {outside}")
        print(f"Wrote: {out_yaml}")
        if args.overlay is not None:
            args.overlay.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(args.overlay), overlay):
                raise ValueError(f"could not write overlay: {args.overlay}")
            print(f"Wrote overlay: {args.overlay}")
        else:
            # Always show final overlay once for visual sign-off.
            _require_highgui()
            cv2.imshow(f"Stage mask saved — {args.stage}", overlay)
            cv2.waitKey(0)
            cv2.destroyAllWindows()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
