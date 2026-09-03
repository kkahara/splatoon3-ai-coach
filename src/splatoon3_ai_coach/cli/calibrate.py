"""The `s3-coach calibrate-timer` command."""

from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.exceptions import ConfigError, S3CoachError
from splatoon3_ai_coach.vision.calibration import calibrate_timer

console = Console()


def calibrate_timer_command(
    video: Path,
    out: Path = typer.Option(..., "--out", help="Calibration output directory."),
    config_path: Path | None = typer.Option(None, "--config"),
    sample_fps: float = typer.Option(1.0, "--sample-fps"),
) -> None:
    """Sample timer glyph tiles from a video for manual labeling."""
    if sample_fps <= 0:
        console.print("[red]Error:[/red] --sample-fps must be greater than 0.")
        raise typer.Exit(code=1)
    try:
        config = load_config(resolve_config_path(config_path))
        manifest_path = calibrate_timer(video, out, config.vision.timer, sample_fps)
    except (ConfigError, S3CoachError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(
        f"Calibration tiles written. Manifest: [bold]{manifest_path}[/bold]\n"
        "Label tiles into templates/{{0..9,colon}}/ before running analyze."
    )
