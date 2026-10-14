"""Assessment layout fix: re-run Experiment E's production-catalog calls.

Research only. The system prompt now states that ``assessment`` holds what the
evidence says and ``recommendations`` is the only place for advice (rule R14
annotates violations). This re-runs Experiment E's ``current`` arm: the same
13 deaths, production catalog, user prompts (checked byte-identical at
``build``), model and two repeats; only the system prompt differs.

Commands: ``build`` writes the inputs (never overwrites) and fails if any user
prompt differs from Experiment E's, ``plan`` is a dry run, ``run`` is guarded
and run-once, ``compare`` sets the new outputs against E's saved ``current``
outputs, all re-validated with the current validator.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import coaching_units_experiment_d as xd
import coaching_units_experiment_e as xe

from splatoon3_ai_coach.coach.claim_validation import validate_assessment
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig

OUT_DIR = xd.REPORT_DIR / "assessment_layout_fix"
E_DIR = xe.OUT_DIR
ARM = "current"
CATALOGS: dict[str, xe.Catalog] = {ARM: xe.CATALOGS[ARM]}


def _cmd_build(args: argparse.Namespace, config: AppConfig) -> int:
    xe.write_inputs(args, config, CATALOGS)
    changed = [
        path.name
        for path in sorted((args.out / "inputs" / ARM).glob("*.user_prompt.txt"))
        if path.read_text("utf-8")
        != (args.e_dir / "inputs" / ARM / path.name).read_text("utf-8")
    ]
    if changed:
        raise SystemExit(f"user prompts differ from Experiment E: {changed}")
    return 0


def _cmd_plan(args: argparse.Namespace, config: AppConfig) -> int:
    system, calls = xe.load_calls(args.out / "inputs")
    doc = xe.plan_doc(system, calls, config.coach, arms=(ARM,))
    (args.out / "dry_run.json").write_text(json.dumps(doc, indent=1) + "\n", "utf-8")
    sys.stdout.write(
        f"calls {len(calls)}; model {doc['model']}; temperature "
        f"{doc['temperature_support']}; stale: {doc['stale_inputs'] or 'none'}\n"
    )
    return 0


def _cmd_run(args: argparse.Namespace, config: AppConfig) -> int:
    return xe.run_arms(args, config, CATALOGS)


def _metrics(record: dict[str, Any]) -> dict[str, Any]:
    """Re-validated layout, recommendation and badge facts for one output."""
    assessment = record.get("assessment")
    if not assessment:
        return {"call_id": record["call_id"], "parsed": False}
    view, _ = xd.split_prompt(record["user_prompt"])
    report = validate_assessment(assessment, view)
    integrity = report["integrity_hits"]
    layout = [h for h in integrity if h["rule"] == "R14"]
    return {
        "call_id": record["call_id"],
        "parsed": True,
        "layout_checks": sorted({h["check"] for h in layout}),
        "has_recommendation": bool(assessment.get("recommendations")),
        "selected_claims": len(assessment.get("selected_claim_ids") or []),
        "other_badge_rules": sorted({h["rule"] for h in integrity if h["rule"] != "R14"}),
        "blocked": report["blocked"],
    }


def _records(out: Path, inputs: Path) -> list[dict[str, Any]]:
    """Call records of the ``current`` arm, each with its user prompt."""
    _, calls = xe.load_calls(inputs)
    prompts = {c.episode: c.user_prompt for c in calls}
    records = []
    for path in sorted((out / "calls").glob(f"*__{ARM}__call_*.json")):
        record = json.loads(path.read_text("utf-8"))
        record["user_prompt"] = prompts[record["episode"]]
        records.append(record)
    return records


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Counts over one arm's outputs."""
    parsed = [r for r in rows if r["parsed"]]
    checks = Counter(c for r in parsed for c in r["layout_checks"])
    badges = Counter(rule for r in parsed for rule in r["other_badge_rules"])
    return {
        "outputs": len(rows),
        "parsed": len(parsed),
        "layout_violations": sum(1 for r in parsed if r["layout_checks"]),
        "layout_checks": dict(sorted(checks.items())),
        "with_recommendation": sum(1 for r in parsed if r["has_recommendation"]),
        "mean_selected_claims": round(
            sum(r["selected_claims"] for r in parsed) / max(len(parsed), 1), 2
        ),
        "blocked": sum(1 for r in parsed if r["blocked"]),
        "outputs_with_other_badges": dict(sorted(badges.items())),
    }


def _cmd_compare(args: argparse.Namespace, _config: AppConfig) -> int:
    before = [_metrics(r) for r in _records(args.e_dir, args.e_dir / "inputs")]
    after = [_metrics(r) for r in _records(args.out, args.out / "inputs")]
    doc = {
        "before_experiment_e": summarize(before),
        "after_fix": summarize(after),
        "per_output_after": after,
    }
    (args.out / "compare.json").write_text(json.dumps(doc, indent=1) + "\n", "utf-8")
    sys.stdout.write(
        json.dumps({k: doc[k] for k in ("before_experiment_e", "after_fix")}, indent=1)
        + "\n"
    )
    return 0


COMMANDS = {
    "build": _cmd_build,
    "plan": _cmd_plan,
    "run": _cmd_run,
    "compare": _cmd_compare,
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
    parser.add_argument("--root", type=Path, default=xe.ROOT)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--e-dir", type=Path, default=E_DIR)
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args(argv)
    return COMMANDS[args.command](args, load_config(args.config))


if __name__ == "__main__":
    raise SystemExit(main())
