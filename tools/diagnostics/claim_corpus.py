#!/usr/bin/env python3
"""Coaching claim-validation corpus: freeze saved model outputs, report metrics.

Each corpus entry keeps one saved model output with the CoachInput and
coaching unit it was written from. Views are rebuilt with current code
(roster capability off, current ranking), so rule changes apply to old
outputs. The corpus grows with outputs that expose a failure mode; it is
never regenerated.

Entries written before ``claim_basis`` existed carry
``prompt_contract: legacy``: their claim IDs and cited values target an older
view, so R7 does not apply to them. Corpus status is computed from rule hits
only (evidence-path resolution depends on the view's vintage).

Usage::

    claim_corpus.py freeze <analysis_dir> <safe_id> <model_label> [--id ID]
        [--contract legacy|claim_basis]
    claim_corpus.py report [--corpus DIR] [--json]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from splatoon3_ai_coach.coach.claim_catalog import CoachingUnitResult
from splatoon3_ai_coach.coach.claim_validation import validate_assessment
from splatoon3_ai_coach.coach.coach_input import CoachInput, strip_roster_evidence
from splatoon3_ai_coach.coach.death_importance import score_death_candidate
from splatoon3_ai_coach.coach.llm_client import CoachingAssessment
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.config.models import CoachConfig

DEFAULT_CORPUS = Path(__file__).resolve().parents[2] / "tests/fixtures/claim_corpus"
_BLOCKING = frozenset({"R1", "R2", "R3", "R4", "R6"})
_CATEGORIES = {
    "roster_R2": lambda h: h["rule"] == "R2",
    "causal_R1": lambda h: h["rule"] == "R1",
    "teammate_opponent_R3": lambda h: h["rule"] == "R3",
    "special_usage_R4": lambda h: h["rule"] == "R4",
    "special_state_R5": lambda h: h.get("check") == "special_state",
    "false_unknown_R9": lambda h: h["rule"] == "R9",
    "internal_id_R8": lambda h: h["rule"] == "R8",
    "missed_claim_R11": lambda h: h["rule"] == "R11",
    "video_seconds_R12": lambda h: h["rule"] == "R12",
    "advice_in_assessment_R14": lambda h: h["rule"] == "R14",
}


def freeze_entry(
    analysis_dir: Path,
    safe_id: str,
    model_label: str,
    entry_id: str,
    contract: str = "claim_basis",
) -> dict[str, Any]:
    """One corpus entry from an analysis directory's saved artifacts."""
    inputs = analysis_dir / "coach_inputs"
    output = analysis_dir / "coach_prototype" / f"{safe_id}.{model_label}.output.json"
    coach_input = CoachInput.model_validate_json(
        (inputs / f"{safe_id}.coach_input.json").read_text(encoding="utf-8")
    )
    unit = json.loads((inputs / f"{safe_id}.coaching.json").read_text(encoding="utf-8"))
    return {
        "id": entry_id,
        "source": {"analysis": analysis_dir.name, "safe_id": safe_id},
        "model": model_label,
        "prompt_contract": contract,
        "match_duration_seconds": unit.get("match_duration_seconds"),
        "coach_input": strip_roster_evidence(coach_input).model_dump(mode="json"),
        "model_output": json.loads(output.read_text(encoding="utf-8")),
        "expected": {"rules": [], "coachable_claims": []},
    }


def corpus_unit(entry: dict[str, Any]) -> tuple[CoachInput, CoachingUnitResult]:
    """Roster-stripped CoachInput and its unit scored with ``CoachConfig`` defaults."""
    coach = CoachConfig()
    coach_input = strip_roster_evidence(CoachInput.model_validate(entry["coach_input"]))
    unit = score_death_candidate(
        coach_input,
        match_duration_seconds=entry.get("match_duration_seconds"),
        weights=coach.death_importance_weights,
        thresholds=coach.death_factor_thresholds,
        modifier_factors=coach.death_modifier_factors,
        ranking_excluded_factors=coach.death_ranking_excluded_factors,
    )
    return coach_input, unit


