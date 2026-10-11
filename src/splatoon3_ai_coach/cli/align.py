"""Commands for Review to gameplay clock alignment."""

from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.alignment.review_alignment import write_alignment
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.exceptions import ConfigError, S3CoachError

console = Console()
align_app = typer.Typer(no_args_is_help=True, help="Cross-source clock alignment.")


@align_app.command("review-clock")
def review_clock(
    review_import: Path = typer.Argument(..., exists=True, file_okay=False),
    gameplay_analysis: Path = typer.Argument(..., exists=True, file_okay=False),
    output: Path = typer.Option(..., "--out"),
    config_path: Path | None = typer.Option(None, "--config"),
) -> None:
    """Align a declared Review import / gameplay analysis pair of one match."""
    try:
        config = load_config(resolve_config_path(config_path))
        out, alignment = write_alignment(review_import, gameplay_analysis, output, config)
    except (ConfigError, S3CoachError, OSError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    cal = alignment.calibration
    console.print(
        f"pair {alignment.pair.pair_status} {alignment.pair.reasons}; "
        f"fit {cal.fit_status} ({cal.reason}); c {cal.c_interval}; output: {out}"
    )
