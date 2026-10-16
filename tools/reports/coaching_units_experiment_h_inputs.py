"""Experiment H stop point 2: build sealed per-arm inputs and the dry run.

Inputs are the production prompts for the frozen design's 22 deaths under
each arm. ``build`` refuses to write if the recomputed classification no
longer matches ``design.json`` and never overwrites. ``plan`` makes no model
call: it rebuilds every prompt and reports any byte difference as stale.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import coaching_units_experiment_d as xd
import coaching_units_experiment_h as xh
from loguru import logger

from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import AppConfig

Views = Mapping[str, Mapping[tuple[str, str], xh.DeathView]]


def slug(match: str, cid: str) -> str:
    """File-safe name for one death."""
    short = match.removeprefix("en-live-playerview-")
    return re.sub(r"[^A-Za-z0-9]+", "_", f"{short}__{cid}").strip("_")


def load_design(out: Path) -> dict[str, Any]:
    """The frozen design document."""
    return json.loads((out / "design.json").read_text(encoding="utf-8"))


def design_deaths(design: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Treatment then control deaths, each tagged with its set and change type."""
    by_key = {(r["match"], r["cid"]): r for r in design["deaths"]}
    out = []
    for group in ("treatment", "controls"):
        for match, cid in design[group]:
            row = by_key[(match, cid)]
            out.append(
                {
                    "match": match,
                    "cid": cid,
                    "set": "treatment" if group == "treatment" else "control",
                    "change_type": row["change_type"],
                }
            )
    return out


def check_design(design: Mapping[str, Any], views: Views) -> list[str]:
    """Differences between the recomputed classification and the frozen one."""
    rows = {(r["match"], r["cid"]): r for r in xh.classify_deaths(views)}
    problems = []
    for frozen in design["deaths"]:
        key = (frozen["match"], frozen["cid"])
        now = rows.get(key)
        if now is None:
            problems.append(f"{key}: death missing")
            continue
        for field in ("change_type", "prompts_identical_b_c", "C_selected"):
            if now[field] != frozen[field]:
                problems.append(f"{key}: {field} {frozen[field]} -> {now[field]}")
    if len(rows) != len(design["deaths"]):
        problems.append(f"death count {len(design['deaths'])} -> {len(rows)}")
    return problems


def input_files(design: Mapping[str, Any], views: Views) -> dict[str, str]:
    """Relative path -> text for every arm's system and user prompts + manifest."""
    files = {f"system_prompt_{arm}.txt": xh.arm_system_prompt(arm) for arm in xh.ARMS}
    entries = []
    for death in design_deaths(design):
        key = (death["match"], death["cid"])
        for arm in xh.ARMS:
            rel = f"{arm}/{slug(*key)}.user_prompt.txt"
            files[rel] = views[arm][key].prompt
            entries.append(
                {**death, "arm": arm, "file": rel, "system": f"system_prompt_{arm}.txt"}
            )
    manifest = {
        "design_sha256": xh._sha(json.dumps(design, sort_keys=True)),
        "arms": list(xh.ARMS),
        "repeats": design["repeats"],
        "provider": design["provider"],
        "model": design["model"],
        "calls": entries,
    }
    files["manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    return files


def build(out: Path, config_path: Path) -> int:
    """Write ``out/inputs`` from the frozen design; never overwrites."""
    inputs = out / "inputs"
    if inputs.exists():
        raise SystemExit(f"{inputs} exists; never overwritten")
    design = load_design(out)
    base = load_config(config_path)
    views = {arm: xh.arm_views(base, arm) for arm in xh.ARMS}
    problems = check_design(design, views)
    if problems:
        raise SystemExit("design no longer reproduces: " + "; ".join(problems))
    files = input_files(design, views)
    for rel, text in sorted(files.items()):
        path = inputs / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    logger.warning("Experiment H inputs: {} files under {}", len(files), inputs)
    return 0


def load_manifest(inputs: Path) -> dict[str, Any]:
    """The inputs manifest."""
    return json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))


def staleness(inputs: Path, base: AppConfig) -> list[str]:
    """Stored inputs that are no longer byte-identical to production output."""
    manifest = load_manifest(inputs)
    views = {arm: xh.arm_views(base, arm) for arm in xh.ARMS}
    problems = []
    for arm in xh.ARMS:
        stored = (inputs / f"system_prompt_{arm}.txt").read_text(encoding="utf-8")
        if stored != xh.arm_system_prompt(arm):
            problems.append(f"system_prompt_{arm}.txt differs from production")
    for call in manifest["calls"]:
        stored = (inputs / call["file"]).read_text(encoding="utf-8")
        if stored != views[call["arm"]][(call["match"], call["cid"])].prompt:
            problems.append(f"{call['file']} differs from production")
    return problems


def roster_summary(view: Mapping[str, Any]) -> str:
    """Compact roster block description for the dry run."""
    roster = view.get("roster")
    if roster is None:
        return "no roster block"
    before, at = roster.get("before_death"), roster.get("at_death")

    def avb(p: Mapping[str, Any] | None) -> str:
        return "-" if not p else f"{p['ally_alive_count']}v{p['opponent_alive_count']}"

    window = [
        f"{w['ally_alive_count']}v{w['opponent_alive_count']}"
        if w.get("ally_alive_count") is not None
        else "-"
        for w in roster.get("player_count_window") or ()
    ]
    return f"before {avb(before)}, at {avb(at)}, window {' '.join(window)}"


