"""Experiment J stop point 4: automatic summary, ablated versions, blind sheet.

Post-run code over the sealed output corpus (``outputs.sha256``); it imports
the stop point 3 modules and never modifies them. Each record is checked with
the sealed ``output_checks`` against the view its own sealed user prompt
carried, and every arm-M output on an eligible death gets the sealed
``ablate``. The automatic summary is arm-identified and applies no gate; it is
read after the absolute ratings are sealed. The absolute sheet shows no arm,
repeat, population, relevance label or note; its rows are shuffled with the
frozen absolute-sheet seed and its key is a separate file. Nothing here opens
the relevance labels or the ideal-coaching notes.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import coaching_units_experiment_d as xd
import coaching_units_experiment_i as xi
import coaching_units_experiment_j as xj
import coaching_units_experiment_j_arms as xa
import coaching_units_experiment_j_census as xc
import coaching_units_experiment_j_dry_run as xr
import coaching_units_experiment_j_run as xrun
from loguru import logger

OUT = xj.OUT_DIR
SHEET_POPULATIONS = ("P1", "P2", "C")
RATING_COLUMNS = (
    "usefulness_1to5",
    "actionable_1to5",
    "specific_to_this_death_1to5",
    "supported_by_footage_yes_no",
    "notes",
)
SHEET_FIXED = (
    "output_id",
    "recording",
    "death_time",
    "death_seconds",
    "footage",
    "assessment",
    "recommendations",
)
KEY_COLUMNS = ("output_id", "call_id", "death_id", "population", "arm", "repeat")
ARTIFACTS = (
    "automatic_checks.json",
    "automatic_summary.json",
    "automatic_summary.md",
    "ablations.json",
    "rating_sheet.csv",
    "rating_key.csv",
)
NOT_BUILT = {
    "c_intended_point_uptake": (
        "the sealed tool freezes uptake for arm M only; C's intended-point uptake "
        "has no frozen definition and is not computed here"
    ),
}


# --- sealed corpus ------------------------------------------------------------


def corpus_problems(out: Path = OUT) -> list[str]:
    """The output seal verifies and every planned cell holds exactly one reply."""
    problems = xr.sums_problems(out / "SHA256SUMS", out)
    if "outputs.sha256" not in xrun.sums(out / "SHA256SUMS"):
        problems.append("SHA256SUMS does not list outputs.sha256")
    problems += xr.sums_problems(out / "outputs.sha256", out)
    listed = set(xrun.sums(out / "outputs.sha256"))
    on_disk = {
        str(p.relative_to(out)) for p in (out / "outputs").rglob("*") if p.is_file()
    }
    if listed != on_disk:
        problems.append(f"outputs/ differs from its seal: {sorted(listed ^ on_disk)}")
    cells = xrun.expected_cells(xrun.load_manifest(out))
    todo, ambiguous = xrun.resume_plan(out, cells)
    problems += ambiguous + [f"{c.call_id}: no reply" for c in todo]
    return problems


def load_records(out: Path = OUT) -> dict[str, dict[str, Any]]:
    """Every call record by ``call_id``."""
    return {
        p.stem: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((out / "outputs" / "calls").glob("*.json"))
    }


def sealed_view(out: Path, cell: xrun.Cell) -> dict[str, Any]:
    """The arm view the model received, recovered from the sealed user prompt."""
    prompt = (out / "inputs" / cell.file).read_text(encoding="utf-8")
    view, _ = xd.split_prompt(prompt)
    if xi.user_prompt(xi.prompt_preamble(prompt), view) != prompt:
        raise SystemExit(f"{cell.file}: view does not re-serialize to the sealed prompt")
    return view


# --- automatic checks (sealed stop point 3 functions) -------------------------


def check_row(
    cell: xrun.Cell, record: Mapping[str, Any], view: Mapping[str, Any]
) -> dict[str, Any]:
    """``output_checks`` for one record; ablated text stays in ``ablations``."""
    checks = xa.output_checks(cell.arm, record.get("assessment"), view)
    ablation = checks.pop("ablation", None)
    if ablation is not None:
        checks["ablation"] = {
            "ablatable": ablation["ablatable"],
            "reasons": ablation["reasons"],
            "kept_recommendation": ablation.get("kept_recommendation"),
        }
    return {
        "call_id": cell.call_id,
        "population": cell.population,
        "arm": cell.arm,
        "repeat": cell.repeat,
        "death_id": cell.death_id,
        **checks,
    }


def ablation_row(
    cell: xrun.Cell, record: Mapping[str, Any], view: Mapping[str, Any]
) -> dict[str, Any] | None:
    """M-minus-map for an arm-M output on an eligible death, else None."""
    if cell.arm != "M" or not view.get("extra_point"):
        return None
    return {
        "call_id": cell.call_id,
        "population": cell.population,
        "repeat": cell.repeat,
        "death_id": cell.death_id,
        **xa.ablate(record.get("assessment")),
    }


def _per_call(total: int, n: int) -> float | None:
    return round(total / n, 3) if n else None


def _count(rows: Sequence[Mapping[str, Any]], field: str) -> int:
    return sum(int(r.get(field) or 0) for r in rows)


def _group(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    parsed = [r for r in rows if r["parsed"]]
    codes = [c for r in parsed for c in r.get("map_rule_violations") or ()]
    fidelity = [e for r in parsed for e in r.get("value_fidelity_errors") or ()]
    ablations = [r["ablation"] for r in rows if r.get("ablation")]
    production, adjusted = (
        _count(parsed, "validator_hits"),
        _count(parsed, "adjusted_hits"),
    )
    return {
        "calls": len(rows),
        "parsed": len(parsed),
        "blocked": sum(bool(r.get("blocked")) for r in parsed),
        "validator_hits_production": production,
        "validator_hits_production_per_call": _per_call(production, len(parsed)),
        "c_adjustment": _count(parsed, "c_adjustment"),
        "r5_window_exempt": _count(parsed, "r5_window_exempt"),
        "validator_hits_adjusted": adjusted,
        "validator_hits_adjusted_per_call": _per_call(adjusted, len(parsed)),
        "any_recommendation": sum(bool(r.get("any_recommendation")) for r in parsed),
        "outputs_with_value_fidelity_errors": sum(
            bool(r.get("value_fidelity_errors")) for r in parsed
        ),
        "value_fidelity_errors": len(fidelity),
        "map_rule_violations": len(codes),
        "map_rule_violation_codes": dict(sorted(Counter(codes).items())),
        "uptake": sum(bool(r.get("uptake")) for r in rows),
        "ablation_applicable": len(ablations),
        "ablatable": sum(a["ablatable"] for a in ablations),
        "ablation_reasons": dict(
            sorted(Counter(x for a in ablations for x in a["reasons"]).items())
        ),
        "ablated_with_recommendation_left": sum(
            bool(a.get("kept_recommendation")) for a in ablations
        ),
    }


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per population and arm, and per arm over all populations; no verdicts."""
    groups: dict[str, dict[str, Any]] = {}
    for population in xa.POPULATION_ARMS:
        for arm in xa.ARMS:
            subset = [r for r in rows if (r["population"], r["arm"]) == (population, arm)]
            if subset:
                groups[f"{population} {arm}"] = _group(subset)
    for arm in xa.ARMS:
        groups[f"all {arm}"] = _group([r for r in rows if r["arm"] == arm])
    return {
        "decision_rule_applied": False,
        "arm_identified": True,
        "read_after": "the absolute ratings are sealed",
        "uptake_and_ablatability_denominator": "all calls (unparsed = not)",
        "calls": len(rows),
        "groups": groups,
        "not_built": NOT_BUILT,
    }


