"""Commands for local Review screenshot evidence."""

from pathlib import Path
from typing import Literal

import typer
from rich.console import Console

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.exceptions import ConfigError, S3CoachError
from splatoon3_ai_coach.review.timeline_pipeline import import_timeline
from splatoon3_ai_coach.review.video_adapter import (
    NoUsableTimelineSamples,
    adapt_video,
)

console = Console()
review_app = typer.Typer(no_args_is_help=True, help="Review screenshot evidence.")
_MODES = {"splat_zones", "tower_control", "rainmaker", "clam_blitz"}


def _battle_mode(mode: str | None) -> str | None:
    """Validate an explicit mode. Extraction does not change with the value."""
    if mode is None:
        return None
    if mode not in _MODES:
        raise typer.BadParameter(
            f"mode must be one of {', '.join(sorted(_MODES))}"
        )
    return mode


@review_app.command("timeline-import")
def timeline_import(
    directory: Path = typer.Argument(..., exists=True, file_okay=False),
    manifest: Path | None = typer.Option(None, "--manifest", exists=True),
    output: Path | None = typer.Option(None, "--out"),
    config_path: Path | None = typer.Option(None, "--config"),
    recording_id: str | None = typer.Option(None, "--recording-id"),
    gameplay_analysis_dir: Path | None = typer.Option(
        None, "--gameplay-analysis-dir", exists=True, file_okay=False
    ),
    ally_row: Literal["top", "bottom"] | None = typer.Option(None, "--ally-row"),
    mode: str | None = typer.Option(None, "--mode"),
) -> None:
    """Import a local Review timeline screenshot sweep."""
    try:
        config = load_config(resolve_config_path(config_path))
        out_dir = output or directory / "review_timeline_import"
        dataset = import_timeline(
            directory,
            config=config.review,
            manifest_path=manifest,
            output_dir=out_dir,
            source_recording_id=recording_id,
            gameplay_analysis_dir=gameplay_analysis_dir,
            ally_row=ally_row,
            battle_mode_id=_battle_mode(mode),
        )
    except (ConfigError, S3CoachError, OSError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(
        f"Imported {dataset.coverage.sample_count} images; "
        f"{dataset.coverage.clocked_sample_count} clocked; "
        f"{len(dataset.death_episodes)} bounded death episodes"
    )


@review_app.command("timeline-video-import")
def timeline_video_import(
    video: Path = typer.Argument(..., exists=True, dir_okay=False),
    output: Path = typer.Option(..., "--out"),
    config_path: Path | None = typer.Option(None, "--config"),
    recording_id: str | None = typer.Option(None, "--recording-id"),
    gameplay_analysis_dir: Path | None = typer.Option(
        None, "--gameplay-analysis-dir", exists=True, file_okay=False
    ),
    ally_row: Literal["top", "bottom"] | None = typer.Option(None, "--ally-row"),
    mode: str | None = typer.Option(None, "--mode"),
) -> None:
    """Adapt a timeline video and import it through the screenshot pipeline."""
    try:
        config = load_config(resolve_config_path(config_path))
        result = adapt_video(
            video,
            output,
            config=config.review,
            source_recording_id=recording_id,
            gameplay_analysis_dir=gameplay_analysis_dir,
            ally_row=ally_row,
            battle_mode_id=_battle_mode(mode),
        )
    except NoUsableTimelineSamples as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except (ConfigError, S3CoachError, OSError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(
        f"Imported {result.dataset.coverage.clocked_sample_count} timeline samples "
        f"from {video}; output: {output}"
    )

