"""Generate a local HTML viewer for a vision_manifest.json."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

# Allow importing the repo-root tools package in editable checkouts.
_TOOLS_ROOT = Path(__file__).resolve().parents[3] / "tools"
if _TOOLS_ROOT.is_dir() and str(_TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(_TOOLS_ROOT))

from vision_manifest_viewer.cli import main as viewer_main  # noqa: E402


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
) -> None:
    """Open a read-only Vision Manifest Viewer for Phase 2 diagnostics."""
    argv = [str(manifest)]
    if out is not None:
        argv.extend(["--out", str(out)])
    if config_path is not None:
        argv.extend(["--config", str(config_path)])
    if no_open:
        argv.append("--no-open")
    code = viewer_main(argv)
    if code:
        raise typer.Exit(code=code)
