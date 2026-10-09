"""Re-annotate coach_prototype ``*.flags.json`` without calling the LLM."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.coach.artifacts import (
    ArtifactError,
    load_assessment,
    prototype_outputs,
    read_document,
    read_prototype_index,
    write_json,
)
from splatoon3_ai_coach.coach.coach import annotate_claim_flags

console = Console()


def coach_reannotate_flags(
    prototype_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="coach_prototype directory containing *.output.json files.",
    ),
) -> None:
    """Rewrite ``*.flags.json`` from existing assessments. Never touches outputs."""
    try:
        outputs = prototype_outputs(prototype_dir)
    except ArtifactError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    if not outputs:
        console.print(f"[yellow]No model outputs recorded in {prototype_dir}[/yellow]")
        raise typer.Exit(code=1)

    by_model_outputs: Counter[str] = Counter()
    by_model_violations: Counter[str] = Counter()
    rewritten = 0
    errors = 0

    views = _views_by_output(prototype_dir)
    for output in outputs:
        try:
            assessment = load_assessment(prototype_dir / output.output_json)
            flags = annotate_claim_flags(assessment, views.get(output.output_json))
            write_json(prototype_dir / output.flags_json, flags)
            rewritten += 1
            by_model_outputs[output.model] += 1
            by_model_violations[output.model] += int(flags.get("hit_count", 0))
            console.print(
                f"[green]OK[/green] {output.output_json} → {output.flags_json} "
                f"(hit_count={flags.get('hit_count', 0)}, "
                f"status={flags.get('validation_status')})"
            )
        except Exception as exc:  # noqa: BLE001 — report and continue
            errors += 1
            console.print(f"[red]FAIL[/red] {output.output_json}: {exc}")

    console.print("")
    console.print(f"{'model':<24} {'outputs':>8} {'violations':>10}")
    for model in sorted(by_model_outputs):
        console.print(
            f"{model:<24} {by_model_outputs[model]:>8} "
            f"{by_model_violations[model]:>10}"
        )
    console.print(
        f"\nRewrote {rewritten} flags file(s); {errors} error(s). "
        "Output JSON files were not modified."
    )
    if errors:
        raise typer.Exit(code=1)


def _views_by_output(prototype_dir: Path) -> dict[str, dict]:
    """The unit's saved ``llm_view.json`` for each output file name."""
    index = read_prototype_index(prototype_dir)
    views: dict[str, dict] = {}
    for entry in index.entries if index is not None else []:
        view = read_document(prototype_dir, entry.llm_view_json)
        if view is None:
            continue
        for output in entry.outputs:
            views[output.output_json] = view
    return views
