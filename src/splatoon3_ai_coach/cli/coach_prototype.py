"""CLI: LLM verbalization over saved CoachInput + claim selection artifacts."""

from __future__ import annotations

import json
import time
from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.cli.coach_common import (
    COACH_INPUTS_META_FILENAME,
    COACHING_INDEX_FILENAME,
    load_json,
    resolve_coach_inputs_dir,
    safe_filename,
    write_assessment_artifacts,
    write_json,
    write_metric,
)
from splatoon3_ai_coach.coach.claim_catalog import CoachingUnitResult
from splatoon3_ai_coach.coach.coach import (
    annotate_claim_flags,
    parse_coaching_assessment,
    serialize_llm_view_user_prompt,
)
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.coaching_candidates import active_factor_ids
from splatoon3_ai_coach.coach.llm_client import normalize_coach_provider
from splatoon3_ai_coach.coach.llm_runs import (
    approx_token_count,
    build_llm_runs,
    empty_coaching_assessment,
    should_skip_llm,
)
from splatoon3_ai_coach.coach.llm_view import CoachLlmView, build_coach_llm_view
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.config import load_config
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
    "should_skip_llm",
    "_build_llm_runs",
    "_LlmRun",
]


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
        help="LLM backend: ollama (default) or nvidia.",
    ),
    model: str | None = typer.Option(
        None, "--model", help="Primary Ollama model (default from config)."
    ),
    nvidia_model: str | None = typer.Option(
        None,
        "--nvidia-model",
        help="NVIDIA model id when --provider nvidia (default from config).",
    ),
    also_baseline: bool = typer.Option(
        False,
        "--also-baseline",
        help=(
            "With --provider ollama, also run baseline_model on the same "
            "CoachInput bytes. Ignored when provider is nvidia."
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
    force_llm: bool = typer.Option(
        False,
        "--force-llm",
        help=(
            "Always call the LLM even when claim selection emits zero points "
            "(A/B comparison). Default skips LLM when selected=[]."
        ),
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output directory (default: ANALYSIS_DIR/coach_prototype).",
    ),
) -> None:
    """Run LLM verbalization on saved CoachInput + claim selection artifacts.

    Requires ``s3-coach coach-inputs`` first. Does not rebuild CoachInput from
    the analysis bundle.
    """
    try:
        cfg_path = resolve_config_path(config)
        app_config = load_config(cfg_path)
        settings = CoachSettings()
        provider_name = normalize_coach_provider(
            provider or settings.llm_provider or app_config.coach.provider
        )
        llm_runs = [] if claims_only else build_llm_runs(
            provider_name=provider_name,
            coach_cfg=app_config.coach,
            settings=settings,
            ollama_model=model,
            nvidia_model_override=nvidia_model,
            also_baseline=also_baseline,
            baseline_model_override=baseline_model,
        )

        inputs_dir = resolve_coach_inputs_dir(analysis_dir, inputs)
        index_path = inputs_dir / COACHING_INDEX_FILENAME
        meta_path = inputs_dir / COACH_INPUTS_META_FILENAME
        if not index_path.is_file():
            raise FileNotFoundError(
                f"Missing {COACHING_INDEX_FILENAME} in {inputs_dir}. "
                f"Run `s3-coach coach-inputs {analysis_dir}` first."
            )
        index_payload = load_json(index_path)
        if not isinstance(index_payload, list) or not index_payload:
            raise ValueError(f"No coaching units in {index_path}")
        meta = load_json(meta_path) if meta_path.is_file() else {}
        match_duration = meta.get("match_duration_seconds")

        units = list(index_payload)
        if limit is not None:
            units = units[: max(0, limit)]

        out_dir = (out or (analysis_dir / "coach_prototype")).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        system_prompt = load_system_prompt()
        (out_dir / "system_prompt.txt").write_text(system_prompt, encoding="utf-8")

        run_labels = [run.label for run in llm_runs]
        summary_lines = [
            f"# Coach prototype — {analysis_dir}",
            "",
            f"- inputs: `{inputs_dir}`",
            f"- claims_only: {claims_only}",
            f"- force_llm: {force_llm}",
            f"- provider: {provider_name}",
            f"- models: {', '.join(run_labels) if run_labels else '(none)'}",
            f"- units: {len(units)}",
            f"- match_duration_seconds: {match_duration}",
            f"- system_prompt: `system_prompt.txt`",
            f"- llm_metrics: `llm_metrics.jsonl`",
            "",
            "## LLM runs",
            "",
        ]

        metrics_path = out_dir / "llm_metrics.jsonl"
        metrics_file = metrics_path.open("w", encoding="utf-8")
        result_index: list[dict[str, object]] = []

        try:
            for entry in units:
                scenario_id = str(entry["scenario_id"])
                safe_id = str(entry.get("safe_id") or safe_filename(scenario_id))
                coach_input = CoachInput.model_validate(
                    load_json(inputs_dir / str(entry["coach_input_json"]))
                )
                unit = CoachingUnitResult.model_validate(
                    load_json(inputs_dir / str(entry["coaching_json"]))
                )
                active = active_factor_ids(unit.to_candidate())
                selected_for_llm = bool(unit.selected_for_llm)
                skip_llm = should_skip_llm(
                    selected_for_llm=selected_for_llm,
                    force_llm=force_llm,
                    has_llm_runs=bool(llm_runs),
                )

                write_json(
                    out_dir / f"{safe_id}.coach_input.json",
                    coach_input.model_dump(mode="json"),
                )
                write_json(
                    out_dir / f"{safe_id}.coaching.json",
                    unit.model_dump(mode="json"),
                )
                for key, suffix in (
                    ("vmv_player", ".vmv.player.txt"),
                    ("vmv_dev", ".vmv.dev.txt"),
                ):
                    src_name = entry.get(key)
                    if isinstance(src_name, str) and (inputs_dir / src_name).is_file():
                        (out_dir / f"{safe_id}{suffix}").write_text(
                            (inputs_dir / src_name).read_text(encoding="utf-8"),
                            encoding="utf-8",
                        )

                result_index.append(
                    {
                        "scenario_id": scenario_id,
                        "candidate_id": unit.candidate_id,
                        "candidate_type": unit.candidate_type,
                        "video_time": unit.video_time,
                        "importance_score": unit.importance_score,
                        "rank": unit.rank,
                        "selected_for_llm": selected_for_llm,
                        "active_factors": active,
                        "coach_input_json": f"{safe_id}.coach_input.json",
                        "coaching_json": f"{safe_id}.coaching.json",
                    }
                )
                console.print(
                    f"[cyan]load[/cyan] {scenario_id} "
                    f"rank={unit.rank} score={unit.importance_score} "
                    f"selected_for_llm={selected_for_llm} factors={active}"
                )

                if skip_llm:
                    assessment = empty_coaching_assessment()
                    for run in llm_runs:
                        write_assessment_artifacts(
                            out_dir=out_dir,
                            safe_id=safe_id,
                            model_label=run.label,
                            assessment=assessment,
                            raw_text=json.dumps(
                                assessment.model_dump(mode="json"),
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
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
                                "model": run.label,
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
                            f"- `{scenario_id}` / `{run.label}` → skipped_llm=true"
                        )
                        console.print(
                            f"[yellow]skip[/yellow] {scenario_id} / {run.label} "
                            f"(selected_for_llm=false; no LLM)"
                        )
                    summary_lines.append("")
                    continue

                llm_view_name = entry.get("llm_view_json")
                if isinstance(llm_view_name, str) and (inputs_dir / llm_view_name).is_file():
                    llm_view = CoachLlmView.model_validate(
                        load_json(inputs_dir / llm_view_name)
                    )
                else:
                    llm_view = build_coach_llm_view(coach_input, unit)
                write_json(
                    out_dir / f"{safe_id}.llm_view.json",
                    llm_view.model_dump(mode="json"),
                )
                user_prompt = serialize_llm_view_user_prompt(llm_view)
                user_prompt_path = out_dir / f"{safe_id}.user_prompt.txt"
                user_prompt_path.write_bytes(user_prompt.encode("utf-8"))

                if not llm_runs:
                    summary_lines.append(
                        f"- `{scenario_id}` → claims-only (no LLM provider)"
                    )
                    summary_lines.append("")
                    continue

                system_tokens = approx_token_count(system_prompt)
                user_tokens = approx_token_count(user_prompt)
                input_tokens = (system_tokens or 0) + (user_tokens or 0)

                for run in llm_runs:
                    model_tag = safe_filename(run.label)
                    out_path = out_dir / f"{safe_id}.{model_tag}.output.json"
                    raw_path = out_dir / f"{safe_id}.{model_tag}.raw.txt"
                    flags_path = out_dir / f"{safe_id}.{model_tag}.flags.json"
                    started = time.perf_counter()
                    try:
                        raw = run.provider.complete(system_prompt, user_prompt)
                        latency_ms = int((time.perf_counter() - started) * 1000)
                        raw_path.write_text(raw, encoding="utf-8")
                        assessment = parse_coaching_assessment(raw)
                        write_json(out_path, assessment.model_dump(mode="json"))
                        flags = annotate_claim_flags(assessment)
                        write_json(flags_path, flags)
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
        finally:
            metrics_file.close()

        write_json(out_dir / COACHING_INDEX_FILENAME, result_index)
        summary_path = out_dir / "summary.md"
        summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
        console.print(f"[bold]Wrote[/bold] {summary_path}")
        console.print(f"[bold]Wrote[/bold] {metrics_path}")
    except (ConfigError, S3CoachError, FileNotFoundError, ValueError, OSError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
