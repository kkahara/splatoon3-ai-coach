"""Experiment I stop point 4: pre-unblinding gate, join, frozen decision rule.

``pregate`` verifies, without opening the key, that the stop-point-2 seal,
the stop-point-3 outputs and automatic artifacts, the key hash and the
sealed rating sheet are unchanged and structurally valid. ``analyze`` runs
the same gate, records the unblinding time and hashes, opens the key once,
validates the join and applies the pre-registered rule of ``DESIGN.md`` with
amendments 1-2 exactly: safety gate on arm M (amendment-1 adjusted
validator count), uptake (at least 14 of 28 eligible M outputs), value
(noise floor 0.1364), then the outcome. Covariates are descriptive only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import mean
from typing import Any

import coaching_units_experiment_i as xi
import coaching_units_experiment_i_results as xres
import coaching_units_experiment_i_run as xr
from loguru import logger

from splatoon3_ai_coach.coach.llm_runs import utc_timestamp

RUN_COMMIT = "4f656e9"
SHEET_COMMIT = "c20b7cb"
SHEET_SHA256 = "b5db14eec2fdfe754c93a4e9019b9df79c317f3ebdce1bf7324a0f00d5d29616"
KEY_SHA256 = "d368951c90c2467eae774aeccc149155e147cf231ace4e324b279d68e0316eca"
RUN_ARTIFACTS = (
    "outputs",
    "RUN.md",
    "automatic_checks.json",
    "automatic_summary.json",
    "automatic_summary.md",
    "rating_key.csv",
)
H_DESIGN = xi.REPORT_DIR / "coaching_units_experiment_h" / "design.json"
RATING_FIELDS = {
    "usefulness": "usefulness_1to5",
    "redundancy": "redundancy_1to5",
    "anchor": "anchor_shows_situation_1to5",
    "missing_context": "missing_context_1to5",
}
MAP_USE = "map_use_correct_incorrect_not_mentioned"
FIXED = ("output_id", "match", "death", "assessment", "recommendations")


# --- pre-unblinding gate (never reads the key's contents) ---------------------


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(text: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    return list(reader.fieldnames or ()), rows


def sheet_problems(filled_text: str, blank_text: str) -> list[str]:
    """Structure, unchanged fixed columns and valid rating values."""
    head_f, filled = _rows(filled_text)
    head_b, blank = _rows(blank_text)
    problems = []
    if head_f != head_b:
        problems.append("header differs from the blank sheet")
    if len(filled) != len(blank) or len(filled) != xr.EXPECTED_CALLS:
        problems.append(f"{len(filled)} rows, expected {xr.EXPECTED_CALLS}")
    for a, b in zip(blank, filled, strict=False):
        if any(a[k] != b.get(k) for k in FIXED):
            problems.append(f"{b.get('output_id')}: fixed column changed")
    for r in filled:
        bad = [k for k in RATING_FIELDS.values() if r.get(k) not in set("12345")]
        if r.get("supported_yes_no") not in ("yes", "no"):
            bad.append("supported_yes_no")
        if r.get(MAP_USE) not in xi.MAP_USE_VALUES:
            bad.append(MAP_USE)
        if None in r:
            bad.append("extra fields")
        if bad:
            problems.append(f"{r.get('output_id')}: invalid {bad}")
    return problems


def _artifact_problems(out: Path) -> list[str]:
    paths = [xr._rel(out / a) for a in RUN_ARTIFACTS]
    problems = []
    if xr._git("diff", "--quiet", RUN_COMMIT, "--", *paths).returncode != 0:
        problems.append(f"run outputs / automatic artifacts differ from {RUN_COMMIT}")
    tracked = set(
        xr._git("ls-tree", "-r", "--name-only", RUN_COMMIT, "--", *paths).stdout.split()
    )
    on_disk = xr._on_disk(paths)
    if on_disk != tracked:
        problems.append(
            f"run file set differs from {RUN_COMMIT}: extra {sorted(on_disk - tracked)}, "
            f"missing {sorted(tracked - on_disk)}"
        )
    return problems


def _call_problems(out: Path) -> list[str]:
    calls = sorted((out / "outputs" / "calls").glob("*"))
    cells = xr.expected_cells(xr.load_manifest(out))
    problems = []
    if {p.name for p in calls} != {f"{c.call_id}.json" for c in cells}:
        problems.append("call records are not exactly the 88 expected cells")
    if (out / "outputs" / "failed_attempts").exists():
        problems.append("failed attempts exist")
    log = (out / "outputs" / "run_log.jsonl").read_text("utf-8").splitlines()
    if len(log) != 2 * xr.EXPECTED_CALLS:
        problems.append(f"run log has {len(log)} lines, expected {2 * xr.EXPECTED_CALLS}")
    return problems


def _sheet_check(out: Path) -> list[str]:
    sheet = out / "rating_sheet.csv"
    rel = xr._rel(sheet)
    problems = []
    if _sha(sheet) != SHEET_SHA256:
        problems.append("rating_sheet.csv hash differs from the sealed hash")
    if xr._git("diff", "--quiet", SHEET_COMMIT, "--", rel).returncode != 0:
        problems.append(f"rating_sheet.csv differs from {SHEET_COMMIT}")
    blank = xr._git("show", f"{RUN_COMMIT}:{rel}").stdout
    return problems + sheet_problems(sheet.read_text("utf-8"), blank)


def _repo_check() -> tuple[list[str], dict[str, Any]]:
    head = xr._git("rev-parse", "HEAD").stdout.strip()
    porcelain = xr._git("status", "--porcelain").stdout.splitlines()
    problems = []
    if xr._git("merge-base", "--is-ancestor", SHEET_COMMIT, "HEAD").returncode != 0:
        problems.append(f"{SHEET_COMMIT} is not an ancestor of HEAD")
    stray = [line for line in porcelain if not line.endswith("docs/follow-ups/")]
    if stray:
        problems.append(f"working tree not clean: {stray}")
    return problems, {"head_commit": head, "porcelain": porcelain}


def pregate(out: Path) -> dict[str, Any]:
    """Every pre-unblinding check; the key is hashed, never parsed."""
    repo_problems, repo = _repo_check()
    key_ok = _sha(out / "rating_key.csv") == KEY_SHA256
    checks = [
        xr._check("repository_state", repo_problems, repo),
        xr._check("stop_point_2_seal_unchanged", xr.seal_problems(out)),
        xr._check(
            "amendments_unchanged",
            []
            if xr.load_manifest(out)["amendments"] == list(xi.AMENDMENTS)
            else ["manifest amendments differ from the sealed tool"],
        ),
        xr._check(
            "run_outputs_and_automatic_artifacts_unchanged", _artifact_problems(out)
        ),
        xr._check("no_additional_model_calls", _call_problems(out)),
        xr._check("rating_key_hash", [] if key_ok else ["rating_key.csv hash differs"]),
        xr._check("rating_sheet_sealed_and_valid", _sheet_check(out)),
    ]
    return {
        "checked_at": utc_timestamp(),
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
        "sheet_sha256": _sha(out / "rating_sheet.csv"),
        "key_sha256": _sha(out / "rating_key.csv"),
    }


# --- join -------------------------------------------------------------------------


def _death_label(cid: str) -> str:
    return f"{float(cid.split(':', 1)[1]):.1f}s"


def join(
    sheet_text: str, key_text: str, manifest: Mapping[str, Any]
) -> tuple[list, list]:
    """Rated rows joined to the key on ``output_id``; returns (rows, problems)."""
    _, sheet = _rows(sheet_text)
    _, key_rows = _rows(key_text)
    key = {r["output_id"]: r for r in key_rows}
    problems = []
    if len(key) != len(key_rows) or len({r["output_id"] for r in sheet}) != len(sheet):
        problems.append("duplicate output_id")
    if set(key) != {r["output_id"] for r in sheet}:
        problems.append("sheet and key output_ids differ")
    cells = xr.expected_cells(manifest)
    cover = xres.coverage(cells, key_rows)
    problems += [f"key {k}: {v}" for k, v in cover.items() if v]
    eligible = {(c.match, c.cid): c.eligible for c in cells}
    rows = []
    for r in sheet:
        k = key.get(r["output_id"])
        if k is None:
            continue
        if (r["match"], r["death"]) != (
            k["match"].removeprefix(xi.MATCH_PREFIX),
            _death_label(k["cid"]),
        ):
            problems.append(f"{r['output_id']}: sheet death does not match key")
        if (k["eligible"] == "True") != eligible.get((k["match"], k["cid"])):
            problems.append(f"{r['output_id']}: key eligibility differs from manifest")
        rows.append(_rated(r, k))
    return rows, problems


def _rated(r: Mapping[str, str], k: Mapping[str, str]) -> dict[str, Any]:
    return {
        "output_id": r["output_id"],
        "call_id": k["call_id"],
        "match": k["match"],
        "cid": k["cid"],
        "eligible": k["eligible"] == "True",
        "null_block": (k["match"], k["cid"]) in xres.NULL_BLOCK,
        "arm": k["arm"],
        "repeat": int(k["repeat"]),
        **{name: int(r[col]) for name, col in RATING_FIELDS.items()},
        "supported": r["supported_yes_no"] == "yes",
        "map_use": r[MAP_USE],
    }


# --- frozen decision rule ----------------------------------------------------------


def _per_death(rows: Sequence[Mapping[str, Any]], arm: str, field: str) -> dict:
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        if r["arm"] == arm:
            values[(r["match"], r["cid"])].append(float(r[field]))
    return {k: mean(v) for k, v in values.items()}


def _rate(hits: int, group: Mapping[str, Any]) -> float:
    return hits / (group["calls"] - group["failed"])


def safety_gate(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Rule 1 on arm M (all calls) against arm A; amendment 1: adjusted count."""
    a, m = summary["arms"]["A"]["all"], summary["arms"]["M"]["all"]
    adj = {arm: _rate(g["validator_hits_adjusted"], g) for arm, g in (("A", a), ("M", m))}
    prod = {
        arm: _rate(g["validator_hits_production"], g) for arm, g in (("A", a), ("M", m))
    }
    checks = {
        "map_rule_violations_zero": m["map_rule_violations"] == 0,
        "value_fidelity_errors_zero": m["value_fidelity_errors"] == 0,
        "blocked_not_above_A": m["blocked"] <= a["blocked"],
        "adjusted_validator_hits_within_margin": adj["M"]
        <= adj["A"] + xi.VALIDATOR_MARGIN,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "figures": {
            arm: {
                "calls": g["calls"],
                "map_rule_violations": g["map_rule_violations"],
                "value_fidelity_errors": g["value_fidelity_errors"],
                "blocked": g["blocked"],
                "validator_hits_production": g["validator_hits_production"],
                "validator_hits_adjusted": g["validator_hits_adjusted"],
                "adjusted_per_call": round(adj[arm], 4),
                "production_per_call": round(prod[arm], 4),
            }
            for arm, g in (("A", a), ("M", m))
        },
        "production_count_view_not_the_gate": {
            "within_margin": prod["M"] <= prod["A"] + xi.VALIDATOR_MARGIN
        },
    }