def corpus_view(entry: dict[str, Any]) -> dict[str, Any]:
    """The current-code LLM view for an entry (roster off, current ranking)."""
    coach_input, unit = corpus_unit(entry)
    return build_coach_llm_view(coach_input, unit).model_dump(mode="json")


def counting_hits(report: dict[str, Any], *, legacy: bool) -> list[dict[str, Any]]:
    """Family and integrity hits that count as violations (no R7 for legacy)."""
    family = [h for h in report["claim_rule_hits"] if h["counts_as_violation"]]
    integrity = [h for h in report["integrity_hits"] if h["counts_as_violation"]]
    if legacy:
        integrity = [h for h in integrity if h["rule"] != "R7"]
    return family + integrity


def _status(hits: list[dict[str, Any]]) -> str:
    if any(h["rule"] in _BLOCKING for h in hits):
        return "blocked_unsupported_claim"
    return "contains_unverified_wording" if hits else "validated"


def evaluate(entry: dict[str, Any]) -> dict[str, Any]:
    """Rule hits, status and claim usage for one entry under current code."""
    view = corpus_view(entry)
    output = CoachingAssessment.model_validate(entry["model_output"])
    report = validate_assessment(output.model_dump(mode="json"), view)
    hits = counting_hits(report, legacy=entry.get("prompt_contract") == "legacy")
    coachable = sorted(e["claim_id"] for e in view["claim_basis"] if e["coachable"])
    used = sorted(set(output.selected_claim_ids) & set(coachable))
    return {
        "id": entry["id"],
        "status": _status(hits),
        "rules": sorted({h["rule"] for h in hits}),
        "hits": hits,
        "coachable_claims": coachable,
        "coachable_used": used,
    }


def load_corpus(corpus: Path = DEFAULT_CORPUS) -> list[dict[str, Any]]:
    """Every ``*.json`` entry in the corpus directory, sorted by file name."""
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(corpus.glob("*.json"))
    ]


def metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-category hit counts so prompt / model changes can be compared."""
    counts = {name: 0 for name in _CATEGORIES}
    judgment: Counter[str] = Counter()
    for result in results:
        for hit in result["hits"]:
            for name, matches in _CATEGORIES.items():
                if matches(hit):
                    counts[name] += 1
            if hit["rule"] == "R6":
                judgment[str(hit.get("subcategory"))] += 1
    units = len(results) or 1
    return {
        "entries": len(results),
        "statuses": dict(Counter(r["status"] for r in results)),
        "hits": counts,
        "judgment_R6_by_subcategory": dict(judgment),
        "coachable_available_per_unit": sum(len(r["coachable_claims"]) for r in results)
        / units,
        "coachable_used_per_unit": sum(len(r["coachable_used"]) for r in results) / units,
        "units_with_no_hits": sum(1 for r in results if not r["rules"]),
    }


def _freeze(args: argparse.Namespace) -> None:
    entry_id = args.id or f"{Path(args.analysis_dir).name[:4]}_{args.safe_id}"
    entry = freeze_entry(
        Path(args.analysis_dir), args.safe_id, args.model_label, entry_id, args.contract
    )
    out = Path(args.corpus) / f"{entry_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(entry, indent=1, ensure_ascii=False) + "\n"
    out.write_text(text, encoding="utf-8")
    print(out)


def _report(args: argparse.Namespace) -> None:
    results = [evaluate(entry) for entry in load_corpus(Path(args.corpus))]
    summary = metrics(results)
    if args.json:
        print(json.dumps({"metrics": summary, "results": results}, indent=1))
        return
    for result in results:
        print(f"{result['id']:<32} {result['status']:<28} {','.join(result['rules'])}")
    print(json.dumps(summary, indent=1))


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    freeze = sub.add_parser("freeze", help="add one saved output to the corpus")
    freeze.add_argument("analysis_dir")
    freeze.add_argument("safe_id")
    freeze.add_argument("model_label")
    freeze.add_argument("--id", default="")
    freeze.add_argument(
        "--contract", choices=("legacy", "claim_basis"), default="claim_basis"
    )
    freeze.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    freeze.set_defaults(run=_freeze)
    report = sub.add_parser("report", help="per-category metrics over the corpus")
    report.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    report.add_argument("--json", action="store_true")
    report.set_defaults(run=_report)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
