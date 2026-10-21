"""N2 citation-compliance experiment: pre-call gate, run once, recovery, score.

``gate`` makes no call. ``run`` evaluates the gate (seal: every sealed file
committed and unchanged since the commit that added ``design.json``;
production code / config / system prompt unchanged since the prompt commit;
byte-for-byte rebuild; design hashes; counts; no output directory) and makes
zero calls on any failure. On a pass every sealed call is made exactly once,
each record written atomically before the next. ``recover`` re-calls only
cells with no record or a provider-error record; a model reply, including an
unparseable one, is a result and is never re-called. ``score`` applies the
frozen scorer once, writes ``scoring.json`` / ``SCORE.md`` and
``SHA256SUMS``, and refuses to overwrite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import citation_compliance_n2 as cc
import citation_compliance_n2_doc as doc
import citation_compliance_n2_score as score
import coaching_units_experiment_d as xd
import coaching_units_experiment_h_run as xhr
import coaching_units_experiment_i_run as xir
from loguru import logger

from splatoon3_ai_coach.coach.llm_client import LLMProvider
from splatoon3_ai_coach.coach.llm_runs import utc_timestamp
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import AppConfig

SEALED_ARTIFACTS = ("design.json", "DESIGN.md", "dry_run.json", "dry_run.md", "inputs")
TOOLING = (
    "tools/reports/citation_compliance_n2.py",
    "tools/reports/citation_compliance_n2_doc.py",
    "tools/reports/citation_compliance_n2_run.py",
    "tools/reports/citation_compliance_n2_score.py",
    "tests/test_citation_compliance_n2.py",
)


@dataclass(frozen=True)
class Cell:
    """One sealed call: a unit's prompt, one repeat."""

    match: str
    cid: str
    repeat: int
    file: str

    @property
    def call_id(self) -> str:
        """Stable output file stem."""
        return f"{Path(self.file).name.removesuffix('.user_prompt.txt')}__r{self.repeat}"


def load_manifest(out: Path) -> dict[str, Any]:
    """The sealed ``inputs/manifest.json``."""
    return json.loads((out / "inputs" / "manifest.json").read_text(encoding="utf-8"))


def expected_cells(manifest: Mapping[str, Any]) -> list[Cell]:
    """Every call in run order: repeat, then unit."""
    return [
        Cell(match=c["match"], cid=c["cid"], repeat=repeat, file=c["file"])
        for repeat in range(1, int(manifest["repeats"]) + 1)
        for c in manifest["calls"]
    ]


def sealed_paths(out: Path) -> list[str]:
    """Repo-relative paths frozen at the seal."""
    rel = out.resolve().relative_to(cc.PROJECT_ROOT)
    return [str(rel / a) for a in SEALED_ARTIFACTS] + list(TOOLING)


def seal_state(out: Path) -> dict[str, Any]:
    """Seal commit (the one that added ``design.json``) and drift since then."""
    paths = sealed_paths(out)
    added = cc.git("log", "--diff-filter=A", "--format=%H", "--", paths[0]).stdout.split()
    seal = added[-1] if added else ""
    problems = [] if seal else ["design.json is not committed"]
    if seal and cc.git("diff", "--quiet", seal, "HEAD", "--", *paths).returncode != 0:
        problems.append(f"sealed files changed since {seal[:7]}")
    if cc.git("status", "--porcelain", "--", *paths).stdout.strip():
        problems.append("sealed files have uncommitted changes")
    tracked = set(cc.git("ls-files", "-z", "--", *paths).stdout.split("\0")) - {""}
    on_disk = {
        str(p.relative_to(cc.PROJECT_ROOT))
        for rel in paths
        for p in (
            [cc.PROJECT_ROOT / rel]
            if (cc.PROJECT_ROOT / rel).is_file()
            else (cc.PROJECT_ROOT / rel).rglob("*")
        )
        if p.is_file()
    }
    if on_disk != tracked:
        problems.append(
            f"sealed file set differs from git: untracked {sorted(on_disk - tracked)}, "
            f"missing {sorted(tracked - on_disk)}"
        )
    return {
        "seal_commit": seal,
        "head_commit": cc.git("rev-parse", "HEAD").stdout.strip(),
        "problems": problems,
    }


def _check(name: str, problems: Sequence[str], detail: Any = None) -> dict[str, Any]:
    return {
        "name": name,
        "ok": not problems,
        "problems": list(problems),
        "detail": detail,
    }


