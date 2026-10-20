"""Experiment I stop point 3: frozen automatic checks, summary, blind sheet.

Each record is checked against its own arm's sealed view with the frozen
checks of ``coaching_units_experiment_i`` (applied exactly as in the stop-point-2
smoke test): production validator count and the amendment-1 adjusted count
(kept separate), blocked, map mention, value fidelity and map-rule
violations over the player-facing sentences. Arm A has no ``map_awareness``
block, so its map checks run against a null block; the map-rule and fidelity
gates apply to arm M only. Nothing here applies the decision rule or reads
human ratings.
"""

from __future__ import annotations

import csv
import io
import json
import random
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import coaching_units_experiment_d as xd
import coaching_units_experiment_i as xi
import coaching_units_experiment_i_run as xr
from coaching_units import RATING_COLUMNS

SHEET_COLUMNS = (
    *RATING_COLUMNS[:-1],
    "map_use_correct_incorrect_not_mentioned",
    RATING_COLUMNS[-1],
)
NULL_BLOCK = {(xi.MATCH_PREFIX + m, cid) for m, cid in xi.UNOBSERVABLE}
SUBSETS = ("all", "eligible", "not_eligible", "null_block")


def load_records(out: Path) -> list[dict[str, Any]]:
    """Every current call record."""
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((out / "outputs" / "calls").glob("*.json"))
    ]


def cell_key(item: Mapping[str, Any] | xr.Cell) -> tuple[str, str, str, int]:
    """``(match, cid, arm, repeat)`` of a record, key row or cell."""
    get = item.__getitem__ if isinstance(item, Mapping) else item.__getattribute__
    return (get("match"), get("cid"), get("arm"), int(get("repeat")))


def coverage(expected: Sequence[Any], found: Sequence[Any]) -> dict[str, list]:
    """Missing, duplicate and unexpected cells of ``found`` against ``expected``."""
    want = {cell_key(c) for c in expected}
    keys = [cell_key(r) for r in found]
    seen: set = set()
    dupes = sorted({k for k in keys if k in seen or seen.add(k)})
    return {
        "missing": sorted(want - set(keys)),
        "duplicate": dupes,
        "unexpected": sorted(set(keys) - want),
    }


def check_record(record: Mapping[str, Any], view: Mapping[str, Any]) -> dict[str, Any]:
    """Frozen checks for one call record against its own arm's view."""
    base = {k: record[k] for k in ("call_id", "match", "cid", "eligible", "arm")}
    base.update(
        repeat=record["repeat"],
        null_block=(record["match"], record["cid"]) in NULL_BLOCK,
    )
    validation = record.get("validation")
    if validation is None:
        return {
            **base,
            "ok": False,
            "call_error": record.get("call_error"),
            "parse_error": record.get("parse_error"),
        }
    block = view.get("map_awareness")
    assessment = record["assessment"]
    sentences = xi.output_sentences(assessment)
    fidelity = xi.value_fidelity_errors(sentences, block)
    return {
        **base,
        "ok": True,
        "blocked": bool(validation["blocked"]),
        **xi.validator_counts(validation, block),
        "mentions_map_check": xi.mentions_map_check(" ".join(sentences)),
        "value_fidelity_errors": fidelity,
        "map_rule_violations": xi.map_rule_violations(assessment, block),
    }


def automatic_checks(out: Path) -> list[dict[str, Any]]:
    """Checks for every current record (arm-identified)."""
    rows = []
    for record in load_records(out):
        prompt = (out / "inputs" / record["file"]).read_text(encoding="utf-8")
        view, _ = xd.split_prompt(prompt)
        rows.append(check_record(record, view))
    return rows


def _per_call(total: int, n: int) -> float | None:
    return round(total / n, 3) if n else None


def _group(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r["ok"]]
    violations = [v for r in ok for v in r["map_rule_violations"]]
    raw = sum(r["validator_hits"] for r in ok)
    adjusted = sum(r["adjusted"] for r in ok)
    return {
        "calls": len(rows),
        "failed": len(rows) - len(ok),
        "blocked": sum(r["blocked"] for r in ok),
        "validator_hits_production": raw,
        "validator_hits_production_per_call": _per_call(raw, len(ok)),
        "r5_window_exempt": sum(r["r5_window_exempt"] for r in ok),
        "validator_hits_adjusted": adjusted,
        "validator_hits_adjusted_per_call": _per_call(adjusted, len(ok)),
        "map_mentions": sum(r["mentions_map_check"] for r in ok),
        "outputs_with_value_fidelity_errors": sum(
            bool(r["value_fidelity_errors"]) for r in ok
        ),
        "value_fidelity_errors": sum(len(r["value_fidelity_errors"]) for r in ok),
        "outputs_with_map_rule_violations": sum(
            bool(r["map_rule_violations"]) for r in ok
        ),
        "map_rule_violations": len(violations),
        "map_rule_violation_codes": {
            c: violations.count(c) for c in sorted(set(violations))
        },
    }


def _subset(rows: Sequence[Mapping[str, Any]], name: str) -> list[Mapping[str, Any]]:
    if name == "eligible":
        return [r for r in rows if r["eligible"]]
    if name == "not_eligible":
        return [r for r in rows if not r["eligible"]]
    if name == "null_block":
        return [r for r in rows if r["null_block"]]
    return list(rows)


