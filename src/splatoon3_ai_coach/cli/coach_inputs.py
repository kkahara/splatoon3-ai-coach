"""CLI: build CoachInput + scored coaching candidates (no LLM)."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.cli.coach_common import (
    COACH_INPUTS_META_FILENAME,
    COACHING_INDEX_FILENAME,
    default_coach_inputs_dir,
    safe_filename,
    write_json,
)
from splatoon3_ai_coach.coach.coaching_candidates import active_factor_ids
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.coach.load_analysis import (
    load_coach_analysis_bundle,
    select_primary_scenario_ids,
)
from splatoon3_ai_coach.coach.match_scoring import score_match
from splatoon3_ai_coach.coach.vmv import format_vmv_developer, format_vmv_player
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.exceptions import ConfigError, S3CoachError

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
        cfg_path = resolve_config_path(config)
        app_config = load_config(cfg_path)
        bundle = load_coach_analysis_bundle(
            analysis_dir,
            min_usable_confidence=app_config.vision.timer.min_usable_confidence,
        )
        primary_ids = select_primary_scenario_ids(bundle.scenarios, limit=limit)
        if not primary_ids:
            console.print("[yellow]No scenarios to coach.[/yellow]")
            raise typer.Exit(code=1)

        scoring = score_match(
            bundle, app_config, limit=limit, max_llm_units=max_llm_units
        )
        match_duration = scoring.match_duration_seconds
        top_n = scoring.max_llm_units
        units = scoring.units
        units_by_rank = scoring.units_by_rank()
        coach_inputs_by_id = scoring.coach_inputs
        config_hash = scoring.importance_config_hash
        out_dir = (out or default_coach_inputs_dir(analysis_dir)).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        eval_lines = [
            f"# Claim eval — {analysis_dir.name}",
            "",
            f"match_duration_seconds: {match_duration}",
            f"max_llm_units: {top_n}",
            f"importance_config_hash: {config_hash}",
            "",
            "| rank | candidate | type | score | selected | active factors |",
            "| ---: | --- | --- | ---: | --- | --- |",
        ]
        index_payload: list[dict[str, object]] = []

        for unit in units_by_rank:
            scenario_id = unit.candidate_id
            coach_input = coach_inputs_by_id[scenario_id]
            active = active_factor_ids(unit.to_candidate())
            safe_id = safe_filename(scenario_id)
            input_name = f"{safe_id}.coach_input.json"
            coaching_name = f"{safe_id}.coaching.json"
            llm_view_name = f"{safe_id}.llm_view.json"
            player_name = f"{safe_id}.vmv.player.txt"
            dev_name = f"{safe_id}.vmv.dev.txt"

            llm_view = build_coach_llm_view(coach_input, unit)
            write_json(out_dir / input_name, coach_input.model_dump(mode="json"))
            write_json(out_dir / coaching_name, unit.model_dump(mode="json"))
            write_json(out_dir / llm_view_name, llm_view.model_dump(mode="json"))
            (out_dir / player_name).write_text(format_vmv_player(unit), encoding="utf-8")
            (out_dir / dev_name).write_text(format_vmv_developer(unit), encoding="utf-8")

            selected_mark = "Y" if unit.selected_for_llm else "N"
            factors_txt = ", ".join(active) if active else "—"
            eval_lines.append(
                f"| {unit.rank} | `{scenario_id}` | {unit.candidate_type} | "
                f"{unit.importance_score:.1f} | {selected_mark} | {factors_txt} |"
            )

            index_payload.append(
                {
                    "scenario_id": scenario_id,
                    "candidate_id": unit.candidate_id,
                    "candidate_type": unit.candidate_type,
                    "safe_id": safe_id,
                    "video_time": unit.video_time,
                    "importance_score": unit.importance_score,
                    "rank": unit.rank,
                    "selected_for_llm": unit.selected_for_llm,
                    "importance_config_hash": config_hash,
                    "active_factors": active,
                    "coach_input_json": input_name,
                    "coaching_json": coaching_name,
                    "llm_view_json": llm_view_name,
                    "vmv_player": player_name,
                    "vmv_dev": dev_name,
                }
            )
            console.print(
                f"[cyan]inputs[/cyan] rank={unit.rank} {scenario_id} "
                f"score={unit.importance_score:.1f} "
                f"selected_for_llm={unit.selected_for_llm} factors={active}"
            )

        eval_lines.extend(
            [
                "",
                "## Checklist",
                "",
                "1. Did top-N pick the right deaths for coaching attention?",
                "2. Are importance factors evidence-grounded (not gates)?",
                "3. Is interpretation a valid principle (not disguised judgment)?",
                "4. Did it invent any “should have” / “bad death” judgments?",
                "5. Does score mean attention worthiness (not how bad)?",
                "6. Does coaching feel useful (selective top-N)?",
                "",
            ]
        )

        # Index in rank order for prototype consumption.
        write_json(out_dir / COACHING_INDEX_FILENAME, index_payload)
        write_json(
            out_dir / COACH_INPUTS_META_FILENAME,
            {
                "analysis_dir": str(analysis_dir.resolve()),
                "match_duration_seconds": match_duration,
                "max_llm_units": top_n,
                "unit_count": len(index_payload),
                "selected_for_llm_count": sum(
                    1 for u in units if u.selected_for_llm
                ),
                "importance_config_hash": config_hash,
                "importance_config": scoring.importance_config,
            },
        )
        eval_path = out_dir / "claim_eval.md"
        eval_path.write_text("\n".join(eval_lines) + "\n", encoding="utf-8")
        console.print(f"[bold]Wrote[/bold] {out_dir / COACHING_INDEX_FILENAME}")
        console.print(f"[bold]Wrote[/bold] {eval_path}")
        console.print(
            f"[bold]Next:[/bold] s3-coach coach-prototype {analysis_dir} "
            f"--inputs {out_dir} writes prompts. Add --call-llm to call the "
            "provider for selected units."
        )
    except (ConfigError, S3CoachError, FileNotFoundError, ValueError, OSError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
