"""Experiment H stop point 3: frozen automatic checks, summary, blind sheet.

The frozen text checks (``coaching_units_experiment_h``) read the text a user
sees on the result page and on the blind sheet: the assessment and the
recommendations. The validator runs on the whole reply against the arm's own
view, as in production. Nothing here applies the decision rule or reads
human ratings.
"""

from __future__ import annotations

import csv
import io
import json
import random
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import mean
from typing import Any

import coaching_units_experiment_d as xd
import coaching_units_experiment_h as xh
import coaching_units_experiment_h_run as xr

SET_ORDER = ("treatment", "control")


def output_text(assessment: Mapping[str, Any] | None) -> str:
    """Assessment plus recommendations, one per line (what the user sees)."""
    if not assessment:
        return ""
    recs = [str(r).strip() for r in assessment.get("recommendations") or ()]
    return "\n".join([str(assessment.get("assessment") or "").strip(), *recs])


def check_record(record: Mapping[str, Any], view: Mapping[str, Any]) -> dict[str, Any]:
    """Frozen checks for one call record against its own arm's view."""
    base = {k: record[k] for k in ("call_id", "match", "cid", "set", "change_type")}
    base.update(arm=record["arm"], repeat=record["repeat"])
    validation = record.get("validation")
    if validation is None:
        return {
            **base,
            "ok": False,
            "call_error": record.get("call_error"),
            "parse_error": record.get("parse_error"),
        }
    text = output_text(record["assessment"])
    return {
        **base,
        "ok": True,
        "blocked": bool(validation["blocked"]),
        "validator_hits": xd._violations(validation),
        "mentions_roster": xh.mentions_roster(text),
        "cited_pairs": sorted(xh.cited_pairs(text)),
        "cited_states": sorted(xh.cited_states(text)),
        "count_fidelity": xh.count_fidelity(text, view),
        "roster_violations": xh.roster_violations(text),
    }


def automatic_checks(out: Path) -> list[dict[str, Any]]:
    """Checks for every current record (arm-identified)."""
    inputs = out / "inputs"
    rows = []
    for record in xr.load_records(out):
        prompt = (inputs / record["file"]).read_text(encoding="utf-8")
        view, _ = xd.split_prompt(prompt)
        rows.append(check_record(record, view))
    return rows


def _group(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r["ok"]]
    mention = [r for r in ok if r["mentions_roster"]]
    violations = [v for r in ok for v in r["roster_violations"]]
    return {
        "calls": len(rows),
        "failed": len(rows) - len(ok),
        "blocked": sum(r["blocked"] for r in ok),
        "validator_hits": sum(r["validator_hits"] for r in ok),
        "validator_hits_per_call": (
            round(sum(r["validator_hits"] for r in ok) / len(ok), 3) if ok else None
        ),
        "roster_mentions": len(mention),
        "count_fidelity_errors": sum(not r["count_fidelity"] for r in ok),
        "fidelity_pass_among_mentions": sum(r["count_fidelity"] for r in mention),
        "roster_violations": len(violations),
        "roster_violation_codes": {
            c: violations.count(c) for c in sorted(set(violations))
        },
    }


