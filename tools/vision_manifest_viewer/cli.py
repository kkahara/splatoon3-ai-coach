"""CLI for the read-only vision manifest viewer."""

from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

from vision_manifest_viewer.html import write_html
from vision_manifest_viewer.loader import load_manifest_view
from vision_manifest_viewer.server import (
    REVIEW_VIDEO_PATH,
    review_page_url,
    serve_review,
)


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
        "--video",
        type=Path,
        default=None,
        help="Source video for Scenario Review playback (starts a local HTTP server).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help=(
            "Preferred review HTTP port when --video is set "
            "(0 = choose an available port)."
        ),
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Write HTML without opening a browser",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Load manifest, write HTML, optionally serve video and open a browser."""
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
    folder_name = manifest_path.parent.name
    if folder_name and folder_name not in {".", ""}:
        view.summary.video_label = folder_name

    video_path = args.video.expanduser().resolve() if args.video else None
    if video_path is not None:
        if not video_path.is_file():
            raise SystemExit(f"video not found: {video_path}")
        view.review_video_url = REVIEW_VIDEO_PATH

    path = write_html(view, out)
    print(f"wrote {path}")
    print(
        f"episodes={len(view.episodes)} "
        f"observations={len(view.observations)} "
        f"markers={len(view.markers)} "
        f"scenarios={len(view.scenario_evidence)}"
    )
    if video_path is not None:
        preferred = args.port if args.port and args.port > 0 else None
        return _serve_and_maybe_open(path, video_path, preferred, args.no_open)
    if not args.no_open:
        webbrowser.open(path.as_uri())
    return 0


def _serve_and_maybe_open(
    html_path: Path,
    video_path: Path,
    port: int | None,
    no_open: bool,
) -> int:
    """Serve the analysis folder plus /review-video until interrupted."""
    root = html_path.parent
    server = serve_review(root, video_path, port=port)
    bound_port = int(server.server_address[1])
    url = review_page_url(html_path.name, port=bound_port)
    print(f"review server {url}")
    print(f"review video {video_path}")
    if not no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("review server stopped")
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
