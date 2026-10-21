"""Experiment J stop point 3: sealed inputs and the dry run (no model calls).

``build`` verifies every stop point 1-2 seal, reproduces the census facts,
and writes ``inputs/`` (system prompts, one user prompt per arm and death,
``manifest.json`` with the call plan) once. ``plan`` rebuilds everything,
compares it byte for byte with ``inputs/``, checks the arm invariants, runs
the production validator and the frozen checks on synthetic compliant M and
C outputs, re-runs the ablation check on Experiment I's stored outputs and
writes ``dry_run.json`` / ``dry_run.md``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import coaching_units_experiment_i as xi
import coaching_units_experiment_j as xj
import coaching_units_experiment_j_arms as xa
import coaching_units_experiment_j_census as xc
from loguru import logger

from splatoon3_ai_coach.coach.claim_basis import RECOMMENDATION_TEMPLATES
from splatoon3_ai_coach.coach.claim_validation import resolve_view_path
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig

OUT = xj.OUT_DIR
INPUTS = OUT / "inputs"
I_CALLS = xi.OUT_DIR / "outputs" / "calls"
SEALS = OUT / "SHA256SUMS"
ANALYSIS_SEALS = OUT / "census_analysis.sha256"
STORED_CHECK = {"ablatable": 27, "of": 28, "no_recommendation_left": 27}


# --- seals --------------------------------------------------------------------


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sums_problems(sums: Path, root: Path) -> list[str]:
    """Lines of a ``shasum -a 256`` file that no longer verify."""
    problems = []
    for line in sums.read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        path = root / name.lstrip("*")
        if not path.is_file():
            problems.append(f"{name}: missing")
        elif _sha(path) != digest:
            problems.append(f"{name}: differs")
    return problems


def seal_problems() -> list[str]:
    """Stop point 1-2 seals and the per-recording analysis outputs."""
    return sums_problems(SEALS, OUT) + sums_problems(ANALYSIS_SEALS, xc.CENSUS_DIR)


# --- inputs -------------------------------------------------------------------

FACT_FIELDS = (
    "back_in_play_source",
    "back_in_play_time",
    "seconds_back_in_play",
    "map_checks_since_back_in_play",
    "seconds_since_alive_map_check",
)


def _analysis(census: Mapping[str, Any], order: int) -> Path:
    rec = next(r for r in census["recordings"] if r["order"] == order)
    return xc.CENSUS_DIR / rec["analysis_dir"]


def unique_deaths(deaths: Mapping[str, Sequence[Mapping]]) -> list[dict[str, Any]]:
    """Every death any population calls, in death order."""
    by_id = {xc.death_id(d): d for group in deaths.values() for d in group}
    return sorted(by_id.values(), key=xc.death_key)


def fact_problems(census: Mapping[str, Any], records: Sequence[Mapping]) -> list[str]:
    """Census facts that the analysis artifacts no longer reproduce."""
    problems = []
    for record in records:
        facts = xi.facts_for(_analysis(census, record["order"]), record["cid"])
        for field in FACT_FIELDS:
            if facts[field] != record[field]:
                problems.append(f"{xc.death_id(record)} {field}")
    return problems


def production(
    census: Mapping[str, Any], records: Sequence[Mapping], config: AppConfig
) -> dict[str, tuple[dict, str]]:
    """Production view and prompt per death id."""
    out: dict[str, tuple[dict, str]] = {}
    for order in sorted({r["order"] for r in records}):
        cids = [r["cid"] for r in records if r["order"] == order]
        views = xa.production_views(_analysis(census, order), cids, config)
        out |= {f"{order:03d}|{cid}": views[cid] for cid in cids}
    return out


def arm_inputs(
    deaths: Mapping[str, Sequence[Mapping]], prod: Mapping[str, tuple[dict, str]]
) -> dict[str, dict[str, dict[str, Any]]]:
    """``{arm: {death_id: {view, prompt}}}`` for every planned call."""
    arms: dict[str, dict[str, dict[str, Any]]] = {arm: {} for arm in xa.ARMS}
    for population, population_arms in xa.POPULATION_ARMS.items():
        for record in deaths[population]:
            view, prompt = prod[xc.death_id(record)]
            for arm in population_arms:
                arm_view = xa.arm_view(arm, view, record)
                arms[arm][xc.death_id(record)] = {
                    "view": arm_view,
                    "prompt": xa.user_prompt(prompt, arm_view),
                }
    return arms


def input_files(
    deaths: Mapping[str, Sequence[Mapping]], arms: Mapping[str, Mapping[str, Mapping]]
) -> dict[str, str]:
    """Relative path -> text: system prompts, user prompts and the manifest."""
    files = {f"system_prompt_{arm}.txt": xa.system_prompt(arm) for arm in xa.ARMS}
    for arm, by_death in arms.items():
        for record in unique_deaths(deaths):
            if xc.death_id(record) in by_death:
                rel = f"{arm}/{xa.slug(record)}.user_prompt.txt"
                files[rel] = by_death[xc.death_id(record)]["prompt"]
    calls = xa.call_plan(deaths)
    design = xa.load_json(OUT / "design.json")
    manifest = {
        "experiment": "J",
        "stop_point": 3,
        "provider": design["model"]["provider"],
        "model": design["model"]["model"],
        "repeats": xa.REPEATS,
        "populations_sha256": _sha(xa.POPULATIONS),
        "call_counts": xa.call_counts(calls),
        "calls": calls,
    }
    files["manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    return files


def staleness(inputs: Path, files: Mapping[str, str]) -> list[str]:
    """Stored inputs that are not byte-identical to a fresh rebuild."""
    if not inputs.exists():
        return ["inputs/ not written"]
    stored = {
        str(p.relative_to(inputs)): p.read_text(encoding="utf-8")
        for p in inputs.rglob("*")
        if p.is_file()
    }
    problems = [f"{rel} differs" for rel in files if stored.get(rel) != files[rel]]
    return problems + [f"{rel} unexpected" for rel in sorted(set(stored) - set(files))]


# --- invariants ---------------------------------------------------------------


def _strip(view: Mapping[str, Any], arm: str) -> dict[str, Any]:
    """Undo the arm's additions; must give back the production view."""
    out = json.loads(json.dumps(view))
    for key in ("back_in_play", "map_awareness", "extra_point"):
        out.pop(key, None)
    out["claim_basis"] = [
        e for e in out["claim_basis"] if e["claim_id"] != xa.MAP_CLAIM_ID
    ]
    if arm == "C":
        for entry in out["claim_basis"]:
            entry["rec_template_ids"] = [
                t for t in entry["rec_template_ids"] if t != xa.REPOSITION_TEMPLATE
            ]
            entry["rec_frames"] = [
                f for f in entry["rec_frames"] if f != xa.REPOSITION_FRAME
            ]
    return out


