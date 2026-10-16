"""Experiment H stop point 4: join sealed ratings with the key, apply the rule.

Applies the pre-registered decision rule of ``DESIGN.md`` exactly: the safety
gate on arm C, Q1 (correction effect), Q2 (value), then the outcome.
Thresholds come from ``coaching_units_experiment_h``; nothing is tuned here.
Covariates are descriptive only.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import mean
from typing import Any

import coaching_units_experiment_h as xh

RATING_FIELDS = {
    "usefulness": "usefulness_1to5",
    "redundancy": "redundancy_1to5",
    "anchor": "anchor_shows_situation_1to5",
    "missing_context": "missing_context_1to5",
}
ROSTER_USE = "roster_use_correct_incorrect_not_mentioned"


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def load_ratings(out: Path) -> list[dict[str, Any]]:
    """Sealed sheet rows joined with the key on ``output_id``."""
    key = {r["output_id"]: r for r in _csv(out / "rating_key.csv")}
    rows = []
    for r in _csv(out / "rating_sheet.csv"):
        k = key[r["output_id"]]
        rows.append(
            {
                "output_id": r["output_id"],
                "match": k["match"],
                "cid": k["cid"],
                "set": k["set"],
                "change_type": k["change_type"],
                "arm": k["arm"],
                "repeat": int(k["repeat"]),
                **{name: int(r[col]) for name, col in RATING_FIELDS.items()},
                "supported": r["supported_yes_no"].strip().lower() == "yes",
                "roster_use": r[ROSTER_USE].strip().lower(),
                "notes": r.get("notes", ""),
            }
        )
    return rows


def _per_death(rows: Sequence[Mapping[str, Any]], arm: str, field: str) -> dict:
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        if r["arm"] == arm:
            values[(r["match"], r["cid"])].append(float(r[field]))
    return {k: mean(v) for k, v in values.items()}


def safety_gate(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Rule 1 on arm C (all deaths) against arm A."""
    a, c = summary["arms"]["A"]["all"], summary["arms"]["C"]["all"]
    checks = {
        "roster_violations_zero": c["roster_violations"] == 0,
        "count_fidelity_errors_zero": c["count_fidelity_errors"] == 0,
        "blocked_not_above_A": c["blocked"] <= a["blocked"],
        "validator_hits_within_margin": c["validator_hits_per_call"]
        <= a["validator_hits_per_call"] + xh.VALIDATOR_MARGIN,
    }
    figures = {
        arm: {
            k: summary["arms"][arm]["all"][k]
            for k in (
                "roster_violations",
                "count_fidelity_errors",
                "blocked",
                "validator_hits_per_call",
            )
        }
        for arm in ("A", "C")
    }
    return {"passed": all(checks.values()), "checks": checks, "figures": figures}


