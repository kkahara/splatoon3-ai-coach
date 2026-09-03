"""The `s3-coach extract` command."""

from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.exceptions import ConfigError, S3CoachError
from splatoon3_ai_coach.extraction.pipeline import run_extraction

console = Console()


def extract(
    video: Path,
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output directory. Defaults to paths.frame_output from the config.",
    ),
    config_path: Path | None = typer.Option(
        None,
        "--config",
        help="Path to a YAML configuration file.",
    ),
) -> None:
    """Extract meaningful gameplay frames and write a JSON manifest."""
    try:
        config = load_config(resolve_config_path(config_path))
        manifest = run_extraction(video, config, output_dir=out)
    except ConfigError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except S3CoachError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    output_dir = out or config.paths.frame_output
    console.print(
        f"Extracted [bold]{len(manifest.frames)}[/bold] frames "
        f"from [bold]{len(manifest.trigger_events)}[/bold] triggers into {output_dir}."
    )
