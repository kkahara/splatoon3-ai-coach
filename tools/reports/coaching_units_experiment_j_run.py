"""Experiment J stop point 4: fail-closed pre-call gate, the 212 calls, resume.

Post-seal code: it imports the stop point 3 modules and never modifies them.
``gate`` evaluates every pre-call check and makes no call. ``run`` makes the
sealed call plan exactly once per cell; when ``outputs/`` already exists it
resumes instead, re-calling only cells with no record or a provider-error
record (archived under ``outputs/failed_attempts/``) and refusing whenever
the records and ``outputs/run_log.jsonl`` disagree. A model reply, including
an unparseable one, is a result and is never re-called or overwritten.
``seal`` hashes the output corpus. Nothing here opens the relevance labels or
the ideal-coaching notes, and no output is evaluated.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import coaching_units_experiment_d as xd
import coaching_units_experiment_h_run as xhr
import coaching_units_experiment_i as xi
import coaching_units_experiment_j as xj
import coaching_units_experiment_j_arms as xa
import coaching_units_experiment_j_census as xc
import coaching_units_experiment_j_dry_run as xr
from loguru import logger

from splatoon3_ai_coach.coach.coach import parse_coaching_assessment
from splatoon3_ai_coach.coach.llm_client import LLMProvider
from splatoon3_ai_coach.coach.llm_runs import utc_timestamp
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT

EXPERIMENT = "coaching_units_experiment_j"
SEAL_COMMIT = "2423ea8"
OUT = xj.OUT_DIR
PROVIDER, MODEL = "cursor", "gpt-5.6-luna"
EXPECTED_DEATHS = {"P1": 35, "P2": 5, "P3": 20, "C": 6}
EXPECTED_CALLS = {"P1": 140, "P2": 20, "P3": 40, "C": 12, "total": 212}
EXPECTED_INPUT_FILES = 110
MAX_ATTEMPTS = 3
SEALED_MODULES = {
    "coaching_units_experiment_d": xd,
    "coaching_units_experiment_i": xi,
    "coaching_units_experiment_j": xj,
    "coaching_units_experiment_j_census": xc,
    "coaching_units_experiment_j_arms": xa,
    "coaching_units_experiment_j_dry_run": xr,
}
RUN_TOOLING = (
    "tools/reports/coaching_units_experiment_j_run.py",
    "tests/test_coaching_units_experiment_j_run.py",
)
ProviderFactory = Callable[[AppConfig, str, str], LLMProvider]


@dataclass(frozen=True)
class Cell:
    """One sealed call: a death under one arm and one repeat."""

    call_id: str
    population: str
    arm: str
    repeat: int
    death_id: str
    file: str
    system: str


def load_manifest(out: Path = OUT) -> dict[str, Any]:
    """The sealed ``inputs/manifest.json``."""
    return json.loads((out / "inputs" / "manifest.json").read_text(encoding="utf-8"))


def expected_cells(manifest: Mapping[str, Any]) -> list[Cell]:
    """Run order: repeat, then death order, then arm (A, M, C)."""
    cells = [
        Cell(**{f: c[f] for f in Cell.__dataclass_fields__}) for c in manifest["calls"]
    ]
    order = {arm: n for n, arm in enumerate(xa.ARMS)}

    def key(cell: Cell) -> tuple:
        order_no, cid = cell.death_id.split("|", 1)
        seconds = float(cid.rsplit(":", 1)[1])
        return (cell.repeat, int(order_no), seconds, order[cell.arm], cell.population)

    return sorted(cells, key=key)


# --- hashes and repository ----------------------------------------------------


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
    )


def sums(path: Path) -> dict[str, str]:
    """``name -> sha256`` from a ``shasum -a 256`` file."""
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        out[name.lstrip("*")] = digest
    return out


def input_problems(out: Path = OUT) -> list[str]:
    """All 110 sealed inputs verify and no other file sits under ``inputs/``."""
    listed = sums(out / "inputs.sha256")
    problems = xr.sums_problems(out / "inputs.sha256", out)
    on_disk = {
        str(p.relative_to(out)) for p in (out / "inputs").rglob("*") if p.is_file()
    }
    if len(listed) != EXPECTED_INPUT_FILES:
        problems.append(f"{len(listed)} sealed inputs, expected {EXPECTED_INPUT_FILES}")
    if on_disk != set(listed):
        extra, missing = sorted(on_disk - set(listed)), sorted(set(listed) - on_disk)
        problems.append(f"input file set differs: extra {extra}, missing {missing}")
    return problems


def code_problems(out: Path = OUT) -> list[str]:
    """Sealed code/test hashes verify, and the imported modules are those files."""
    sealed = sums(out / "stop_point_3_code.sha256")
    problems = xr.sums_problems(out / "stop_point_3_code.sha256", PROJECT_ROOT)
    for name, module in SEALED_MODULES.items():
        rel = str(Path(module.__file__).resolve().relative_to(PROJECT_ROOT))
        if rel != f"tools/reports/{name}.py" or rel not in sealed:
            problems.append(f"{name} imported from {rel}, not the sealed file")
    return problems


def _sealed_paths(out: Path) -> list[str]:
    rel = str(out.resolve().relative_to(PROJECT_ROOT))
    names = [*sums(out / "SHA256SUMS"), "SHA256SUMS", "inputs"]
    return [f"{rel}/{n}" for n in names] + list(sums(out / "stop_point_3_code.sha256"))


def repository_problems(out: Path = OUT, seal: str = SEAL_COMMIT) -> dict[str, Any]:
    """Seal commit, ancestry, sealed paths unchanged, runner committed clean."""
    head = _git("rev-parse", "HEAD").stdout.strip()
    problems = []
    if _git("cat-file", "-e", f"{seal}^{{commit}}").returncode != 0:
        problems.append(f"seal commit {seal} not found")
    elif _git("merge-base", "--is-ancestor", seal, "HEAD").returncode != 0:
        problems.append(f"seal {seal} is not an ancestor of HEAD")
    elif _git("diff", "--quiet", seal, "--", *_sealed_paths(out)).returncode != 0:
        problems.append(f"sealed paths differ from {seal}")
    tracked = set(_git("ls-files", "--", *RUN_TOOLING).stdout.split())
    clean = _git("diff", "--quiet", "HEAD", "--", *RUN_TOOLING).returncode == 0
    if tracked != set(RUN_TOOLING) or not clean:
        problems.append("run tooling is not committed unmodified at HEAD")
    return {"head_commit": head, "seal_commit": seal, "problems": problems}


# --- frozen plan checks -------------------------------------------------------


def population_problems() -> list[str]:
    """P1=35, P2=5, P3=20, C=6 from the sealed populations."""
    deaths = xa.population_deaths(xa.load_json(xa.POPULATIONS), xa.load_json(xc.CENSUS))
    counts = {k: len(v) for k, v in deaths.items()}
    return [] if counts == EXPECTED_DEATHS else [f"populations {counts}"]


def plan_problems(manifest: Mapping[str, Any], out: Path = OUT) -> list[str]:
    """The sealed manifest is exactly the frozen plan: arms, repeats, counts, files."""
    deaths = xa.population_deaths(xa.load_json(xa.POPULATIONS), xa.load_json(xc.CENSUS))
    calls = manifest["calls"]
    listed = sums(out / "inputs.sha256")
    problems = []
    if calls != xa.call_plan(deaths):
        problems.append("manifest calls differ from the frozen call plan")
    if (
        manifest["call_counts"] != EXPECTED_CALLS
        or xa.call_counts(calls) != EXPECTED_CALLS
    ):
        problems.append(f"call counts {manifest['call_counts']}")
    if int(manifest["repeats"]) != 2 or {c["repeat"] for c in calls} != {1, 2}:
        problems.append("repeats differ from 2")
    for call in calls:
        if call["arm"] not in xa.POPULATION_ARMS[call["population"]]:
            problems.append(f"{call['call_id']}: arm not assigned to population")
        if (
            f"inputs/{call['file']}" not in listed
            or f"inputs/{call['system']}" not in listed
        ):
            problems.append(f"{call['call_id']}: input not sealed")
    if len({c["call_id"] for c in calls}) != len(calls):
        problems.append("duplicate call ids")
    return problems


def model_problems(manifest: Mapping[str, Any], provider: str, model: str) -> list[str]:
    """Frozen provider and model in the design, the manifest and this run."""
    design = xa.load_json(OUT / "design.json")["model"]
    seen = {
        "design": (design["provider"], design["model"]),
        "manifest": (manifest["provider"], manifest["model"]),
        "run": (provider, model),
    }
    return [f"{k} {v}" for k, v in seen.items() if v != (PROVIDER, MODEL)]


def amendment_3_problems() -> list[str]:
    """Amendment 3 is sealed and still delegates to Experiment I's rule."""
    problems = []
    if "AMENDMENT_3.md" not in sums(OUT / "SHA256SUMS"):
        problems.append("AMENDMENT_3.md not sealed")
    if "xi.r5_window_exempt(" not in inspect.getsource(xa.r5_window_exemption):
        problems.append("r5_window_exemption does not call xi.r5_window_exempt")
    if xa.xi is not xi or sys.modules.get("coaching_units_experiment_i") is not xi:
        problems.append("arms module does not use the sealed Experiment I module")
    hit = {"rule": "R5", "check": "map_absence", "text": "No map check since respawn."}
    validation = {"integrity_hits": [hit]}
    view = {
        "map_awareness": {"map_checks_since_back_in_play": 0},
        "back_in_play": {"seconds_back_in_play": 20.0},
    }
    block = {"map_checks_since_back_in_play": 0, "seconds_back_in_play": 20.0}
    if xa.r5_window_exemption("M", validation, view) != len(
        xi.r5_window_exempt(validation, block)
    ):
        problems.append("r5_window_exemption disagrees with r5_window_exempt")
    return problems


