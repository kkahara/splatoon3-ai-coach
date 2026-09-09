"""CLI: constrained CoachInput → Ollama multi-model coaching prototype."""

from __future__ import annotations

import json
import re
from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.coach.coach import (
    annotate_claim_flags,
    parse_coaching_assessment,
    serialize_coach_input_user_prompt,
)
from splatoon3_ai_coach.coach.coach_input import build_coach_input_for_scenario
from splatoon3_ai_coach.coach.llm_client import OllamaProvider
from splatoon3_ai_coach.coach.load_analysis import (
    load_coach_analysis_bundle,
    select_primary_scenario_ids,
)
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.config.settings import CoachSettings
from splatoon3_ai_coach.exceptions import ConfigError, S3CoachError

console = Console()


def coach_prototype(
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
    limit: int = typer.Option(10, "--limit", help="Max coaching units to run."),
    model: str | None = typer.Option(
        None, "--model", help="Primary Ollama model (default from config)."
    ),
    also_baseline: bool = typer.Option(
        False,
        "--also-baseline",
        help="Also run baseline_model on the same CoachInput bytes.",
    ),
    baseline_model: str | None = typer.Option(
        None, "--baseline-model", help="Override baseline Ollama model."
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output directory (default: ANALYSIS_DIR/coach_prototype).",
    ),
) -> None:
    """Run CoachInput units through one or two Ollama models for comparison."""
    try:
        cfg_path = resolve_config_path(config)
        app_config = load_config(cfg_path)
        settings = CoachSettings()
        base_url = (
            settings.ollama_base_url
            or app_config.coach.ollama_base_url
        )
        primary_model = model or app_config.coach.model
        secondary = baseline_model or app_config.coach.baseline_model
        models = [primary_model]
        if also_baseline and secondary not in models:
            models.append(secondary)

        bundle = load_coach_analysis_bundle(
            analysis_dir,
            min_usable_confidence=app_config.vision.timer.min_usable_confidence,
        )
        primary_ids = select_primary_scenario_ids(bundle.scenarios, limit=limit)
        if not primary_ids:
            console.print("[yellow]No scenarios to coach.[/yellow]")
            raise typer.Exit(code=1)

        out_dir = (out or (analysis_dir / "coach_prototype")).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        system_prompt = load_system_prompt()
        (out_dir / "system_prompt.txt").write_text(system_prompt, encoding="utf-8")

        summary_lines = [
            f"# Coach prototype — {analysis_dir}",
            "",
            f"- models: {', '.join(models)}",
            f"- units: {len(primary_ids)}",
            f"- system_prompt: identical for all runs (`system_prompt.txt`)",
            f"- CoachInput JSON: identical bytes per unit across models",
            "",
        ]

        for scenario_id in primary_ids:
            coach_input = build_coach_input_for_scenario(
                scenario_id,
                bundle.scenarios,
                bundle.contexts,
                bundle.game_clock,
                max_gap_seconds=app_config.coach.game_clock_max_lookup_gap_seconds,
                player_count_clock=bundle.player_count_clock,
                player_count_max_gap_seconds=(
                    app_config.coach.player_count_max_lookup_gap_seconds
                ),
                player_count_window_offsets_seconds=(
                    app_config.coach.player_count_window_offsets_seconds
                ),
                player_count_context_lookback_seconds=(
                    app_config.coach.player_count_context_lookback_seconds
                ),
            )
            user_prompt = serialize_coach_input_user_prompt(coach_input)
            safe_id = _safe_filename(scenario_id)
            input_path = out_dir / f"{safe_id}.coach_input.json"
            # Shared input artifact (byte-identical for every model).
            input_path.write_text(
                json.dumps(
                    coach_input.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            user_prompt_path = out_dir / f"{safe_id}.user_prompt.txt"
            user_prompt_path.write_bytes(user_prompt.encode("utf-8"))

            summary_lines.append(f"## `{scenario_id}`")
            summary_lines.append(f"- coach_input: `{input_path.name}`")
            summary_lines.append(f"- user_prompt: `{user_prompt_path.name}`")

            for model_name in models:
                provider = OllamaProvider(model_name, base_url=base_url)
                model_tag = _safe_filename(model_name)
                out_path = out_dir / f"{safe_id}.{model_tag}.output.json"
                raw_path = out_dir / f"{safe_id}.{model_tag}.raw.txt"
                flags_path = out_dir / f"{safe_id}.{model_tag}.flags.json"
                try:
                    raw = provider.complete(system_prompt, user_prompt)
                    raw_path.write_text(raw, encoding="utf-8")
                    assessment = parse_coaching_assessment(raw)
                    out_path.write_text(
                        json.dumps(
                            assessment.model_dump(mode="json"),
                            ensure_ascii=False,
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    # Flags are annotations only; assessment file is untouched.
                    flags = annotate_claim_flags(assessment)
                    flags_path.write_text(
                        json.dumps(flags, ensure_ascii=False, indent=2, sort_keys=True)
                        + "\n",
                        encoding="utf-8",
                    )
                    summary_lines.append(
                        f"- `{model_name}` → `{out_path.name}` "
                        f"(flags hit_count={flags['hit_count']})"
                    )
                    console.print(
                        f"[green]OK[/green] {scenario_id} / {model_name} "
                        f"→ {out_path.name}"
                    )
                except Exception as exc:  # noqa: BLE001 — prototype must continue
                    if not raw_path.is_file():
                        raw_path.write_text(f"ERROR: {exc}\n", encoding="utf-8")
                    summary_lines.append(
                        f"- `{model_name}` → FAILED (`{raw_path.name}`): {exc}"
                    )
                    console.print(
                        f"[red]FAIL[/red] {scenario_id} / {model_name}: {exc}"
                    )

            summary_lines.append("")

        summary_path = out_dir / "summary.md"
        summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
        console.print(f"[bold]Wrote[/bold] {summary_path}")
    except (ConfigError, S3CoachError, FileNotFoundError, ValueError, OSError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _safe_filename(value: str) -> str:
    """Filesystem-safe token for scenario/model names."""
    return re.sub(r"[^\w.\-]+", "_", value)
