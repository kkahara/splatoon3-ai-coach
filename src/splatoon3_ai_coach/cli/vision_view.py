"""Generate a local HTML viewer for a vision_manifest.json."""

from __future__ import annotations

from pathlib import Path

import typer

from splatoon3_ai_coach.cli.repo_tools import require_repo_tools


def vision_view(
    manifest: Path = typer.Argument(..., help="Path to vision_manifest.json"),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="HTML output path (default: next to the manifest).",
    ),
    config_path: Path | None = typer.Option(
        None,
        "--config",
        help="Optional YAML config for detector ROI overlays.",
    ),
    no_open: bool = typer.Option(
        False,
        "--no-open",
        help="Write HTML without opening a browser.",
    ),
    video: Path | None = typer.Option(
        None,
        "--video",
        help="Source video for Scenario Review (starts a local HTTP server).",
    ),
    port: int = typer.Option(
        0,
        "--port",
        help="Preferred review HTTP port when --video is set (0 = auto).",
    ),
    review_queue: Path | None = typer.Option(
        None,
        "--review-queue",
        help=(
            "activation_study review_queue.json for SPECIAL_USED attribution "
            "labeling (default: study path if present)."
        ),
    ),
) -> None:
    """Open a read-only Vision Manifest Viewer for Phase 2 diagnostics."""
    require_repo_tools("vision-view")
    from vision_manifest_viewer.cli import main as viewer_main

    argv = [str(manifest)]
    if out is not None:
        argv.extend(["--out", str(out)])
    if config_path is not None:
        argv.extend(["--config", str(config_path)])
    if video is not None:
        argv.extend(["--video", str(video)])
        if port:
            argv.extend(["--port", str(port)])
    if review_queue is not None:
        argv.extend(["--review-queue", str(review_queue)])
    if no_open:
        argv.append("--no-open")
    code = viewer_main(argv)
    if code:
        raise typer.Exit(code=code)
