"""CLI for the read-only vision manifest viewer."""

from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

from vision_manifest_viewer.html import write_html
from vision_manifest_viewer.loader import load_manifest_view


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone argparse CLI."""
    parser = argparse.ArgumentParser(
        prog="vision-manifest-viewer",
        description=(
            "Generate a read-only HTML diagnostic viewer for a vision_manifest.json."
        ),
    )
    parser.add_argument(
        "manifest",
        type=Path,
        help="Path to vision_manifest.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="HTML output path (default: <manifest-dir>/vision_manifest_viewer.html)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional configs/default.yaml for detector ROI overlays",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Write HTML without opening a browser",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Load manifest, write HTML, optionally open in a browser."""
    args = build_parser().parse_args(argv)
    manifest_path = args.manifest.expanduser().resolve()
    if not manifest_path.exists():
        raise SystemExit(f"manifest not found: {manifest_path}")

    out = args.out
    if out is None:
        out = manifest_path.parent / "vision_manifest_viewer.html"
    else:
        out = out.expanduser().resolve()

    config_path = args.config.expanduser().resolve() if args.config else None
    view = load_manifest_view(manifest_path, config_path=config_path)
    # Prefer analysis folder name as the video label when identity is opaque.
    folder_name = manifest_path.parent.name
    if folder_name and folder_name not in {".", ""}:
        view.summary.video_label = folder_name

    path = write_html(view, out)
    print(f"wrote {path}")
    print(
        f"episodes={len(view.episodes)} "
        f"observations={len(view.observations)} "
        f"markers={len(view.markers)}"
    )
    if not args.no_open:
        webbrowser.open(path.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