def rebuild_problems(config: AppConfig, out: Path = OUT) -> list[str]:
    """No regeneration: a fresh in-memory build equals the sealed inputs."""
    _, deaths, _, arms, facts = xr.prepare(config)
    files = xr.input_files(deaths, arms)
    return facts + xr.staleness(out / "inputs", files)


def _check(name: str, problems: Sequence[str], detail: Any = None) -> dict[str, Any]:
    return {
        "name": name,
        "ok": not problems,
        "problems": list(problems),
        "detail": detail,
    }


def _guarded(name: str, check: Callable[[], list[str]]) -> dict[str, Any]:
    """A check that raises fails closed with the reason recorded."""
    try:
        return _check(name, check())
    except (SystemExit, Exception) as exc:  # noqa: BLE001 - any error fails the gate
        return _check(name, [f"{type(exc).__name__}: {exc}"])


def precall_gate(
    config: AppConfig, provider: str, model: str, outputs_check: dict, out: Path = OUT
) -> dict[str, Any]:
    """Every pre-call check; ``passed`` only when all of them hold."""
    manifest = load_manifest(out)
    repo = repository_problems(out)
    checks = [
        _check("repository_state", repo["problems"], repo),
        _guarded("stop_point_1_to_3_seals", xr.seal_problems),
        _guarded("sealed_inputs_110", lambda: input_problems(out)),
        _guarded("sealed_code_and_tests", lambda: code_problems(out)),
        _guarded("populations_35_5_20_6", population_problems),
        _guarded("call_plan_140_20_40_12_212", lambda: plan_problems(manifest, out)),
        _check("provider_model", model_problems(manifest, provider, model)),
        _check(
            "roster_evidence_off",
            [] if config.coach.roster_evidence is False else ["roster_evidence on"],
        ),
        _guarded(
            "no_regeneration_rebuild_byte_equal", lambda: rebuild_problems(config, out)
        ),
        _guarded(
            "sealed_dry_run_criterion_met",
            lambda: (
                []
                if xa.load_json(out / "dry_run.json")["dry_run_criterion_met"] is True
                else ["sealed dry run criterion not met"]
            ),
        ),
        _guarded("amendment_3_delegates_to_experiment_i", amendment_3_problems),
        outputs_check,
    ]
    return {
        "experiment": EXPERIMENT,
        "checked_at": utc_timestamp(),
        "provider": provider,
        "model": model,
        "seal_commit": SEAL_COMMIT,
        "head_commit": repo["head_commit"],
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
        "inputs_sha256": _sha(out / "inputs.sha256"),
    }


