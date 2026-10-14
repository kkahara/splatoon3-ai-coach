"""Experiment F: minimal grounding correction of the Experiment E templates.

Research only. One arm, ``reworded``: the Experiment E expanded catalog with
the two experiment claims' frames and recommendations reworded so they quote
only values the claim carries. Claim ids, eligibility, evidence paths, the 13
deaths, the system prompt, the model and the two repeats are unchanged.

The blind sheet mixes the 26 reworded outputs with Experiment E's 26
``expanded`` outputs (re-rated in the same session); the key is separate.

Commands mirror Experiment E: ``build``, ``plan``, ``run``, ``summary``,
``sheet``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import coaching_units_experiment_d as xd
import coaching_units_experiment_e as xe
from loguru import logger

from splatoon3_ai_coach.coach import claim_basis as cb
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig

OUT_DIR = xd.REPORT_DIR / "coaching_units_experiment_f"
E_DIR = xe.OUT_DIR
ARMS = ("reworded",)
E_ARM = "expanded"
RATING_SEED = 20261015

REWORDED_TEMPLATES: tuple[cb.RecommendationTemplate, ...] = (
    cb.RecommendationTemplate(
        "rec_after_splat_slow_down",
        "After you get a splat, slow down for a moment before pushing on.",
        re.compile(r"(?i)\bslow\s+down\b"),
    ),
    cb.RecommendationTemplate(
        "rec_final30_respawn_time",
        "In the last 30 seconds, keep in mind that a respawn uses up part of the "
        "time left.",
        re.compile(
            r"(?i)\brespawn\b[^.]*\b(?:time\s+left|remaining\s+time|time\s+remaining)\b"
        ),
    ),
)

_E_CLAIMS = {c.claim_id: c for c in xe.EXPERIMENT_CLAIMS}

REWORDED_CLAIMS: tuple[cb.ClaimDefinition, ...] = (
    replace(
        _E_CLAIMS["process_after_own_splat"],
        frame="Your splat and this death were {time_since_previous_splat}s apart.",
        rec_template_ids=("rec_after_splat_slow_down",),
    ),
    replace(
        _E_CLAIMS["process_final30_respawn_cost"],
        frame="This death happened with {display} left, inside the last 30 seconds.",
        rec_template_ids=("rec_final30_respawn_time",),
    ),
)

CATALOGS: dict[str, xe.Catalog] = {"reworded": (REWORDED_CLAIMS, REWORDED_TEMPLATES)}


def plan_markdown(
    doc: dict[str, Any], title: str = "Experiment F", arm: str = ARMS[0]
) -> str:
    """Human-readable dry run with per-death recommendation coverage."""
    lines = [
        f"# {title} dry run (no LLM calls)",
        "",
        f"- Provider / model: `{doc['provider']}` / `{doc['model']}`",
        f"- Temperature support: **{doc['temperature_support']}** "
        f"({doc['temperature_note']})",
        "- Stale inputs: "
        + ("; ".join(doc["stale_inputs"]) if doc["stale_inputs"] else "none"),
        f"- Calls: {len(doc['calls'])}",
        "",
        f"| death | {arm} templates |",
        "| --- | --- |",
        *[
            f"| {r['death']} | {', '.join(r.get(arm, [])) or '(none)'} |"
            for r in doc["coverage"]
        ],
    ]
    return "\n".join(lines) + "\n"


def sheet_records(
    new_dir: Path, old_dir: Path, old_arm: str = E_ARM
) -> list[dict[str, Any]]:
    """This experiment's records plus the previous experiment's ``old_arm`` ones."""
    new_records = xe._records(new_dir)
    old_records = [r for r in xe._records(old_dir) if r.get("arm") == old_arm]
    if not new_records or not old_records:
        raise SystemExit("sheet needs both this and the previous experiment's records")
    return [*new_records, *old_records]


def _cmd_build(args: argparse.Namespace, config: AppConfig) -> int:
    return xe.write_inputs(args, config, CATALOGS)


def _cmd_plan(args: argparse.Namespace, config: AppConfig) -> int:
    system, calls = xe.load_calls(args.out / "inputs")
    doc = xe.plan_doc(system, calls, config.coach, ARMS)
    (args.out / "dry_run.json").write_text(json.dumps(doc, indent=1) + "\n", "utf-8")
    text = plan_markdown(doc)
    (args.out / "dry_run.md").write_text(text, "utf-8")
    sys.stdout.write(text)
    return 0


def _cmd_run(args: argparse.Namespace, config: AppConfig) -> int:
    return xe.run_arms(args, config, CATALOGS)


def _cmd_summary(args: argparse.Namespace, _config: AppConfig) -> int:
    rows = xd.summarize(xe._records(args.out), ARMS)
    (args.out / "summary.json").write_text(json.dumps(rows, indent=1) + "\n", "utf-8")
    sys.stdout.write(json.dumps(rows, indent=1) + "\n")
    return 0


def _cmd_sheet(args: argparse.Namespace, _config: AppConfig) -> int:
    sheet_path = args.out / "rating_sheet.csv"
    key_path = args.out / "rating_key.csv"
    if sheet_path.exists() or key_path.exists():
        raise SystemExit("rating sheet or key exists; never overwritten")
    records = sheet_records(args.out, args.e_dir)
    sheet, key = xd.round2_sheet(records, seed=RATING_SEED)
    sheet_path.write_text(sheet, encoding="utf-8")
    key_path.write_text(key, encoding="utf-8")
    logger.info("wrote {} ({} outputs)", sheet_path, len(records))
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
    parser.add_argument("--root", type=Path, default=xe.ROOT)
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--e-dir", type=Path, default=E_DIR)
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args(argv)
    return COMMANDS[args.command](args, load_config(args.config))


if __name__ == "__main__":
    raise SystemExit(main())