def summary_markdown(doc: Mapping[str, Any]) -> str:
    """``automatic_summary.md``: arm-identified figures, no gate verdict."""
    lines = [
        "# Experiment J automatic summary (frozen checks; decision rule not applied)",
        "",
        "Arm-identified: read after the absolute ratings are sealed. Sealed stop",
        "point 3 `output_checks` and `ablate`, each against the view its own sealed",
        "prompt carried. Production counts, the C adjustment and the Amendment 3",
        "R5 window exemption are separate columns. Uptake and ablatability count",
        "unparsed outputs as not.",
        "",
        "| group | calls | parsed | blocked | production hits (per call) | C adj. "
        "| R5 exempt | adjusted hits (per call) | any rec. | fidelity errors "
        "| map-rule violations | uptake | ablatable / applicable | ablated with rec. |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- "
        "| --- | --- |",
    ]
    for name, g in doc["groups"].items():
        codes = ", ".join(f"{c} {n}" for c, n in g["map_rule_violation_codes"].items())
        lines.append(
            f"| {name} | {g['calls']} | {g['parsed']} | {g['blocked']} | "
            f"{g['validator_hits_production']} "
            f"({g['validator_hits_production_per_call']}) | {g['c_adjustment']} | "
            f"{g['r5_window_exempt']} | {g['validator_hits_adjusted']} "
            f"({g['validator_hits_adjusted_per_call']}) | {g['any_recommendation']} | "
            f"{g['value_fidelity_errors']} | "
            f"{g['map_rule_violations']}{f' ({codes})' if codes else ''} | "
            f"{g['uptake']} | {g['ablatable']} / {g['ablation_applicable']} | "
            f"{g['ablated_with_recommendation_left']} |"
        )
    lines += ["", "Ablation refusal reasons by group:", ""]
    lines += [
        f"- {name}: "
        + (", ".join(f"{r} {n}" for r, n in g["ablation_reasons"].items()) or "none")
        for name, g in doc["groups"].items()
        if g["ablation_applicable"]
    ]
    lines += ["", "Not built:", ""]
    lines += [f"- `{k}`: {v}" for k, v in doc["not_built"].items()]
    return "\n".join(lines) + "\n"


