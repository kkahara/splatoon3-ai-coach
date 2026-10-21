"""Revalidate coach_prototype outputs with today's validator, without the LLM."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.coach.artifacts import ArtifactError
from splatoon3_ai_coach.coach.revalidation import (
    RevalidationOutcome,
    RevalidationPlan,
    apply_revalidations,
    plan_revalidations,
)

console = Console(soft_wrap=True)


def coach_reannotate_flags(
    prototype_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="coach_prototype directory containing *.output.json files.",
    ),
    replace_revalidation: bool = typer.Option(
        False,
        "--replace-revalidation",
        help=(
            "Replace existing *.revalidation.json files whose result differs. "
            "Never affects *.flags.json."
        ),
    ),
) -> None:
    """Write a current-validator recheck beside each output.

    Writes ``{safe_id}.{model_tag}.revalidation.json``. Never modifies,
    creates or replaces ``*.output.json`` or the generation-time
    ``*.flags.json`` (the original validation status). Identical existing
    revalidations are kept as they are; a differing one stops the run and
    nothing is written unless ``--replace-revalidation`` is given.
    """
    try:
        plan = plan_revalidations(prototype_dir, replace=replace_revalidation)
    except ArtifactError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if not plan.items and not plan.errors:
        console.print(f"[yellow]No model outputs recorded in {prototype_dir}[/yellow]")
        raise typer.Exit(code=1)
    _print_plan(plan)
    if not plan.writable:
        console.print(
            "\n[red]Nothing written.[/red] Fix the errors, or re-run with "
            "--replace-revalidation to replace differing revalidations. "
            "Flags and outputs were not modified."
        )
        raise typer.Exit(code=1)
    written = apply_revalidations(prototype_dir, plan)
    console.print(
        f"\nWrote {len(written)} revalidation file(s). "
        "Flags and outputs were not modified."
    )


def _print_plan(plan: RevalidationPlan) -> None:
    for item in plan.items:
        rev = item.revalidation
        note = f" (differs: {', '.join(item.differing)})" if item.differing else ""
        colour = "red" if item.outcome is RevalidationOutcome.CONFLICT else "green"
        console.print(
            f"[{colour}]{item.outcome.value}[/{colour}] {rev.output_json} "
            f"(status={rev.validation_status.value}, "
            f"contract=v{rev.validation_contract_version}){note}"
        )
    for name, message in plan.errors:
        console.print(f"[red]FAIL[/red] {name}: {message}")
    counts = Counter(item.outcome.value for item in plan.items)
    summary = ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
    console.print(f"\n{summary or 'no outputs'}; {len(plan.errors)} error(s)")
