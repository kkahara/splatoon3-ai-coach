"""Coaching diversity experiment: current selection vs the advice-family cap.

Report-level comparison of ``coach.report_advice_family_cap`` (off in
production). Both arms share one frozen recording list, one config and one
code state; only the flag differs. Subcommands, in order:

* ``freeze``   write ``recordings.json`` from a canonical analysis list
* ``census``   score every report under both arms in memory; ``census.json``
               and ``key/evidence_findings.md`` (suppressed-moment evidence)
* ``prepare``  per changed report and arm, a shadow analysis dir with that
               arm's ``coach_inputs/``; one user prompt per unique selected
               unit under ``inputs/``; ``calls.json``; ``dry_run.md``. No call.
* ``run``      exactly-once provider calls (``--live`` required); see
               ``coaching_report_diversity_run``
* ``assemble`` build each arm's ``coach_prototype/``, render the public
               report, write blinded packets, the rating sheet and the key
* ``analyze``  after rating: join the completed sheet with the key

Nothing here writes outside ``OUT`` or reads sealed experiment labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from loguru import logger

from splatoon3_ai_coach.coach.artifacts import load_unit_artifacts, read_coaching_index
from splatoon3_ai_coach.coach.claim_basis import build_claim_basis
from splatoon3_ai_coach.coach.coach import serialize_llm_view_user_prompt
from splatoon3_ai_coach.coach.coach_inputs_build import write_coach_inputs
from splatoon3_ai_coach.coach.llm_runs import approx_token_count
from splatoon3_ai_coach.coach.load_analysis import (
    load_coach_analysis_bundle,
    select_primary_scenario_ids,
)
from splatoon3_ai_coach.coach.match_scoring import MatchScoring, score_match
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.coach.report_diversity import MomentSelection, advice_families
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT

_TOOLS = str(Path(__file__).resolve().parents[1])
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

EXPERIMENT = "coaching_report_diversity"
OUT = PROJECT_ROOT / "analysis" / "importance_report" / EXPERIMENT
ARMS = ("current", "capped")
SEED = 20261103
LIMIT = 50
PROVIDER, MODEL = "cursor", "gpt-5.6-luna"
EXCLUDED = {
    "analysis/4e8f661b1a7846fb9471a78ba5e33835": (
        "multi-match recording (25 deaths across several matches), not one report"
    ),
    "analysis/en-2026-07-05 17-34-46/": (
        "subclips of the recording kept as analysis/experiment_j_census/013"
    ),
}


# --- shared helpers -----------------------------------------------------------


def load_json(path: Path) -> Any:
    """Parse one JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def dump_json(path: Path, payload: Any) -> None:
    """Write sorted, indented JSON with a trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    """Hex sha256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def arm_config(base: AppConfig, arm: str) -> AppConfig:
    """``base`` with only ``coach.report_advice_family_cap`` set for ``arm``."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}")
    coach = base.coach.model_copy(update={"report_advice_family_cap": arm == "capped"})
    return base.model_copy(update={"coach": coach})


def base_config() -> AppConfig:
    """The repository default config (production coach settings)."""
    return load_config(default_config_path())


def report_dir(out: Path, report_id: str, arm: str) -> Path:
    """Shadow analysis dir of one report under one arm."""
    return out / "arms" / report_id / arm


# --- freeze -------------------------------------------------------------------


def freeze(source: Path, out: Path = OUT) -> Path:
    """Write ``recordings.json``: the canonical list minus explicit exclusions."""
    path = out / "recordings.json"
    if path.exists():
        raise SystemExit(f"{path} exists; the recording list is frozen")
    listed = sorted(load_json(source))
    excluded = [
        {"analysis": rel, "reason": reason}
        for rel in listed
        for prefix, reason in EXCLUDED.items()
        if rel == prefix.rstrip("/") or rel.startswith(prefix)
    ]
    dropped = {e["analysis"] for e in excluded}
    dump_json(
        path,
        {
            "experiment": EXPERIMENT,
            "selection": "one analysis per source recording (diversity audit)",
            "analyses": [rel for rel in listed if rel not in dropped],
            "excluded": excluded,
        },
    )
    return path


# --- census -------------------------------------------------------------------


def score_both(rel: str, base: AppConfig) -> dict[str, MatchScoring] | None:
    """Both arms' scoring for one analysis; ``None`` without deaths."""
    analysis = PROJECT_ROOT / rel
    bundle = load_coach_analysis_bundle(
        analysis,
        min_usable_confidence=base.vision.timer.min_usable_confidence,
        playback_config=base.coach.playback_clock,
    )
    if not select_primary_scenario_ids(bundle.scenarios, limit=LIMIT):
        return None
    return {arm: score_match(bundle, arm_config(base, arm), limit=LIMIT) for arm in ARMS}


