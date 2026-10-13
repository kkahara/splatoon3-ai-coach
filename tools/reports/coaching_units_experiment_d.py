"""Experiment D runner: the deferred coaching-unit arms through one model.

Research only. Reads the inputs ``coaching_units.py`` wrote under
``analysis/importance_report/coaching_units_deferred/`` and never rebuilds
them. Outputs go to a sibling folder because ``coaching_units.py`` replaces
the deferred folder whenever the report is regenerated.

``plan`` (the default) is a dry run: it lists every call, checks the inputs
against the current system prompt and view schema, and reports whether the
chosen provider honors temperature 0. It constructs no provider and calls no
model. ``run`` needs an explicit provider, model and ``--confirm``, refuses
stale inputs and existing outputs, and refuses a provider that does not honor
the requested temperature unless ``--allow-unhonored-temperature`` is given.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coaching_units import OBSERVATIONS_HEADER, RATING_COLUMNS, RATING_SEED
from loguru import logger

from splatoon3_ai_coach.coach.claim_basis import ClaimBasisEntry
from splatoon3_ai_coach.coach.claim_validation import (
    ReferenceKind,
    resolve_evidence_reference,
)
from splatoon3_ai_coach.coach.coach import (
    annotate_claim_flags,
    parse_coaching_assessment,
)
from splatoon3_ai_coach.coach.llm_client import (
    COACH_PROVIDERS,
    CursorProvider,
    LLMProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    SettingSupport,
)
from splatoon3_ai_coach.coach.llm_runs import build_llm_runs, utc_timestamp
from splatoon3_ai_coach.coach.llm_view import LLM_VIEW_SCHEMA_VERSION
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import CoachConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.config.settings import CoachSettings

REPORT_DIR = PROJECT_ROOT / "analysis" / "importance_report"
DEFERRED_DIR = REPORT_DIR / "coaching_units_deferred"
OUT_DIR = REPORT_DIR / "coaching_units_experiment_d"
ARMS = ("individual", "episode", "episode_by_factor")
PROVIDER_CLASSES: dict[str, type[LLMProvider]] = {
    "ollama": OllamaProvider,
    "nvidia": OpenAICompatibleProvider,
    "cursor": CursorProvider,
}
SUPPORT_NOTES = {
    "ollama": "sent as options.temperature on /api/chat",
    "nvidia": "sent as temperature on /chat/completions",
    "cursor": (
        "never sent: the Cursor SDK has no sampling field (AgentOptions has "
        "none; model params are context / reasoning / fast only), and the "
        "SDK takes one joined message, no separate system prompt"
    ),
}


@dataclass(frozen=True)
class DCall:
    """One deferred call: an arm's unit for one episode."""

    episode: str
    match: str
    arm: str
    index: int
    file: str
    lead: str
    members: tuple[str, ...]
    user_prompt: str

    @property
    def call_id(self) -> str:
        """Stable identifier used for the output file name."""
        return f"{self.episode}__{self.arm}__call_{self.index}"


def load_calls(deferred_dir: Path) -> tuple[str, list[DCall]]:
    """System prompt and every call listed in the deferred manifest."""
    manifest = json.loads((deferred_dir / "manifest.json").read_text(encoding="utf-8"))
    system = (deferred_dir / "system_prompt.txt").read_text(encoding="utf-8")
    calls: list[DCall] = []
    for episode in manifest["episodes"]:
        for arm in ARMS:
            for i, entry in enumerate(episode["arms"].get(arm, []), start=1):
                calls.append(
                    DCall(
                        episode=episode["episode"],
                        match=episode["match"],
                        arm=arm,
                        index=i,
                        file=entry["file"],
                        lead=entry["lead"],
                        members=tuple(entry["members"]),
                        user_prompt=(deferred_dir / entry["file"]).read_text(
                            encoding="utf-8"
                        ),
                    )
                )
    return system, calls


