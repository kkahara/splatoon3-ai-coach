"""CLI: build CoachInput + scored coaching candidates (no LLM)."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.cli.coach_common import default_coach_inputs_dir
from splatoon3_ai_coach.coach.claim_catalog import CoachingUnitResult
from splatoon3_ai_coach.coach.coach_inputs_build import write_coach_inputs
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.exceptions import ConfigError, S3CoachError

NO_COACHING_UNITS_EXIT_CODE = 3
"""Exit code when the analysis has no DEATH_EPISODE to coach (not an error)."""

console = Console()


def coach_inputs(
    analysis_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Analyze output directory with vision_manifest + scenarios.",
    ),
    config: Path | None = typer.Option(
        None, "--config", "-c", help="Path to YAML config."
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        help=(
            "Max candidates to generate (build cap). Full-match ranking needs "
            "all candidates built."
        ),
    ),
    max_llm_units: int | None = typer.Option(
        None,
        "--max-llm-units",
        help="Override coach.max_llm_units (top-N selected for LLM).",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output directory (default: ANALYSIS_DIR/coach_inputs).",
    ),
) -> None:
    """Build CoachInput + importance-scored candidates per DEATH_EPISODE (no LLM)."""
    try:
        app_config = load_config(resolve_config_path(config))
        build = write_coach_inputs(
            analysis_dir,
            app_config,
            out_dir=out or default_coach_inputs_dir(analysis_dir),
            limit=limit,
            max_llm_units=max_llm_units,
            on_unit=_print_unit,
        )
        if build is None:
            console.print("[yellow]No deaths detected; no scenarios to coach.[/yellow]")
            raise typer.Exit(code=NO_COACHING_UNITS_EXIT_CODE)
        console.print(f"[bold]Wrote[/bold] {build.index_path}")
        console.print(f"[bold]Wrote[/bold] {build.eval_path}")
        if build.report_diversity_path is not None:
            console.print(f"[bold]Wrote[/bold] {build.report_diversity_path}")
        console.print(
            f"[bold]Next:[/bold] s3-coach coach-prototype {analysis_dir} "
            f"--inputs {build.out_dir} writes prompts. Add --call-llm to call the "
            "provider for selected units."
        )
    except (ConfigError, S3CoachError, FileNotFoundError, ValueError, OSError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _print_unit(unit: CoachingUnitResult, active: list[str]) -> None:
    console.print(
        f"[cyan]inputs[/cyan] rank={unit.rank} {unit.candidate_id} "
        f"score={unit.importance_score:.1f} "
        f"selected_for_llm={unit.selected_for_llm} factors={active}"
    )