def count_problems(manifest: Mapping[str, Any]) -> list[str]:
    """12 units, 24 unique cells, the registered provider / model / repeats."""
    cells = expected_cells(manifest)
    problems = []
    if [(c["match"], c["cid"]) for c in manifest["calls"]] != [u.key for u in cc.UNITS]:
        problems.append("manifest units differ from the design")
    if int(manifest["repeats"]) != cc.REPEATS:
        problems.append("manifest repeats differ from the design")
    if len(cells) != len(cc.UNITS) * cc.REPEATS or len({c.call_id for c in cells}) != len(
        cells
    ):
        problems.append(f"{len(cells)} cells ({len({c.call_id for c in cells})} unique)")
    return problems


def gate_checks(
    out: Path, config: AppConfig, provider: str, model: str, outputs_check: dict
) -> list[dict[str, Any]]:
    """Every pre-call check, each with its problems."""
    manifest, design = load_manifest(out), cc.load_design(out)
    seal = seal_state(out)
    files = cc.input_files(design, config)
    registered = (manifest["provider"], manifest["model"]) == (cc.PROVIDER, cc.MODEL)
    design_sha = cc.sha(json.dumps(design, sort_keys=True))
    return [
        _check(
            "provider_model",
            []
            if registered and (provider, model) == (cc.PROVIDER, cc.MODEL)
            else [f"got {provider}/{model}"],
            f"{provider}/{model}",
        ),
        _check("seal", seal["problems"], seal),
        _check(
            "production_unchanged_since_prompt_commit",
            cc.prompt_commit_state()["problems"],
        ),
        _check(
            "design_hash",
            []
            if manifest["design_sha256"] == design_sha
            else ["manifest design_sha256 differs from design.json"],
        ),
        _check(
            "rebuild_byte_equal_current_production", cc.staleness(out / "inputs", files)
        ),
        _check("prompt_and_view_hashes", cc.hash_problems(design, files)),
        _check(
            "production_roster_evidence_off",
            [] if config.coach.roster_evidence is False else ["roster_evidence on"],
        ),
        _check("counts_12_units_24_calls", count_problems(manifest)),
        outputs_check,
    ]


def precall_gate(
    out: Path, config: AppConfig, provider: str, model: str, outputs_check: dict
) -> dict[str, Any]:
    """The gate record; ``passed`` only when every check holds."""
    checks = gate_checks(out, config, provider, model, outputs_check)
    return {
        "experiment": cc.EXPERIMENT,
        "checked_at": utc_timestamp(),
        "provider": provider,
        "model": model,
        "passed": all(c["ok"] for c in checks),
        "checks": checks,
    }


def outputs_absent_check(out: Path) -> dict[str, Any]:
    """The output destination must not exist before ``run``."""
    path = out / "outputs"
    return _check("output_destination_empty", [f"{path} exists"] if path.exists() else [])


def call_cell(
    cell: Cell, inputs: Path, provider: LLMProvider, meta: Mapping[str, Any], attempt: int
) -> dict[str, Any]:
    """One provider call; provider errors are recorded, not raised."""
    system = (inputs / "system_prompt.txt").read_text(encoding="utf-8")
    prompt = (inputs / cell.file).read_text(encoding="utf-8")
    record: dict[str, Any] = {
        "experiment": True,
        "experiment_name": cc.EXPERIMENT,
        "call_id": cell.call_id,
        **asdict(cell),
        **meta,
        "attempt": attempt,
        "system_sha256": cc.sha(system),
        "user_prompt_sha256": cc.sha(prompt),
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
    out: Path, cells: Sequence[Cell], provider: LLMProvider, meta: Mapping, phase: str
) -> int:
    """Call each cell once, writing its record before the next; returns failures."""
    failures = 0
    for i, cell in enumerate(cells, start=1):
        attempt = 1 + len(
            list(xir._failed_dir(out).glob(f"{cell.call_id}.attempt*.json"))
        )
        base = {"phase": phase, "call_id": cell.call_id, "attempt": attempt}
        xir._log(out, {**base, "event": "start", "at": utc_timestamp()})
        record = call_cell(cell, out / "inputs", provider, meta, attempt)
        xir._write_json_atomic(out / "outputs" / "calls" / f"{cell.call_id}.json", record)
        failures += record["call_error"] is not None
        xir._log(
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
            "N2 {} {}/{} {} {} ({}s)",
            phase,
            i,
            len(cells),
            cell.call_id,
            "ok" if record["call_error"] is None else "PROVIDER ERROR",
            record["elapsed_seconds"],
        )
    return failures


def _meta(gate: Mapping[str, Any]) -> dict[str, Any]:
    seal = next(c for c in gate["checks"] if c["name"] == "seal")["detail"]
    return {
        "provider": gate["provider"],
        "model": gate["model"],
        "seal_commit": seal["seal_commit"],
        "run_commit": seal["head_commit"],
    }


