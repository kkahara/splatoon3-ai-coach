"""The `s3-coach analyze` command."""

from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.analysis.pipeline import (
    SCENARIO_CONTEXTS_JSON_FILENAME,
    SCENARIOS_JSON_FILENAME,
    run_analysis,
)
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import VisionLanguage
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.exceptions import ConfigError, S3CoachError
from splatoon3_ai_coach.media.vision_manifest import VISION_MANIFEST_FILENAME

console = Console()


def analyze(
    video: Path,
    out: Path = typer.Option(..., "--out", help="Analysis output directory."),
    config_path: Path | None = typer.Option(None, "--config"),
    language: VisionLanguage | None = typer.Option(
        None,
        "--language",
        help="UI language for text templates/OCR (en|ja). Overrides config.",
    ),
    debug_persist_cadence_frames: bool = typer.Option(
        False,
        "--debug-persist-cadence-frames",
        help="Save already-observed cadence frames as debug snapshots.",
    ),
) -> None:
    """Run cadence vision analysis, producing vision_manifest.json."""
    try:
        config = load_config(resolve_config_path(config_path))
        if language is not None:
            config.vision.language = language
        manifest = run_analysis(
            video,
            config,
            out,
            debug_persist_cadence_frames=debug_persist_cadence_frames,
        )
    except (ConfigError, S3CoachError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    timing = manifest.timing
    timing_line = ""
    if timing is not None:
        timing_line = (
            f"Timing: decode={timing.decode_seconds:.3f}s "
            f"total={timing.total_seconds:.3f}s "
            f"realtime_factor={timing.realtime_factor:.3f}\n"
        )
    console.print(
        f"Analysis complete: [bold]{len(manifest.frame_results)}[/bold] frame results, "
        f"[bold]{len(manifest.state_snapshots)}[/bold] state snapshots, "
        f"[bold]{len(manifest.game_events)}[/bold] events.\n"
        f"{timing_line}"
        f"Vision manifest: {out / VISION_MANIFEST_FILENAME}\n"
        f"Scenarios: {out / SCENARIOS_JSON_FILENAME}\n"
        f"Scenario contexts: {out / SCENARIO_CONTEXTS_JSON_FILENAME}"
    )
