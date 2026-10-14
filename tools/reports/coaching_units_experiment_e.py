"""Experiment E: recommendation-catalog coverage on the Experiment D deaths.

Research only. Two arms on the same 13 deaths (the Experiment D ``individual``
units), the same system prompt and model, one death per unit:

* ``current``: the production claim catalog.
* ``expanded``: the production catalog plus two process claims, each grounding
  one recommendation template (``EXPERIMENT_CLAIMS`` / ``EXPERIMENT_TEMPLATES``).

The expanded catalog exists only inside this process (``expanded_catalog``);
production code and config are not changed. Every call runs ``REPEATS`` times
so model variation is measured, not mistaken for an arm effect.

Commands: ``build`` writes the inputs (never overwrites), ``plan`` is a dry run
(no provider is constructed), ``run`` is guarded and run-once like Experiment
D, ``summary`` aggregates validator hits per arm, ``sheet`` writes the blind
rating sheet and its key.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

import coaching_units_experiment_d as xd
from coaching_units import load_root
from loguru import logger

from splatoon3_ai_coach.coach import claim_basis as cb
from splatoon3_ai_coach.coach.claim_catalog import DeathImportanceFactorId as _F
from splatoon3_ai_coach.coach.llm_runs import build_llm_runs
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig, CoachConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.config.settings import CoachSettings

OUT_DIR = xd.REPORT_DIR / "coaching_units_experiment_e"
ROOT = PROJECT_ROOT / "analysis" / "_rescore_v2"
ARMS = ("current", "expanded")
REPEATS = 2
RATING_SEED = 20261014

EXPERIMENT_TEMPLATES: tuple[cb.RecommendationTemplate, ...] = (
    cb.RecommendationTemplate(
        "rec_after_splat_check",
        "After you get a splat, take a second to check your ink and surroundings "
        "before pushing on.",
        re.compile(
            r"(?i)\b(?:check|scan|look\s+at|watch)\s+(?:your\s+)?(?:ink\s+and\s+)?"
            r"surroundings\b|\bafter\s+(?:you\s+)?(?:get|got|getting|land|landing)\s+"
            r"a\s+splat\b"
        ),
    ),
    cb.RecommendationTemplate(
        "rec_final30_weigh_fight",
        "In the last 30 seconds, weigh whether a fight is worth the respawn time "
        "before committing.",
        re.compile(
            r"(?i)\bweigh\b|\bworth\s+the\s+respawn\b"
            r"|\b(?:last|final|closing)\s+(?:30|thirty)\s+seconds\b[^.]*\b(?:fight|commit)"
        ),
    ),
)

EXPERIMENT_CLAIMS: tuple[cb.ClaimDefinition, ...] = (
    cb.ClaimDefinition(
        claim_id="process_after_own_splat",
        level=cb.ClaimLevel.B,
        frame=(
            "Right after you get a splat is a moment when the next opponent may "
            "already be close."
        ),
        evidence_paths=("death.time_since_previous_splat",),
        eligible=cb._requires(_F.DEATH_OWN_SPLAT_BEFORE.value),
        coachable=True,
        rec_template_ids=("rec_after_splat_check",),
    ),
    cb.ClaimDefinition(
        claim_id="process_final30_respawn_cost",
        level=cb.ClaimLevel.B,
        frame=(
            "In the last 30 seconds, time spent respawning is a large share of the "
            "time left."
        ),
        evidence_paths=("clock.seconds_remaining",),
        eligible=cb._requires(_F.DEATH_FINAL_30S.value),
        coachable=True,
        rec_template_ids=("rec_final30_weigh_fight",),
    ),
)


Catalog = tuple[tuple[cb.ClaimDefinition, ...], tuple[cb.RecommendationTemplate, ...]]

CATALOGS: dict[str, Catalog] = {
    "current": ((), ()),
    "expanded": (EXPERIMENT_CLAIMS, EXPERIMENT_TEMPLATES),
}


@contextmanager
def patched_catalog(
    claims: Sequence[cb.ClaimDefinition], templates: Sequence[cb.RecommendationTemplate]
) -> Iterator[None]:
    """Add claims and templates to the in-process catalog, then restore it."""
    saved = cb.CLAIM_DEFINITIONS
    cb.CLAIM_DEFINITIONS = (*saved, *claims)
    for claim in claims:
        cb.CLAIMS_BY_ID[claim.claim_id] = claim
    for template in templates:
        cb.RECOMMENDATION_TEMPLATES[template.template_id] = template
    try:
        yield
    finally:
        cb.CLAIM_DEFINITIONS = saved
        for claim in claims:
            cb.CLAIMS_BY_ID.pop(claim.claim_id, None)
        for template in templates:
            cb.RECOMMENDATION_TEMPLATES.pop(template.template_id, None)


def expanded_catalog() -> AbstractContextManager[None]:
    """The production catalog plus the Experiment E claims and templates."""
    return patched_catalog(EXPERIMENT_CLAIMS, EXPERIMENT_TEMPLATES)


def arm_catalog(
    arm: str, catalogs: Mapping[str, Catalog] = CATALOGS
) -> AbstractContextManager[None]:
    """The catalog an arm builds and validates with."""
    return patched_catalog(*catalogs[arm])


def death_units(deferred_dir: Path) -> list[tuple[str, str]]:
    """``(match, lead)`` of every Experiment D ``individual`` call, in order."""
    _, calls = xd.load_calls(deferred_dir)
    return [(c.match, c.lead) for c in calls if c.arm == "individual"]


def _slug(match: str, lead: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", f"{match}__{lead}").strip("_")[:80]


def arm_prompts(
    config: AppConfig,
    root: Path,
    deaths: Sequence[tuple[str, str]],
    catalog: AbstractContextManager[None],
) -> dict[tuple[str, str], str]:
    """User prompt for each death, with views built under ``catalog``."""
    with catalog:
        matches = load_root(root, config)
    prompts = {(m.name, d.cid): d.prompt for m in matches for d in m.deaths}
    missing = [d for d in deaths if d not in prompts]
    if missing:
        raise SystemExit(f"deaths not found under {root}: {missing}")
    return {d: prompts[d] for d in deaths}


def build_files(
    config: AppConfig,
    root: Path,
    deaths: Sequence[tuple[str, str]],
    catalogs: Mapping[str, Catalog] = CATALOGS,
) -> dict[str, str]:
    """Input files (relative path -> text) for every arm and the manifest."""
    system = load_system_prompt(roster_evidence=config.coach.roster_evidence)
    files = {"system_prompt.txt": system}
    entries = []
    for arm in catalogs:
        catalog = arm_catalog(arm, catalogs)
        for (match, lead), prompt in arm_prompts(config, root, deaths, catalog).items():
            rel = f"{arm}/{_slug(match, lead)}.user_prompt.txt"
            files[rel] = prompt
            entries.append({"arm": arm, "match": match, "lead": lead, "file": rel})
    manifest = {"arms": list(catalogs), "repeats": REPEATS, "calls": entries}
    files["manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    return files


def load_calls(inputs_dir: Path) -> tuple[str, list[xd.DCall]]:
    """System prompt and every call, each input repeated ``repeats`` times."""
    manifest = json.loads((inputs_dir / "manifest.json").read_text(encoding="utf-8"))
    system = (inputs_dir / "system_prompt.txt").read_text(encoding="utf-8")
    calls = []
    for entry in manifest["calls"]:
        prompt = (inputs_dir / entry["file"]).read_text(encoding="utf-8")
        for repeat in range(1, int(manifest["repeats"]) + 1):
            calls.append(
                xd.DCall(
                    episode=_slug(entry["match"], entry["lead"]),
                    match=entry["match"],
                    arm=entry["arm"],
                    index=repeat,
                    file=entry["file"],
                    lead=entry["lead"],
                    members=(entry["lead"],),
                    user_prompt=prompt,
                )
            )
    return system, calls


def coverage(calls: Sequence[xd.DCall]) -> list[dict[str, Any]]:
    """Per death and arm: the recommendation templates its view grounds."""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for call in calls:
        lead, _ = xd.split_prompt(call.user_prompt)
        row = seen.setdefault((call.match, call.lead), {"death": call.episode})
        row[call.arm] = sorted(
            t
            for e in lead.get("claim_basis") or ()
            for t in e.get("rec_template_ids") or ()
        )
    return list(seen.values())


def plan_doc(
    system: str,
    calls: Sequence[xd.DCall],
    coach: CoachConfig,
    arms: Sequence[str] = ARMS,
) -> dict[str, Any]:
    """Dry run: sizes, staleness against the current prompt, coverage."""
    doc = xd.plan_report(system, calls, coach, "cursor", coach.cursor_model)
    doc["arms"] = [
        {
            "arm": arm,
            "calls": sum(1 for c in calls if c.arm == arm),
            "user_prompt_chars": sum(len(c.user_prompt) for c in calls if c.arm == arm),
        }
        for arm in arms
    ]
    doc["coverage"] = coverage(calls)
    return doc


def plan_markdown(doc: dict[str, Any]) -> str:
    """Human-readable dry run with per-death recommendation coverage."""
    lines = [
        "# Experiment E dry run (no LLM calls)",
        "",
        f"- Provider / model: `{doc['provider']}` / `{doc['model']}`",
        f"- Temperature support: **{doc['temperature_support']}** "
        f"({doc['temperature_note']})",
        "- Stale inputs: "
        + ("; ".join(doc["stale_inputs"]) if doc["stale_inputs"] else "none"),
        f"- Calls: {len(doc['calls'])} "
        + ", ".join(f"{a['arm']} {a['calls']}" for a in doc["arms"]),
        "",
        "| death | current templates | expanded templates |",
        "| --- | --- | --- |",
        *[
            f"| {r['death']} | {', '.join(r.get('current', [])) or '(none)'} | "
            f"{', '.join(r.get('expanded', [])) or '(none)'} |"
            for r in doc["coverage"]
        ],
    ]
    return "\n".join(lines) + "\n"


def write_inputs(
    args: argparse.Namespace, config: AppConfig, catalogs: Mapping[str, Catalog]
) -> int:
    """Write every arm's inputs under ``args.out / inputs``; never overwrites."""
    inputs = args.out / "inputs"
    if inputs.exists():
        raise SystemExit(f"{inputs} exists; never overwritten")
    files = build_files(config, args.root, death_units(args.deferred), catalogs)
    for rel, text in sorted(files.items()):
        path = inputs / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    logger.info("Experiment inputs: {} files under {}", len(files), inputs)
    return 0