# --- outputs ------------------------------------------------------------------


def _outputs(out: Path) -> Path:
    return out / "outputs"


def outputs_absent_check(out: Path = OUT) -> dict[str, Any]:
    """A fresh run needs an absent output destination."""
    path = _outputs(out)
    return _check(
        "output_destination_absent", [f"{path} exists"] if path.exists() else []
    )


def write_json_atomic(path: Path, doc: Any) -> None:
    """Write via a temporary file and rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", "utf-8")
    os.replace(tmp, path)


def write_record(path: Path, record: Mapping[str, Any]) -> None:
    """A call record; never replaces an existing model reply."""
    if path.exists() and json.loads(path.read_text("utf-8"))["call_error"] is None:
        raise SystemExit(f"refusing to overwrite a model reply: {path.name}")
    write_json_atomic(path, record)


def log_event(out: Path, entry: Mapping[str, Any]) -> None:
    """Append one fsynced line to ``outputs/run_log.jsonl``."""
    path = _outputs(out) / "run_log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def read_log(out: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Log entries and unparseable lines."""
    path = _outputs(out) / "run_log.jsonl"
    entries, problems = [], []
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    for n, line in enumerate(lines, start=1):
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            problems.append(f"run_log line {n} unparseable")
    return entries, problems


class RunLock:
    """``outputs/run.lock`` (O_EXCL); a lock held by a live process refuses."""

    def __init__(self, out: Path) -> None:
        self.path = _outputs(out) / "run.lock"

    def __enter__(self) -> RunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            pid = int(self.path.read_text("utf-8").strip() or 0)
            if _alive(pid):
                raise SystemExit(f"another run holds {self.path} (pid {pid})")
            self.path.unlink()
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc: object) -> None:
        self.path.unlink(missing_ok=True)


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# --- calls --------------------------------------------------------------------