def uptake(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Rule 2 (amendment 2): at least 14 of 28 eligible M outputs mention the map."""
    g = summary["arms"]["M"]["eligible"]
    ok = g["calls"] == xi.UPTAKE_OF and g["map_mentions"] >= xi.UPTAKE_MIN
    return {
        "passed": ok,
        "mentions": g["map_mentions"],
        "of": g["calls"],
        "required": f">= {xi.UPTAKE_MIN} of {xi.UPTAKE_OF}",
    }


def value(ratings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Rule 3: eligible usefulness M - A > floor; supported M >= A; no harm."""
    use = {arm: _per_death(ratings, arm, "usefulness") for arm in xi.ARMS}
    eligible = sorted({(r["match"], r["cid"]) for r in ratings if r["eligible"]})
    deaths = sorted(use["A"])
    diff_e = mean(use["M"][d] - use["A"][d] for d in eligible)
    diff_all = mean(use["M"][d] - use["A"][d] for d in deaths)
    sup = {
        arm: mean(float(r["supported"]) for r in ratings if r["arm"] == arm)
        for arm in xi.ARMS
    }
    checks = {
        "eligible_usefulness_gain_exceeds_floor": diff_e > xi.NOISE_FLOOR,
        "supported_rate_M_not_lower": sup["M"] >= sup["A"],
        "all_deaths_no_harm": diff_all >= -xi.NOISE_FLOOR,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "noise_floor": xi.NOISE_FLOOR,
        "eligible_deaths": len(eligible),
        "all_deaths": len(deaths),
        "eligible_usefulness_m_minus_a": round(diff_e, 4),
        "all_usefulness_m_minus_a": round(diff_all, 4),
        "supported_rate": {k: round(v, 4) for k, v in sup.items()},
        "eligible_mean_usefulness": {
            arm: round(mean(use[arm][d] for d in eligible), 4) for arm in xi.ARMS
        },
        "all_mean_usefulness": {
            arm: round(mean(use[arm][d] for d in deaths), 4) for arm in xi.ARMS
        },
    }


OUTCOMES = {
    "passes_1_2_3": (
        "Passes 1, 2 and 3: recommend a production change (separate decision: "
        "derived field in the coaching layer, prompt section, claim)."
    ),
    "passes_1_2_fails_3": (
        "Passes 1 and 2, fails 3: handled correctly but no demonstrated value; "
        "production unchanged."
    ),
    "fails_1_or_2": "Fails 1 or 2: production unchanged; investigate.",
}


def outcome(gate: Mapping, up: Mapping, val: Mapping) -> str:
    """Rule 4."""
    if not (gate["passed"] and up["passed"]):
        return "fails_1_or_2"
    return "passes_1_2_3" if val["passed"] else "passes_1_2_fails_3"


# --- descriptive -------------------------------------------------------------------


def _subset(rows: Sequence[Mapping[str, Any]], name: str) -> list[Mapping[str, Any]]:
    return xres._subset(rows, name)


def rating_means(ratings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per arm and subset: n, mean of each rating, supported rate, map_use counts."""
    out: dict[str, Any] = {}
    for arm in xi.ARMS:
        out[arm] = {}
        for s in xres.SUBSETS:
            rows = [r for r in _subset(ratings, s) if r["arm"] == arm]
            out[arm][s] = {
                "n": len(rows),
                **{f: round(mean(r[f] for r in rows), 4) for f in RATING_FIELDS},
                "supported_rate": round(mean(float(r["supported"]) for r in rows), 4),
                "map_use": dict(sorted(Counter(r["map_use"] for r in rows).items())),
            }
    return out


def repeat_consistency(ratings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Descriptive: per arm, mean over deaths of |usefulness r1 - r2|."""
    out = {}
    for arm in xi.ARMS:
        by: dict[tuple[str, str], dict[int, int]] = defaultdict(dict)
        for r in ratings:
            if r["arm"] == arm:
                by[(r["match"], r["cid"])][r["repeat"]] = r["usefulness"]
        diffs = [abs(v[1] - v[2]) for v in by.values()]
        out[arm] = {
            "mean_abs_r1_minus_r2": round(mean(diffs), 4),
            "deaths_identical": sum(d == 0 for d in diffs),
            "deaths": len(diffs),
        }
    return out


def _band(seconds: float) -> str:
    return "10-30 s" if seconds <= 30 else "over 30 s"


def covariates(
    ratings: Sequence[Mapping[str, Any]], design: Mapping[str, Any], selected: Mapping
) -> list[dict[str, Any]]:
    """Mean usefulness and map_use=correct per arm by band / production selection."""
    seconds = {
        (xi.MATCH_PREFIX + d["match"], d["scenario_id"]): d["seconds_back_in_play"]
        for d in design["deaths"]
    }
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in ratings:
        key = (r["match"], r["cid"])
        if r["eligible"]:
            groups[f"eligible, back in play {_band(seconds[key])}"].append(r)
        groups[f"production selected={selected.get(key)}"].append(r)
    return [
        {
            "group": name,
            "deaths": len({(r["match"], r["cid"]) for r in rows}),
            **{
                f"usefulness_{arm}": round(
                    mean(r["usefulness"] for r in rows if r["arm"] == arm), 3
                )
                for arm in xi.ARMS
            },
            **{
                f"map_correct_{arm}": sum(
                    r["map_use"] == "correct" for r in rows if r["arm"] == arm
                )
                for arm in xi.ARMS
            },
        }
        for name, rows in sorted(groups.items())
    ]


def per_death(ratings: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per death: eligibility and mean usefulness / supported per arm."""
    use = {arm: _per_death(ratings, arm, "usefulness") for arm in xi.ARMS}
    sup = {arm: _per_death(ratings, arm, "supported") for arm in xi.ARMS}
    flags = {(r["match"], r["cid"]): (r["eligible"], r["null_block"]) for r in ratings}
    return [
        {
            "match": k[0].removeprefix(xi.MATCH_PREFIX),
            "cid": k[1],
            "eligible": flags[k][0],
            "null_block": flags[k][1],
            **{f"usefulness_{a}": use[a][k] for a in xi.ARMS},
            **{f"supported_{a}": sup[a][k] for a in xi.ARMS},
        }
        for k in sorted(use["A"])
    ]


def path_anomaly(out: Path) -> dict[str, Any]:
    """Unresolved evidence paths by arm: ``primary_context.map_awareness.*`` vs other."""
    counts: Counter = Counter()
    for record in xres.load_records(out):
        for hit in record["validation"]["unresolved_evidence_paths"]:
            path = str(hit.get("path") if isinstance(hit, Mapping) else hit)
            kind = (
                "primary_context.map_awareness.*"
                if path.startswith("primary_context.map_awareness.")
                else "other"
            )
            counts[f"{record['arm']} {kind}"] += 1
    return dict(sorted(counts.items()))


def _selected() -> dict[tuple[str, str], bool]:
    design = json.loads(H_DESIGN.read_text("utf-8"))
    return {(d["match"], d["cid"]): d["A_selected"] for d in design["deaths"]}


def _distribution(ratings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        f: {
            arm: dict(
                sorted(Counter(str(r[f]) for r in ratings if r["arm"] == arm).items())
            )
            for arm in xi.ARMS
        }
        for f in (*RATING_FIELDS, "supported", "map_use")
    }


def analysis_doc(out: Path, ratings: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Rule, outcome and descriptive tables from the joined ratings."""
    summary = json.loads((out / "automatic_summary.json").read_text("utf-8"))
    design = json.loads((out / "design.json").read_text("utf-8"))
    gate, up, val = safety_gate(summary), uptake(summary), value(ratings)
    return {
        "ratings": len(ratings),
        "safety_gate": gate,
        "uptake": up,
        "value": val,
        "outcome": outcome(gate, up, val),
        "automatic_summary_arms": summary["arms"],
        "path_anomaly": path_anomaly(out),
        "rating_means": rating_means(ratings),
        "repeat_consistency": repeat_consistency(ratings),
        "covariates": covariates(ratings, design, _selected()),
        "per_death": per_death(ratings),
        "rating_distribution": _distribution(ratings),
    }


# --- report ------------------------------------------------------------------------


def _yn(flag: bool) -> str:
    return "pass" if flag else "fail"


def _gate_lines(doc: Mapping[str, Any]) -> list[str]:
    g, up, v = doc["safety_gate"], doc["uptake"], doc["value"]
    fa, fm = g["figures"]["A"], g["figures"]["M"]
    c = g["checks"]
    return [
        f"### 1. Safety gate (arm M, all {fm['calls']} calls): **{_yn(g['passed'])}**",
        "",
        f"- Map-rule violations M {fm['map_rule_violations']} (must be 0): "
        f"{_yn(c['map_rule_violations_zero'])}",
        f"- Value-fidelity errors M {fm['value_fidelity_errors']} (must be 0): "
        f"{_yn(c['value_fidelity_errors_zero'])}",
        f"- Blocked outputs M {fm['blocked']} vs A {fa['blocked']}: "
        f"{_yn(c['blocked_not_above_A'])}",
        f"- Validator hits per call, amendment-1 adjusted (the gate): M "
        f"{fm['adjusted_per_call']} ({fm['validator_hits_adjusted']} / {fm['calls']}) "
        f"vs A {fa['adjusted_per_call']} + {xi.VALIDATOR_MARGIN}: "
        f"{_yn(c['adjusted_validator_hits_within_margin'])}",
        f"- Production count (reported, not the gate): M {fm['production_per_call']} "
        f"({fm['validator_hits_production']} / {fm['calls']}) vs A "
        f"{fa['production_per_call']} + {xi.VALIDATOR_MARGIN}: "
        f"{_yn(g['production_count_view_not_the_gate']['within_margin'])}",
        "",
        f"### 2. Uptake: **{_yn(up['passed'])}**",
        "",
        f"- Eligible arm-M outputs mentioning the map check: {up['mentions']} of "
        f"{up['of']} (required {up['required']}). Uptake is not success by itself.",
        "",
        f"### 3. Value: **{_yn(v['passed'])}**",
        "",
        f"- Eligible deaths ({v['eligible_deaths']}): mean usefulness M - A "
        f"{v['eligible_usefulness_m_minus_a']} (M {v['eligible_mean_usefulness']['M']}, "
        f"A {v['eligible_mean_usefulness']['A']}) vs floor {v['noise_floor']} "
        f"(must exceed): {_yn(v['checks']['eligible_usefulness_gain_exceeds_floor'])}",
        f"- Supported rate, all {v['all_deaths']} deaths: M {v['supported_rate']['M']} "
        f"vs A {v['supported_rate']['A']} (M >= A): "
        f"{_yn(v['checks']['supported_rate_M_not_lower'])}",
        f"- All {v['all_deaths']} deaths: mean usefulness M - A "
        f"{v['all_usefulness_m_minus_a']} (M {v['all_mean_usefulness']['M']}, "
        f"A {v['all_mean_usefulness']['A']}) vs -{v['noise_floor']} (must be >=): "
        f"{_yn(v['checks']['all_deaths_no_harm'])}",
    ]


def _means_lines(doc: Mapping[str, Any]) -> list[str]:
    lines = [
        "| arm | subset | n | usefulness | redundancy | anchor | missing context "
        "| supported | map_use |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for arm, groups in doc["rating_means"].items():
        for s, g in groups.items():
            use = ", ".join(f"{k} {n}" for k, n in g["map_use"].items())
            lines.append(
                f"| {arm} | {s} | {g['n']} | {g['usefulness']} | {g['redundancy']} | "
                f"{g['anchor']} | {g['missing_context']} | {g['supported_rate']} "
                f"| {use} |"
            )
    return lines


def _descriptive_lines(doc: Mapping[str, Any]) -> list[str]:
    rc = doc["repeat_consistency"]
    return [
        "Rating means (higher missing context = more missing):",
        "",
        *_means_lines(doc),
        "",
        "Repeat consistency (not the frozen floor; the floor is Experiment H's "
        "measured B-vs-C value): mean over deaths of |usefulness r1 - r2|: "
        + "; ".join(
            f"{arm} {v['mean_abs_r1_minus_r2']} ({v['deaths_identical']} of "
            f"{v['deaths']} deaths identical)"
            for arm, v in rc.items()
        ),
        "",
        "| group | deaths | usefulness A / M | map_use=correct A / M |",
        "| --- | --- | --- | --- |",
        *[
            f"| {c['group']} | {c['deaths']} | {c['usefulness_A']} / {c['usefulness_M']} "
            f"| {c['map_correct_A']} / {c['map_correct_M']} |"
            for c in doc["covariates"]
        ],
        "",
        "Production selection is Experiment H's `A_selected` (roster off).",
        "",
        "| match | death | eligible | usefulness A / M | supported A / M |",
        "| --- | --- | --- | --- | --- |",
        *[
            f"| {d['match']} | `{d['cid']}` | "
            f"{'null block' if d['null_block'] else ('Y' if d['eligible'] else 'N')} | "
            f"{d['usefulness_A']} / {d['usefulness_M']} | "
            f"{d['supported_A']} / {d['supported_M']} |"
            for d in doc["per_death"]
        ],
    ]


def report_markdown(doc: Mapping[str, Any], unblinding: Mapping[str, Any]) -> str:
    """``REPORT.md``: rule as pre-registered, anomaly, then descriptive tables."""
    anomaly = ", ".join(f"{k} {n}" for k, n in doc["path_anomaly"].items())
    lines = [
        "# Experiment I report: map-check coaching",
        "",
        "Decision rule applied exactly as pre-registered in `DESIGN.md` with",
        "amendments 1-2 (thresholds and noise floor unchanged). Directional only:",
        "one rater, small n. Any production change is a separate decision.",
        "",
        f"Ratings: {doc['ratings']} rows sealed at `{SHEET_COMMIT}` (sha256 "
        f"`{unblinding['sheet_sha256']}`) before the key was opened. Key sha256 "
        f"`{unblinding['key_sha256']}`. Unblinded {unblinding['unblinded_at']}; "
        "pre-unblinding gate and join checks passed (`analysis.json`).",
        "",
        f"## Outcome: {OUTCOMES[doc['outcome']]}",
        "",
        "## Pre-registered decision rule",
        "",
        *_gate_lines(doc),
        "",
        "## Automatic-validator anomaly (recorded, not corrected)",
        "",
        "Unresolved evidence paths by arm and path family: "
        f"{anomaly}. The model cited the arm-M block as "
        "`primary_context.map_awareness.*`; the block is top level in the view, "
        "so the production validator reports `unknown_block`. These hits are "
        "part of both the production and the amendment-1 adjusted counts as "
        "frozen; the validator was not changed or re-run.",
        "",
        "## Descriptive (not gates)",
        "",
        *_descriptive_lines(doc),
    ]
    return "\n".join(lines) + "\n"


def write_inventory(out: Path) -> Path:
    """``SHA256SUMS``: every experiment file except itself."""
    target = out / "SHA256SUMS"
    files = sorted(p for p in out.rglob("*") if p.is_file() and p != target)
    target.write_text(
        "".join(f"{_sha(p)}  {p.relative_to(out)}\n" for p in files), encoding="utf-8"
    )
    return target


def analyze(out: Path) -> int:
    """Gate, unblind once, join, apply the rule; never overwrites."""
    if (out / "analysis.json").exists() or (out / "REPORT.md").exists():
        raise SystemExit("analysis.json or REPORT.md exists; stop point 4 runs once")
    gate = pregate(out)
    if not gate["passed"]:
        failed = [c for c in gate["checks"] if not c["ok"]]
        raise SystemExit(f"pre-unblinding gate failed; key not opened: {failed}")
    unblinding = {
        "unblinded_at": utc_timestamp(),
        "sheet_sha256": gate["sheet_sha256"],
        "key_sha256": gate["key_sha256"],
    }
    key_text = (out / "rating_key.csv").read_text("utf-8")
    sheet_text = (out / "rating_sheet.csv").read_text("utf-8")
    ratings, problems = join(sheet_text, key_text, xr.load_manifest(out))
    doc = {
        "pre_unblinding_gate": gate,
        "unblinding": unblinding,
        "join_problems": problems,
    }
    if problems or len(ratings) != xr.EXPECTED_CALLS:
        xr._write_json_atomic(out / "unblinding_failed.json", doc)
        raise SystemExit(f"join failed; no analysis: {problems}")
    doc.update(analysis_doc(out, ratings))
    xr._write_json_atomic(out / "analysis.json", doc)
    (out / "REPORT.md").write_text(report_markdown(doc, unblinding), encoding="utf-8")
    write_inventory(out)
    sys.stdout.write(report_markdown(doc, unblinding))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``pregate`` (key not opened) / ``analyze`` (unblinds once)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("pregate", "analyze"))
    parser.add_argument("--out", type=Path, default=xi.OUT_DIR)
    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    if args.command == "analyze":
        return analyze(args.out)
    gate = pregate(args.out)
    sys.stdout.write(
        json.dumps({c["name"]: c["problems"] or "ok" for c in gate["checks"]}, indent=1)
        + "\n"
    )
    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
