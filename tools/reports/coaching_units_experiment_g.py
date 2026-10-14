"""Experiment G: sharper final-30 facts and a new after-splat recommendation.

Research only. One arm, ``sharpened``, built on the Experiment F catalog:

* The final-30 process claim is replaced by three mutually exclusive factual
  claims computed from the observed death clock and the out-of-play duration
  (play time only when playback is verified normal): back in play with about
  N seconds on the clock, not back before the clock reached 0:00, or no return
  to play observed. All keep F's final-30 recommendation.
* The after-splat claim keeps F's frame; its recommendation becomes
  "After a splat, decide where you are going next before you move up."

The 13 deaths, eligibility triggers, evidence, system prompt, model and two
repeats are unchanged. The blind sheet mixes the 26 outputs with Experiment
F's 26 ``reworded`` outputs. Commands mirror Experiment F.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import coaching_units_experiment_d as xd
import coaching_units_experiment_e as xe
import coaching_units_experiment_f as xf
from loguru import logger

from splatoon3_ai_coach.coach import claim_basis as cb
from splatoon3_ai_coach.coach.claim_catalog import CoachingUnitResult
from splatoon3_ai_coach.coach.claim_catalog import DeathImportanceFactorId as _F
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig

OUT_DIR = xd.REPORT_DIR / "coaching_units_experiment_g"
F_DIR = xf.OUT_DIR
ARMS = ("sharpened",)
F_ARM = "reworded"
RATING_SEED = 20261016
FINAL30_REC = "rec_final30_respawn_time"

NEXT_MOVE_TEMPLATE = cb.RecommendationTemplate(
    "rec_after_splat_next_move",
    "After a splat, decide where you are going next before you move up.",
    re.compile(
        r"(?i)\bdecide\s+where\b|\bwhere\s+you\s*(?:'re|\s+are)\s+(?:going|headed)\s+next\b"
    ),
)

_F_TEMPLATES = {t.template_id: t for t in xf.REWORDED_TEMPLATES}
_F_CLAIMS = {c.claim_id: c for c in xf.REWORDED_CLAIMS}
_FINAL30 = cb._factor(_F.DEATH_FINAL_30S, cb._clock_values)


def final30_case(coach_input: CoachInput, unit: CoachingUnitResult) -> cb.Values | None:
    """Final-30 values plus which of the three cases applies, or ``None``."""
    values = _FINAL30(coach_input, unit)
    death = coach_input.primary_context.death_episode
    if not values or death is None:
        return None
    if not death.has_active_again:
        return {**values, "case": "no_return_observed"}
    durations = coach_input.durations
    span = durations.death_to_active_again if durations is not None else None
    if span is None or span.value is None:
        return None
    back_with = round((values["seconds_remaining"] - span.value) * 2) / 2
    out = {**values, "death_to_active_again": span.value, "clock": span.clock}
    if back_with > 0:
        return {**out, "back_with": back_with, "case": "back_with_time"}
    return {**out, "case": "not_back_by_zero"}


def _case(name: str) -> cb.Eligibility:
    def check(coach_input: CoachInput, unit: CoachingUnitResult) -> cb.Values | None:
        values = final30_case(coach_input, unit)
        if values is None or values.pop("case") != name:
            return None
        return values

    return check


def _final30_claim(claim_id: str, case: str, frame: str) -> cb.ClaimDefinition:
    return cb.ClaimDefinition(
        claim_id=claim_id,
        level=cb.ClaimLevel.B,
        frame=frame,
        evidence_paths=("clock.seconds_remaining", "death.death_to_active_again"),
        eligible=_case(case),
        coachable=True,
        rec_template_ids=(FINAL30_REC,),
    )


SHARPENED_CLAIMS: tuple[cb.ClaimDefinition, ...] = (
    replace(
        _F_CLAIMS["process_after_own_splat"],
        rec_template_ids=(NEXT_MOVE_TEMPLATE.template_id,),
    ),
    _final30_claim(
        "process_final30_back_with_time",
        "back_with_time",
        "You died with {display} left and were back in play with about "
        "{back_with}s on the clock.",
    ),
    _final30_claim(
        "process_final30_not_back_by_zero",
        "not_back_by_zero",
        "You died with {display} left and were not back in play before the match "
        "clock reached 0:00.",
    ),
    _final30_claim(
        "process_final30_no_return_observed",
        "no_return_observed",
        "You died with {display} left; no return to play was observed after this death.",
    ),
)

SHARPENED_TEMPLATES: tuple[cb.RecommendationTemplate, ...] = (
    NEXT_MOVE_TEMPLATE,
    _F_TEMPLATES[FINAL30_REC],
)

CATALOGS: dict[str, xe.Catalog] = {"sharpened": (SHARPENED_CLAIMS, SHARPENED_TEMPLATES)}


def _cmd_build(args: argparse.Namespace, config: AppConfig) -> int:
    return xe.write_inputs(args, config, CATALOGS)


def _cmd_plan(args: argparse.Namespace, config: AppConfig) -> int:
    system, calls = xe.load_calls(args.out / "inputs")
    doc = xe.plan_doc(system, calls, config.coach, ARMS)
    (args.out / "dry_run.json").write_text(json.dumps(doc, indent=1) + "\n", "utf-8")
    text = xf.plan_markdown(doc, "Experiment G", ARMS[0])
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
    records = xf.sheet_records(args.out, args.f_dir, F_ARM)
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
    parser.add_argument("--f-dir", type=Path, default=F_DIR)
    parser.add_argument("--config", type=Path, default=default_config_path())
    args = parser.parse_args(argv)
    return COMMANDS[args.command](args, load_config(args.config))


if __name__ == "__main__":
    raise SystemExit(main())