def invariance_problems(scorings: Mapping[str, MatchScoring]) -> list[str]:
    """Everything except ``selected_for_llm`` is identical across arms."""
    current, capped = scorings["current"], scorings["capped"]
    problems = []
    strip = {"selected_for_llm"}
    if [u.model_dump(exclude=strip) for u in current.units] != [
        u.model_dump(exclude=strip) for u in capped.units
    ]:
        problems.append("unit scores, ranks or factors differ")
    for cid, ci in current.coach_inputs.items():
        if ci.model_dump(mode="json") != capped.coach_inputs[cid].model_dump(mode="json"):
            problems.append(f"{cid}: CoachInput differs")
    for cu, ku in zip(current.units, capped.units, strict=True):
        a = build_claim_basis(current.coach_inputs[cu.candidate_id], cu)
        b = build_claim_basis(capped.coach_inputs[ku.candidate_id], ku)
        if [e.model_dump() for e in a] != [e.model_dump() for e in b]:
            problems.append(f"{cu.candidate_id}: claim_basis differs")
    return problems


def _families_of_selected(scoring: MatchScoring) -> list[list[str]]:
    diagnostics = scoring.report_diversity
    if diagnostics is not None:
        return [m.advice_families for m in diagnostics.moments if m.selected]
    return [
        advice_families(scoring.coach_inputs[u.candidate_id], u)
        for u in scoring.units_by_rank()
        if u.selected_for_llm
    ]


def _repeats(families: Sequence[Sequence[str]]) -> int:
    flat = [f for fams in families for f in fams]
    return len(flat) - len(set(flat))


def census_record(rel: str, scorings: Mapping[str, MatchScoring]) -> dict[str, Any]:
    """One report's census row; full diagnostics only when selection changed."""
    current, capped = scorings["current"], scorings["capped"]
    diagnostics = capped.report_diversity
    assert diagnostics is not None
    record: dict[str, Any] = {
        "analysis": rel,
        "deaths": len(current.units),
        "importance_config_hash": {
            "current": current.importance_config_hash,
            "capped": capped.importance_config_hash,
        },
        "selected": {
            arm: [u.candidate_id for u in s.units_by_rank() if u.selected_for_llm]
            for arm, s in scorings.items()
        },
        "repeated_family_selections": {
            arm: _repeats(_families_of_selected(s)) for arm, s in scorings.items()
        },
        "changed": diagnostics.changed,
        "invariance_problems": invariance_problems(scorings),
    }
    if diagnostics.changed:
        record["diagnostics"] = diagnostics.model_dump(mode="json")
    return record


def census(out: Path = OUT) -> dict[str, Any]:
    """Score every frozen report under both arms; write ``census.json``."""
    recordings = load_json(out / "recordings.json")
    base = base_config()
    rows = []
    for rel in recordings["analyses"]:
        scorings = score_both(rel, base)
        if scorings is not None:
            rows.append(census_record(rel, scorings))
    changed = [r for r in rows if r["changed"]]
    for number, row in enumerate(changed, start=1):
        row["report_id"] = f"r{number:02d}"
    payload = {
        "experiment": EXPERIMENT,
        "reports_with_deaths": len(rows),
        "changed_reports": len(changed),
        "suppressed_moments": _count_actions(changed, "suppressed"),
        "promoted_moments": _count_actions(changed, "promoted"),
        "invariance_problems": sum(len(r["invariance_problems"]) for r in rows),
        "reports": rows,
    }
    dump_json(out / "census.json", payload)
    (out / "key").mkdir(parents=True, exist_ok=True)
    (out / "key" / "evidence_findings.md").write_text(
        evidence_findings(changed), encoding="utf-8"
    )
    return payload


def _count_actions(rows: Sequence[Mapping[str, Any]], action: str) -> int:
    return sum(m["action"] == action for r in rows for m in r["diagnostics"]["moments"])


# --- distinct evidence findings ----------------------------------------------


