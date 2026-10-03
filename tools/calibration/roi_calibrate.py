#!/usr/bin/env python3
"""Interactive ROI calibration for project normalized ``[x1,y1,x2,y2]`` boxes.

Uses OpenCV HighGUI ``selectROI`` (requires the optional display extra::

    pip install -e ".[display]"

Omit the input path to pick an image/video via a native file dialog
(Tk when available; macOS falls back to ``osascript``).
Does not write production YAML — prints copy/paste values only.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

from splatoon3_ai_coach.types import NormalizedBox
from splatoon3_ai_coach.vision.roi import normalized_box_from_pixels

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
_VIDEO_SUFFIXES = {".mov", ".mp4", ".mkv", ".avi", ".m4v", ".webm"}
_FILE_DIALOG_TYPES = [
    (
        "Images / videos",
        " ".join(
            f"*{ext}"
            for ext in sorted(_IMAGE_SUFFIXES | _VIDEO_SUFFIXES)
        ),
    ),
    ("All files", "*.*"),
]
_PICKER_FALLBACK = (
    "Pass an image or video path on the command line instead."
)


def pick_input_path() -> Path:
    """Open a native file dialog; raise ValueError on cancel or picker failure."""
    errors: list[str] = []
    for picker in (_pick_via_tkinter, _pick_via_osascript):
        try:
            return picker()
        except _PickerUnavailable as exc:
            errors.append(str(exc))
        except ValueError:
            raise
    detail = "; ".join(errors) if errors else "no picker available"
    raise ValueError(f"file picker unavailable ({detail}). {_PICKER_FALLBACK}")


class _PickerUnavailable(Exception):
    """Internal: this picker backend cannot run on this machine."""


def _pick_via_tkinter() -> Path:
    """Tk ``askopenfilename`` when ``_tkinter`` is available."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # noqa: BLE001 — missing _tkinter / Tk
        raise _PickerUnavailable(str(exc)) from exc

    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askopenfilename(
            title="Select image or video for ROI calibration",
            filetypes=_FILE_DIALOG_TYPES,
        )
    except Exception as exc:  # noqa: BLE001 — display / Tk runtime failures
        raise _PickerUnavailable(str(exc)) from exc
    finally:
        if root is not None:
            root.destroy()

    if not chosen:
        raise ValueError("no file selected")
    return Path(chosen)


def _pick_via_osascript() -> Path:
    """macOS native ``choose file`` via AppleScript (no Tk required)."""
    if sys.platform != "darwin":
        raise _PickerUnavailable("osascript only on macOS")
    # No type filter: load_frame validates image/video suffixes afterward.
    script = (
        'try\n'
        'set theFile to choose file with prompt '
        '"Select image or video for ROI calibration"\n'
        "return POSIX path of theFile\n"
        "on error\n"
        'return ""\n'
        "end try"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise _PickerUnavailable(str(exc)) from exc
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "osascript failed").strip()
        raise _PickerUnavailable(err)
    chosen = result.stdout.strip()
    if not chosen:
        raise ValueError("no file selected")
    return Path(chosen)


def _require_highgui() -> None:
    """Exit if this OpenCV build cannot run interactive ROI selection."""
    if not hasattr(cv2, "selectROI"):
        print(
            "OpenCV HighGUI selectROI is unavailable. "
            'Install display support: pip install -e ".[display]"',
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        # Headless wheels often expose selectROI but fail on window create.
        cv2.namedWindow("__roi_calibrate_probe__", cv2.WINDOW_NORMAL)
        cv2.destroyWindow("__roi_calibrate_probe__")
    except cv2.error as exc:
        print(
            f"OpenCV HighGUI is unavailable ({exc}). "
            'Install display support: pip install -e ".[display]"',
            file=sys.stderr,
        )
        raise SystemExit(2) from exc


def load_frame(path: Path, time_seconds: float | None) -> np.ndarray:
    """Load a still frame or a video frame at ``time_seconds`` (default 0)."""
    suffix = path.suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"could not read image: {path}")
        return image
    if suffix in _VIDEO_SUFFIXES:
        return _load_video_frame(path, 0.0 if time_seconds is None else time_seconds)
    raise ValueError(
        f"unsupported input type {suffix!r} for {path}; "
        f"expected image {_IMAGE_SUFFIXES} or video {_VIDEO_SUFFIXES}"
    )


