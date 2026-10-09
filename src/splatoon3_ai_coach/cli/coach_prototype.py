"""CLI: LLM verbalization over saved CoachInput + claim selection artifacts."""

from __future__ import annotations

import json
import time
from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.cli.coach_common import resolve_coach_inputs_dir, write_metric
from splatoon3_ai_coach.coach.artifacts import (
    COACH_INPUTS_META_FILENAME,
    COACH_PROTOTYPE_DIRNAME,
    COACHING_INDEX_FILENAME,
    SYSTEM_PROMPT_FILENAME,
    VISION_MANIFEST_FILENAME,
    PrototypeIndex,
    PrototypeIndexEntry,
    PrototypeOutput,
    artifact_staleness,
    load_json,
    load_unit_artifacts,
    output_file_names,
    read_coaching_index,
    unit_file_names,
    write_assessment_artifacts,
    write_json,
    write_prototype_index,
)
from splatoon3_ai_coach.coach.coach import (
    annotate_claim_flags,
    parse_coaching_assessment,
    serialize_llm_view_user_prompt,
)
from splatoon3_ai_coach.coach.coaching_candidates import active_factor_ids
from splatoon3_ai_coach.coach.llm_client import normalize_coach_provider
from splatoon3_ai_coach.coach.llm_runs import (
    approx_token_count,
    build_llm_runs,
    empty_coaching_assessment,
    run_provenance,
    utc_timestamp,
)
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import CoachConfig
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.config.settings import CoachSettings
from splatoon3_ai_coach.exceptions import ConfigError, S3CoachError

console = Console()

# Re-exports for tests that still import from this CLI module.
from splatoon3_ai_coach.coach.llm_runs import (  # noqa: E402
    _build_llm_runs,
    _LlmRun,
)

__all__ = [
    "approx_token_count",
    "coach_prototype",
    "empty_coaching_assessment",
    "_build_llm_runs",
    "_LlmRun",
]


def _prepared_model_label(
    provider_name: str,
    coach_cfg: CoachConfig,
    *,
    model_override: str | None,
    nvidia_override: str | None,
    cursor_override: str | None = None,
) -> str:
    """Model label for skip artifacts written before any provider call."""
    if provider_name == "nvidia":
        return nvidia_override or coach_cfg.nvidia_model
    if provider_name == "cursor":
        return cursor_override or coach_cfg.cursor_model
    return model_override or coach_cfg.model


def _manifest_or_none(analysis_dir: Path) -> dict | None:
    path = analysis_dir / VISION_MANIFEST_FILENAME
    return load_json(path) if path.is_file() else None


def _finish_entry(
    entry: PrototypeIndexEntry,
    outputs: list[PrototypeOutput],
    primary_model: str,
) -> None:
    """Record a unit's outputs and name the primary model's output, if written."""
    entry.outputs = outputs
    entry.primary_output_json = next(
        (o.output_json for o in outputs if o.model == primary_model), None
    )


