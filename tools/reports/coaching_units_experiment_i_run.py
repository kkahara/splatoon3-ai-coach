"""Experiment I stop point 3: pre-call gate, run once, guarded recovery.

``run`` evaluates the pre-call gate (repository state, the stop-point-2 seal
at ``SEAL_COMMIT``, design and input hashes, byte-for-byte rebuild against
current production, the frozen invariants, the 22 / 14 / 8 / 88 counts, the
amendment-2 null block, an empty output destination). Any failure makes zero
calls. On a pass the gate record is written first, then every
``(death, arm, repeat)`` call is made exactly once, each record written
atomically before the next call.

``recover`` is the only retry path. It refuses unless the call records and
``outputs/run_log.jsonl`` agree on what completed, then re-calls only cells
with no record or a provider-error record (archived under
``outputs/failed_attempts/``). A model reply, including an unparseable one,
is a result and is never re-called.

``summary`` / ``sheet`` live in ``coaching_units_experiment_i_results``.
The sealed stop-point-2 modules are imported, never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import coaching_units_experiment_d as xd
import coaching_units_experiment_h as xh
import coaching_units_experiment_h_inputs as xhi
import coaching_units_experiment_h_run as xhr
import coaching_units_experiment_i as xi
import coaching_units_experiment_i_inputs as xii
from loguru import logger

from splatoon3_ai_coach.coach.llm_client import LLMProvider
from splatoon3_ai_coach.coach.llm_runs import utc_timestamp
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT

SEAL_COMMIT = "79be6ac"
EXPERIMENT = "coaching_units_experiment_i"
EXPECTED_DEATHS, EXPECTED_ELIGIBLE, EXPECTED_CALLS = 22, 14, 88
SEALED_CODE = (
    "tools/reports/coaching_units_experiment_i.py",
    "tools/reports/coaching_units_experiment_i_inputs.py",
    "tests/test_coaching_units_experiment_i.py",
)
SEALED_ARTIFACTS = ("design.json", "DESIGN.md", "dry_run.json", "dry_run.md", "inputs")
RUN_TOOLING = (
    "tools/reports/coaching_units_experiment_i_run.py",
    "tools/reports/coaching_units_experiment_i_results.py",
    "tests/test_coaching_units_experiment_i_run.py",
)


@dataclass(frozen=True)
class Cell:
    """One expected call: a death under one arm, one repeat."""

    match: str
    cid: str
    eligible: bool
    arm: str
    repeat: int
    file: str
    system: str

    @property
    def call_id(self) -> str:
        """Stable output file stem."""
        return f"{self.arm}__{xhi.slug(self.match, self.cid)}__r{self.repeat}"


def load_manifest(out: Path) -> dict[str, Any]:
    """The sealed ``inputs/manifest.json``."""
    return json.loads((out / "inputs" / "manifest.json").read_text(encoding="utf-8"))


def expected_cells(manifest: Mapping[str, Any]) -> list[Cell]:
    """Every call in run order: repeat, then death, then arm."""
    return [
        Cell(
            match=c["match"],
            cid=c["cid"],
            eligible=bool(c["eligible"]),
            arm=c["arm"],
            repeat=repeat,
            file=c["file"],
            system=c["system"],
        )
        for repeat in range(1, int(manifest["repeats"]) + 1)
        for c in manifest["calls"]
    ]


# --- repository and seal -------------------------------------------------------


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
    )


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(path: Path) -> str:
    return str(path.resolve().relative_to(PROJECT_ROOT))


def sealed_paths(out: Path) -> list[str]:
    """Repo-relative paths that must be byte-identical to the seal."""
    return [_rel(out / a) for a in SEALED_ARTIFACTS] + list(SEALED_CODE)


def _on_disk(paths: Sequence[str]) -> set[str]:
    found: set[str] = set()
    for rel in paths:
        path = PROJECT_ROOT / rel
        if path.is_dir():
            found |= {_rel(p) for p in path.rglob("*") if p.is_file()}
        elif path.is_file():
            found.add(rel)
    return found


def seal_problems(out: Path, seal: str = SEAL_COMMIT) -> list[str]:
    """Sealed files that differ from ``seal`` in bytes or in file set."""
    paths = sealed_paths(out)
    if _git("cat-file", "-e", f"{seal}^{{commit}}").returncode != 0:
        return [f"seal commit {seal} not found"]
    problems = []
    if _git("diff", "--quiet", seal, "--", *paths).returncode != 0:
        problems.append(f"sealed files differ from {seal}")
    tracked = set(_git("ls-tree", "-r", "--name-only", seal, "--", *paths).stdout.split())
    on_disk = _on_disk(paths)
    if on_disk != tracked:
        problems.append(
            f"sealed file set differs from {seal}: extra "
            f"{sorted(on_disk - tracked)}, missing {sorted(tracked - on_disk)}"
        )
    return problems


def repository_state(seal: str = SEAL_COMMIT) -> dict[str, Any]:
    """HEAD, seal ancestry and whether the run tooling is committed unmodified."""
    head = _git("rev-parse", "HEAD").stdout.strip()
    seal_full = _git("rev-parse", "--verify", "--quiet", f"{seal}^{{commit}}").stdout
    ancestor = _git("merge-base", "--is-ancestor", seal, "HEAD").returncode == 0
    tracked = set(_git("ls-files", "--", *RUN_TOOLING).stdout.split())
    clean = _git("diff", "--quiet", "HEAD", "--", *RUN_TOOLING).returncode == 0
    problems = []
    if not seal_full.strip():
        problems.append(f"seal commit {seal} not found")
    if not ancestor:
        problems.append(f"seal {seal} is not an ancestor of HEAD")
    if tracked != set(RUN_TOOLING) or not clean:
        problems.append("run tooling is not committed unmodified at HEAD")
    return {
        "head_commit": head,
        "seal_commit": seal_full.strip(),
        "seal_is_ancestor_of_head": ancestor,
        "run_tooling_committed_clean": tracked == set(RUN_TOOLING) and clean,
        "porcelain": _git("status", "--porcelain").stdout.splitlines(),
        "problems": problems,
    }


def input_hashes(out: Path) -> dict[str, Any]:
    """sha256 of every sealed input file, the design files and an aggregate."""
    inputs = out / "inputs"
    files = {
        str(p.relative_to(inputs)): _sha_file(p)
        for p in sorted(inputs.rglob("*"))
        if p.is_file()
    }
    aggregate = hashlib.sha256(
        "".join(f"{rel}\0{sha}\n" for rel, sha in sorted(files.items())).encode()
    ).hexdigest()
    return {
        "design.json": _sha_file(out / "design.json"),
        "DESIGN.md": _sha_file(out / "DESIGN.md"),
        "inputs_aggregate": aggregate,
        "inputs": files,
    }


# --- gate checks -----------------------------------------------------------------


def count_problems(design: Mapping[str, Any], manifest: Mapping[str, Any]) -> list[str]:
    """22 deaths, 14 eligible / 8 not, 44 sealed prompts, 88 unique calls."""
    deaths = design["deaths"]
    eligible = sum(xii.effective_eligible(d) for d in deaths)
    cells = expected_cells(manifest)
    problems = []
    if len(deaths) != EXPECTED_DEATHS:
        problems.append(f"{len(deaths)} deaths, expected {EXPECTED_DEATHS}")
    if eligible != EXPECTED_ELIGIBLE:
        problems.append(f"{eligible} eligible, expected {EXPECTED_ELIGIBLE}")
    if len(deaths) - eligible != EXPECTED_DEATHS - EXPECTED_ELIGIBLE:
        problems.append(f"{len(deaths) - eligible} not eligible")
    if tuple(manifest["arms"]) != xi.ARMS or int(manifest["repeats"]) != xi.REPEATS:
        problems.append("manifest arms / repeats differ from the design")
    if len(cells) != EXPECTED_CALLS or len({c.call_id for c in cells}) != len(cells):
        problems.append(f"{len(cells)} cells ({len({c.call_id for c in cells})} unique)")
    want = {(xi.MATCH_PREFIX + d["match"], d["scenario_id"]): d for d in deaths}
    for call in manifest["calls"]:
        death = want.get((call["match"], call["cid"]))
        if death is None or call["eligible"] != xii.effective_eligible(death):
            problems.append(f"manifest eligibility wrong: {call['match']} {call['cid']}")
    if xi.UPTAKE_OF != EXPECTED_ELIGIBLE * xi.REPEATS:
        problems.append("uptake denominator differs from eligible M outputs")
    return problems


def _sealed_view(inputs: Path, arm: str, key: tuple[str, str]) -> dict[str, Any]:
    text = (inputs / arm / f"{xhi.slug(*key)}.user_prompt.txt").read_text("utf-8")
    return xd.split_prompt(text)[0]


def _claimed(view: Mapping[str, Any]) -> bool:
    return any(e["claim_id"] == xi.EXPERIMENT_CLAIM_ID for e in view["claim_basis"])


def sealed_view_problems(out: Path, design: Mapping[str, Any]) -> list[str]:
    """On the sealed files: M = A + authorized additions, same preamble."""
    inputs = out / "inputs"
    problems = []
    for death in design["deaths"]:
        key = xii._key(death)
        tag = f"{death['match']} {key[1]}"
        texts = {
            arm: (inputs / arm / f"{xhi.slug(*key)}.user_prompt.txt").read_text("utf-8")
            for arm in xi.ARMS
        }
        if xi.prompt_preamble(texts["A"]) != xi.prompt_preamble(texts["M"]):
            problems.append(f"{tag}: preamble differs between A and M")
        a, m = _sealed_view(inputs, "A", key), _sealed_view(inputs, "M", key)
        if "map_awareness" in a or _claimed(a):
            problems.append(f"{tag}: arm A carries an arm-M addition")
        if xii._strip_m(m) != a:
            problems.append(f"{tag}: sealed M is not sealed A + map_awareness / claim")
        if _claimed(m) != xii.effective_eligible(death):
            problems.append(f"{tag}: experiment claim present={_claimed(m)}")
    return problems


def null_block_problems(out: Path, manifest: Mapping[str, Any]) -> list[str]:
    """Amendment 2: null block, no claim, not eligible, no map-check advice."""
    problems = []
    for match, cid in xi.UNOBSERVABLE:
        key = (xi.MATCH_PREFIX + match, cid)
        m = _sealed_view(out / "inputs", "M", key)
        if "map_awareness" not in m or m["map_awareness"] is not None:
            problems.append(f"{match} {cid}: M map_awareness is not null")
        if _claimed(m):
            problems.append(f"{match} {cid}: M carries the experiment claim")
        flags = {
            c["eligible"] for c in manifest["calls"] if (c["match"], c["cid"]) == key
        }
        if flags != {False}:
            problems.append(f"{match} {cid}: manifest eligibility {flags}")
    if "the block is null, give no map-check advice" not in xi.MAP_SECTION:
        problems.append("system M lacks the null-block no-advice rule")
    return problems


def _check(name: str, problems: Sequence[str], detail: Any = None) -> dict[str, Any]:
    return {
        "name": name,
        "ok": not problems,
        "problems": list(problems),
        "detail": detail,
    }


def gate_checks(
    out: Path, config: AppConfig, provider: str, model: str, outputs_check: dict
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every pre-call check (each records its problems) and the hashes."""
    manifest, design = load_manifest(out), xii.load_design(out)
    repo = repository_state()
    hashes = input_hashes(out)
    design_sha = xh._sha(json.dumps(design, sort_keys=True))
    facts, arms = xii.arm_inputs(design, config)
    stale = xii.staleness(out / "inputs", xii.input_files(design, arms))
    registered = (manifest["provider"], manifest["model"]) == (xi.PROVIDER, xi.MODEL)
    checks = [
        _check(
            "provider_model",
            []
            if registered and (provider, model) == (xi.PROVIDER, xi.MODEL)
            else [f"got {provider}/{model}"],
            f"{provider}/{model}",
        ),
        _check("repository_state", repo["problems"], repo),
        _check("seal_bytes_and_file_set", seal_problems(out)),
        _check(
            "design_hash",
            []
            if manifest["design_sha256"] == design_sha
            else ["manifest design_sha256 does not match design.json"],
            design_sha,
        ),
        _check("derived_facts_reproduce_design", xii.fact_problems(design, facts)),
        _check("rebuild_byte_equal_current_production", stale),
        _check(
            "production_roster_evidence_off",
            [] if config.coach.roster_evidence is False else ["roster_evidence on"],
        ),
        _check("frozen_invariants", xii.invariant_problems(design, arms)),
        _check(
            "sealed_m_differs_only_by_authorized_additions",
            sealed_view_problems(out, design),
        ),
        _check("counts_22_14_8_88", count_problems(design, manifest)),
        _check(
            "amendment_2_null_block_20_33_31_199_5", null_block_problems(out, manifest)
        ),
        outputs_check,
    ]
    return checks, hashes