# --- blind absolute sheet -----------------------------------------------------


def absolute_seed(out: Path = OUT) -> int:
    """The frozen absolute-sheet row-order seed from the sealed ``design.json``."""
    design = json.loads((out / "design.json").read_text(encoding="utf-8"))
    return int(design["seeds"]["absolute_sheet_row_order"])


def sheet_cells(
    cells: Sequence[xrun.Cell], deaths: Mapping[str, Mapping[str, Any]]
) -> list[xrun.Cell]:
    """P1 and P2 A/M outputs plus C outputs, in canonical order."""
    order = {arm: n for n, arm in enumerate(xa.ARMS)}
    chosen = [c for c in cells if c.population in SHEET_POPULATIONS]
    return sorted(
        chosen,
        key=lambda c: (xc.death_key(deaths[c.death_id]), order[c.arm], c.repeat),
    )


def _displayed(record: Mapping[str, Any]) -> tuple[str, str]:
    output = record.get("assessment")
    if not output:
        return "(no rateable output: unparseable reply)", ""
    recs = " || ".join(str(r).strip() for r in output.get("recommendations") or ())
    return str(output.get("assessment") or "").strip(), recs


def blind_sheet(
    cells: Sequence[xrun.Cell],
    records: Mapping[str, Mapping[str, Any]],
    deaths: Mapping[str, Mapping[str, Any]],
    seed: int,
    movies: Path = xj.MOVIES,
) -> tuple[str, str]:
    """(sheet CSV, key CSV); ids are assigned after the seeded shuffle."""
    rows = sheet_cells(cells, deaths)
    random.Random(seed).shuffle(rows)
    sheet, key = io.StringIO(), io.StringIO()
    sheet_w = csv.writer(sheet, lineterminator="\n")
    key_w = csv.writer(key, lineterminator="\n")
    sheet_w.writerow((*SHEET_FIXED, *RATING_COLUMNS))
    key_w.writerow(KEY_COLUMNS)
    for n, cell in enumerate(rows, start=1):
        output_id = f"o{n:03d}"
        death = deaths[cell.death_id]
        assessment, recs = _displayed(records[cell.call_id])
        sheet_w.writerow(
            (
                output_id,
                death["video"],
                xc._clock(float(death["death_time"])),
                f"{float(death['death_time']):.1f}",
                xj.display_path(movies / str(death["video"])),
                assessment,
                recs,
                *[""] * len(RATING_COLUMNS),
            )
        )
        key_w.writerow(
            (
                output_id,
                cell.call_id,
                cell.death_id,
                cell.population,
                cell.arm,
                cell.repeat,
            )
        )
    return sheet.getvalue(), key.getvalue()


# --- build ----------------------------------------------------------------------


def census_deaths(out: Path = OUT) -> dict[str, dict[str, Any]]:
    """Sealed census records by death id."""
    census = json.loads((out / "census.json").read_text(encoding="utf-8"))
    return {xc.death_id(d): d for d in census["deaths"]}


def _json(doc: Any) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def build(out: Path = OUT) -> dict[str, Any]:
    """Every stop point 4 artifact once; refuses if any exists or the seal fails."""
    existing = [a for a in ARTIFACTS if (out / a).exists()]
    if existing:
        raise SystemExit(f"stop point 4 artifacts exist; never overwritten: {existing}")
    problems = corpus_problems(out)
    if problems:
        raise SystemExit(f"sealed output corpus does not verify: {problems}")
    cells = xrun.expected_cells(xrun.load_manifest(out))
    records, deaths = load_records(out), census_deaths(out)
    rows, ablations = [], []
    for cell in cells:
        view = sealed_view(out, cell)
        rows.append(check_row(cell, records[cell.call_id], view))
        ablation = ablation_row(cell, records[cell.call_id], view)
        if ablation is not None:
            ablations.append(ablation)
    summary = summarize(rows)
    sheet, key = blind_sheet(cells, records, deaths, absolute_seed(out))
    texts = {
        "automatic_checks.json": _json(rows),
        "automatic_summary.json": _json(summary),
        "automatic_summary.md": summary_markdown(summary),
        "ablations.json": _json(ablations),
        "rating_sheet.csv": sheet,
        "rating_key.csv": key,
    }
    for name, text in texts.items():
        xc.write_once(out / name, text)
    return {
        "checked_calls": len(rows),
        "ablation_rows": len(ablations),
        "sheet_rows": len(key.splitlines()) - 1,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``build`` writes the stop point 4 artifacts once."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("build",))
    parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    sys.stdout.write(json.dumps(build()) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