def _death_invariants(
    arm: str, record: Mapping, view: Mapping, prod: tuple[dict, str], prompt: str
) -> list[str]:
    tag = f"{arm} {xc.death_id(record)}"
    problems = []
    if _strip(view, arm) != prod[0]:
        problems.append(f"{tag}: view is not production + arm blocks")
    if arm == "A" and prompt != prod[1]:
        problems.append(f"{tag}: prompt differs from production serialization")
    if arm != "A" and view["back_in_play"] != xa.back_in_play_block(record):
        problems.append(f"{tag}: back_in_play")
    if arm == "M":
        claimed = any(e["claim_id"] == xa.MAP_CLAIM_ID for e in view["claim_basis"])
        eligible = bool(record["eligible"])
        if claimed != eligible or (view["extra_point"] is not None) != eligible:
            problems.append(f"{tag}: extra point present={claimed}, eligible={eligible}")
    if arm == "C" and view["extra_point"]["claim_id"] != record["c_point"]:
        problems.append(f"{tag}: extra point is not the natural C claim")
    if arm == "C" and "map_awareness" in view:
        problems.append(f"{tag}: C has map_awareness")
    return problems


def invariant_problems(
    deaths: Mapping[str, Sequence[Mapping]],
    arms: Mapping[str, Mapping[str, Mapping]],
    prod: Mapping[str, tuple[dict, str]],
) -> list[str]:
    """A = production; M / C = production + their blocks; system M / C = A + section."""
    base = xa.system_prompt("A")
    problems = []
    for arm in ("M", "C"):
        if xa.system_prompt(arm) != base.rstrip("\n") + "\n\n" + xa.section(arm):
            problems.append(f"system {arm} is not system A + section")
    by_id = {xc.death_id(d): d for d in unique_deaths(deaths)}
    for arm, by_death in arms.items():
        for death_id, item in by_death.items():
            problems += _death_invariants(
                arm, by_id[death_id], item["view"], prod[death_id], item["prompt"]
            )
    return problems