def precall_gate(
    out: Path, config: AppConfig, provider: str, model: str, outputs_check: dict
) -> dict[str, Any]:
    """The full gate record; ``passed`` only when every check holds."""
    checks, hashes = gate_checks(out, config, provider, model, outputs_check)
    return {
        "experiment": EXPERIMENT,
        "checked_at": utc_timestamp(),
        "provider": provider,
        "model": model,
        "seal_commit": SEAL_COMMIT,
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
        "hashes": hashes,
    }


def outputs_absent_check(out: Path) -> dict[str, Any]:
    """The output destination must not exist before ``run``."""
    path = out / "outputs"
    return _check("output_destination_empty", [f"{path} exists"] if path.exists() else [])


# --- calls -------------------------------------------------------------------------


def _write_json_atomic(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", "utf-8")
    os.replace(tmp, path)


def _log(out: Path, entry: Mapping[str, Any]) -> None:
    path = out / "outputs" / "run_log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _failed_dir(out: Path) -> Path:
    return out / "outputs" / "failed_attempts"


def call_cell(
    cell: Cell, inputs: Path, provider: LLMProvider, meta: Mapping[str, Any], attempt: int
) -> dict[str, Any]:
    """One provider call for ``cell``; provider errors are recorded, not raised."""
    system = (inputs / cell.system).read_text(encoding="utf-8")
    prompt = (inputs / cell.file).read_text(encoding="utf-8")
    record: dict[str, Any] = {
        "experiment": True,
        "experiment_name": EXPERIMENT,
        "call_id": cell.call_id,
        **asdict(cell),
        **meta,
        "attempt": attempt,
        "system_sha256": xh._sha(system),
        "user_prompt_sha256": xh._sha(prompt),
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
        record.update({"call_error": None, "raw_output": raw, **xhr.evaluate(raw, view)})
    record["elapsed_seconds"] = round(time.monotonic() - start, 1)
    return record


def run_cells(
    out: Path,
    cells: Sequence[Cell],
    provider: LLMProvider,
    meta: Mapping[str, Any],
    phase: str,
) -> int:
    """Call each cell once, writing its record before the next; returns failures."""
    calls_dir = out / "outputs" / "calls"
    failures = 0
    for i, cell in enumerate(cells, start=1):
        attempt = 1 + len(list(_failed_dir(out).glob(f"{cell.call_id}.attempt*.json")))
        base = {"phase": phase, "call_id": cell.call_id, "attempt": attempt}
        _log(out, {**base, "event": "start", "at": utc_timestamp()})
        record = call_cell(cell, out / "inputs", provider, meta, attempt)
        _write_json_atomic(calls_dir / f"{cell.call_id}.json", record)
        ok = record["call_error"] is None
        failures += not ok
        _log(
            out,
            {
                **base,
                "event": "end",
                "executed_at": record["executed_at"],
                "elapsed_seconds": record["elapsed_seconds"],
                "call_error": record["call_error"],
            },
        )
        logger.warning(
            "I {} {}/{} {} {} ({}s)",
            phase,
            i,
            len(cells),
            cell.call_id,
            "ok" if ok else "PROVIDER ERROR",
            record["elapsed_seconds"],
        )
    return failures


# --- recovery ----------------------------------------------------------------------


def _read_log(out: Path) -> tuple[list[dict[str, Any]], list[str]]:
    path = out / "outputs" / "run_log.jsonl"
    entries, problems = [], []
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for n, line in enumerate(lines, start=1):
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            problems.append(f"run_log line {n} unparseable")
    return entries, problems


def _identity(cell: Cell) -> dict[str, Any]:
    return {"call_id": cell.call_id, **asdict(cell)}


def _record_problems(path: Path, cell: Cell | None) -> tuple[dict | None, list[str]]:
    if cell is None:
        return None, [f"unexpected record {path.name}"]
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, [f"unparseable record {path.name}"]
    wrong = [k for k, v in _identity(cell).items() if record.get(k) != v]
    return record, [f"{path.name}: identity mismatch {wrong}"] if wrong else []


def recovery_plan(out: Path, cells: Sequence[Cell]) -> tuple[list[Cell], list[str]]:
    """Cells to re-call and every reason recovery would be ambiguous."""
    by_id = {c.call_id: c for c in cells}
    calls_dir = out / "outputs" / "calls"
    entries, problems = _read_log(out)
    records: dict[str, dict] = {}
    for path in sorted(calls_dir.glob("*")):
        if path.suffix != ".json":
            problems.append(f"stray file {path.name}")
            continue
        record, bad = _record_problems(path, by_id.get(path.stem))
        problems += bad
        if record is not None and not bad:
            records[path.stem] = record
    ended_ok = {
        e["call_id"]
        for e in entries
        if e.get("event") == "end" and e["call_error"] is None
    }
    todo = []
    for cell in cells:
        record = records.get(cell.call_id)
        if record is None and cell.call_id in ended_ok:
            problems.append(f"{cell.call_id}: log shows a reply but no record exists")
        elif record is not None and record["call_error"] is None:
            continue
        elif record is None or record["call_error"] is not None:
            todo.append(cell)
    return todo, problems


def archive_failed(out: Path, cell: Cell) -> None:
    """Move a provider-error record aside before its cell is re-called."""
    path = out / "outputs" / "calls" / f"{cell.call_id}.json"
    if not path.exists():
        return
    record = json.loads(path.read_text(encoding="utf-8"))
    if record["call_error"] is None:
        raise SystemExit(f"refusing to archive a model reply: {cell.call_id}")
    dest = _failed_dir(out) / f"{cell.call_id}.attempt{record['attempt']}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, dest)


# --- commands ------------------------------------------------------------------------


def _meta(gate: Mapping[str, Any]) -> dict[str, Any]:
    repo = next(c for c in gate["checks"] if c["name"] == "repository_state")["detail"]
    return {
        "provider": gate["provider"],
        "model": gate["model"],
        "seal_commit": repo["seal_commit"],
        "run_commit": repo["head_commit"],
    }


def run(out: Path, config: AppConfig, provider: str, model: str) -> int:
    """Gate, then all 88 calls exactly once; zero calls if the gate fails."""
    gate = precall_gate(out, config, provider, model, outputs_absent_check(out))
    if not gate["passed"]:
        failed = [c for c in gate["checks"] if not c["ok"]]
        xhr._write_json(out / "precall_gate_failed.json", gate)
        raise SystemExit(f"pre-call gate failed; 0 calls made: {failed}")
    _write_json_atomic(out / "outputs" / "precall_gate.json", gate)
    cells = expected_cells(load_manifest(out))
    llm = xhr._provider(config, provider, model)
    failures = run_cells(out, cells, llm, _meta(gate), "run")
    logger.warning("I run done: {} cells, {} provider errors", len(cells), failures)
    return 1 if failures else 0


def recover(out: Path, config: AppConfig, provider: str, model: str) -> int:
    """Re-call only missing / provider-error cells; refuses when ambiguous."""
    if not (out / "outputs" / "precall_gate.json").is_file():
        raise SystemExit("nothing to recover: run did not pass its gate")
    cells = expected_cells(load_manifest(out))
    todo, problems = recovery_plan(out, cells)
    outputs_check = _check("recovery_unambiguous", problems, [c.call_id for c in todo])
    gate = precall_gate(out, config, provider, model, outputs_check)
    if not gate["passed"]:
        failed = [c for c in gate["checks"] if not c["ok"]]
        raise SystemExit(f"recovery gate failed; 0 calls made: {failed}")
    stamp = gate["checked_at"].replace(":", "")
    _write_json_atomic(out / "outputs" / f"recovery_gate_{stamp}.json", gate)
    for cell in todo:
        archive_failed(out, cell)
    llm = xhr._provider(config, provider, model)
    failures = run_cells(out, todo, llm, _meta(gate), "recover")
    logger.warning("I recover done: {} cells, {} provider errors", len(todo), failures)
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``gate`` (no calls) / ``run`` / ``recover`` / ``summary`` / ``sheet``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("gate", "run", "recover", "summary", "sheet"))
    parser.add_argument("--out", type=Path, default=xi.OUT_DIR)
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--provider", default=xi.PROVIDER)
    parser.add_argument("--model", default=None)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    config = load_config(args.config.resolve())
    if args.command == "gate":
        gate = precall_gate(
            args.out,
            config,
            args.provider,
            args.model or xi.MODEL,
            outputs_absent_check(args.out),
        )
        sys.stdout.write(
            json.dumps(
                {c["name"]: c["problems"] or "ok" for c in gate["checks"]}, indent=1
            )
            + "\n"
        )
        return 0 if gate["passed"] else 1
    if args.command in ("run", "recover"):
        if not (args.confirm and args.model):
            raise SystemExit(f"{args.command} needs --model and --confirm")
        command = run if args.command == "run" else recover
        return command(args.out, config, args.provider, args.model)
    import coaching_units_experiment_i_results as xres

    if args.command == "summary":
        doc = xres.write_summary(args.out)
        sys.stdout.write(json.dumps(doc["coverage"]) + "\n")
        return 0 if not any(doc["coverage"].values()) else 1
    result = xres.write_sheet(args.out)
    sys.stdout.write(json.dumps(result) + "\n")
    return (
        0 if not any(any(c.values()) for c in (result["records"], result["key"])) else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
