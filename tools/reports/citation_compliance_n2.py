"""N2 citation-compliance experiment (pre-registered in ``DESIGN.md``).

Research only; a citation-interface experiment, not a coaching-value one (no
ratings). One question: under the production prompt as of ``PROMPT_COMMIT``,
does the model cite supplied evidence as literal scalar CoachLlmView (N2)
paths with the value at that path, without legacy ``source_path`` /
CoachInput / hybrid forms?

One arm: the production view, user prompt and system prompt
(``coach.roster_evidence=false``) with the production provider and model,
over 12 death units chosen by a fixed rule, 2 repeats. The scorer
(``citation_compliance_n2_score``) and the gate are frozen here, before any
model output exists. ``design`` / ``build`` / ``plan`` make no model call;
``gate`` / ``run`` / ``recover`` / ``score`` live in
``citation_compliance_n2_run``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import citation_compliance_n2_score as score
import coaching_units_experiment_d as xd
import coaching_units_experiment_h as xh
from loguru import logger

from splatoon3_ai_coach.coach.artifacts import COACH_EVIDENCE_VERSION, safe_filename
from splatoon3_ai_coach.coach.coach import annotate_claim_flags
from splatoon3_ai_coach.coach.llm_client import CoachingAssessment
from splatoon3_ai_coach.coach.llm_view import LLM_VIEW_SCHEMA_VERSION
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT

EXPERIMENT = "citation_compliance_n2"
OUT_DIR = PROJECT_ROOT / "analysis" / "importance_report" / EXPERIMENT
ANALYSIS_ROOT = PROJECT_ROOT / "analysis"
MATCH_PREFIX = "en-live-playerview-"
PROMPT_COMMIT = "3a3121b"
PRODUCTION_PATHS = ("src", "configs/default.yaml")
SYSTEM_PROMPT_FILE = "src/splatoon3_ai_coach/coach/prompts/coach_system.txt"
PROVIDER = "cursor"
MODEL = "gpt-5.6-luna"
REPEATS = 2
REQUIRED_FAMILIES = (
    "clock",
    "timeline",
    "special",
    "map",
    "low_ink",
    "related",
    "claim_basis",
    "source_path",
)
SELECTION_RULE = (
    "(a) the 7 deaths whose Experiment I old-prompt outputs carried the "
    "historical legacy / object-path citations (named here, rebuilt under "
    "current production; Experiment I prompts and outputs are not used); "
    "(b) every local death view with low_ink evidence; (c) every local death "
    "view with two related units. (b) and (c) are checked against a census "
    "of all local death views."
)
_HISTORY = "Experiment I old-prompt outputs on this death cited "
_CLOCK_LABEL = "game_clock_samples[label=death].observation.seconds_remaining"
_DURATION_LABEL = "durations.time_since_previous_death"
_DURATION_OBJECT = "timeline.time_since_previous_death (no .value)"


@dataclass(frozen=True)
class Unit:
    """One selected death unit and why it was selected."""

    match: str
    cid: str
    group: str
    reason: str

    @property
    def key(self) -> tuple[str, str]:
        """``(analysis dir name, candidate id)``."""
        return (MATCH_PREFIX + self.match, self.cid)

    @property
    def slug(self) -> str:
        """Stable file stem."""
        return safe_filename(f"{self.match}_{self.cid}")


UNITS = (
    Unit(
        "2026-10-11 18-25-16",
        "death_episode:175.500",
        "experiment_i_history",
        f"{_HISTORY}{_CLOCK_LABEL}; final 30s claim",
    ),
    Unit(
        "2026-10-11 20-33-31",
        "death_episode:199.500",
        "experiment_i_history",
        f"{_HISTORY}{_DURATION_LABEL} and {_DURATION_OBJECT}; quick re-death",
    ),
    Unit(
        "2026-10-11 22-20-42",
        "death_episode:152.000",
        "experiment_i_history",
        f"{_HISTORY}{_DURATION_LABEL}; quick re-death, related engagement",
    ),
    Unit(
        "2026-10-11 22-31-53",
        "death_episode:208.500",
        "experiment_i_history",
        f"{_HISTORY}{_CLOCK_LABEL}; own splat before, related leads_to",
    ),
    Unit(
        "2026-10-11 22-37-47",
        "death_episode:58.000",
        "experiment_i_history",
        f"{_HISTORY}{_CLOCK_LABEL}; score / zone_control, special ready",
    ),
    Unit(
        "2026-10-11 22-57-30",
        "death_episode:308.000",
        "experiment_i_history",
        f"{_HISTORY}{_CLOCK_LABEL}; score / zone_control, behind in count",
    ),
    Unit(
        "2026-10-12 20-17-32",
        "death_episode:65.500",
        "experiment_i_history",
        f"{_HISTORY}{_DURATION_LABEL} and {_DURATION_OBJECT}; score / zone_control",
    ),
    Unit(
        "2026-10-10 23-43-31",
        "death_episode:205.000",
        "low_ink",
        "low_ink at death; related leads_to; citation-contract fixture",
    ),
    Unit(
        "2026-10-10 23-56-41",
        "death_episode:46.000",
        "low_ink",
        "low_ink at death; first 30s claim",
    ),
    Unit(
        "2026-10-12 19-47-46",
        "death_episode:80.000",
        "low_ink",
        "low_ink at death; score / zone_control; 7 claims incl. map overlay false",
    ),
    Unit(
        "2026-10-10 23-43-31",
        "death_episode:226.000",
        "related_two",
        "two related units (one leads_to); own splat before",
    ),
    Unit(
        "2026-10-11 22-57-30",
        "death_episode:142.500",
        "related_two",
        "two related units (one leads_to); score / zone_control; most source_path labels",
    ),
)


def sha(text: str) -> str:
    """sha256 hex of UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def view_sha(view: Mapping[str, Any]) -> str:
    """sha256 of the view exactly as the user prompt serializes it."""
    return sha(
        json.dumps(view, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run git in the repository root (never raises)."""
    return subprocess.run(
        ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False
    )


def local_matches() -> list[str]:
    """Every local live analysis directory name."""
    return sorted(p.name for p in ANALYSIS_ROOT.glob(MATCH_PREFIX + "*") if p.is_dir())


def production_views(
    config: AppConfig, matches: Sequence[str]
) -> dict[tuple[str, str], xh.DeathView]:
    """Every death unit of ``matches`` through the production builders."""
    if config.coach.roster_evidence is not False:
        raise SystemExit("production config must have coach.roster_evidence=false")
    return xh.arm_views(config, "A", matches)


def features(view: Mapping[str, Any]) -> dict[str, Any]:
    """Which evidence families a view supplies (non-null, non-provenance leaves)."""
    leaves = {
        p: score.walk(view, p)[1]
        for p in score.view_index(view).nodes
        if not isinstance(score.walk(view, p)[1], dict | list)
    }

    def present(prefix: str) -> bool:
        return any(
            p.startswith(prefix) and v is not None and p.split(".")[-1] != "source_path"
            for p, v in leaves.items()
        )

    return {
        **{f: present(f) for f in ("clock", "timeline", "special", "map", "low_ink")},
        "related": len(view.get("related") or []),
        "claim_basis": len(view.get("claim_basis") or []),
        "source_path": sum(1 for p in leaves if p.split(".")[-1] == "source_path"),
        "score": present("score."),
        "zone_control": present("zone_control."),
    }


def selection_problems(census: Mapping[tuple[str, str], Mapping[str, Any]]) -> list[str]:
    """The rule's (b) / (c) sets must equal the named units; families covered."""
    named = {
        g: {u.key for u in UNITS if u.group == g} for g in ("low_ink", "related_two")
    }
    rule = {
        "low_ink": {k for k, f in census.items() if f["low_ink"]},
        "related_two": {k for k, f in census.items() if f["related"] >= 2},
    }
    problems = [
        f"{g}: rule selects {sorted(rule[g])}" for g in rule if rule[g] != named[g]
    ]
    problems += [f"unit {u.key} not in census" for u in UNITS if u.key not in census]
    for family in REQUIRED_FAMILIES:
        if not any(census.get(u.key, {}).get(family) for u in UNITS):
            problems.append(f"family {family} not exercised")
    return problems


def cli_equivalence(config_path: Path) -> dict[str, Any]:
    """Selected views against ``s3-coach coach-inputs`` output (temp dirs)."""
    config = load_config(config_path)
    views = production_views(config, sorted({u.key[0] for u in UNITS}))
    matched, problems = 0, []
    with tempfile.TemporaryDirectory() as tmp:
        for match in sorted({u.key[0] for u in UNITS}):
            dest = Path(tmp) / safe_filename(match)
            cli = xh._cli_views(ANALYSIS_ROOT / match, config_path, dest)
            for unit in (u for u in UNITS if u.key[0] == match):
                same = cli.get(unit.cid) == views[unit.key].view
                matched += same
                problems += [] if same else [f"{unit.key} differs from coach-inputs"]
    return {"matched": matched, "total": len(UNITS), "problems": problems}


def prompt_commit_state() -> dict[str, Any]:
    """Production code / config / system prompt unchanged since PROMPT_COMMIT."""
    full = git("rev-parse", "--verify", "--quiet", f"{PROMPT_COMMIT}^{{commit}}").stdout
    committed = git("diff", "--quiet", PROMPT_COMMIT, "HEAD", "--", *PRODUCTION_PATHS)
    working = git("status", "--porcelain", "--", *PRODUCTION_PATHS).stdout.strip()
    at_commit = git("show", f"{PROMPT_COMMIT}:{SYSTEM_PROMPT_FILE}").stdout
    problems = []
    if not full.strip():
        problems.append(f"{PROMPT_COMMIT} not found")
    if committed.returncode != 0:
        problems.append(f"production paths changed since {PROMPT_COMMIT}")
    if working:
        problems.append("production paths have uncommitted changes")
    if at_commit != load_system_prompt(roster_evidence=False):
        problems.append(f"system prompt differs from {PROMPT_COMMIT}")
    return {
        "prompt_commit": full.strip(),
        "system_prompt_sha256": sha(at_commit),
        "problems": problems,
    }


def _unit_doc(unit: Unit, death: xh.DeathView) -> dict[str, Any]:
    return {
        **asdict(unit),
        "analysis": unit.key[0],
        "features": features(death.view),
        "view_sha256": view_sha(death.view),
        "user_prompt_sha256": sha(death.prompt),
        "user_prompt_chars": len(death.prompt),
        "importance_score": death.score,
        "rank": death.rank,
        "selected_for_llm": death.selected,
    }


def frozen_scoring() -> dict[str, Any]:
    """The scoring rules and gate, as recorded in the design."""
    return {
        "classes": list(score.CLASSES),
        "compliant_class": score.COMPLIANT,
        "gate_groups": {g: list(c) for g, c in score.GATE_GROUPS.items()},
        "gate_rules": score.GATE_RULES,
        "value_rule": (
            "value compared by meaning at the exact location: numbers exactly "
            "(no tolerance), booleans and null by JSON value, text exactly; "
            "written form (exact / remark / lenient) is a secondary metric"
        ),
        "secondary_metrics": [
            "value written form",
            "prose before the path",
            "several facts in one entry",
            "selected claim cited by one of its evidence_paths",
            "outputs with no citation",
            "units compliant on every repeat",
            "production validator verdict and every disagreement with the strict verdict",
            "other validator hits (recorded only; not part of the gate)",
        ],
    }


def design_doc(config_path: Path) -> dict[str, Any]:
    """The full pre-registered design (no model calls)."""
    config = load_config(config_path)
    views = production_views(config, local_matches())
    census = {key: features(death.view) for key, death in views.items()}
    problems = selection_problems(census)
    state = prompt_commit_state()
    equivalence = cli_equivalence(config_path)
    problems += state["problems"] + equivalence["problems"]
    if problems:
        raise SystemExit("design not reproducible: " + "; ".join(problems))
    system = load_system_prompt(roster_evidence=False)
    first = views[UNITS[0].key].prompt
    return {
        "schema_version": 1,
        "experiment": EXPERIMENT,
        "question": (
            "Under the rewritten production prompt, does the model reliably cite "
            "supplied evidence using literal scalar CoachLlmView (N2) paths with "
            "correct values, without falling back to legacy source_path / "
            "CoachInput / hybrid forms?"
        ),
        "git_head": git("rev-parse", "HEAD").stdout.strip(),
        **{k: state[k] for k in ("prompt_commit", "system_prompt_sha256")},
        "user_preamble_sha256": sha(first[: first.index("{")]),
        "config_sha256": sha(config_path.read_text(encoding="utf-8")),
        "roster_evidence": config.coach.roster_evidence,
        "llm_view_schema_version": LLM_VIEW_SCHEMA_VERSION,
        "coach_evidence_version": COACH_EVIDENCE_VERSION,
        "provider": PROVIDER,
        "model": MODEL,
        "temperature_support": str(xd.temperature_support(PROVIDER)),
        "temperature_note": xd.SUPPORT_NOTES[PROVIDER],
        "repeats": REPEATS,
        "calls": len(UNITS) * REPEATS,
        "system_prompt_chars": len(system),
        "selection_rule": SELECTION_RULE,
        "census": {"death_views": len(census), "matches": len(local_matches())},
        "cli_equivalence": equivalence,
        "units": [_unit_doc(u, views[u.key]) for u in UNITS],
        "scoring": frozen_scoring(),
    }


def load_design(out: Path) -> dict[str, Any]:
    """The frozen design document."""
    return json.loads((out / "design.json").read_text(encoding="utf-8"))


def input_files(design: Mapping[str, Any], config: AppConfig) -> dict[str, str]:
    """Relative path -> text for the system prompt, user prompts and manifest."""
    views = production_views(config, sorted({u.key[0] for u in UNITS}))
    files = {"system_prompt.txt": load_system_prompt(roster_evidence=False)}
    calls = []
    for unit in UNITS:
        rel = f"{unit.slug}.user_prompt.txt"
        files[rel] = views[unit.key].prompt
        calls.append({"match": unit.key[0], "cid": unit.cid, "file": rel})
    manifest = {
        "experiment": EXPERIMENT,
        "design_sha256": sha(json.dumps(design, sort_keys=True)),
        "provider": PROVIDER,
        "model": MODEL,
        "repeats": REPEATS,
        "system": "system_prompt.txt",
        "calls": calls,
    }
    files["manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    return files


def staleness(inputs: Path, files: Mapping[str, str]) -> list[str]:
    """Stored inputs that are not byte-identical to a fresh rebuild."""
    stored = {
        str(p.relative_to(inputs)): p.read_text(encoding="utf-8")
        for p in inputs.rglob("*")
        if p.is_file()
    }
    problems = [f"{rel} differs" for rel in files if stored.get(rel) != files[rel]]
    return problems + [f"{rel} unexpected" for rel in sorted(set(stored) - set(files))]


def hash_problems(design: Mapping[str, Any], files: Mapping[str, str]) -> list[str]:
    """Rebuilt prompts and views must carry the design's recorded hashes."""
    problems = []
    if sha(files["system_prompt.txt"]) != design["system_prompt_sha256"]:
        problems.append("system prompt hash differs from the design")
    for unit, row in zip(UNITS, design["units"], strict=True):
        prompt = files[f"{unit.slug}.user_prompt.txt"]
        view, _ = xd.split_prompt(prompt)
        if (row["match"], row["cid"]) != (unit.match, unit.cid):
            problems.append(f"design unit order differs at {unit.key}")
        if (
            sha(prompt) != row["user_prompt_sha256"]
            or view_sha(view) != row["view_sha256"]
        ):
            problems.append(f"{unit.key}: prompt / view hash differs from the design")
    return problems


def build(out: Path, config_path: Path) -> int:
    """Write ``out/inputs`` from the frozen design; never overwrites."""
    inputs = out / "inputs"
    if inputs.exists():
        raise SystemExit(f"{inputs} exists; never overwritten")
    design = load_design(out)
    files = input_files(design, load_config(config_path))
    problems = hash_problems(design, files)
    if problems:
        raise SystemExit("design no longer reproduces: " + "; ".join(problems))
    for rel, text in sorted(files.items()):
        (inputs / rel).parent.mkdir(parents=True, exist_ok=True)
        (inputs / rel).write_text(text, encoding="utf-8")
    logger.warning("{} inputs: {} files under {}", EXPERIMENT, len(files), inputs)
    return 0


# --- dry run: scorer smoke test on fixed synthetic citations ---------------------

SMOKE_UNIT = UNITS[7]


def _first_null_leaf(view: Mapping[str, Any]) -> str:
    return next(
        p
        for p in score.view_index(view).nodes
        if score.walk(view, p)[1] is None and p.split(".")[-1] != "source_path"
    )


def smoke_cases(view: Mapping[str, Any]) -> list[tuple[str, tuple[str, ...]]]:
    """Fixed synthetic citations and their expected classes (frozen)."""
    clock, death = view["clock"], view["death"]["death_to_active_again"]
    leads = view["related"][0]["relations"]["leads_to_death_episode_id"]
    ok = (score.COMPLIANT,)
    return [
        (f"clock.display = {json.dumps(clock['display'])}", ok),
        (f"clock.seconds_remaining = {clock['seconds_remaining']}", ok),
        (f"death.death_to_active_again.value = {death['value']} seconds", ok),
        (f"clock.display = {clock['display']}", ok),
        (f"related[0].relations.leads_to_death_episode_id = {json.dumps(leads)}", ok),
        (
            f"claim_basis[0].claim_id = {json.dumps(view['claim_basis'][0]['claim_id'])}",
            ok,
        ),
        (f"{_first_null_leaf(view)} = null", ok),
        (f"Clock: clock.display = {json.dumps(clock['display'])}", ok),
        (
            f"clock.display = {json.dumps(clock['display'])}, "
            f"clock.seconds_remaining = {clock['seconds_remaining']}",
            ok + ok,
        ),
        (
            f"clock.seconds_remaining = {clock['seconds_remaining'] + 1}",
            ("n2_scalar_wrong_value",),
        ),
        (
            f"death.death_to_active_again.value = {death['value'] + 0.04}",
            ("n2_scalar_wrong_value",),
        ),
        (f"death.death_to_active_again = {death['value']}", ("n2_object_asserted",)),
        (
            f"clock.source_path = {json.dumps(clock['source_path'])}",
            ("source_path_field",),
        ),
        (f"{_CLOCK_LABEL} = {clock['seconds_remaining']}", ("legacy_label",)),
        (
            f"{clock['source_path']}.display = {json.dumps(clock['display'])}",
            ("legacy_label",),
        ),
        (
            "primary_context.timeline.time_since_previous_death.value = 1.0",
            ("n1_coachinput",),
        ),
        ("related[0].scenario.start_time = 1.0", ("n1_coachinput",)),
        ('map_check_before_death.status = "observed"', ("relative_path",)),
        ("clock.seconds_left = 1", ("unresolved_path",)),
        ('enemy.weapon = "splattershot"', ("unsupported_invented",)),
        ("death.death_to_active_again.value", ("malformed",)),
        (f"clock.display: {json.dumps(clock['display'])}", ("malformed",)),
        ("The clock showed the final seconds of the match", ("malformed",)),
    ]


def smoke_test(view: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Strict classes vs expectations, and the production validator's verdict."""
    index = score.view_index(view)
    rows = []
    for text, expected in smoke_cases(view):
        entry = score.score_entry(view, text, index)
        got = tuple(c["class"] for c in entry["citations"])
        validation = annotate_claim_flags(
            CoachingAssessment(evidence_used=[text]), dict(view)
        )
        compared = score.validator_comparison(validation, [entry])["entries"][0]
        rows.append(
            {
                "text": text,
                "expected": list(expected),
                "got": list(got),
                "ok": got == expected,
                "value_forms": [c["value_form"] for c in entry["citations"]],
                "validator_kind": compared["validator_kind"],
                "validator_accepts": compared["validator_accepts"],
                "strict_compliant": entry["compliant"],
            }
        )
    return rows


def plan_doc(out: Path, config: AppConfig) -> dict[str, Any]:
    """Dry run: two rebuilds, staleness, hashes, scorer smoke test; no call."""
    design = load_design(out)
    files = input_files(design, config)
    again = input_files(design, config)
    view, _ = xd.split_prompt(files[f"{SMOKE_UNIT.slug}.user_prompt.txt"])
    smoke = smoke_test(view)
    return {
        "dry_run": True,
        "llm_calls_made": 0,
        "provider": PROVIDER,
        "model": MODEL,
        "repeats": REPEATS,
        "planned_calls": len(UNITS) * REPEATS,
        "rebuild_deterministic": files == again,
        "stale_inputs": staleness(out / "inputs", files),
        "hash_problems": hash_problems(design, files),
        "prompt_commit_problems": prompt_commit_state()["problems"],
        "smoke_unit": list(SMOKE_UNIT.key),
        "smoke_failures": [r["text"] for r in smoke if not r["ok"]],
        "smoke_test": smoke,
    }


def plan(out: Path, config_path: Path) -> int:
    """Write ``dry_run.json`` / ``dry_run.md``; no model call."""
    import citation_compliance_n2_doc as doc

    result = plan_doc(out, load_config(config_path))
    (out / "dry_run.json").write_text(json.dumps(result, indent=1) + "\n", "utf-8")
    text = doc.dry_run_markdown(result)
    (out / "dry_run.md").write_text(text, "utf-8")
    sys.stdout.write(text)
    bad = (
        not result["rebuild_deterministic"]
        or result["stale_inputs"]
        or result["hash_problems"]
        or result["prompt_commit_problems"]
        or result["smoke_failures"]
    )
    return 1 if bad else 0


def design(out: Path, config_path: Path) -> int:
    """Write ``design.json`` / ``DESIGN.md`` once; refuses to overwrite."""
    import citation_compliance_n2_doc as doc

    target = out / "design.json"
    if target.exists():
        raise SystemExit(f"{target} exists; the design is frozen")
    result = design_doc(config_path)
    out.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    text = doc.design_markdown(result)
    (out / "DESIGN.md").write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: ``design`` / ``build`` / ``plan`` (no calls); ``gate`` / ``run`` /
    ``recover`` / ``score`` (``citation_compliance_n2_run``)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "command", choices=("design", "build", "plan", "gate", "run", "recover", "score")
    )
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--provider", default=PROVIDER)
    parser.add_argument("--model", default=None)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args(argv)
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    config_path = args.config.resolve()
    if args.command in ("design", "build", "plan"):
        command = {"design": design, "build": build, "plan": plan}[args.command]
        return command(args.out, config_path)
    import citation_compliance_n2_run as run

    return run.dispatch(args, config_path)


if __name__ == "__main__":
    raise SystemExit(main())