def _cmd_build(args: argparse.Namespace, config: AppConfig) -> int:
    return write_inputs(args, config, CATALOGS)


def _cmd_plan(args: argparse.Namespace, config: AppConfig) -> int:
    system, calls = load_calls(args.out / "inputs")
    doc = plan_doc(system, calls, config.coach)
    (args.out / "dry_run.json").write_text(json.dumps(doc, indent=1) + "\n", "utf-8")
    text = plan_markdown(doc)
    (args.out / "dry_run.md").write_text(text, "utf-8")
    sys.stdout.write(text)
    return 0


def _cmd_run(args: argparse.Namespace, config: AppConfig) -> int:
    return run_arms(args, config, CATALOGS)


def run_arms(
    args: argparse.Namespace, config: AppConfig, catalogs: Mapping[str, Catalog]
) -> int:
    """Guarded, run-once calls; each arm runs and validates under its catalog."""
    coach = config.coach
    if not (args.confirm and args.model):
        raise SystemExit("run needs --model and --confirm (no default model)")
    system, calls = load_calls(args.out / "inputs")
    stale = xd.staleness(system, calls, coach)
    if stale:
        raise SystemExit("refusing stale inputs: " + "; ".join(stale))
    if (args.out / "calls").exists():
        raise SystemExit(f"outputs already exist under {args.out / 'calls'}; run once")
    support = xd.temperature_support(args.provider)
    if support != "supported" and not args.allow_unhonored_temperature:
        raise SystemExit(
            f"provider {args.provider} temperature support is {support}: "
            "pass --allow-unhonored-temperature to run anyway"
        )
    provider = build_llm_runs(
        provider_name=args.provider,
        coach_cfg=coach,
        settings=CoachSettings(),
        ollama_model=args.model,
        nvidia_model_override=args.model,
        also_baseline=False,
        baseline_model_override=None,
        cursor_model_override=args.model,
    )[0].provider
    failures = 0
    for arm in catalogs:
        with arm_catalog(arm, catalogs):
            subset = [c for c in calls if c.arm == arm]
            failures += xd.run_calls(
                system, subset, provider, args.provider, args.model, args.out
            )
    return 1 if failures else 0