def run(out: Path, config: AppConfig, provider: str, model: str) -> int:
    """Gate, then every sealed call exactly once; zero calls if the gate fails."""
    gate = precall_gate(out, config, provider, model, outputs_absent_check(out))
    if not gate["passed"]:
        xhr._write_json(out / "precall_gate_failed.json", gate)
        failed = [c["name"] for c in gate["checks"] if not c["ok"]]
        raise SystemExit(f"pre-call gate failed; 0 calls made: {failed}")
    xir._write_json_atomic(out / "outputs" / "precall_gate.json", gate)
    cells = expected_cells(load_manifest(out))
    failures = run_cells(
        out, cells, xhr._provider(config, provider, model), _meta(gate), "run"
    )
    logger.warning("N2 run done: {} cells, {} provider errors", len(cells), failures)
    return 1 if failures else 0


def recover(out: Path, config: AppConfig, provider: str, model: str) -> int:
    """Re-call only missing / provider-error cells; refuses when ambiguous."""
    if not (out / "outputs" / "precall_gate.json").is_file():
        raise SystemExit("nothing to recover: run did not pass its gate")
    cells = expected_cells(load_manifest(out))
    todo, problems = xir.recovery_plan(out, cells)
    check = _check("recovery_unambiguous", problems, [c.call_id for c in todo])
    gate = precall_gate(out, config, provider, model, check)
    if not gate["passed"]:
        failed = [c["name"] for c in gate["checks"] if not c["ok"]]
        raise SystemExit(f"recovery gate failed; 0 calls made: {failed}")
    stamp = gate["checked_at"].replace(":", "")
    xir._write_json_atomic(out / "outputs" / f"recovery_gate_{stamp}.json", gate)
    for cell in todo:
        xir.archive_failed(out, cell)
    failures = run_cells(
        out, todo, xhr._provider(config, provider, model), _meta(gate), "recover"
    )
    return 1 if failures else 0


def _records(out: Path, cells: Sequence[Cell]) -> list[dict[str, Any]]:
    records = []
    for cell in cells:
        path = out / "outputs" / "calls" / f"{cell.call_id}.json"
        if not path.is_file():
            raise SystemExit(f"missing record {cell.call_id}; run / recover first")
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["call_error"] is not None:
            raise SystemExit(f"provider error in {cell.call_id}; recover first")
        records.append(record)
    return records


def _noncompliant(outputs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"call_id": o["call_id"], "text": e["text"], "class": c["class"], **c}
        for o in outputs
        for e in o["entries"]
        for c in e["citations"]
        if c["class"] != score.COMPLIANT
    ]


def write_checksums(out: Path) -> int:
    """``SHA256SUMS`` over every file of the experiment except itself."""
    files = sorted(p for p in out.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    lines = [
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(out)}"
        for p in files
    ]
    (out / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def score_run(out: Path) -> int:
    """Apply the frozen scorer once; write scoring.json, SCORE.md, SHA256SUMS."""
    if (out / "scoring.json").exists():
        raise SystemExit("scoring.json exists; scoring is immutable")
    cells = expected_cells(load_manifest(out))
    outputs = []
    for record in _records(out, cells):
        prompt = (out / "inputs" / record["file"]).read_text(encoding="utf-8")
        outputs.append(score.score_output(xd.split_prompt(prompt)[0], record))
    result = {
        "experiment": cc.EXPERIMENT,
        "scored_at": utc_timestamp(),
        "summary": score.summary(outputs, len(cells)),
        "noncompliant": _noncompliant(outputs),
        "outputs": outputs,
    }
    xir._write_json_atomic(out / "scoring.json", result)
    (out / "SCORE.md").write_text(doc.score_markdown(result), encoding="utf-8")
    logger.warning(
        "N2 scored: gate {}; {} files checksummed",
        "PASS" if result["summary"]["gate"]["passed"] else "FAIL",
        write_checksums(out),
    )
    return 0


def dispatch(args: argparse.Namespace, config_path: Path) -> int:
    """``gate`` (no calls) / ``run`` / ``recover`` / ``score``."""
    if args.command == "score":
        return score_run(args.out)
    config = load_config(config_path)
    if args.command == "gate":
        gate = precall_gate(
            args.out,
            config,
            args.provider,
            args.model or cc.MODEL,
            outputs_absent_check(args.out),
        )
        report = {c["name"]: c["problems"] or "ok" for c in gate["checks"]}
        sys.stdout.write(json.dumps(report, indent=1) + "\n")
        return 0 if gate["passed"] else 1
    if not (args.confirm and args.model):
        raise SystemExit(f"{args.command} needs --model and --confirm")
    command = run if args.command == "run" else recover
    return command(args.out, config, args.provider, args.model)
