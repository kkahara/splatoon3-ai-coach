"""The `s3-coach analyze` command."""

from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.analysis.pipeline import run_analysis
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.exceptions import ConfigError, S3CoachError
from splatoon3_ai_coach.media.vision_manifest import VISION_MANIFEST_FILENAME

console = Console()


def analyze(
    video: Path,
    out: Path = typer.Option(..., "--out", help="Analysis output directory."),
    config_path: Path | None = typer.Option(None, "--config"),
    reuse_extraction: bool = typer.Option(
        False,
        "--reuse-extraction",
        help="Reuse an existing extraction manifest in --out/frames when present.",
    ),
    debug_persist_cadence_frames: bool = typer.Option(
        False,
        "--debug-persist-cadence-frames",
        help="Save cadence-sampled frames for debugging.",
    ),
) -> None:
    """Run extraction and vision analysis, producing vision_manifest.json."""
    try:
        config = load_config(resolve_config_path(config_path))
        manifest = run_analysis(
            video,
            config,
            out,
            reuse_extraction=reuse_extraction,
            debug_persist_cadence_frames=debug_persist_cadence_frames,
        )
    except (ConfigError, S3CoachError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"Analysis complete: [bold]{len(manifest.frame_results)}[/bold] frame results, "
        f"[bold]{len(manifest.state_snapshots)}[/bold] state snapshots, "
        f"[bold]{len(manifest.game_events)}[/bold] events.\n"
        f"Vision manifest: {out / VISION_MANIFEST_FILENAME}"
    )