def divergence_rows(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per death: B-vs-C text divergence, repeat r with repeat r, mean over r."""
    texts = {
        xr.cell_key(r): output_text(r.get("assessment"))
        for r in records
        if r.get("assessment")
    }
    deaths = {(r["match"], r["cid"]): (r["set"], r["change_type"]) for r in records}
    out = []
    for (match, cid), (group, change) in deaths.items():
        values = [
            xh.divergence(texts[(match, cid, "B", n)], texts[(match, cid, "C", n)])
            for n in range(1, xh.REPEATS + 1)
            if (match, cid, "B", n) in texts and (match, cid, "C", n) in texts
        ]
        out.append(
            {
                "match": match,
                "cid": cid,
                "set": group,
                "change_type": change,
                "pairs": len(values),
                "divergence": round(mean(values), 4) if values else None,
            }
        )
    return out


def _mean_div(rows: Sequence[Mapping[str, Any]]) -> float | None:
    values = [r["divergence"] for r in rows if r["divergence"] is not None]
    return round(mean(values), 4) if values else None


def summarize(
    checks: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
    cover: Mapping[str, list],
) -> dict[str, Any]:
    """Automatic per-arm and per-set figures; no gate verdicts."""
    by = {
        arm: {
            "all": _group([r for r in checks if r["arm"] == arm]),
            **{
                s: _group([r for r in checks if r["arm"] == arm and r["set"] == s])
                for s in SET_ORDER
            },
            xh.AT_OR_BEFORE_DEATH: _group(
                [
                    r
                    for r in checks
                    if r["arm"] == arm and r["change_type"] == xh.AT_OR_BEFORE_DEATH
                ]
            ),
        }
        for arm in xh.ARMS
    }
    div = divergence_rows(records)
    return {
        "decision_rule_applied": False,
        "text_scope": "assessment + recommendations",
        "coverage": cover,
        "arms": by,
        "divergence_b_c": {
            "treatment_mean": _mean_div([r for r in div if r["set"] == "treatment"]),
            "control_mean": _mean_div([r for r in div if r["set"] == "control"]),
            "at_or_before_death_mean": _mean_div(
                [r for r in div if r["change_type"] == xh.AT_OR_BEFORE_DEATH]
            ),
            "window_only_mean": _mean_div(
                [r for r in div if r["change_type"] == xh.WINDOW_ONLY]
            ),
            "deaths": div,
        },
    }


def blind_sheet(
    records: Sequence[Mapping[str, Any]], seed: int = xh.RATING_SEED
) -> tuple[str, str]:
    """(sheet CSV, key CSV): one row per cell; the sheet names no arm or repeat."""
    rows = sorted(records, key=lambda r: r["call_id"])
    random.Random(seed).shuffle(rows)
    sheet, key = io.StringIO(), io.StringIO()
    sheet_w = csv.writer(sheet, lineterminator="\n")
    key_w = csv.writer(key, lineterminator="\n")
    sheet_w.writerow(
        (
            "output_id",
            "match",
            "death",
            "assessment",
            "recommendations",
            *xr.SHEET_COLUMNS,
        )
    )
    key_w.writerow(
        ("output_id", "call_id", "match", "cid", "set", "change_type", "arm", "repeat")
    )
    for n, r in enumerate(rows, start=1):
        output_id = f"output_{n:03d}"
        a = r.get("assessment")
        if a:
            assessment = a["assessment"].strip()
            recs = " || ".join(s.strip() for s in a["recommendations"])
        else:
            assessment = "(no rateable output: provider error or unparseable reply)"
            recs = ""
        sheet_w.writerow(
            (
                output_id,
                r["match"].removeprefix("en-live-playerview-"),
                f"{float(r['cid'].split(':', 1)[1]):.1f}s",
                assessment,
                recs,
                *[""] * len(xr.SHEET_COLUMNS),
            )
        )
        key_w.writerow(
            (
                output_id,
                r["call_id"],
                r["match"],
                r["cid"],
                r["set"],
                r["change_type"],
                r["arm"],
                r["repeat"],
            )
        )
    return sheet.getvalue(), key.getvalue()


def summary_markdown(doc: Mapping[str, Any]) -> str:
    """Arm-identified automatic summary (read after ratings are sealed)."""
    cover = doc["coverage"]
    lines = [
        "# Experiment H automatic summary (frozen checks; decision rule not applied)",
        "",
        "Arm-identified. Automatic checks only; no human rating and no gate",
        f"verdict. Text checks read {doc['text_scope']}.",
        "",
        f"- Coverage: missing {len(cover['missing'])}, duplicate "
        f"{len(cover['duplicate'])}, unexpected {len(cover['unexpected'])}",
        "",
        "| arm | subset | calls | failed | blocked | validator hits (per call) "
        "| roster mentions | fidelity errors | fidelity pass / mentions "
        "| roster-rule violations |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for arm, groups in doc["arms"].items():
        for name, g in groups.items():
            codes = ", ".join(f"{c} {n}" for c, n in g["roster_violation_codes"].items())
            lines.append(
                f"| {arm} | {name} | {g['calls']} | {g['failed']} | {g['blocked']} | "
                f"{g['validator_hits']} ({g['validator_hits_per_call']}) | "
                f"{g['roster_mentions']} | {g['count_fidelity_errors']} | "
                f"{g['fidelity_pass_among_mentions']} / {g['roster_mentions']} | "
                f"{g['roster_violations']}{f' ({codes})' if codes else ''} |"
            )
    d = doc["divergence_b_c"]
    lines += [
        "",
        "B-vs-C divergence (1 - difflib ratio, repeat r with r, mean per death):",
        f"treatment {d['treatment_mean']}, control {d['control_mean']}, "
        f"at_or_before_death {d['at_or_before_death_mean']}, "
        f"window_only {d['window_only_mean']}.",
    ]
    return "\n".join(lines) + "\n"


def write_summary(out: Path) -> dict[str, Any]:
    """``automatic_checks.json``, ``automatic_summary.json`` / ``.md``."""
    records = xr.load_records(out)
    cells = xr.expected_cells(json.loads((out / "inputs" / "manifest.json").read_text()))
    checks = automatic_checks(out)
    doc = summarize(checks, records, xr.coverage(cells, records))
    xr._write_json(out / "automatic_checks.json", checks)
    xr._write_json(out / "automatic_summary.json", doc)
    (out / "automatic_summary.md").write_text(summary_markdown(doc), encoding="utf-8")
    return doc


def write_sheet(out: Path) -> dict[str, Any]:
    """Blind sheet + key, never overwritten; verifies cells in records and key."""
    sheet_path, key_path = out / "rating_sheet.csv", out / "rating_key.csv"
    if sheet_path.exists() or key_path.exists():
        raise SystemExit("rating sheet or key exists; never overwritten")
    records = xr.load_records(out)
    cells = xr.expected_cells(json.loads((out / "inputs" / "manifest.json").read_text()))
    sheet, key = blind_sheet(records)
    key_rows = list(csv.DictReader(io.StringIO(key)))
    result = {
        "records": xr.coverage(cells, records),
        "key": xr.coverage(cells, key_rows),
        "sheet_rows": len(key_rows),
    }
    sheet_path.write_text(sheet, encoding="utf-8")
    key_path.write_text(key, encoding="utf-8")
    return result