def evidence_findings(changed: Sequence[Mapping[str, Any]]) -> str:
    """Markdown: each suppressed moment's evidence against the retained moments."""
    lines = [
        "# Suppressed moments: evidence not present in retained moments",
        "",
        "Suppression is a selection outcome, not a redundancy verdict. Per "
        "category: `not_observed`, `same_value_in_retained` (an equal value in "
        "a retained moment) or `different_from_retained`.",
        "",
    ]
    for row in changed:
        moments = [
            MomentSelection.model_validate(m) for m in row["diagnostics"]["moments"]
        ]
        times = {m.candidate_id: m.video_time for m in moments}
        lines += [f"## {row['report_id']} — `{row['analysis']}`", ""]
        for m in moments:
            if m.action == "promoted":
                lines.append(
                    f"- promoted video {m.video_time:.1f}s rank {m.rank} "
                    f"score {m.importance_score:g} families {m.advice_families}"
                )
        for m in moments:
            if m.action == "suppressed":
                lines += _suppressed_lines(m, times)
        lines.append("")
    return "\n".join(lines) + "\n"


def _suppressed_lines(m: MomentSelection, times: Mapping[str, float]) -> list[str]:
    repeats = ", ".join(f"{times[c]:.1f}s" for c in m.repeats_family_of)
    claims = m.coachable_claims_not_in_retained or "none"
    lines = [
        f"- suppressed video {m.video_time:.1f}s rank {m.rank} score "
        f"{m.importance_score:g} families {m.advice_families} (repeats {repeats})",
        f"  - coachable claims not in retained: {claims}",
    ]
    for comparison in m.distinct_evidence:
        value = json.dumps(comparison.suppressed, sort_keys=True)
        lines.append(f"  - {comparison.category}: {comparison.status} {value}")
    return lines


# --- prepare ------------------------------------------------------------------


def changed_reports(out: Path = OUT) -> list[dict[str, Any]]:
    """Census rows whose selection changed, in report_id order."""
    return [r for r in load_json(out / "census.json")["reports"] if r["changed"]]


def write_arm(out: Path, row: Mapping[str, Any], arm: str, base: AppConfig) -> Path:
    """Shadow analysis dir: symlinked manifest plus this arm's coach_inputs."""
    shadow = report_dir(out, row["report_id"], arm)
    shadow.mkdir(parents=True)
    source = PROJECT_ROOT / row["analysis"]
    (shadow / "vision_manifest.json").symlink_to(source / "vision_manifest.json")
    build = write_coach_inputs(
        source, arm_config(base, arm), out_dir=shadow / "coach_inputs", limit=LIMIT
    )
    if build is None:
        raise SystemExit(f"{row['analysis']}: no deaths at prepare time")
    selected = [
        u.candidate_id for u in build.scoring.units_by_rank() if u.selected_for_llm
    ]
    if selected != row["selected"][arm]:
        raise SystemExit(f"{row['report_id']}/{arm}: selection differs from census")
    return shadow


def arm_prompts(shadow: Path) -> dict[str, tuple[str, str, str]]:
    """``candidate_id -> (safe_id, llm_view sha, user prompt)`` for selected units."""
    inputs = shadow / "coach_inputs"
    prompts = {}
    for entry in read_coaching_index(inputs).entries:
        if not entry.selected_for_llm:
            continue
        view_bytes = (inputs / entry.llm_view_json).read_bytes()
        view = load_unit_artifacts(inputs, entry).llm_view
        prompts[entry.candidate_id] = (
            entry.safe_id,
            sha256_bytes(view_bytes),
            serialize_llm_view_user_prompt(view),
        )
    return prompts