def summarize(checks: Sequence[Mapping[str, Any]], cover: Mapping[str, list]) -> dict:
    """Automatic per-arm, per-subset figures; no gate verdicts."""
    arms = {
        arm: {
            s: _group(_subset([r for r in checks if r["arm"] == arm], s)) for s in SUBSETS
        }
        for arm in xi.ARMS
    }
    return {
        "decision_rule_applied": False,
        "text_scope": "assessment + recommendations + claim sentences",
        "coverage": cover,
        "arms": arms,
        "eligible_m_map_mentions": {
            "mentions": arms["M"]["eligible"]["map_mentions"],
            "of": arms["M"]["eligible"]["calls"],
            "pre_registered_denominator": xi.UPTAKE_OF,
        },
    }


def blind_sheet(
    records: Sequence[Mapping[str, Any]], seed: int = xi.RATING_SEED
) -> tuple[str, str]:
    """(sheet CSV, key CSV): one row per cell; the sheet names no arm or repeat."""
    rows = sorted(records, key=lambda r: r["call_id"])
    random.Random(seed).shuffle(rows)
    sheet, key = io.StringIO(), io.StringIO()
    sheet_w = csv.writer(sheet, lineterminator="\n")
    key_w = csv.writer(key, lineterminator="\n")
    sheet_w.writerow(
        ("output_id", "match", "death", "assessment", "recommendations", *SHEET_COLUMNS)
    )
    key_w.writerow(("output_id", "call_id", "match", "cid", "eligible", "arm", "repeat"))
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
                r["match"].removeprefix(xi.MATCH_PREFIX),
                f"{float(r['cid'].split(':', 1)[1]):.1f}s",
                assessment,
                recs,
                *[""] * len(SHEET_COLUMNS),
            )
        )
        key_w.writerow(
            (
                output_id,
                r["call_id"],
                r["match"],
                r["cid"],
                r["eligible"],
                r["arm"],
                r["repeat"],
            )
        )
    return sheet.getvalue(), key.getvalue()


def summary_markdown(doc: Mapping[str, Any]) -> str:
    """Arm-identified automatic summary (read after ratings are sealed)."""
    cover = doc["coverage"]
    up = doc["eligible_m_map_mentions"]
    lines = [
        "# Experiment I automatic summary (frozen checks; decision rule not applied)",
        "",
        "Arm-identified. Automatic checks only; no human rating and no gate",
        f"verdict. Text checks read {doc['text_scope']}. Production and",
        "amendment-1 adjusted validator counts are separate columns. Arm A has",
        "no map_awareness block: its map checks run against a null block and are",
        "descriptive only (the map-rule and fidelity gates apply to arm M).",
        "",
        f"- Coverage: missing {len(cover['missing'])}, duplicate "
        f"{len(cover['duplicate'])}, unexpected {len(cover['unexpected'])}",
        f"- Eligible arm-M outputs mentioning the map check: {up['mentions']} of "
        f"{up['of']} (pre-registered denominator {up['pre_registered_denominator']})",
        "",
        "| arm | subset | calls | failed | blocked | production hits (per call) "
        "| R5 window exempt | adjusted hits (per call) | map mentions "
        "| outputs with fidelity errors | map-rule violations |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for arm, groups in doc["arms"].items():
        for name, g in groups.items():
            codes = ", ".join(
                f"{c} {n}" for c, n in g["map_rule_violation_codes"].items()
            )
            lines.append(
                f"| {arm} | {name} | {g['calls']} | {g['failed']} | {g['blocked']} | "
                f"{g['validator_hits_production']} "
                f"({g['validator_hits_production_per_call']}) | "
                f"{g['r5_window_exempt']} | {g['validator_hits_adjusted']} "
                f"({g['validator_hits_adjusted_per_call']}) | {g['map_mentions']} | "
                f"{g['outputs_with_value_fidelity_errors']} | "
                f"{g['map_rule_violations']}{f' ({codes})' if codes else ''} |"
            )
    return "\n".join(lines) + "\n"


def _write_json(path: Path, doc: Any) -> None:
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", "utf-8")


def write_summary(out: Path) -> dict[str, Any]:
    """``automatic_checks.json``, ``automatic_summary.json`` / ``.md``."""
    records = load_records(out)
    cells = xr.expected_cells(xr.load_manifest(out))
    checks = automatic_checks(out)
    doc = summarize(checks, coverage(cells, records))
    _write_json(out / "automatic_checks.json", checks)
    _write_json(out / "automatic_summary.json", doc)
    (out / "automatic_summary.md").write_text(summary_markdown(doc), encoding="utf-8")
    return doc


def write_sheet(out: Path) -> dict[str, Any]:
    """Blind sheet + key, never overwritten; verifies cells in records and key."""
    sheet_path, key_path = out / "rating_sheet.csv", out / "rating_key.csv"
    if sheet_path.exists() or key_path.exists():
        raise SystemExit("rating sheet or key exists; never overwritten")
    records = load_records(out)
    cells = xr.expected_cells(xr.load_manifest(out))
    sheet, key = blind_sheet(records)
    key_rows = list(csv.DictReader(io.StringIO(key)))
    result = {
        "records": coverage(cells, records),
        "key": coverage(cells, key_rows),
        "sheet_rows": len(key_rows),
    }
    sheet_path.write_text(sheet, encoding="utf-8")
    key_path.write_text(key, encoding="utf-8")
    return result