def coach_prototype(
    analysis_dir: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Analyze output directory (used to locate coach_inputs by default).",
    ),
    config: Path | None = typer.Option(
        None, "--config", "-c", help="Path to YAML config."
    ),
    inputs: Path | None = typer.Option(
        None,
        "--inputs",
        help="Directory from `coach-inputs` (default: ANALYSIS_DIR/coach_inputs).",
    ),
    limit: int | None = typer.Option(
        None,
        "--limit",
        help="Max units to run (default: all units in coaching_index.json).",
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        help="LLM backend: ollama (default), nvidia, or cursor.",
    ),
    model: str | None = typer.Option(
        None, "--model", help="Primary Ollama model (default from config)."
    ),
    nvidia_model: str | None = typer.Option(
        None,
        "--nvidia-model",
        help="NVIDIA model id when --provider nvidia (default from config).",
    ),
    cursor_model: str | None = typer.Option(
        None,
        "--cursor-model",
        help="Cursor model id when --provider cursor (default from config).",
    ),
    also_baseline: bool = typer.Option(
        False,
        "--also-baseline",
        help=(
            "With --provider ollama, also run baseline_model on the same "
            "CoachInput bytes. Ignored for nvidia and cursor."
        ),
    ),
    baseline_model: str | None = typer.Option(
        None, "--baseline-model", help="Override baseline Ollama model."
    ),
    claims_only: bool = typer.Option(
        False,
        "--claims-only",
        help="Skip LLM calls; copy/verify saved claim artifacts only.",
    ),
    call_llm: bool = typer.Option(
        False,
        "--call-llm",
        help=(
            "Call the provider for units with selected_for_llm. "
            "Default writes system_prompt.txt and those units' user_prompt.txt "
            "without calling the provider."
        ),
    ),
    force_llm: bool = typer.Option(
        False,
        "--force-llm",
        help=(
            "With --call-llm, also call the provider for units that are not "
            "selected_for_llm."
        ),
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output directory (default: ANALYSIS_DIR/coach_prototype).",
    ),
    allow_stale: bool = typer.Option(
        False,
        "--allow-stale",
        help=(
            "Proceed even when coach_inputs predate the current evidence "
            "version or analysis (they are not rebuilt)."
        ),
    ),
) -> None:
    """Prepare prototype prompts, and call the provider only with --call-llm.

    Requires ``s3-coach coach-inputs`` first. Does not rebuild CoachInput from
    the analysis bundle. ``selected_for_llm`` stays an eligibility mark.
    """
    try:
        cfg_path = resolve_config_path(config)
        app_config = load_config(cfg_path)
        settings = CoachSettings()
        provider_name = normalize_coach_provider(
            provider or settings.llm_provider or app_config.coach.provider
        )
        spend_calls = call_llm and not claims_only
        llm_runs = [] if not spend_calls else build_llm_runs(
            provider_name=provider_name,
            coach_cfg=app_config.coach,
            settings=settings,
            ollama_model=model,
            nvidia_model_override=nvidia_model,
            also_baseline=also_baseline,
            baseline_model_override=baseline_model,
            cursor_model_override=cursor_model,
        )

        inputs_dir = resolve_coach_inputs_dir(analysis_dir, inputs)
        meta_path = inputs_dir / COACH_INPUTS_META_FILENAME
        if not (inputs_dir / COACHING_INDEX_FILENAME).is_file():
            raise FileNotFoundError(
                f"Missing {COACHING_INDEX_FILENAME} in {inputs_dir}. "
                f"Run `s3-coach coach-inputs {analysis_dir}` first."
            )
        coaching_index = read_coaching_index(inputs_dir)
        staleness = artifact_staleness(
            coaching_index, _manifest_or_none(analysis_dir)
        )
        if staleness.stale:
            if not allow_stale:
                raise ValueError(f"{staleness.message()} (or pass --allow-stale)")
            console.print(f"[yellow]warning:[/yellow] {staleness.message()}")
        if not coaching_index.entries:
            raise ValueError(
                f"No coaching units in {inputs_dir / COACHING_INDEX_FILENAME}"
            )
        meta = load_json(meta_path) if meta_path.is_file() else {}
        match_duration = meta.get("match_duration_seconds")

        units = list(coaching_index.entries)
        if limit is not None:
            units = units[: max(0, limit)]

        out_dir = (out or (analysis_dir / COACH_PROTOTYPE_DIRNAME)).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        system_prompt = load_system_prompt(
            roster_evidence=app_config.coach.roster_evidence
        )
        (out_dir / SYSTEM_PROMPT_FILENAME).write_text(system_prompt, encoding="utf-8")

        run_labels = [run.label for run in llm_runs]
        prepared_label = _prepared_model_label(
            provider_name,
            app_config.coach,
            model_override=model,
            nvidia_override=nvidia_model,
            cursor_override=cursor_model,
        )
        primary_model = (run_labels or [prepared_label])[0]
        summary_lines = [
            f"# Coach prototype — {analysis_dir}",
            "",
            f"- inputs: `{inputs_dir}`",
            f"- claims_only: {claims_only}",
            f"- call_llm: {spend_calls}",
            f"- force_llm: {force_llm}",
            f"- provider: {provider_name}",
            f"- models: {', '.join(run_labels) if run_labels else '(none)'}",
            f"- primary_model: {primary_model}",
            f"- units: {len(units)}",
            f"- match_duration_seconds: {match_duration}",
            "- system_prompt: `system_prompt.txt`",
            "- llm_metrics: `llm_metrics.jsonl`",
            "",
            "## LLM runs",
            "",
        ]

        metrics_path = out_dir / "llm_metrics.jsonl"
        metrics_file = metrics_path.open("w", encoding="utf-8")
        result_entries: list[PrototypeIndexEntry] = []

        try:
            for entry in units:
                scenario_id = entry.scenario_id
                safe_id = entry.safe_id
                names = unit_file_names(safe_id)
                artifacts = load_unit_artifacts(inputs_dir, entry)
                coach_input = artifacts.coach_input
                unit = artifacts.unit
                active = active_factor_ids(unit.to_candidate())
                selected_for_llm = bool(unit.selected_for_llm)
                skip_llm = (not selected_for_llm) and not (spend_calls and force_llm)

                write_json(
                    out_dir / names.coach_input,
                    coach_input.model_dump(mode="json"),
                )
                write_json(out_dir / names.coaching, unit.model_dump(mode="json"))
                for src_name, dest_name in (
                    (entry.vmv_player, names.vmv_player),
                    (entry.vmv_dev, names.vmv_dev),
                ):
                    if src_name and (inputs_dir / src_name).is_file():
                        (out_dir / dest_name).write_text(
                            (inputs_dir / src_name).read_text(encoding="utf-8"),
                            encoding="utf-8",
                        )

                outputs: list[PrototypeOutput] = []
                result_entry = PrototypeIndexEntry(
                    scenario_id=scenario_id,
                    candidate_id=unit.candidate_id,
                    candidate_type=unit.candidate_type,
                    safe_id=safe_id,
                    video_time=unit.video_time,
                    importance_score=unit.importance_score,
                    rank=unit.rank,
                    selected_for_llm=selected_for_llm,
                    active_factors=active,
                    coach_input_json=names.coach_input,
                    coaching_json=names.coaching,
                )
                result_entries.append(result_entry)
                console.print(
                    f"[cyan]load[/cyan] {scenario_id} "
                    f"rank={unit.rank} score={unit.importance_score} "
                    f"selected_for_llm={selected_for_llm} factors={active}"
                )

                if skip_llm:
                    assessment = empty_coaching_assessment()
                    raw_text = json.dumps(
                        assessment.model_dump(mode="json"),
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    labels = run_labels or [prepared_label]
                    for label in labels:
                        outputs.append(
                            write_assessment_artifacts(
                                out_dir=out_dir,
                                safe_id=safe_id,
                                model_label=label,
                                assessment=assessment,
                                raw_text=raw_text + "\n",
                                flags=annotate_claim_flags(assessment),
                                skipped_llm=True,
                            )
                        )
                        write_metric(
                            metrics_file,
                            {
                                "scenario_id": scenario_id,
                                "importance_score": unit.importance_score,
                                "rank": unit.rank,
                                "selected_for_llm": selected_for_llm,
                                "active_factors": active,
                                "skipped_llm": True,
                                "provider": provider_name,
                                "model": label,
                                "system_tokens_est": None,
                                "user_tokens_est": None,
                                "input_tokens_est": None,
                                "output_tokens_est": None,
                                "latency_ms": 0,
                                "success": True,
                                "error": None,
                            },
                        )
                        summary_lines.append(
                            f"- `{scenario_id}` / `{label}` → skipped_llm=true"
                        )
                        console.print(
                            f"[yellow]skip[/yellow] {scenario_id} / {label} "
                            f"(selected_for_llm=false; no LLM)"
                        )
                    summary_lines.append("")
                    _finish_entry(result_entry, outputs, primary_model)
                    continue

                llm_view = artifacts.llm_view or build_coach_llm_view(
                    coach_input, unit, app_config.coach.death_factor_thresholds
                )
                write_json(out_dir / names.llm_view, llm_view.model_dump(mode="json"))
                llm_view_bytes = (out_dir / names.llm_view).read_bytes()
                user_prompt = serialize_llm_view_user_prompt(llm_view)
                user_prompt_path = out_dir / names.user_prompt
                user_prompt_path.write_bytes(user_prompt.encode("utf-8"))
                result_entry.llm_view_json = names.llm_view
                result_entry.user_prompt_txt = names.user_prompt

                if not spend_calls:
                    summary_lines.append(
                        f"- `{scenario_id}` → prepared `{user_prompt_path.name}` "
                        "(no provider call)"
                    )
                    summary_lines.append("")
                    console.print(
                        "[yellow]prepared[/yellow] "
                        f"{scenario_id} → {user_prompt_path.name}"
                    )
                    continue

                system_tokens = approx_token_count(system_prompt)
                user_tokens = approx_token_count(user_prompt)
                input_tokens = (system_tokens or 0) + (user_tokens or 0)

                for run in llm_runs:
                    run_names = output_file_names(safe_id, run.label)
                    out_path = out_dir / run_names.output
                    raw_path = out_dir / run_names.raw
                    started = time.perf_counter()
                    executed_at = utc_timestamp()
                    try:
                        raw = run.provider.complete(system_prompt, user_prompt)
                        latency_ms = int((time.perf_counter() - started) * 1000)
                        raw_path.write_text(raw, encoding="utf-8")
                        assessment = parse_coaching_assessment(raw)
                        flags = annotate_claim_flags(assessment, llm_view)
                        outputs.append(
                            write_assessment_artifacts(
                                out_dir=out_dir,
                                safe_id=safe_id,
                                model_label=run.label,
                                assessment=assessment,
                                raw_text=raw,
                                flags=flags,
                                skipped_llm=False,
                                provenance=run_provenance(
                                    run,
                                    provider_name=provider_name,
                                    system_prompt=system_prompt,
                                    user_prompt=user_prompt,
                                    llm_view_bytes=llm_view_bytes,
                                    importance_config_hash=meta.get(
                                        "importance_config_hash"
                                    ),
                                    executed_at=executed_at,
                                ),
                            )
                        )
                        write_metric(
                            metrics_file,
                            {
                                "scenario_id": scenario_id,
                                "importance_score": unit.importance_score,
                                "rank": unit.rank,
                                "selected_for_llm": selected_for_llm,
                                "active_factors": active,
                                "skipped_llm": False,
                                "provider": provider_name,
                                "model": run.label,
                                "system_tokens_est": system_tokens,
                                "user_tokens_est": user_tokens,
                                "input_tokens_est": input_tokens,
                                "output_tokens_est": approx_token_count(raw),
                                "latency_ms": latency_ms,
                                "success": True,
                                "error": None,
                            },
                        )
                        summary_lines.append(
                            f"- `{scenario_id}` / `{run.label}` → `{out_path.name}` "
                            f"(flags hit_count={flags['hit_count']}, "
                            f"latency_ms={latency_ms})"
                        )
                        console.print(
                            f"[green]OK[/green] {scenario_id} / {run.label} "
                            f"→ {out_path.name} ({latency_ms} ms)"
                        )
                    except Exception as exc:  # noqa: BLE001 — prototype must continue
                        latency_ms = int((time.perf_counter() - started) * 1000)
                        if not raw_path.is_file():
                            raw_path.write_text(f"ERROR: {exc}\n", encoding="utf-8")
                        write_metric(
                            metrics_file,
                            {
                                "scenario_id": scenario_id,
                                "importance_score": unit.importance_score,
                                "rank": unit.rank,
                                "selected_for_llm": selected_for_llm,
                                "active_factors": active,
                                "skipped_llm": False,
                                "provider": provider_name,
                                "model": run.label,
                                "system_tokens_est": system_tokens,
                                "user_tokens_est": user_tokens,
                                "input_tokens_est": input_tokens,
                                "output_tokens_est": None,
                                "latency_ms": latency_ms,
                                "success": False,
                                "error": str(exc),
                            },
                        )
                        summary_lines.append(
                            f"- `{scenario_id}` / `{run.label}` → FAILED "
                            f"(`{raw_path.name}`): {exc}"
                        )
                        console.print(
                            f"[red]FAIL[/red] {scenario_id} / {run.label}: {exc}"
                        )

                summary_lines.append("")
                _finish_entry(result_entry, outputs, primary_model)
        finally:
            metrics_file.close()

        write_prototype_index(
            out_dir,
            PrototypeIndex(
                provider=provider_name,
                primary_model=primary_model,
                entries=result_entries,
            ),
        )
        summary_path = out_dir / "summary.md"
        summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
        console.print(f"[bold]Wrote[/bold] {summary_path}")
        console.print(f"[bold]Wrote[/bold] {metrics_path}")
    except (ConfigError, S3CoachError, FileNotFoundError, ValueError, OSError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