def call_cell(
    cell: Cell, out: Path, provider: LLMProvider, meta: Mapping[str, Any], attempt: int
) -> dict[str, Any]:
    """One provider call; refuses if a sealed input changed; errors are recorded."""
    listed = sums(out / "inputs.sha256")
    system_path, prompt_path = out / "inputs" / cell.system, out / "inputs" / cell.file
    for path in (system_path, prompt_path):
        if _sha(path) != listed[f"inputs/{path.relative_to(out / 'inputs')}"]:
            raise SystemExit(f"sealed input changed: {path.name}; no call made")
    system = system_path.read_text(encoding="utf-8")
    prompt = prompt_path.read_text(encoding="utf-8")
    record: dict[str, Any] = {
        "experiment": True,
        "experiment_name": EXPERIMENT,
        **asdict(cell),
        **meta,
        "attempt": attempt,
        "system_sha256": _sha(system_path),
        "user_prompt_sha256": _sha(prompt_path),
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
        record.update({"call_error": None, "raw_output": raw, **_parse(raw)})
    record["elapsed_seconds"] = round(time.monotonic() - start, 1)
    return record


def _parse(raw: str) -> dict[str, Any]:
    """Parse only; outputs are evaluated later, never here."""
    try:
        assessment = parse_coaching_assessment(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return {"parse_error": type(exc).__name__, "assessment": None}
    return {"parse_error": None, "assessment": assessment.model_dump(mode="json")}


def attempts_started(out: Path, call_id: str) -> int:
    """How many times the log shows this cell's call starting."""
    entries, _ = read_log(out)
    return sum(e.get("event") == "start" and e.get("call_id") == call_id for e in entries)


def run_cells(
    out: Path,
    cells: Sequence[Cell],
    provider: LLMProvider,
    meta: Mapping[str, Any],
    phase: str,
) -> int:
    """Call each cell once, record before the next; returns provider errors."""
    failures = 0
    for i, cell in enumerate(cells, start=1):
        attempt = attempts_started(out, cell.call_id) + 1
        if attempt > MAX_ATTEMPTS:
            raise SystemExit(f"{cell.call_id}: {MAX_ATTEMPTS} attempts used; stopping")
        base = {"phase": phase, "call_id": cell.call_id, "attempt": attempt}
        log_event(out, {**base, "event": "start", "at": utc_timestamp()})
        record = call_cell(cell, out, provider, meta, attempt)
        write_record(_outputs(out) / "calls" / f"{cell.call_id}.json", record)
        ok = record["call_error"] is None
        failures += not ok
        end = {"event": "end", "elapsed_seconds": record["elapsed_seconds"]}
        log_event(out, {**base, **end, "call_error": record["call_error"]})
        logger.warning(
            "J {} {}/{} {} {} ({}s)",
            phase,
            i,
            len(cells),
            cell.call_id,
            "ok" if ok else "PROVIDER ERROR",
            record["elapsed_seconds"],
        )
    return failures


# --- resume -------------------------------------------------------------------


def _record(path: Path, cell: Cell | None) -> tuple[dict | None, list[str]]:
    if cell is None:
        return None, [f"record outside the sealed plan: {path.name}"]
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, [f"unparseable record {path.name}"]
    wrong = [k for k, v in asdict(cell).items() if record.get(k) != v]
    return record, [f"{path.name}: identity mismatch {wrong}"] if wrong else []


def resume_plan(out: Path, cells: Sequence[Cell]) -> tuple[list[Cell], list[str]]:
    """Cells still owed a reply, and every reason resuming would be ambiguous."""
    by_id = {c.call_id: c for c in cells}
    entries, problems = read_log(out)
    records: dict[str, dict] = {}
    for path in sorted((_outputs(out) / "calls").glob("*")):
        if path.suffix != ".json":
            problems.append(f"stray file {path.name}")
            continue
        record, bad = _record(path, by_id.get(path.stem))
        problems += bad
        if record is not None and not bad:
            records[path.stem] = record
    logged = {e.get("call_id") for e in entries}
    problems += [f"log names a call outside the plan: {c}" for c in logged - set(by_id)]
    replied = {
        e["call_id"]
        for e in entries
        if e.get("event") == "end" and e["call_error"] is None
    }
    todo = []
    for cell in cells:
        record = records.get(cell.call_id)
        if record is None and cell.call_id in replied:
            problems.append(f"{cell.call_id}: log shows a reply but no record exists")
        elif record is None or record["call_error"] is not None:
            todo.append(cell)
    return todo, problems


def archive_failed(out: Path, cell: Cell) -> None:
    """Move a provider-error record aside before its cell is re-called."""
    path = _outputs(out) / "calls" / f"{cell.call_id}.json"
    if not path.exists():
        return
    record = json.loads(path.read_text(encoding="utf-8"))
    if record["call_error"] is None:
        raise SystemExit(f"refusing to archive a model reply: {cell.call_id}")
    dest = (
        _outputs(out)
        / "failed_attempts"
        / f"{cell.call_id}.attempt{record['attempt']}.json"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(path, dest)


# --- commands -----------------------------------------------------------------


def _meta(gate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "provider": gate["provider"],
        "model": gate["model"],
        "seal_commit": gate["seal_commit"],
        "run_commit": gate["head_commit"],
    }


def run(
    config: AppConfig,
    provider: str,
    model: str,
    factory: ProviderFactory = xhr._provider,
    out: Path = OUT,
    gate_fn: Callable[..., dict[str, Any]] = precall_gate,
) -> int:
    """Gate, then every owed cell once; resumes when ``outputs/`` exists."""
    cells = expected_cells(load_manifest(out))
    fresh = not _outputs(out).exists()
    if fresh:
        todo, outputs_check = cells, outputs_absent_check(out)
    else:
        if not (_outputs(out) / "precall_gate.json").is_file():
            raise SystemExit("outputs/ exists without a passed gate; refusing")
        todo, problems = resume_plan(out, cells)
        outputs_check = _check("resume_unambiguous", problems, [c.call_id for c in todo])
    gate = gate_fn(config, provider, model, outputs_check, out)
    if not gate["passed"]:
        failed = [c["name"] for c in gate["checks"] if not c["ok"]]
        write_json_atomic(out / "precall_gate_failed.json", gate)
        raise SystemExit(f"pre-call gate failed; 0 calls made: {failed}")
    spent = [c.call_id for c in todo if attempts_started(out, c.call_id) >= MAX_ATTEMPTS]
    if spent:
        raise SystemExit(f"{MAX_ATTEMPTS} attempts used: {spent}; 0 calls made")
    name = "precall_gate.json" if fresh else f"resume_gate_{gate['checked_at']}.json"
    write_json_atomic(_outputs(out) / name.replace(":", ""), gate)
    with RunLock(out):
        for cell in todo:
            archive_failed(out, cell)
        llm = factory(config, provider, model)
        failures = run_cells(out, todo, llm, _meta(gate), "run" if fresh else "resume")
    logger.warning("J {}: {} cells, {} provider errors", "run", len(todo), failures)
    return 1 if failures else 0


def seal_outputs(out: Path = OUT) -> dict[str, Any]:
    """Hash the output corpus once every planned cell has a model reply."""
    cells = expected_cells(load_manifest(out))
    todo, problems = resume_plan(out, cells)
    if todo or problems or (_outputs(out) / "run.lock").exists():
        raise SystemExit(f"not sealable: {len(todo)} owed, problems {problems}")
    files = sorted(p for p in _outputs(out).rglob("*") if p.is_file())
    lines = [f"{_sha(p)}  {p.relative_to(out)}" for p in files]
    target = out / "outputs.sha256"
    if target.exists():
        raise SystemExit(f"{target} exists; it is sealed")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"files": len(lines), "calls": len(cells)}


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``gate`` (no calls) / ``run`` (or resume) / ``seal``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("gate", "run", "seal"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    config = load_config(default_config_path())
    config.coach.roster_evidence = False
    if args.command == "gate":
        gate = precall_gate(config, PROVIDER, MODEL, outputs_absent_check())
        summary = {c["name"]: c["problems"] or "ok" for c in gate["checks"]}
        sys.stdout.write(json.dumps(summary, indent=1) + "\n")
        return 0 if gate["passed"] else 1
    if args.command == "seal":
        sys.stdout.write(json.dumps(seal_outputs()) + "\n")
        return 0
    if not (args.confirm and args.model):
        raise SystemExit("run needs --model and --confirm")
    return run(config, PROVIDER, args.model)


if __name__ == "__main__":
    raise SystemExit(main())