def _records(out: Path) -> list[dict[str, Any]]:
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((out / "calls").glob("*.json"))
    ]


def _cmd_summary(args: argparse.Namespace, _config: AppConfig) -> int:
    rows = xd.summarize(_records(args.out), ARMS)
    (args.out / "summary.json").write_text(json.dumps(rows, indent=1) + "\n", "utf-8")
    sys.stdout.write(json.dumps(rows, indent=1) + "\n")
    return 0


def _cmd_sheet(args: argparse.Namespace, _config: AppConfig) -> int:
    sheet_path = args.out / "rating_sheet.csv"
    key_path = args.out / "rating_key.csv"
    if sheet_path.exists() or key_path.exists():
        raise SystemExit("rating sheet or key exists; never overwritten")
    sheet, key = xd.round2_sheet(_records(args.out), seed=RATING_SEED)
    sheet_path.write_text(sheet, encoding="utf-8")
    key_path.write_text(key, encoding="utf-8")
    sys.stdout.write(f"wrote {sheet_path} ({sheet.count(chr(10)) - 1} outputs)\n")
    return 0


COMMANDS = {
    "build": _cmd_build,
    "plan": _cmd_plan,
    "run": _cmd_run,
    "summary": _cmd_summary,
    "sheet": _cmd_sheet,
}


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=tuple(COMMANDS))
    parser.add_argument(
        "--provider", choices=tuple(xd.PROVIDER_CLASSES), default="cursor"
    )
    parser.add_argument("--model", default=None)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--allow-unhonored-temperature", action="store_true")
    parser.add_argument("--deferred", type=Path, default=xd.DEFERRED_DIR)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args(argv)
    return COMMANDS[args.command](args, load_config(args.config))


if __name__ == "__main__":
    raise SystemExit(main())