def q1(
    summary: Mapping[str, Any],
    checks: Sequence[Mapping[str, Any]],
    ratings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Rule 2: fidelity among C mentions, divergence, roster_use correct C >= B."""
    abd = [
        r
        for r in checks
        if r["arm"] == "C" and r["change_type"] == xh.AT_OR_BEFORE_DEATH and r["ok"]
    ]
    mentions = [r for r in abd if r["mentions_roster"]]
    passed = sum(r["count_fidelity"] for r in mentions)
    rate = passed / len(mentions) if mentions else None
    fidelity_ok = bool(mentions) and rate >= xh.FIDELITY_MIN
    div = summary["divergence_b_c"]
    divergence_ok = div["treatment_mean"] > div["control_mean"]
    correct = {
        arm: sum(
            r["roster_use"] == "correct"
            for r in ratings
            if r["arm"] == arm and r["set"] == "treatment"
        )
        for arm in ("B", "C")
    }
    mention_freq = {
        arm: {
            s: summary["arms"][arm][s]["roster_mentions"]
            for s in ("treatment", "control", xh.AT_OR_BEFORE_DEATH)
        }
        for arm in xh.ARMS
    }
    return {
        "passed": fidelity_ok and divergence_ok and correct["C"] >= correct["B"],
        "c_abd_outputs": len(abd),
        "c_abd_mentions": len(mentions),
        "c_abd_fidelity_pass": passed,
        "c_abd_fidelity_rate": rate,
        "fidelity_ok": fidelity_ok,
        "divergence_treatment": div["treatment_mean"],
        "divergence_control": div["control_mean"],
        "divergence_ok": divergence_ok,
        "treatment_roster_correct": correct,
        "roster_correct_ok": correct["C"] >= correct["B"],
        "roster_mentions": mention_freq,
    }


def q2(ratings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Rule 3: usefulness C - A beyond the control noise floor; supported C >= A."""
    use = {arm: _per_death(ratings, arm, "usefulness") for arm in xh.ARMS}
    deaths = sorted(use["A"])
    diff = mean(use["C"][d] - use["A"][d] for d in deaths)
    controls = sorted({(r["match"], r["cid"]) for r in ratings if r["set"] == "control"})
    floor = mean(abs(use["B"][d] - use["C"][d]) for d in controls)
    supported = {
        arm: mean(float(r["supported"]) for r in ratings if r["arm"] == arm)
        for arm in xh.ARMS
    }
    missing = {
        arm: mean(r["missing_context"] for r in ratings if r["arm"] == arm)
        for arm in xh.ARMS
    }
    return {
        "passed": diff > floor and supported["C"] >= supported["A"],
        "usefulness_c_minus_a": round(diff, 4),
        "noise_floor": round(floor, 4),
        "usefulness_ok": diff > floor,
        "supported_rate": {k: round(v, 4) for k, v in supported.items()},
        "supported_ok": supported["C"] >= supported["A"],
        "mean_usefulness": {arm: round(mean(use[arm].values()), 4) for arm in xh.ARMS},
        "mean_missing_context": {k: round(v, 4) for k, v in missing.items()},
    }


def outcome(gate: Mapping, q1_doc: Mapping, q2_doc: Mapping) -> str:
    """Rule 4."""
    if not (gate["passed"] and q1_doc["passed"]):
        return "keep_off_investigate"
    return "recommend_enable" if q2_doc["passed"] else "keep_off_no_value"


def covariates(
    ratings: Sequence[Mapping[str, Any]], selected: Mapping[tuple[str, str], bool]
) -> list[dict[str, Any]]:
    """Mean usefulness and roster_use=correct per arm by change type / selection."""
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in ratings:
        groups[f"change_type={r['change_type']}"].append(r)
        groups[f"C_selected={selected[(r['match'], r['cid'])]}"].append(r)
    out = []
    for name in sorted(groups):
        rows = groups[name]
        out.append(
            {
                "group": name,
                "deaths": len({(r["match"], r["cid"]) for r in rows}),
                **{
                    f"usefulness_{arm}": round(
                        mean(r["usefulness"] for r in rows if r["arm"] == arm), 3
                    )
                    for arm in xh.ARMS
                },
                **{
                    f"roster_correct_{arm}": sum(
                        r["roster_use"] == "correct" for r in rows if r["arm"] == arm
                    )
                    for arm in xh.ARMS
                },
            }
        )
    return out


def analyze(out: Path) -> dict[str, Any]:
    """Full stop point 4 document."""
    summary = json.loads((out / "automatic_summary.json").read_text("utf-8"))
    checks = json.loads((out / "automatic_checks.json").read_text("utf-8"))
    design = json.loads((out / "design.json").read_text("utf-8"))
    ratings = load_ratings(out)
    selected = {(d["match"], d["cid"]): d["C_selected"] for d in design["deaths"]}
    gate, q1_doc, q2_doc = safety_gate(summary), q1(summary, checks, ratings), q2(ratings)
    roster_rows = [r for r in ratings if r["roster_use"] != "not_mentioned"]
    return {
        "ratings": len(ratings),
        "safety_gate": gate,
        "q1": q1_doc,
        "q2": q2_doc,
        "outcome": outcome(gate, q1_doc, q2_doc),
        "covariates": covariates(ratings, selected),
        "rated_roster_use": [
            {
                k: r[k]
                for k in (
                    "output_id",
                    "match",
                    "cid",
                    "set",
                    "arm",
                    "repeat",
                    "roster_use",
                )
            }
            for r in roster_rows
        ],
        "rating_distribution": {
            field: {
                arm: dict(
                    sorted(_count(r[field] for r in ratings if r["arm"] == arm).items())
                )
                for arm in xh.ARMS
            }
            for field in (*RATING_FIELDS, "roster_use")
        },
    }


def _count(values: Any) -> dict:
    out: dict = defaultdict(int)
    for v in values:
        out[str(v)] += 1
    return dict(out)


OUTCOMES = {
    "recommend_enable": "Passes 1, 2 and 3: recommend enabling `coach.roster_evidence`.",
    "keep_off_no_value": (
        "Passes 1 and 2, fails 3: roster evidence is handled correctly but "
        "shows no demonstrated value. Keep `coach.roster_evidence` off."
    ),
    "keep_off_investigate": (
        "Fails 1 or 2: keep off; investigate the coach's roster handling. "
        "The detector is not reopened."
    ),
}


def _yn(flag: bool) -> str:
    return "pass" if flag else "fail"


def _rule_lines(doc: Mapping[str, Any]) -> list[str]:
    g, a, b = doc["safety_gate"], doc["q1"], doc["q2"]
    fa, fc = g["figures"]["A"], g["figures"]["C"]
    m = a["roster_mentions"]
    return [
        f"### 1. Safety gate (arm C): **{_yn(g['passed'])}**",
        "",
        f"- Roster-rule violations C {fc['roster_violations']} (must be 0): "
        f"{_yn(g['checks']['roster_violations_zero'])}",
        f"- Count-fidelity errors C {fc['count_fidelity_errors']} (must be 0): "
        f"{_yn(g['checks']['count_fidelity_errors_zero'])}",
        f"- Blocked outputs C {fc['blocked']} vs A {fa['blocked']}: "
        f"{_yn(g['checks']['blocked_not_above_A'])}",
        f"- Validator hits per call C {fc['validator_hits_per_call']} vs A "
        f"{fa['validator_hits_per_call']} + {xh.VALIDATOR_MARGIN}: "
        f"{_yn(g['checks']['validator_hits_within_margin'])}",
        "",
        f"### 2. Q1 correction effect: **{_yn(a['passed'])}**",
        "",
        f"- C outputs on at_or_before_death deaths mentioning roster: "
        f"{a['c_abd_mentions']} of {a['c_abd_outputs']}; count fidelity "
        f"{a['c_abd_fidelity_pass']} / {a['c_abd_mentions']} "
        f"(>= {xh.FIDELITY_MIN:.0%} required, zero mentions fails): "
        f"{_yn(a['fidelity_ok'])}",
        f"- Mean B-vs-C divergence treatment {a['divergence_treatment']} vs "
        f"control {a['divergence_control']} (treatment must exceed): "
        f"{_yn(a['divergence_ok'])}",
        f"- Treatment outputs rated roster_use=correct: C "
        f"{a['treatment_roster_correct']['C']} vs B "
        f"{a['treatment_roster_correct']['B']} (C >= B): {_yn(a['roster_correct_ok'])}",
        "- Roster-mention frequency (automatic; treatment / control / "
        "at_or_before_death, of 22 / 22 / 10): "
        + "; ".join(
            f"{arm} {v['treatment']} / {v['control']} / {v[xh.AT_OR_BEFORE_DEATH]}"
            for arm, v in m.items()
        ),
        "",
        f"### 3. Q2 value: **{_yn(b['passed'])}**",
        "",
        f"- Mean usefulness C - A over all 22 deaths {b['usefulness_c_minus_a']} "
        f"vs noise floor {b['noise_floor']} (must exceed): {_yn(b['usefulness_ok'])}",
        f"- Supported rate C {b['supported_rate']['C']} vs A "
        f"{b['supported_rate']['A']} (C >= A): {_yn(b['supported_ok'])}",
        "- Mean usefulness A / B / C: "
        + " / ".join(str(v) for v in b["mean_usefulness"].values()),
        "- Mean missing context A / B / C (higher = more missing): "
        + " / ".join(str(v) for v in b["mean_missing_context"].values()),
    ]


def report_markdown(doc: Mapping[str, Any], exposure: str) -> str:
    """``REPORT.md``: the rule as pre-registered, then descriptive covariates."""
    lines = [
        "# Experiment H report: roster evidence in coaching",
        "",
        "Decision rule applied exactly as pre-registered in `DESIGN.md`",
        "(thresholds unchanged). Directional only: one rater, small n. Any",
        "production change is a separate decision.",
        "",
        f"Ratings: {doc['ratings']} rows, sealed before the key was opened. "
        f"Blinding: {exposure}",
        "",
        f"## Outcome: {OUTCOMES[doc['outcome']]}",
        "",
        "## Decision rule",
        "",
        *_rule_lines(doc),
        "",
        "## Covariates (descriptive, not a gate)",
        "",
        "| group | deaths | usefulness A / B / C | roster_use=correct A / B / C |",
        "| --- | --- | --- | --- |",
        *[
            f"| {c['group']} | {c['deaths']} | {c['usefulness_A']} / "
            f"{c['usefulness_B']} / {c['usefulness_C']} | {c['roster_correct_A']} / "
            f"{c['roster_correct_B']} / {c['roster_correct_C']} |"
            for c in doc["covariates"]
        ],
        "",
        "Outputs rated as using roster evidence:",
        "",
        "| output | death | set | arm | repeat | roster_use |",
        "| --- | --- | --- | --- | --- | --- |",
        *[
            f"| {r['output_id']} | {r['match'].removeprefix('en-live-playerview-')} "
            f"`{r['cid']}` | {r['set']} | {r['arm']} | {r['repeat']} | "
            f"{r['roster_use']} |"
            for r in doc["rated_roster_use"]
        ],
    ]
    return "\n".join(lines) + "\n"
