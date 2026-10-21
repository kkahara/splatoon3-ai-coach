"""Experiment J stop point 5a: validate and seal the completed absolute sheet.

Rules fixed before any filled row exists. ``validate`` compares the filled
``rating_sheet.csv`` with the sealed blank sheet (read from git at
``BLANK_COMMIT``, whose hash must equal the stop point 4 seal): same header,
same rows in the same order, unchanged fixed columns, and every rating in
range. Problems name a row id and a column, never a value. ``seal`` runs once,
only on a valid sheet; the filled file is hashed as-is and never rewritten.
This module reads the filled sheet, the blank sheet and the seal files only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import coaching_units_experiment_j as xj
import coaching_units_experiment_j_outputs as xo
import coaching_units_experiment_j_run as xrun

from splatoon3_ai_coach.coach.llm_runs import utc_timestamp
from splatoon3_ai_coach.config.paths import PROJECT_ROOT

OUT = xj.OUT_DIR
SHEET_NAME = "rating_sheet.csv"
BLANK_COMMIT = "6b637cd"
EXPECTED_ROWS = 172
HEADER = (*xo.SHEET_FIXED, *xo.RATING_COLUMNS)
SCORES = ("usefulness_1to5", "actionable_1to5", "specific_to_this_death_1to5")
SUPPORTED = "supported_by_footage_yes_no"
SCORE_VALUES = {"1", "2", "3", "4", "5"}
SUPPORTED_VALUES = {"yes", "no"}
SEAL = "absolute_ratings.sha256"
SEAL_RECORD = "absolute_ratings_seal.json"


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blank_text(out: Path = OUT, commit: str = BLANK_COMMIT) -> str:
    """The sealed blank sheet as committed at ``commit``."""
    rel = (out / SHEET_NAME).relative_to(PROJECT_ROOT).as_posix()
    shown = xrun._git("show", f"{commit}:{rel}")
    if shown.returncode != 0:
        raise SystemExit(f"blank sheet not found at {commit}")
    return shown.stdout


def blank_problems(blank: str, out: Path = OUT) -> list[str]:
    """The git blank sheet is the one stop point 4 sealed."""
    sealed = xrun.sums(out / "stop_point_4.sha256").get(SHEET_NAME)
    if _sha_bytes(blank.encode("utf-8")) != sealed:
        return ["blank sheet differs from the stop point 4 seal"]
    return []


def _table(text: str) -> tuple[list[str], list[list[str]]]:
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    return (rows[0], rows[1:]) if rows else ([], [])


def _rating_problems(rid: str, row: dict[str, str]) -> list[str]:
    problems = [
        f"{rid}: {c} invalid" for c in SCORES if row[c].strip() not in SCORE_VALUES
    ]
    if row[SUPPORTED].strip().lower() not in SUPPORTED_VALUES:
        problems.append(f"{rid}: {SUPPORTED} invalid")
    return problems


def sheet_problems(
    filled: str, blank: str, expected_rows: int = EXPECTED_ROWS
) -> list[str]:
    """Every reason the filled sheet is not a complete, valid rating of the blank."""
    head_f, rows_f = _table(filled)
    head_b, rows_b = _table(blank)
    if tuple(head_b) != HEADER:
        return ["blank sheet header is not the frozen header"]
    if tuple(head_f) != HEADER:
        return ["header differs from the frozen header"]
    problems = []
    if len(rows_b) != expected_rows or len(rows_f) != expected_rows:
        problems.append(f"{len(rows_f)} rows, expected {expected_rows}")
    if [r[:1] for r in rows_f] != [r[:1] for r in rows_b]:
        problems.append("output_id set or order differs from the blank sheet")
        return problems
    for raw_f, raw_b in zip(rows_f, rows_b, strict=True):
        rid = raw_b[0]
        if len(raw_f) != len(HEADER):
            problems.append(f"{rid}: stray or missing cells")
            continue
        row = dict(zip(HEADER, raw_f, strict=True))
        base = dict(zip(HEADER, raw_b, strict=True))
        problems += [
            f"{rid}: fixed column {c} changed"
            for c in xo.SHEET_FIXED
            if row[c] != base[c]
        ]
        problems += _rating_problems(rid, row)
    return problems


def validate(out: Path = OUT, expected_rows: int = EXPECTED_ROWS) -> dict[str, Any]:
    """Validation result for the filled sheet (no rating values reported)."""
    blank = blank_text(out)
    filled = (out / SHEET_NAME).read_text(encoding="utf-8-sig")
    problems = blank_problems(blank, out) + sheet_problems(filled, blank, expected_rows)
    return {
        "rows": len(_table(filled)[1]),
        "valid": not problems,
        "problems": problems,
        "blank_commit": BLANK_COMMIT,
        "blank_sha256": _sha_bytes(blank.encode("utf-8")),
    }


def seal(out: Path = OUT, expected_rows: int = EXPECTED_ROWS) -> dict[str, Any]:
    """Once, on a valid sheet: record, hash file, and one ``SHA256SUMS`` line."""
    if (out / SEAL).exists() or (out / SEAL_RECORD).exists():
        raise SystemExit("absolute ratings are already sealed")
    result = validate(out, expected_rows)
    if not result["valid"]:
        raise SystemExit(f"not sealed; sheet invalid: {result['problems']}")
    sheet_sha = _sha_bytes((out / SHEET_NAME).read_bytes())
    record = {
        "experiment": "J",
        "stage": "absolute ratings sealed",
        "sealed_at": utc_timestamp(),
        "rows": result["rows"],
        "blank_commit": BLANK_COMMIT,
        "blank_sha256": result["blank_sha256"],
        "rating_sheet_sha256": sheet_sha,
        "opened_before_seal": [SHEET_NAME],
    }
    text = json.dumps(record, indent=1) + "\n"
    (out / SEAL_RECORD).write_text(text, encoding="utf-8")
    lines = (
        f"{sheet_sha}  {SHEET_NAME}\n{_sha_bytes(text.encode('utf-8'))}  {SEAL_RECORD}\n"
    )
    (out / SEAL).write_text(lines, encoding="utf-8")
    with (out / "SHA256SUMS").open("a", encoding="utf-8") as sums:
        sums.write(f"{_sha_bytes(lines.encode('utf-8'))}  {SEAL}\n")
    return {**record, "seal_sha256": _sha_bytes(lines.encode("utf-8"))}


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``validate`` (read only) / ``seal`` (once)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("validate", "seal"))
    args = parser.parse_args(argv)
    if args.command == "seal":
        sys.stdout.write(json.dumps(seal(), indent=1) + "\n")
        return 0
    result = validate()
    sys.stdout.write(json.dumps(result, indent=1) + "\n")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
