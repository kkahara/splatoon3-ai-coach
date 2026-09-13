"""CLI: constrained CoachInput → claim selection / VMV / optional LLM."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.console import Console

from splatoon3_ai_coach.coach.claim_selection import (
    resolve_match_duration_seconds,
    select_coaching_unit,
)
from splatoon3_ai_coach.coach.coach import (
    annotate_claim_flags,
    parse_coaching_assessment,
    serialize_coach_input_user_prompt,
)
from splatoon3_ai_coach.coach.coach_input import build_coach_input_for_scenario
from splatoon3_ai_coach.coach.llm_client import (
    LLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    normalize_coach_provider,
)
from splatoon3_ai_coach.coach.load_analysis import (
    load_coach_analysis_bundle,
    select_primary_scenario_ids,
)
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.coach.vmv import format_vmv_developer, format_vmv_player
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import CoachConfig
from splatoon3_ai_coach.config.paths import resolve_config_path
from splatoon3_ai_coach.config.settings import CoachSettings
from splatoon3_ai_coach.exceptions import ConfigError, S3CoachError

console = Console()


@dataclass(frozen=True)
class _LlmRun:
    """One LLM invocation label + provider (same system/user bytes)."""

    label: str
    provider: LLMProvider


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
        help="Skip LLM calls; write deterministic claim selection + VMV only.",
    ),
    out: Path | None = typer.Option(
        None,
        "--out",
        help="Output directory (default: ANALYSIS_DIR/coach_prototype).",
    ),
) -> None:
    """Select coaching claims (0–3) per DEATH_EPISODE; optionally call an LLM."""
    try:
        cfg_path = resolve_config_path(config)
        app_config = load_config(cfg_path)
        settings = CoachSettings()
        provider_name = normalize_coach_provider(
            provider or settings.llm_provider or app_config.coach.provider
        )
        llm_runs = [] if claims_only else _build_llm_runs(
            provider_name=provider_name,
            coach_cfg=app_config.coach,
            settings=settings,
            ollama_model=model,
            nvidia_model_override=nvidia_model,
            also_baseline=also_baseline,
            baseline_model_override=baseline_model,
        )

        bundle = load_coach_analysis_bundle(
            analysis_dir,
            min_usable_confidence=app_config.vision.timer.min_usable_confidence,
        )
        primary_ids = select_primary_scenario_ids(bundle.scenarios, limit=limit)
        if not primary_ids:
            console.print("[yellow]No scenarios to coach.[/yellow]")
            raise typer.Exit(code=1)

        match_duration = resolve_match_duration_seconds(
            bundle.game_clock,
            candidates=tuple(app_config.vision.lifecycle.opening_clock_seconds),
        )

        out_dir = (out or (analysis_dir / "coach_prototype")).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        system_prompt = load_system_prompt()
        (out_dir / "system_prompt.txt").write_text(system_prompt, encoding="utf-8")

        run_labels = [run.label for run in llm_runs]
        summary_lines = [
            f"# Coach prototype — {analysis_dir}",
            "",
            f"- claims_only: {claims_only}",
            f"- provider: {provider_name}",
            f"- models: {', '.join(run_labels) if run_labels else '(none)'}",
            f"- units: {len(primary_ids)} (DEATH_EPISODE primary)",
            f"- match_duration_seconds: {match_duration}",
            f"- system_prompt: `system_prompt.txt`",
            "",
            "## Deterministic claim selection",
            "",
        ]
        eval_lines = [
            f"# Claim eval — {analysis_dir.name}",
            "",
            f"match_duration_seconds: {match_duration}",
            "",
        ]

        index_payload: list[dict[str, object]] = []

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
            unit = select_coaching_unit(
                coach_input,
                match_duration_seconds=match_duration,
            )
            user_prompt = serialize_coach_input_user_prompt(coach_input)
            safe_id = _safe_filename(scenario_id)
            input_path = out_dir / f"{safe_id}.coach_input.json"
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
            coaching_path = out_dir / f"{safe_id}.coaching.json"
            coaching_path.write_text(
                json.dumps(
                    unit.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            player_path = out_dir / f"{safe_id}.vmv.player.txt"
            player_path.write_text(format_vmv_player(unit), encoding="utf-8")
            dev_path = out_dir / f"{safe_id}.vmv.dev.txt"
            dev_path.write_text(format_vmv_developer(unit), encoding="utf-8")
            user_prompt_path = out_dir / f"{safe_id}.user_prompt.txt"
            user_prompt_path.write_bytes(user_prompt.encode("utf-8"))

            selected = [p.claim_id.value for p in unit.coaching_points]
            eligible = [c.value for c in unit.eligible_claim_ids]
            summary_lines.append(f"## `{scenario_id}`")
            summary_lines.append(f"- eligible: {eligible}")
            summary_lines.append(f"- selected: {selected}")
            summary_lines.append(f"- coaching: `{coaching_path.name}`")
            summary_lines.append(f"- vmv player: `{player_path.name}`")
            summary_lines.append(f"- vmv dev: `{dev_path.name}`")

            eval_lines.append(f"## `{scenario_id}`")
            eval_lines.append(f"- eligible: `{eligible}`")
            eval_lines.append(f"- selected: `{selected}`")
            if not selected:
                eval_lines.append(
                    "- note: zero points (selective coaching / no useful claim)"
                )
            for point in unit.coaching_points:
                eval_lines.append(f"- claim `{point.claim_id.value}`:")
                eval_lines.append(f"  - statement: {point.statement}")
                eval_lines.append(
                    f"  - interpretation: {_null(point.interpretation)}"
                )
                eval_lines.append(
                    f"  - recommendation: {_null(point.recommendation)}"
                )
            eval_lines.append("")

            index_payload.append(
                {
                    "scenario_id": scenario_id,
                    "video_time": unit.video_time,
                    "eligible_claim_ids": eligible,
                    "selected_claim_ids": selected,
                    "coaching_json": coaching_path.name,
                    "vmv_player": player_path.name,
                    "vmv_dev": dev_path.name,
                }
            )
            console.print(
                f"[cyan]claims[/cyan] {scenario_id} "
                f"eligible={eligible} selected={selected}"
            )

            for run in llm_runs:
                model_tag = _safe_filename(run.label)
                out_path = out_dir / f"{safe_id}.{model_tag}.output.json"
                raw_path = out_dir / f"{safe_id}.{model_tag}.raw.txt"
                flags_path = out_dir / f"{safe_id}.{model_tag}.flags.json"
                try:
                    raw = run.provider.complete(system_prompt, user_prompt)
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
                    flags = annotate_claim_flags(assessment)
                    flags_path.write_text(
                        json.dumps(
                            flags, ensure_ascii=False, indent=2, sort_keys=True
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    summary_lines.append(
                        f"- `{run.label}` → `{out_path.name}` "
                        f"(flags hit_count={flags['hit_count']})"
                    )
                    console.print(
                        f"[green]OK[/green] {scenario_id} / {run.label} "
                        f"→ {out_path.name}"
                    )
                except Exception as exc:  # noqa: BLE001 — prototype must continue
                    if not raw_path.is_file():
                        raw_path.write_text(f"ERROR: {exc}\n", encoding="utf-8")
                    summary_lines.append(
                        f"- `{run.label}` → FAILED (`{raw_path.name}`): {exc}"
                    )
                    console.print(
                        f"[red]FAIL[/red] {scenario_id} / {run.label}: {exc}"
                    )

            summary_lines.append("")

        eval_lines.extend(
            [
                "## Checklist",
                "",
                "1. Did it select the right facts (not dump every eligible claim)?",
                "2. Are statements evidence-grounded?",
                "3. Is interpretation a valid principle (not disguised judgment)?",
                "4. Did it invent any “should have” judgments?",
                "5. Does it correctly produce no recommendation / zero points?",
                "6. Does coaching feel useful (selective)?",
                "",
            ]
        )

        (out_dir / "coaching_index.json").write_text(
            json.dumps(index_payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        summary_path = out_dir / "summary.md"
        summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
        eval_path = out_dir / "claim_eval.md"
        eval_path.write_text("\n".join(eval_lines) + "\n", encoding="utf-8")
        console.print(f"[bold]Wrote[/bold] {summary_path}")
        console.print(f"[bold]Wrote[/bold] {eval_path}")
    except (ConfigError, S3CoachError, FileNotFoundError, ValueError, OSError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc


def _build_llm_runs(
    *,
    provider_name: str,
    coach_cfg: CoachConfig,
    settings: CoachSettings,
    ollama_model: str | None,
    nvidia_model_override: str | None,
    also_baseline: bool,
    baseline_model_override: str | None,
) -> list[_LlmRun]:
    """Build primary (+ optional Ollama baseline) runs. Never logs API keys."""
    ollama_base = settings.ollama_base_url or coach_cfg.ollama_base_url
    baseline = baseline_model_override or coach_cfg.baseline_model
    runs: list[_LlmRun] = []

    if provider_name == "ollama":
        primary = ollama_model or coach_cfg.model
        runs.append(
            _LlmRun(
                label=primary,
                provider=OllamaProvider(primary, base_url=ollama_base),
            )
        )
    elif provider_name == "nvidia":
        api_key = settings.nvidia_api_key
        if not api_key or not api_key.strip():
            raise ConfigError(
                "provider=nvidia requires S3_COACH_NVIDIA_API_KEY. "
                "Put it in a gitignored .env in the repo root "
                "(S3_COACH_NVIDIA_API_KEY=...) or export it in the same "
                "process that runs s3-coach. Do not put the key in YAML. "
                "Note: exporting in one terminal does not apply to Cursor "
                "Debug launches."
            )
        nim_model = nvidia_model_override or coach_cfg.nvidia_model
        runs.append(
            _LlmRun(
                label=nim_model,
                provider=OpenAICompatibleProvider(
                    nim_model,
                    api_key=api_key,
                    base_url=coach_cfg.openai_compatible_base_url,
                ),
            )
        )
    else:  # pragma: no cover — normalize_coach_provider already validates
        raise ConfigError(f"Unsupported provider: {provider_name}")

    # Cross-model comparison is Ollama-only. NVIDIA runs are single-model.
    if (
        provider_name == "ollama"
        and also_baseline
        and baseline not in {run.label for run in runs}
    ):
        runs.append(
            _LlmRun(
                label=baseline,
                provider=OllamaProvider(baseline, base_url=ollama_base),
            )
        )
    return runs


def _null(value: str | None) -> str:
    return "null" if value is None else value


def _safe_filename(value: str) -> str:
    """Filesystem-safe token for scenario/model names."""
    return re.sub(r"[^\w.\-]+", "_", value)