def path_problems(arms: Mapping[str, Mapping[str, Mapping]]) -> list[str]:
    """Literal block paths (claim evidence paths, extra_point) must resolve."""
    problems = []
    for arm in ("M", "C"):
        for death_id, item in arms[arm].items():
            view = item["view"]
            point = view.get("extra_point")
            if point is None:
                continue
            entry = next(
                e for e in view["claim_basis"] if e["claim_id"] == point["claim_id"]
            )
            paths = [*entry["evidence_paths"], "back_in_play.seconds_back_in_play"]
            paths += ["extra_point.claim_id", "extra_point.rec_template_id"]
            problems += [
                f"{arm} {death_id}: {p}"
                for p in paths
                if not resolve_view_path(view, p)[0]
            ]
            if point["rec_template_id"] not in entry["rec_template_ids"]:
                problems.append(f"{arm} {death_id}: template not on the claim entry")
    return problems


# --- synthetic outputs ----------------------------------------------------------

NEUTRAL = "This is one habit to build, not the reason for the death."


def _fmt(value: float) -> str:
    return f"{float(value):g}"


def compliant(view: Mapping[str, Any]) -> dict[str, Any]:
    """A synthetic output that follows the arm's section exactly."""
    point = view["extra_point"]
    entry = next(e for e in view["claim_basis"] if e["claim_id"] == point["claim_id"])
    values = {
        k: _fmt(v) if isinstance(v, (int, float)) else v
        for k, v in entry["values"].items()
    }
    sentence = entry["frame"].format(**values)
    template = point["rec_template_id"]
    frame = (
        xa.REPOSITION_FRAME
        if template == xa.REPOSITION_TEMPLATE
        else RECOMMENDATION_TEMPLATES[template].frame
    )
    cited = [f"{p} = {resolve_view_path(view, p)[1]}" for p in entry["evidence_paths"]]
    return {
        "assessment": f"{sentence} {NEUTRAL}",
        "claims": [{"claim_id": point["claim_id"], "level": "A", "sentence": sentence}],
        "evidence_used": cited,
        "limitations": [],
        "recommendations": [frame],
        "selected_claim_ids": [point["claim_id"]],
    }


def amendment_3_cases(
    observed: Mapping, alive_check: Mapping
) -> list[tuple[str, Mapping, dict]]:
    """R5 map_absence hits that Amendment 3 must keep in the adjusted count."""
    good = compliant(observed)
    window = "No map check was observed since you were back in play."
    return [
        (
            "a3_unanchored_absence",
            observed,
            {
                **good,
                "assessment": f"No map check was observed before this death. {NEUTRAL}",
            },
        ),
        ("a3_absence_with_alive_check", alive_check, {"assessment": window}),
    ]