def death_rows(inputs: Path, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Per death: prompt sizes and roster block per arm, B/C identity."""
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for call in manifest["calls"]:
        prompt = (inputs / call["file"]).read_text(encoding="utf-8")
        view, _ = xd.split_prompt(prompt)
        row = rows.setdefault(
            (call["match"], call["cid"]),
            {k: call[k] for k in ("match", "cid", "set", "change_type")},
        )
        row[f"{call['arm']}_chars"] = len(prompt)
        row[f"{call['arm']}_roster"] = roster_summary(view)
        row[f"{call['arm']}_prompt"] = prompt
    for row in rows.values():
        row["b_equals_c"] = row.pop("B_prompt") == row.pop("C_prompt")
        row.pop("A_prompt")
    return list(rows.values())


def plan_doc(inputs: Path, base: AppConfig) -> dict[str, Any]:
    """Dry run: calls, sizes, staleness, temperature handling, roster blocks."""
    manifest = load_manifest(inputs)
    repeats = int(manifest["repeats"])
    provider = manifest["provider"]
    support = xd.temperature_support(provider)
    systems = {
        arm: (inputs / f"system_prompt_{arm}.txt").read_text(encoding="utf-8")
        for arm in xh.ARMS
    }
    arms = []
    for arm in xh.ARMS:
        calls = [c for c in manifest["calls"] if c["arm"] == arm]
        chars = sum(len((inputs / c["file"]).read_text("utf-8")) for c in calls)
        arms.append(
            {
                "arm": arm,
                "inputs": len(calls),
                "calls": len(calls) * repeats,
                "user_prompt_chars_per_repeat": chars,
                "system_prompt_chars": len(systems[arm]),
            }
        )
    deaths = death_rows(inputs, manifest)
    return {
        "dry_run": True,
        "llm_calls_made": 0,
        "provider": provider,
        "model": manifest["model"],
        "repeats": repeats,
        "temperature_support": support,
        "temperature_honored": support == "supported",
        "temperature_note": xd.SUPPORT_NOTES[provider],
        "stale_inputs": staleness(inputs, base),
        "invariant_problems": check_rows(deaths),
        "total_calls": sum(a["calls"] for a in arms),
        "arms": arms,
        "deaths": deaths,
    }


def plan_markdown(doc: Mapping[str, Any]) -> str:
    """Human-readable dry run."""
    lines = [
        "# Experiment H dry run (no LLM calls)",
        "",
        f"- Provider / model: `{doc['provider']}` / `{doc['model']}`",
        f"- Temperature support: **{doc['temperature_support']}** "
        f"({doc['temperature_note']})",
        "- Stale inputs (rebuilt from production, byte comparison): "
        + ("; ".join(doc["stale_inputs"]) if doc["stale_inputs"] else "none"),
        "- Invariants (A has no roster block; control B = C; treatment B != C): "
        + (
            "; ".join(doc["invariant_problems"])
            if doc["invariant_problems"]
            else "all hold"
        ),
        f"- Calls: {doc['total_calls']} ({doc['repeats']} repeats); "
        f"model calls made: {doc['llm_calls_made']}",
        "",
        "| arm | inputs | calls | user-prompt chars (per repeat) | system chars |",
        "| --- | --- | --- | --- | --- |",
        *[
            f"| {a['arm']} | {a['inputs']} | {a['calls']} | "
            f"{a['user_prompt_chars_per_repeat']} | {a['system_prompt_chars']} |"
            for a in doc["arms"]
        ],
        "",
        "| set | death | change | A roster | B roster | C roster | B = C |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *[
            f"| {r['set']} | {r['match'].removeprefix('en-live-playerview-')} "
            f"`{r['cid']}` | {r['change_type']} | {r['A_roster']} | "
            f"{r['B_roster']} | {r['C_roster']} | {'yes' if r['b_equals_c'] else 'no'} |"
            for r in doc["deaths"]
        ],
    ]
    return "\n".join(lines) + "\n"


def plan(out: Path, config_path: Path) -> int:
    """Write ``dry_run.json`` / ``dry_run.md``; no model call."""
    doc = plan_doc(out / "inputs", load_config(config_path))
    (out / "dry_run.json").write_text(json.dumps(doc, indent=1) + "\n", "utf-8")
    text = plan_markdown(doc)
    (out / "dry_run.md").write_text(text, "utf-8")
    sys.stdout.write(text)
    return 0 if not (doc["stale_inputs"] or doc["invariant_problems"]) else 1


def check_rows(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Structural invariants of the built inputs."""
    problems = []
    for r in rows:
        if r["A_roster"] != "no roster block":
            problems.append(f"{r['cid']}: arm A has a roster block")
        if r["set"] == "control" and not r["b_equals_c"]:
            problems.append(f"{r['cid']}: control B and C prompts differ")
        if r["set"] == "treatment" and r["b_equals_c"]:
            problems.append(f"{r['cid']}: treatment B and C prompts identical")
    return problems
