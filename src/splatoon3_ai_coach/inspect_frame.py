"""Developer utility: resolve a timestamp to the closest extracted JPEG frame.

Usage::

    python -m splatoon3_ai_coach.inspect_frame --time 16.25
    python -m splatoon3_ai_coach.inspect_frame --time 16.25 \\
        --out analysis/2026-07-07\\ 23-49-06
    python -m splatoon3_ai_coach.inspect_frame --time 16.25 --context 2 --no-open

This module does not run detectors or change manifests. It only reads an
existing extraction (or analysis) directory via the same manifest helpers
used by the pipeline.
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

from splatoon3_ai_coach.exceptions import ManifestError
from splatoon3_ai_coach.extraction.models import ManifestFrame
from splatoon3_ai_coach.media.manifest import MANIFEST_FILENAME, load_manifest
from splatoon3_ai_coach.media.vision_manifest import VISION_MANIFEST_FILENAME


def closest_frame(
    frames: list[ManifestFrame],
    timestamp: float,
) -> ManifestFrame:
    """Return the extracted frame whose timestamp is closest to ``timestamp``."""
    if not frames:
        raise ManifestError("Extraction manifest contains no frames")
    return min(frames, key=lambda frame: abs(frame.timestamp - timestamp))


def neighboring_frames(
    frames: list[ManifestFrame],
    matched: ManifestFrame,
    context: int,
) -> tuple[list[ManifestFrame], list[ManifestFrame]]:
    """Return up to ``context`` frames before and after ``matched`` in time order."""
    if context <= 0:
        return [], []
    ordered = sorted(
        frames,
        key=lambda frame: (frame.timestamp, frame.source_frame_index),
    )
    try:
        index = next(
            i
            for i, frame in enumerate(ordered)
            if frame.path == matched.path and frame.timestamp == matched.timestamp
        )
    except StopIteration:
        index = min(
            range(len(ordered)),
            key=lambda i: abs(ordered[i].timestamp - matched.timestamp),
        )
    before = ordered[max(0, index - context) : index]
    after = ordered[index + 1 : index + 1 + context]
    return before, after


def resolve_extraction_dir(out: Path | None = None) -> Path:
    """Locate an extraction frames directory from ``--out`` or the cwd.

    Accepts either an analysis output directory (containing ``frames/`` and
    optionally ``vision_manifest.json``) or the ``frames/`` directory itself.
    """
    candidates: list[Path] = []
    if out is not None:
        candidates.append(out.expanduser().resolve())
    candidates.append(Path.cwd().resolve())

    for base in candidates:
        if (base / MANIFEST_FILENAME).is_file():
            return base
        frames_dir = base / "frames"
        if (frames_dir / MANIFEST_FILENAME).is_file():
            return frames_dir
        if (base / VISION_MANIFEST_FILENAME).is_file() and (
            frames_dir / MANIFEST_FILENAME
        ).is_file():
            return frames_dir

    searched = ", ".join(str(path) for path in candidates)
    raise ManifestError(
        "Could not find an extraction manifest. Pass --out pointing at an "
        f"analyze output directory (or its frames/), searched: {searched}"
    )


def open_image(path: Path) -> None:
    """Open ``path`` with the OS default image viewer when possible."""
    resolved = path.resolve()
    system = platform.system()
    if system == "Darwin":
        subprocess.run(["open", str(resolved)], check=False)
    elif system == "Windows":
        os.startfile(str(resolved))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(resolved)], check=False)


def _print_frame(label: str, frame: ManifestFrame) -> None:
    """Print one frame summary line."""
    print(
        f"{label}: t={frame.timestamp:.3f} "
        f"index={frame.source_frame_index} path={frame.path}"
    )


def run(
    timestamp: float,
    *,
    out: Path | None = None,
    context: int = 0,
    open_viewer: bool = True,
) -> ManifestFrame:
    """Resolve ``timestamp`` against the extraction manifest and print details."""
    frames_dir = resolve_extraction_dir(out)
    manifest = load_manifest(frames_dir)
    matched = closest_frame(manifest.frames, timestamp)

    print(f"requested timestamp: {timestamp:.3f}")
    print(f"matched frame timestamp: {matched.timestamp:.3f}")
    print(f"source frame index: {matched.source_frame_index}")
    print(f"frame file path: {matched.path}")
    print(f"extraction dir: {frames_dir}")

    if context > 0:
        before, after = neighboring_frames(manifest.frames, matched, context)
        print(f"context (±{context}):")
        for frame in before:
            _print_frame("  before", frame)
        _print_frame("  matched", matched)
        for frame in after:
            _print_frame("  after", frame)

    if open_viewer:
        if matched.path.exists():
            open_image(matched.path)
        else:
            print(
                f"warning: frame file missing, not opening: {matched.path}",
                file=sys.stderr,
            )

    return matched


def _build_parser() -> argparse.ArgumentParser:
    """Build the developer CLI parser."""
    parser = argparse.ArgumentParser(
        prog="python -m splatoon3_ai_coach.inspect_frame",
        description=(
            "Resolve a detector/analysis timestamp to the closest extracted JPEG."
        ),
    )
    parser.add_argument(
        "--time",
        type=float,
        required=True,
        help="Timestamp in seconds (e.g. 16.25 from a vision reading).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Analyze output directory (contains vision_manifest.json and frames/) "
            "or the frames/ directory itself. Defaults to the current directory."
        ),
    )
    parser.add_argument(
        "--context",
        type=int,
        default=0,
        metavar="N",
        help="Also list the N closest extracted frames before and after the match.",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the matched JPEG in the OS image viewer.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``python -m splatoon3_ai_coach.inspect_frame``."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.context < 0:
        parser.error("--context must be >= 0")
    try:
        run(
            args.time,
            out=args.out,
            context=args.context,
            open_viewer=not args.no_open,
        )
    except ManifestError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