def negative_cases(
    eligible: Mapping, not_eligible: Mapping
) -> list[tuple[str, Mapping, dict]]:
    """Outputs the frozen checks must catch (case, view, output)."""
    good = compliant(eligible)
    seconds = float(eligible["back_in_play"]["seconds_back_in_play"])
    wrong = json.loads(json.dumps(good).replace(_fmt(seconds), _fmt(seconds + 7)))
    paraphrase = {
        **good,
        "assessment": f"You went {_fmt(seconds)}s without a map check. {NEUTRAL}",
    }
    causal = (
        "You died because you did not check the map in the "
        f"{_fmt(seconds)}s since you respawned."
    )
    return [
        ("causal", eligible, {**good, "assessment": causal}),
        (
            "map_content",
            eligible,
            {**good, "assessment": "A map check would have shown the opponent flanking."},
        ),
        ("wrong_seconds", eligible, wrong),
        ("paraphrased_map_sentence", eligible, paraphrase),
        (
            "advice_not_eligible",
            not_eligible,
            {"recommendations": [good["recommendations"][0]]},
        ),
    ]


def _row(case: str, arm: str, death_id: str, view: Mapping, output: Mapping) -> dict:
    checks = xa.output_checks(arm, output, view)
    status = ((view.get("map") or {}).get("map_check_before_death") or {}).get("status")
    return {
        "case": case,
        "arm": arm,
        "death_id": death_id,
        "map_status": status,
        **checks,
    }


def synthetic_rows(
    deaths: Mapping[str, Sequence[Mapping]], arms: Mapping[str, Mapping[str, Mapping]]
) -> list[dict[str, Any]]:
    """Compliant M (P1, P2) and C outputs, then the negative cases."""
    rows = []
    rated = sorted([*deaths["P1"], *deaths["P2"]], key=xc.death_key)
    for arm, records in (("M", rated), ("C", deaths["C"])):
        for record in records:
            view = arms[arm][xc.death_id(record)]["view"]
            death_id = xc.death_id(record)
            rows.append(_row("compliant", arm, death_id, view, compliant(view)))
    m_views = {k: v["view"] for k, v in arms["M"].items()}
    first = m_views[xc.death_id(deaths["P1"][0])]
    p3 = m_views[xc.death_id(deaths["P3"][0])]
    for case, view, output in negative_cases(first, p3):
        rows.append(_row(case, "M", "synthetic", view, output))
    observed = next(
        m_views[xc.death_id(r)] for r in rated if _status(m_views, r) == "observed"
    )
    checked = next(
        m_views[xc.death_id(r)]
        for r in deaths["P3"]
        if r["map_checks_since_back_in_play"] >= 1
    )
    for case, view, output in amendment_3_cases(observed, checked):
        rows.append(_row(case, "M", "synthetic", view, output))
    return rows


def _status(views: Mapping[str, Mapping], record: Mapping) -> str | None:
    view = views[xc.death_id(record)]
    return ((view.get("map") or {}).get("map_check_before_death") or {}).get("status")


