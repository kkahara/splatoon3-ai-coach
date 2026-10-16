"""Experiment H stop point 3: guarded run, automatic summary, blind sheet.

``run`` verifies the inputs sealed at ``SEAL_COMMIT`` (git byte identity,
design hash, production staleness, arm invariants) before the first call,
then makes every ``(death, arm, repeat)`` call exactly once. It refuses if
any output exists. ``recover`` is the only retry path: it re-calls cells
whose record is missing or holds a provider error, archives the failed
record under ``outputs/failed_attempts/`` and never touches a cell with a
model reply (an unparseable reply is a result, not a failure). Every
attempt is appended to ``outputs/run_log.jsonl``.

``summary`` applies the frozen checks of ``coaching_units_experiment_h``
(arm-identified, automatic). ``sheet`` writes the blind rating sheet (no
arm, repeat or set) and the separate key. Neither applies the decision rule.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import coaching_units_experiment_d as xd
import coaching_units_experiment_h as xh
import coaching_units_experiment_h_inputs as xi
from coaching_units import RATING_COLUMNS
from loguru import logger

from splatoon3_ai_coach.coach.coach import (
    annotate_claim_flags,
    parse_coaching_assessment,
)
from splatoon3_ai_coach.coach.llm_client import LLMProvider
from splatoon3_ai_coach.coach.llm_runs import build_llm_runs, utc_timestamp
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.config.settings import CoachSettings

SEAL_COMMIT = "9c030403f1a21fa908a6e242351821f9019ddd1c"
EXPERIMENT = "coaching_units_experiment_h"
SHEET_COLUMNS = (
    *RATING_COLUMNS[:-1],
    "roster_use_correct_incorrect_not_mentioned",
    RATING_COLUMNS[-1],
)


@dataclass(frozen=True)
class Cell:
    """One expected call: a death under one arm, one repeat."""

    match: str
    cid: str
    set: str
    change_type: str
    arm: str
    repeat: int
    file: str
    system: str

    @property
    def call_id(self) -> str:
        """Stable output file stem."""
        return f"{self.arm}__{xi.slug(self.match, self.cid)}__r{self.repeat}"


def expected_cells(manifest: Mapping[str, Any]) -> list[Cell]:
    """Every call in run order: repeat, then death, then arm."""
    return [
        Cell(
            match=c["match"],
            cid=c["cid"],
            set=c["set"],
            change_type=c["change_type"],
            arm=c["arm"],
            repeat=repeat,
            file=c["file"],
            system=c["system"],
        )
        for repeat in range(1, int(manifest["repeats"]) + 1)
        for c in manifest["calls"]
    ]


# --- seal verification --------------------------------------------------------


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
    )


def seal_problems(out: Path, seal: str = SEAL_COMMIT) -> list[str]:
    """Files under ``out/inputs`` + ``design.json`` not byte-identical to ``seal``."""
    sealed = [out / "design.json", out / "inputs"]
    rel = [str(p.relative_to(PROJECT_ROOT)) for p in sealed]
    problems = []
    if _git("diff", "--quiet", seal, "--", *rel).returncode != 0:
        problems.append(f"sealed files differ from {seal[:7]}")
    tracked = set(_git("ls-tree", "-r", "--name-only", seal, "--", *rel).stdout.split())
    on_disk = {str(sealed[0].relative_to(PROJECT_ROOT))} | {
        str(p.relative_to(PROJECT_ROOT)) for p in sealed[1].rglob("*") if p.is_file()
    }
    if on_disk != tracked:
        problems.append(
            f"sealed file set differs from {seal[:7]}: "
            f"extra {sorted(on_disk - tracked)}, missing {sorted(tracked - on_disk)}"
        )
    return problems


def verify_sealed(out: Path, base: AppConfig) -> list[str]:
    """Every reason the sealed inputs may not be used; empty when usable."""
    inputs = out / "inputs"
    manifest = xi.load_manifest(inputs)
    design = xi.load_design(out)
    problems = seal_problems(out)
    if manifest["design_sha256"] != xh._sha(json.dumps(design, sort_keys=True)):
        problems.append("manifest design_sha256 does not match design.json")
    problems += xi.staleness(inputs, base)
    problems += xi.check_rows(xi.death_rows(inputs, manifest))
    n = len(expected_cells(manifest))
    if n != len(design["treatment"] + design["controls"]) * len(xh.ARMS) * xh.REPEATS:
        problems.append(f"expected cell count {n} does not match the design")
    return problems


# --- calls -----------------------------------------------------------------------


def evaluate(raw: str, view: Mapping[str, Any]) -> dict[str, Any]:
    """Parse and validate one reply against its own arm's view (never rewritten)."""
    try:
        assessment = parse_coaching_assessment(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return {"parse_error": type(exc).__name__, "assessment": None, "validation": None}
    return {
        "parse_error": None,
        "assessment": assessment.model_dump(mode="json"),
        "validation": annotate_claim_flags(assessment, dict(view)),
    }


def call_cell(
    cell: Cell, inputs: Path, provider: LLMProvider, meta: Mapping[str, str], attempt: int
) -> dict[str, Any]:
    """One provider call for ``cell``; provider errors are recorded, not raised."""
    system = (inputs / cell.system).read_text(encoding="utf-8")
    prompt = (inputs / cell.file).read_text(encoding="utf-8")
    record: dict[str, Any] = {
        "experiment": True,
        "experiment_name": EXPERIMENT,
        "call_id": cell.call_id,
        **cell.__dict__,
        **meta,
        "attempt": attempt,
        "generation_settings": {
            k: v.model_dump(mode="json")
            for k, v in provider.generation_settings().items()
        },
        "input_hashes": provider.input_hashes(system, prompt),
        "executed_at": utc_timestamp(),
    }
    start = time.monotonic()
    try:
        raw = provider.complete(system, prompt)
    except RuntimeError as exc:
        record.update({"call_error": str(exc), "raw_output": None})
    else:
        view, _ = xd.split_prompt(prompt)
        record.update({"call_error": None, "raw_output": raw, **evaluate(raw, view)})
    record["elapsed_seconds"] = round(time.monotonic() - start, 1)
    return record


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", "utf-8")


def _log(out: Path, entry: Mapping[str, Any]) -> None:
    path = out / "outputs" / "run_log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def run_cells(
    out: Path,
    cells: Sequence[Cell],
    provider: LLMProvider,
    meta: Mapping[str, str],
    phase: str,
) -> int:
    """Call each cell once, writing its record before the next; returns failures."""
    calls_dir = out / "outputs" / "calls"
    failures = 0
    for i, cell in enumerate(cells, start=1):
        attempt = 1 + len(list(_failed_dir(out).glob(f"{cell.call_id}.attempt*.json")))
        record = call_cell(cell, out / "inputs", provider, meta, attempt)
        _write_json(calls_dir / f"{cell.call_id}.json", record)
        ok = record["call_error"] is None
        failures += not ok
        _log(
            out,
            {
                "phase": phase,
                "call_id": cell.call_id,
                "attempt": attempt,
                "executed_at": record["executed_at"],
                "elapsed_seconds": record["elapsed_seconds"],
                "call_error": record["call_error"],
            },
        )
        logger.warning(
            "H {} {}/{} {} {} ({}s)",
            phase,
            i,
            len(cells),
            cell.call_id,
            "ok" if ok else "PROVIDER ERROR",
            record["elapsed_seconds"],
        )
    return failures