def split_prompt(user_prompt: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Lead CoachLlmView and appended member views recovered from a prompt."""
    head, _, tail = user_prompt.partition(OBSERVATIONS_HEADER)
    start = head.find("{")
    if start < 0:
        raise ValueError("user prompt has no CoachLlmView JSON")
    lead, _ = json.JSONDecoder().raw_decode(head[start:])
    members = [json.loads(line) for line in tail.splitlines() if line.strip()]
    return lead, members


def staleness(system: str, calls: Sequence[DCall], coach: CoachConfig) -> list[str]:
    """Reasons the stored inputs no longer match production, if any."""
    problems: list[str] = []
    current = load_system_prompt(roster_evidence=coach.roster_evidence)
    if system != current:
        problems.append(
            f"system_prompt.txt differs from the current production system prompt "
            f"({len(system)} vs {len(current)} characters)"
        )
    versions: set[Any] = set()
    entry_keys: set[frozenset[str]] = set()
    for call in calls:
        lead, members = split_prompt(call.user_prompt)
        for view in (lead, *members):
            versions.add(view.get("schema_version"))
            entry_keys.update(frozenset(e) for e in view.get("claim_basis") or ())
    if versions != {LLM_VIEW_SCHEMA_VERSION}:
        problems.append(
            f"view schema_version {sorted(versions, key=str)} != current "
            f"{LLM_VIEW_SCHEMA_VERSION}"
        )
    current_keys = frozenset(ClaimBasisEntry.model_fields)
    missing = sorted(set().union(*(current_keys - k for k in entry_keys)))
    if missing:
        problems.append(
            "claim_basis entries lack current fields "
            + ", ".join(missing)
            + " (views predate the current CoachLlmView)"
        )
    return problems


def temperature_support(provider: str) -> SettingSupport:
    """Declared temperature support of a provider class (no construction)."""
    return PROVIDER_CLASSES[provider].temperature_support


def plan_rows(system: str, calls: Sequence[DCall]) -> list[dict[str, Any]]:
    """Per-arm calls and characters, the same cells as the report."""
    rows: dict[str, dict[str, int]] = defaultdict(lambda: {"calls": 0, "user": 0})
    for call in calls:
        rows[call.arm]["calls"] += 1
        rows[call.arm]["user"] += len(call.user_prompt)
    return [
        {
            "arm": arm,
            "calls": rows[arm]["calls"],
            "user_prompt_chars": rows[arm]["user"],
            "total_input_chars": rows[arm]["user"] + rows[arm]["calls"] * len(system),
        }
        for arm in ARMS
    ]


def plan_report(
    system: str,
    calls: Sequence[DCall],
    coach: CoachConfig,
    provider: str,
    model: str,
) -> dict[str, Any]:
    """Dry-run document: calls, sizes, staleness and temperature handling."""
    support = temperature_support(provider)
    return {
        "dry_run": True,
        "llm_calls_made": 0,
        "provider": provider,
        "model": model,
        "requested_temperature": coach.llm_temperature,
        "requested_seed": coach.llm_seed,
        "temperature_support": support,
        "temperature_honored": support == "supported",
        "temperature_note": SUPPORT_NOTES[provider],
        "stale_inputs": staleness(system, calls, coach),
        "arms": plan_rows(system, calls),
        "calls": [
            {
                "call_id": c.call_id,
                "lead": c.lead,
                "members": list(c.members),
                "user_prompt_chars": len(c.user_prompt),
            }
            for c in calls
        ],
    }


def plan_markdown(doc: dict[str, Any]) -> str:
    """Human-readable dry-run summary."""
    lines = [
        "# Experiment D dry run (no LLM calls)",
        "",
        f"- Provider / model: `{doc['provider']}` / `{doc['model']}`",
        f"- Requested temperature {doc['requested_temperature']}, "
        f"seed {doc['requested_seed']}",
        f"- Temperature support: **{doc['temperature_support']}** "
        f"({doc['temperature_note']})",
        "- Stale inputs: "
        + ("; ".join(doc["stale_inputs"]) if doc["stale_inputs"] else "none"),
        "",
        "| arm | calls | user-prompt chars | total input chars |",
        "| --- | --- | --- | --- |",
        *[
            f"| {r['arm']} | {r['calls']} | {r['user_prompt_chars']} | "
            f"{r['total_input_chars']} |"
            for r in doc["arms"]
        ],
        "",
        f"Total calls: {len(doc['calls'])}",
    ]
    return "\n".join(lines) + "\n"


def member_only_references(
    unresolved: Sequence[dict[str, Any]], members: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Unresolved lead references that do resolve in an appended member view."""
    found = []
    for ref in unresolved:
        for i, view in enumerate(members):
            hit = resolve_evidence_reference(view, str(ref["text"]))
            if hit["kind"] != ReferenceKind.UNRESOLVED.value:
                found.append({**hit, "member_index": i})
                break
    return found


def evaluate(raw: str, call: DCall) -> dict[str, Any]:
    """Parse and validate one reply against the lead view (never rewritten)."""
    lead, members = split_prompt(call.user_prompt)
    try:
        assessment = parse_coaching_assessment(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return {"parse_error": type(exc).__name__, "assessment": None}
    flags = annotate_claim_flags(assessment, lead)
    return {
        "parse_error": None,
        "assessment": assessment.model_dump(mode="json"),
        "validation": flags,
        "member_only_references": member_only_references(
            flags["unresolved_evidence_paths"], members
        ),
    }


def run_calls(
    system: str,
    calls: Sequence[DCall],
    provider: LLMProvider,
    provider_name: str,
    model: str,
    out_dir: Path,
) -> int:
    """Call once per deferred call; write one record each. Returns failures."""
    out_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for call in calls:
        record: dict[str, Any] = {
            "experiment": True,
            "experiment_name": "coaching_units_experiment_d",
            "call_id": call.call_id,
            "episode": call.episode,
            "match": call.match,
            "arm": call.arm,
            "lead": call.lead,
            "members": list(call.members),
            "provider": provider_name,
            "model": model,
            "generation_settings": {
                k: v.model_dump(mode="json")
                for k, v in provider.generation_settings().items()
            },
            "input_hashes": provider.input_hashes(system, call.user_prompt),
            "executed_at": utc_timestamp(),
        }
        try:
            raw = provider.complete(system, call.user_prompt)
        except RuntimeError as exc:
            failures += 1
            record.update({"call_error": str(exc), "raw_output": None})
        else:
            record.update({"call_error": None, "raw_output": raw, **evaluate(raw, call)})
        path = out_dir / "calls" / f"{call.call_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", "utf-8")
        logger.info("Experiment D {} -> {}", call.call_id, path.name)
    return failures


def summarize(records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-arm counts from saved records (rule hits = validator violations)."""
    rows = []
    for arm in ARMS:
        recs = [r for r in records if r["arm"] == arm]
        ok = [r for r in recs if r.get("validation")]
        rows.append(
            {
                "arm": arm,
                "calls": len(recs),
                "call_errors": sum(1 for r in recs if r.get("call_error")),
                "parse_errors": sum(1 for r in recs if r.get("parse_error")),
                "blocked": sum(1 for r in ok if r["validation"]["blocked"]),
                "rule_hits": sum(_violations(r["validation"]) for r in ok),
                "unresolved_paths": sum(
                    len(r["validation"]["unresolved_evidence_paths"]) for r in ok
                ),
                "member_only_references": sum(
                    len(r["member_only_references"]) for r in ok
                ),
                "recommendations": sum(
                    len(r["assessment"]["recommendations"]) for r in ok
                ),
            }
        )
    return rows


def _anchors(call_ids: Sequence[str]) -> str:
    return "; ".join(f"{float(c.split(':', 1)[1]):.1f}s" for c in call_ids)


def round2_sheet(
    records: Sequence[dict[str, Any]], seed: int = RATING_SEED
) -> tuple[str, str]:
    """(blind sheet CSV, key CSV) of model outputs; the sheet never names an arm.

    Shows what the public result page shows: assessment and recommendations.
    """
    rows = sorted((r for r in records if r.get("assessment")), key=lambda r: r["call_id"])
    random.Random(seed).shuffle(rows)
    sheet, key = io.StringIO(), io.StringIO()
    sheet_w = csv.writer(sheet, lineterminator="\n")
    key_w = csv.writer(key, lineterminator="\n")
    sheet_w.writerow(
        (
            "output_id",
            "match",
            "anchors",
            "assessment",
            "recommendations",
            *RATING_COLUMNS,
        )
    )
    key_w.writerow(("output_id", "call_id", "episode", "arm", "lead", "members"))
    for n, r in enumerate(rows, start=1):
        output_id = f"output_{n:03d}"
        a = r["assessment"]
        sheet_w.writerow(
            (
                output_id,
                r["match"],
                _anchors(r["members"]),
                a["assessment"].strip(),
                " || ".join(s.strip() for s in a["recommendations"]),
                *[""] * len(RATING_COLUMNS),
            )
        )
        key_w.writerow(
            (
                output_id,
                r["call_id"],
                r["episode"],
                r["arm"],
                r["lead"],
                ";".join(r["members"]),
            )
        )
    return sheet.getvalue(), key.getvalue()


def _violations(validation: dict[str, Any]) -> int:
    return (
        int(validation.get("hit_count", 0))
        + sum(1 for h in validation["causal_claim_hits"] if h["counts_as_violation"])
        + sum(
            1
            for h in validation["unsupported_recommendation_hits"]
            if h["counts_as_violation"]
        )
        + len(validation["unresolved_evidence_paths"])
        + len(validation["integrity_hits"])
    )


def _cmd_plan(args: argparse.Namespace, coach: CoachConfig) -> int:
    system, calls = load_calls(args.deferred)
    model = args.model or _default_model(args.provider, coach)
    doc = plan_report(system, calls, coach, args.provider, model)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "dry_run.json").write_text(json.dumps(doc, indent=1) + "\n", "utf-8")
    text = plan_markdown(doc)
    (args.out / "dry_run.md").write_text(text, "utf-8")
    sys.stdout.write(text)
    return 0


def _cmd_run(args: argparse.Namespace, coach: CoachConfig) -> int:
    if not (args.confirm and args.model):
        raise SystemExit("run needs --model and --confirm (no default model)")
    system, calls = load_calls(args.deferred)
    stale = staleness(system, calls, coach)
    if stale:
        raise SystemExit("refusing stale inputs: " + "; ".join(stale))
    if (args.out / "calls").exists():
        raise SystemExit(f"outputs already exist under {args.out / 'calls'}; run once")
    support = temperature_support(args.provider)
    if support != "supported" and not args.allow_unhonored_temperature:
        raise SystemExit(
            f"provider {args.provider} temperature support is {support}: "
            "pass --allow-unhonored-temperature to run anyway"
        )
    runs = build_llm_runs(
        provider_name=args.provider,
        coach_cfg=coach,
        settings=CoachSettings(),
        ollama_model=args.model,
        nvidia_model_override=args.model,
        also_baseline=False,
        baseline_model_override=None,
        cursor_model_override=args.model,
    )
    provider = runs[0].provider
    failures = run_calls(system, calls, provider, args.provider, args.model, args.out)
    return 1 if failures else 0


def _cmd_summary(args: argparse.Namespace, _coach: CoachConfig) -> int:
    records = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((args.out / "calls").glob("*.json"))
    ]
    rows = summarize(records)
    (args.out / "summary.json").write_text(json.dumps(rows, indent=1) + "\n", "utf-8")
    sys.stdout.write(json.dumps(rows, indent=1) + "\n")
    return 0


def _cmd_sheet(args: argparse.Namespace, _coach: CoachConfig) -> int:
    sheet_path = args.out / "round2_rating_sheet.csv"
    key_path = args.out / "round2_rating_key.csv"
    if sheet_path.exists() or key_path.exists():
        raise SystemExit(
            f"{sheet_path.name} or {key_path.name} exists; never overwritten"
        )
    records = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((args.out / "calls").glob("*.json"))
    ]
    sheet, key = round2_sheet(records)
    sheet_path.write_text(sheet, encoding="utf-8")
    key_path.write_text(key, encoding="utf-8")
    sys.stdout.write(f"wrote {sheet_path} ({sheet.count(chr(10)) - 1} outputs)\n")
    return 0


def _default_model(provider: str, coach: CoachConfig) -> str:
    return {
        "ollama": coach.model,
        "nvidia": coach.nvidia_model,
        "cursor": coach.cursor_model,
    }[provider]


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command",
        nargs="?",
        default="plan",
        choices=("plan", "run", "summary", "sheet"),
    )
    parser.add_argument("--provider", choices=COACH_PROVIDERS, default="cursor")
    parser.add_argument("--model", default=None)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--allow-unhonored-temperature", action="store_true")
    parser.add_argument("--deferred", type=Path, default=DEFERRED_DIR)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args(argv)
    coach = load_config(args.config).coach
    command = {
        "plan": _cmd_plan,
        "run": _cmd_run,
        "summary": _cmd_summary,
        "sheet": _cmd_sheet,
    }[args.command]
    return command(args, coach)


if __name__ == "__main__":
    raise SystemExit(main())
