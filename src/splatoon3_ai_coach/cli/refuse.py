"""The `s3-coach refuse` command."""

from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.analysis.pipeline import refuse_analysis
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.exceptions import ConfigError, S3CoachError

console = Console()


def refuse(
    analysis_dir: Path,
    config_path: Path | None = typer.Option(None, "--config"),
) -> None:
    """Re-fuse an analysis from its stored readings and rebuild scenarios (no decode)."""
    try:
        config = load_config(resolve_config_path(config_path))
        manifest = refuse_analysis(analysis_dir, config)
    except (ConfigError, S3CoachError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(
        f"Re-fused [bold]{len(manifest.frame_results)}[/bold] frame results into "
        f"[bold]{len(manifest.state_snapshots)}[/bold] state snapshots, "
        f"[bold]{len(manifest.game_events)}[/bold] events in {analysis_dir}"
    )