def synthetic_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Compliant outputs: production, adjustments and adjusted hits per arm."""
    out = {}
    for arm in ("M", "C"):
        group = [r for r in rows if r["case"] == "compliant" and r["arm"] == arm]
        statuses = sorted({str(r["map_status"]) for r in group})
        out[arm] = {
            "outputs": len(group),
            "production_hits": sum(r["validator_hits"] for r in group),
            "c_adjustment_removed": sum(r["c_adjustment"] for r in group),
            "amendment_3_r5_window_exempt": sum(r["r5_window_exempt"] for r in group),
            "adjusted_hits": sum(r["adjusted_hits"] for r in group),
            "outputs_with_production_hits": sum(r["validator_hits"] > 0 for r in group),
            "outputs_with_adjusted_hits": sum(r["adjusted_hits"] > 0 for r in group),
            "rules": dict(Counter(rule for r in group for rule in r["hit_rules"])),
            "production_hits_by_map_status": {
                s: sum(r["validator_hits"] for r in group if str(r["map_status"]) == s)
                for s in statuses
            },
            "adjusted_hits_by_map_status": {
                s: sum(r["adjusted_hits"] for r in group if str(r["map_status"]) == s)
                for s in statuses
            },
            "value_fidelity_errors": sum(len(r["value_fidelity_errors"]) for r in group),
            "blocked": sum(r["blocked"] for r in group),
        }
    return out


# --- Experiment I stored outputs (structure only) -------------------------------


def stored_ablation(calls: Path = I_CALLS) -> dict[str, Any]:
    """The frozen ablation rule on Experiment I's eligible M outputs."""
    rows = []
    for path in sorted(calls.glob("M__*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["eligible"]:
            rows.append({"call_id": record["call_id"], **xa.ablate(record["assessment"])})
    ablatable = [r for r in rows if r["ablatable"]]
    doc = {
        "of": len(rows),
        "ablatable": len(ablatable),
        "no_recommendation_left": sum(not r["kept_recommendation"] for r in ablatable),
        "failures": [
            {"call_id": r["call_id"], "reasons": r["reasons"]}
            for r in rows
            if not r["ablatable"]
        ],
    }
    return {**doc, "matches_design": {k: doc[k] for k in STORED_CHECK} == STORED_CHECK}


# --- dry run -------------------------------------------------------------------


def prepare(config: AppConfig) -> tuple[dict, dict, dict, dict, list[str]]:
    """Seal-checked populations, production views, arm inputs, fact problems."""
    seals = seal_problems()
    if seals:
        raise SystemExit("seals do not verify: " + "; ".join(seals))
    census = xa.load_json(xc.CENSUS)
    deaths = xa.population_deaths(xa.load_json(xa.POPULATIONS), census)
    records = unique_deaths(deaths)
    prod = production(census, records, config)
    return census, deaths, prod, arm_inputs(deaths, prod), fact_problems(census, records)


def call_budget(calls: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Expected calls against the frozen upper bound."""
    bound = xa.load_json(OUT / "design.json")["calls_upper_bound"]
    counts = xa.call_counts(calls)
    p1_p2 = counts["P1"] + counts["P2"]
    return {
        "expected": counts,
        "upper_bound": bound,
        "within_bound": p1_p2 <= bound["p1_p2"]
        and counts["P3"] <= bound["p3"]
        and counts["total"] <= bound["total_approx_max"],
        "model_calls_made": 0,
    }


def plan_doc(config: AppConfig) -> dict[str, Any]:
    """Everything the dry run checks; makes no model call."""
    _, deaths, prod, arms, facts = prepare(config)
    files = input_files(deaths, arms)
    rows = synthetic_rows(deaths, arms)
    calls = xa.call_plan(deaths)
    summary = synthetic_summary(rows)
    doc = {
        "dry_run": True,
        "llm_calls_made": 0,
        "seal_problems": [],
        "fact_problems": facts,
        "stale_inputs": staleness(INPUTS, files),
        "invariant_problems": invariant_problems(deaths, arms, prod),
        "path_problems": path_problems(arms),
        "populations": {k: len(v) for k, v in deaths.items()},
        "calls": call_budget(calls),
        "system_chars": {arm: len(xa.system_prompt(arm)) for arm in xa.ARMS},
        "synthetic_summary": summary,
        "synthetic": rows,
        "stored_ablation": stored_ablation(),
    }
    kept = {
        r["case"]: r["adjusted_hits"] > 0 and r["r5_window_exempt"] < r["validator_hits"]
        for r in rows
        if r["case"].startswith("a3_")
    }
    doc["amendment_3_non_qualifying_hits_kept"] = kept
    doc["dry_run_criterion_met"] = (
        all(summary[a]["adjusted_hits"] == 0 for a in ("M", "C"))
        and not doc["path_problems"]
        and all(kept.values())
    )
    return doc


def _listed(items: Sequence[str]) -> str:
    return "; ".join(items) if items else "none"


def _synthetic_lines(doc: Mapping[str, Any]) -> list[str]:
    lines = [
        "| case | arm | death | map status | production hits | C adj. "
        "| A3 R5 exempt | adjusted | rules "
        "| map-rule violations | value errors | uptake | ablation |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in doc["synthetic"]:
        ablation = r.get("ablation") or {}
        reasons = ", ".join(ablation.get("reasons") or ["-"])
        lines.append(
            f"| {r['case']} | {r['arm']} | `{r['death_id']}` | {r['map_status']} "
            f"| {r['validator_hits']} | {r['c_adjustment']} | {r['r5_window_exempt']} "
            f"| {r['adjusted_hits']} "
            f"| {', '.join(r['hit_rules']) or '-'} "
            f"| {', '.join(r.get('map_rule_violations') or []) or '-'} "
            f"| {len(r.get('value_fidelity_errors') or [])} | {r.get('uptake', '-')} "
            f"| {'yes' if ablation.get('ablatable') else reasons} |"
        )
    return lines


def plan_markdown(doc: Mapping[str, Any]) -> str:
    """Human-readable dry run."""
    calls, stored = doc["calls"], doc["stored_ablation"]
    s = doc["synthetic_summary"]
    lines = [
        "# Experiment J dry run (stop point 3; no model calls)",
        "",
        "- Dry-run criterion (compliant outputs: 0 hits after the pre-registered "
        "C adjustment and the Amendment 3 R5 window exemption; literal block "
        "paths resolve; non-qualifying R5 hits stay counted): "
        f"**{'met' if doc['dry_run_criterion_met'] else 'NOT met'}**",
        "- Amendment 3 non-qualifying R5 hits kept: "
        f"{doc['amendment_3_non_qualifying_hits_kept']}",
        f"- Seals: verified. Census fact mismatches: {_listed(doc['fact_problems'])}",
        f"- Stale inputs: {_listed(doc['stale_inputs'])}",
        f"- Invariants: {_listed(doc['invariant_problems'])}",
        f"- Literal block paths: {_listed(doc['path_problems'])}",
        f"- Populations (deaths): {doc['populations']}",
        f"- Expected calls: {calls['expected']}; upper bound {calls['upper_bound']}; "
        f"within bound: {calls['within_bound']}; "
        f"model calls made: {calls['model_calls_made']}",
        f"- System prompt chars: {doc['system_chars']}",
        f"- Experiment I stored outputs: {stored['ablatable']} of {stored['of']} "
        f"ablatable, {stored['no_recommendation_left']} with no recommendation left; "
        f"failures {stored['failures']}; matches design: {stored['matches_design']}",
        "",
        "## Compliant synthetic outputs",
        "",
        *[f"- {arm}: {json.dumps(s[arm], sort_keys=True)}" for arm in ("M", "C")],
        "",
        "## Every synthetic output",
        "",
        *_synthetic_lines(doc),
    ]
    return "\n".join(lines) + "\n"


def build(config: AppConfig) -> int:
    """Write ``inputs/`` once."""
    if INPUTS.exists():
        raise SystemExit(f"{INPUTS} exists; never overwritten")
    _, deaths, _, arms, facts = prepare(config)
    if facts:
        raise SystemExit("census facts no longer reproduce: " + "; ".join(facts))
    for rel, text in sorted(input_files(deaths, arms).items()):
        path = INPUTS / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    logger.warning("Experiment J inputs written under {}", INPUTS)
    return 0


def plan(config: AppConfig) -> int:
    """Write ``dry_run.json`` / ``dry_run.md``; no model call."""
    doc = plan_doc(config)
    (OUT / "dry_run.json").write_text(json.dumps(doc, indent=1) + "\n", "utf-8")
    text = plan_markdown(doc)
    (OUT / "dry_run.md").write_text(text, "utf-8")
    sys.stdout.write(text[: text.index("## Every")])
    problems = doc["fact_problems"] or doc["stale_inputs"] or doc["invariant_problems"]
    return 1 if problems or not doc["dry_run_criterion_met"] else 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``build`` / ``plan`` (stop point 3)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("build", "plan"))
    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    config = load_config(default_config_path())
    config.coach.roster_evidence = False
    return build(config) if args.command == "build" else plan(config)


if __name__ == "__main__":
    raise SystemExit(main())
