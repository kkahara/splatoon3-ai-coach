"""Re-annotate coach_prototype ``*.flags.json`` without calling the LLM."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.coach.coach import annotate_claim_flags
from splatoon3_ai_coach.coach.llm_client import CoachingAssessment

console = Console()

_KNOWN_MODEL_TAGS = (
    "gpt-oss_20b",
    "llama3.1_8b",
)


def _model_from_output_name(filename: str) -> str:
    """Extract sanitized model tag from ``{stem}.{model}.output.json``."""
    base = filename.removesuffix(".output.json")
    for tag in sorted(_KNOWN_MODEL_TAGS, key=len, reverse=True):
        suffix = f".{tag}"
        if base.endswith(suffix):
            return tag
    # Fallback: last dotted segment (best-effort).
    return base.rsplit(".", 1)[-1]


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
    outputs = sorted(prototype_dir.glob("*.output.json"))
    if not outputs:
        console.print(f"[yellow]No *.output.json in {prototype_dir}[/yellow]")
        raise typer.Exit(code=1)

    by_model_outputs: Counter[str] = Counter()
    by_model_violations: Counter[str] = Counter()
    rewritten = 0
    errors = 0

    for out_path in outputs:
        model = _model_from_output_name(out_path.name)
        flags_path = out_path.with_name(
            out_path.name.replace(".output.json", ".flags.json")
        )
        try:
            assessment = CoachingAssessment.model_validate_json(
                out_path.read_text(encoding="utf-8")
            )
            flags = annotate_claim_flags(assessment)
            flags_path.write_text(
                json.dumps(flags, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            rewritten += 1
            by_model_outputs[model] += 1
            by_model_violations[model] += int(flags.get("hit_count", 0))
            console.print(
                f"[green]OK[/green] {out_path.name} → {flags_path.name} "
                f"(hit_count={flags.get('hit_count', 0)})"
            )
        except Exception as exc:  # noqa: BLE001 — report and continue
            errors += 1
            console.print(f"[red]FAIL[/red] {out_path.name}: {exc}")

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