def _load_video_frame(path: Path, time_seconds: float) -> np.ndarray:
    """Seek a video to ``time_seconds`` and return one BGR frame."""
    if time_seconds < 0:
        raise ValueError(f"--time must be >= 0, got {time_seconds}")
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"could not open video: {path}")
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, time_seconds * 1000.0)
        ok, frame = capture.read()
        if not ok or frame is None:
            raise ValueError(
                f"could not read frame at t={time_seconds:.3f}s from {path}"
            )
        return frame
    finally:
        capture.release()


def select_pixel_roi(image: np.ndarray, window_title: str) -> tuple[int, int, int, int]:
    """Run ``cv2.selectROI`` and return pixel ``(x1, y1, x2, y2)``."""
    _require_highgui()
    x, y, w, h = cv2.selectROI(window_title, image, showCrosshair=True, fromCenter=False)
    cv2.destroyAllWindows()
    if w <= 0 or h <= 0:
        raise ValueError("ROI selection cancelled or empty")
    height, width = image.shape[:2]
    x1, y1 = int(x), int(y)
    x2, y2 = int(x + w), int(y + h)
    x1 = max(0, min(width, x1))
    y1 = max(0, min(height, y1))
    x2 = max(0, min(width, x2))
    y2 = max(0, min(height, y2))
    if x2 <= x1 or y2 <= y1:
        raise ValueError("ROI selection cancelled or empty")
    return x1, y1, x2, y2


def format_report(
    *,
    width: int,
    height: int,
    name: str | None,
    pixels: tuple[int, int, int, int],
    normalized: NormalizedBox,
) -> str:
    """Human-readable calibration report + YAML snippet."""
    x1, y1, x2, y2 = pixels
    nx1, ny1, nx2, ny2 = normalized
    lines = [
        f"Image: {width}x{height}",
    ]
    if name:
        lines.append(f"ROI: {name}")
    lines.extend(
        [
            f"Pixels: x1={x1}, y1={y1}, x2={x2}, y2={y2}",
            f"Normalized: [{nx1:.6f}, {ny1:.6f}, {nx2:.6f}, {ny2:.6f}]",
            "",
            "YAML:",
        ]
    )
    yaml_roi = f"roi: [{nx1:.6f}, {ny1:.6f}, {nx2:.6f}, {ny2:.6f}]"
    if name:
        lines.append(f"{name}:")
        lines.append(f"  {yaml_roi}")
    else:
        lines.append(yaml_roi)
    return "\n".join(lines)


def write_diagnostic(
    image: np.ndarray,
    pixels: tuple[int, int, int, int],
    normalized: NormalizedBox,
    path: Path,
    *,
    name: str | None = None,
) -> None:
    """Draw the selected ROI and save a diagnostic JPEG/PNG."""
    out = image.copy()
    x1, y1, x2, y2 = pixels
    cv2.rectangle(out, (x1, y1), (x2, y2), (0, 165, 255), 2)
    nx1, ny1, nx2, ny2 = normalized
    label_name = name or "roi"
    lines = [
        label_name,
        f"px [{x1},{y1},{x2},{y2}]",
        f"norm [{nx1:.4f},{ny1:.4f},{nx2:.4f},{ny2:.4f}]",
    ]
    text_y = max(20, y1 - 8)
    for i, line in enumerate(lines):
        cv2.putText(
            out,
            line,
            (x1, text_y + i * 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), out):
        raise ValueError(f"could not write diagnostic image: {path}")


def build_parser() -> argparse.ArgumentParser:
    """CLI parser for ROI calibration."""
    parser = argparse.ArgumentParser(
        description=(
            "Interactively select a rectangular ROI and print project-normalized "
            "[x1,y1,x2,y2] coordinates. Requires opencv HighGUI "
            '(pip install -e ".[display]").'
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
        "--name",
        default=None,
        help="Optional ROI name for the report / YAML key (e.g. special_gauge)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path for a diagnostic image with the ROI drawn",
    )
    parser.add_argument(
        "--time",
        type=float,
        default=None,
        help="Video seek time in seconds (default 0 for video inputs)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    try:
        path: Path = args.input if args.input is not None else pick_input_path()
        if not path.is_file():
            print(f"input not found: {path}", file=sys.stderr)
            return 1
        image = load_frame(path, args.time)
        height, width = image.shape[:2]
        window = f"ROI calibrate — {path.name}"
        pixels = select_pixel_roi(image, window)
        normalized = normalized_box_from_pixels(
            *pixels, width, height, decimals=6
        )
        print(
            format_report(
                width=width,
                height=height,
                name=args.name,
                pixels=pixels,
                normalized=normalized,
            )
        )
        if args.output is not None:
            write_diagnostic(
                image,
                pixels,
                normalized,
                args.output,
                name=args.name,
            )
            print(f"\nWrote diagnostic: {args.output}")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