def report_calls(out: Path, row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One call per unit selected in either arm; shared units must match exactly."""
    per_arm = {arm: arm_prompts(report_dir(out, row["report_id"], arm)) for arm in ARMS}
    calls = []
    for cid in sorted({c for prompts in per_arm.values() for c in prompts}):
        arms = [arm for arm in ARMS if cid in per_arm[arm]]
        variants = {per_arm[arm][cid] for arm in arms}
        if len(variants) != 1:
            raise SystemExit(f"{row['report_id']} {cid}: arms send different prompts")
        safe_id, view_sha, prompt = variants.pop()
        call_id = f"{row['report_id']}-{safe_id}"
        path = out / "inputs" / f"{call_id}.user.txt"
        path.write_bytes(prompt.encode("utf-8"))
        calls.append(
            {
                "call_id": call_id,
                "report_id": row["report_id"],
                "candidate_id": cid,
                "safe_id": safe_id,
                "arms": arms,
                "user_prompt": f"inputs/{path.name}",
                "user_prompt_sha256": sha256_bytes(path.read_bytes()),
                "llm_view_sha256": view_sha,
            }
        )
    return calls


def prepare(out: Path = OUT) -> dict[str, Any]:
    """Shadow arms, prompts, ``calls.json``, ``inputs.sha256``, ``dry_run.md``."""
    if (out / "arms").exists() or (out / "inputs").exists():
        raise SystemExit("arms/ or inputs/ exists; prepare runs once")
    base = base_config()
    rows = changed_reports(out)
    (out / "inputs").mkdir(parents=True)
    system = load_system_prompt(roster_evidence=base.coach.roster_evidence)
    (out / "inputs" / "system_prompt.txt").write_bytes(system.encode("utf-8"))
    calls = []
    for row in rows:
        for arm in ARMS:
            write_arm(out, row, arm, base)
        calls.extend(report_calls(out, row))
    plan = {
        "experiment": EXPERIMENT,
        "provider": PROVIDER,
        "model": MODEL,
        "seed": SEED,
        "system_prompt": "inputs/system_prompt.txt",
        "system_prompt_sha256": sha256_bytes(system.encode("utf-8")),
        "reports": [r["report_id"] for r in rows],
        "calls": calls,
    }
    dump_json(out / "calls.json", plan)
    write_input_sums(out)
    (out / "dry_run.md").write_text(dry_run(plan, out), encoding="utf-8")
    return plan


def write_input_sums(out: Path) -> Path:
    """``inputs.sha256`` over every file under ``inputs/``."""
    lines = [
        f"{sha256_bytes(p.read_bytes())}  {p.relative_to(out)}"
        for p in sorted((out / "inputs").iterdir())
        if p.is_file()
    ]
    path = out / "inputs.sha256"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def dry_run(plan: Mapping[str, Any], out: Path) -> str:
    """Call counts and token estimates; no provider is contacted."""
    calls = plan["calls"]
    shared = sum(len(c["arms"]) == 2 for c in calls)
    only = {arm: sum(c["arms"] == [arm] for c in calls) for arm in ARMS}
    system_tokens = approx_token_count((out / plan["system_prompt"]).read_text()) or 0
    user_tokens = sum(
        approx_token_count((out / c["user_prompt"]).read_text()) or 0 for c in calls
    )
    return "\n".join(
        [
            f"# Dry run — {EXPERIMENT}",
            "",
            f"- provider/model: {plan['provider']} / {plan['model']}",
            f"- changed reports: {len(plan['reports'])}",
            f"- unique calls: {len(calls)} (shared by both arms {shared}, "
            f"current only {only['current']}, capped only {only['capped']})",
            f"- estimated input tokens: {system_tokens * len(calls) + user_tokens}",
            "- no provider call was made",
            "",
        ]
    )


# --- CLI ----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Subcommand dispatch; ``run`` and ``assemble`` live in the run module."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=OUT)
    sub = parser.add_subparsers(dest="command", required=True)
    frz = sub.add_parser("freeze")
    frz.add_argument("source", type=Path)
    sub.add_parser("census")
    sub.add_parser("prepare")
    run = sub.add_parser("run")
    run.add_argument("--live", action="store_true", help="Make provider calls.")
    sub.add_parser("assemble")
    review = sub.add_parser("review-ui", help="Local blinded human evaluation UI.")
    review.add_argument("--port", type=int, default=8766)
    ana = sub.add_parser("analyze", help="Unblind a completed rating sheet.")
    ana.add_argument("sheet", type=Path)
    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    return _dispatch(args)


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "review-ui":
        from coaching_diversity_review import serve

        serve(args.out / "blind", args.port)
        return 0

    import coaching_report_diversity_run as xrun

    out = args.out
    if args.command == "freeze":
        logger.warning("wrote {}", freeze(args.source, out))
    elif args.command == "census":
        summary = census(out)
        logger.warning(
            "{} reports; {} changed; {} suppressed; {} promoted; invariance problems {}",
            summary["reports_with_deaths"],
            summary["changed_reports"],
            summary["suppressed_moments"],
            summary["promoted_moments"],
            summary["invariance_problems"],
        )
    elif args.command == "prepare":
        logger.warning("{} calls planned; see dry_run.md", len(prepare(out)["calls"]))
    elif args.command == "run":
        return xrun.run(out, live=args.live)
    elif args.command == "assemble":
        logger.warning("assembled {} blinded reports", len(xrun.assemble(out)))
    elif args.command == "analyze":
        logger.warning("{}", xrun.analyze(out, args.sheet))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