def _failed_dir(out: Path) -> Path:
    return out / "outputs" / "failed_attempts"


def retry_cells(out: Path, cells: Sequence[Cell]) -> list[Cell]:
    """Cells with no record or a provider-error record (never a model reply)."""
    calls_dir = out / "outputs" / "calls"
    todo = []
    for cell in cells:
        path = calls_dir / f"{cell.call_id}.json"
        if not path.exists():
            todo.append(cell)
        elif json.loads(path.read_text(encoding="utf-8"))["call_error"] is not None:
            todo.append(cell)
    return todo


def archive_failed(out: Path, cell: Cell) -> None:
    """Move a provider-error record aside before its cell is re-called."""
    path = out / "outputs" / "calls" / f"{cell.call_id}.json"
    if not path.exists():
        return
    record = json.loads(path.read_text(encoding="utf-8"))
    dest = _failed_dir(out) / f"{cell.call_id}.attempt{record['attempt']}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    path.rename(dest)


def _provider(config: AppConfig, provider: str, model: str) -> LLMProvider:
    return build_llm_runs(
        provider_name=provider,
        coach_cfg=config.coach,
        settings=CoachSettings(),
        ollama_model=model,
        nvidia_model_override=model,
        also_baseline=False,
        baseline_model_override=None,
        cursor_model_override=model,
    )[0].provider


def _guard(out: Path, config: AppConfig, provider: str, model: str) -> list[Cell]:
    manifest = xi.load_manifest(out / "inputs")
    if (provider, model) != (manifest["provider"], manifest["model"]):
        raise SystemExit(
            f"pre-registered {manifest['provider']}/{manifest['model']}, "
            f"got {provider}/{model}"
        )
    problems = verify_sealed(out, config)
    if problems:
        raise SystemExit("refusing: sealed inputs unusable: " + "; ".join(problems))
    return expected_cells(manifest)


def run(out: Path, config: AppConfig, provider: str, model: str) -> int:
    """All 132 calls, exactly once; refuses if any output exists."""
    if (out / "outputs").exists():
        raise SystemExit(f"{out / 'outputs'} exists; run once (use recover)")
    cells = _guard(out, config, provider, model)
    meta = {"provider": provider, "model": model}
    failures = run_cells(out, cells, _provider(config, provider, model), meta, "run")
    logger.warning("H run done: {} cells, {} provider errors", len(cells), failures)
    return 1 if failures else 0


def recover(out: Path, config: AppConfig, provider: str, model: str) -> int:
    """Re-call only missing / provider-error cells; archives the failed records."""
    if not (out / "outputs" / "calls").exists():
        raise SystemExit("nothing to recover: run has not started")
    cells = retry_cells(out, _guard(out, config, provider, model))
    for cell in cells:
        archive_failed(out, cell)
    meta = {"provider": provider, "model": model}
    failures = run_cells(out, cells, _provider(config, provider, model), meta, "recover")
    logger.warning("H recover done: {} cells, {} provider errors", len(cells), failures)
    return 1 if failures else 0


# --- completeness ------------------------------------------------------------


def load_records(out: Path) -> list[dict[str, Any]]:
    """Every current call record."""
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted((out / "outputs" / "calls").glob("*.json"))
    ]


def cell_key(item: Mapping[str, Any] | Cell) -> tuple[str, str, str, int]:
    """``(match, cid, arm, repeat)`` of a record, key row or cell."""
    get = item.__getitem__ if isinstance(item, Mapping) else item.__getattribute__
    return (get("match"), get("cid"), get("arm"), int(get("repeat")))


def coverage(expected: Iterable[Any], found: Iterable[Any]) -> dict[str, list]:
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
