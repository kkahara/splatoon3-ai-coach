"""Experiment I stop point 2: build sealed per-arm inputs and the dry run.

``build`` recomputes the derived ``map_awareness`` facts, refuses to write if
they differ from the frozen ``design.json`` and never overwrites. ``plan``
makes no model call: it rebuilds every input and reports byte differences as
stale, checks the arm invariants, and runs the production validator plus the
frozen map checks on fixed synthetic outputs (settling the claim question
before any call).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import coaching_units_experiment_d as xd
import coaching_units_experiment_h as xh
import coaching_units_experiment_h_inputs as xhi
import coaching_units_experiment_i as xi
from loguru import logger

from splatoon3_ai_coach.coach.coach import annotate_claim_flags
from splatoon3_ai_coach.coach.llm_client import CoachingAssessment
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import AppConfig

H_INPUTS = xi.REPORT_DIR / "coaching_units_experiment_h" / "inputs"
FACT_FIELDS = (
    "back_in_play_source",
    "back_in_play_time",
    "seconds_back_in_play",
    "map_checks_since_back_in_play",
    "seconds_since_alive_map_check",
    "eligible",
)


def load_design(out: Path) -> dict[str, Any]:
    """The frozen design document."""
    return json.loads((out / "design.json").read_text(encoding="utf-8"))


def _key(death: Mapping[str, Any]) -> tuple[str, str]:
    return (xi.MATCH_PREFIX + death["match"], death["scenario_id"])


def null_block_expected(death: Mapping[str, Any]) -> bool:
    """Amendment 2: the death whose map observability is insufficient."""
    return (death["match"], death["scenario_id"]) in xi.UNOBSERVABLE


def effective_eligible(death: Mapping[str, Any]) -> bool:
    """Design eligibility after amendment 2."""
    return bool(death["eligible"]) and not null_block_expected(death)


def fact_problems(design: Mapping[str, Any], facts: Mapping[tuple, Mapping]) -> list[str]:
    """Recomputed derived facts that differ from the frozen design."""
    problems = []
    for death in design["deaths"]:
        now = facts[_key(death)]
        for field in FACT_FIELDS:
            a, b = death[field], now[field]
            same = (
                abs(a - b) < 1e-6
                if isinstance(a, float) and isinstance(b, float)
                else a == b
            )
            if not same:
                problems.append(f"{_key(death)} {field}: {a} -> {b}")
    return problems


def arm_inputs(
    design: Mapping[str, Any], base: AppConfig
) -> tuple[dict[tuple, dict], dict[str, dict[tuple, dict[str, Any]]]]:
    """Derived facts and per-arm ``{view, prompt}`` for every design death."""
    keys = [_key(d) for d in design["deaths"]]
    matches = sorted({k[0] for k in keys})
    a_views = xh.arm_views(base, "A", matches)
    facts = {k: xi.facts_for(xi.ANALYSIS_ROOT / k[0], k[1]) for k in keys}
    arms: dict[str, dict[tuple, dict[str, Any]]] = {"A": {}, "M": {}}
    for key in keys:
        death = a_views[key]
        preamble = xi.prompt_preamble(death.prompt)
        view_m = xi.m_view(death.view, facts[key])
        arms["A"][key] = {"view": death.view, "prompt": death.prompt}
        arms["M"][key] = {"view": view_m, "prompt": xi.user_prompt(preamble, view_m)}
    return facts, arms


def input_files(
    design: Mapping[str, Any], arms: Mapping[str, Mapping[tuple, Mapping]]
) -> dict[str, str]:
    """Relative path -> text for both arms' prompts and the manifest."""
    files = {f"system_prompt_{arm}.txt": xi.arm_system_prompt(arm) for arm in xi.ARMS}
    calls = []
    for death in design["deaths"]:
        key = _key(death)
        for arm in xi.ARMS:
            rel = f"{arm}/{xhi.slug(*key)}.user_prompt.txt"
            files[rel] = arms[arm][key]["prompt"]
            calls.append(
                {
                    "match": key[0],
                    "cid": key[1],
                    "eligible": effective_eligible(death),
                    "arm": arm,
                    "file": rel,
                    "system": f"system_prompt_{arm}.txt",
                }
            )
    manifest = {
        "design_sha256": xh._sha(json.dumps(design, sort_keys=True)),
        "arms": list(xi.ARMS),
        "repeats": xi.REPEATS,
        "provider": xi.PROVIDER,
        "model": xi.MODEL,
        "amendments": list(xi.AMENDMENTS),
        "calls": calls,
    }
    files["manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    return files


def build(out: Path, config_path: Path) -> int:
    """Write ``out/inputs`` from the frozen design; never overwrites."""
    inputs = out / "inputs"
    if inputs.exists():
        raise SystemExit(f"{inputs} exists; never overwritten")
    design = load_design(out)
    facts, arms = arm_inputs(design, load_config(config_path))
    problems = fact_problems(design, facts)
    if problems:
        raise SystemExit("design no longer reproduces: " + "; ".join(problems))
    files = input_files(design, arms)
    for rel, text in sorted(files.items()):
        path = inputs / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    logger.warning("Experiment I inputs: {} files under {}", len(files), inputs)
    return 0


def staleness(inputs: Path, files: Mapping[str, str]) -> list[str]:
    """Stored inputs that are not byte-identical to a fresh rebuild."""
    stored = {
        str(p.relative_to(inputs)): p.read_text(encoding="utf-8")
        for p in inputs.rglob("*")
        if p.is_file()
    }
    problems = [f"{rel} differs" for rel in files if stored.get(rel) != files[rel]]
    problems += [f"{rel} unexpected" for rel in sorted(set(stored) - set(files))]
    return problems


def _strip_m(view: Mapping[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(view))
    out.pop("map_awareness", None)
    out["claim_basis"] = [
        e for e in out.get("claim_basis", []) if e["claim_id"] != xi.EXPERIMENT_CLAIM_ID
    ]
    return out


def invariant_problems(
    design: Mapping[str, Any], arms: Mapping[str, Mapping[tuple, Mapping]]
) -> list[str]:
    """A = production (and = Experiment H arm A); M = A + block (+ claim)."""
    problems = []
    systems = {arm: xi.arm_system_prompt(arm) for arm in xi.ARMS}
    if systems["A"] != xh.arm_system_prompt("A"):
        problems.append("system A differs from production")
    if not systems["M"].endswith(xi.MAP_SECTION) or not systems["M"].startswith(
        systems["A"].rstrip("\n")
    ):
        problems.append("system M is not system A + map section")
    for death in design["deaths"]:
        key = _key(death)
        a, m = arms["A"][key], arms["M"][key]
        tag = f"{key[0].removeprefix(xi.MATCH_PREFIX)} {key[1]}"
        h_file = H_INPUTS / "A" / f"{xhi.slug(*key)}.user_prompt.txt"
        if h_file.read_text(encoding="utf-8") != a["prompt"]:
            problems.append(f"{tag}: A prompt differs from Experiment H arm A")
        if xi.user_prompt(xi.prompt_preamble(a["prompt"]), a["view"]) != a["prompt"]:
            problems.append(f"{tag}: serialization differs from production")
        if "map_awareness" in a["view"]:
            problems.append(f"{tag}: arm A has a map_awareness block")
        if _strip_m(m["view"]) != a["view"]:
            problems.append(f"{tag}: M view is not A view + map_awareness")
        block = m["view"]["map_awareness"]
        claimed = any(
            e["claim_id"] == xi.EXPERIMENT_CLAIM_ID for e in m["view"]["claim_basis"]
        )
        if (block is None) != null_block_expected(death):
            problems.append(f"{tag}: block null={block is None}")
        if bool(block and block["eligible"]) != effective_eligible(death):
            problems.append(f"{tag}: eligibility differs from design + amendment 2")
        if claimed != effective_eligible(death):
            problems.append(f"{tag}: experiment claim present={claimed}")
    return problems


SMOKE_ELIGIBLE = ("2026-10-11 22-57-30", "death_episode:308.000")
SMOKE_NOT_ELIGIBLE = ("2026-10-11 22-20-42", "death_episode:152.000")


def smoke_cases(seconds: float) -> list[dict[str, Any]]:
    """Fixed synthetic outputs (frozen before any model call)."""
    intended = {
        "assessment": f"No map check was observed in the {seconds}s since you were "
        "back in play.",
        "evidence_used": [f"map_awareness.seconds_back_in_play = {seconds}"],
        "recommendations": ["Take a quick look at the map before moving up or engaging."],
        "selected_claim_ids": [xi.EXPERIMENT_CLAIM_ID],
    }
    return [
        {"case": "intended", "death": "eligible", "assessment": intended},
        {
            "case": "causal",
            "death": "eligible",
            "assessment": {
                "assessment": f"You died because you did not check the map in the "
                f"{seconds}s since you respawned."
            },
        },
        {
            "case": "map_content",
            "death": "eligible",
            "assessment": {
                "assessment": "A map check would have shown the opponent flanking."
            },
        },
        {
            "case": "wrong_seconds",
            "death": "eligible",
            "assessment": {"assessment": "No map check in the 30s since you respawned."},
        },
        {
            "case": "advice_not_eligible",
            "death": "not_eligible",
            "assessment": {"recommendations": intended["recommendations"]},
        },
    ]


def _smoke_row(case: Mapping[str, Any], view: Mapping[str, Any], label: str) -> dict:
    assessment = CoachingAssessment.model_validate(case["assessment"])
    validation = annotate_claim_flags(assessment, dict(view))
    block = view.get("map_awareness")
    dumped = assessment.model_dump(mode="json")
    sentences = xi.output_sentences(dumped)
    return {
        "case": case["case"],
        "view": label,
        **xi.validator_counts(validation, block),
        "hit_rules": sorted(
            {f"{h['rule']}:{h['check']}" for h in validation["integrity_hits"]}
            | (
                {"unsupported_recommendation"}
                if validation["unsupported_recommendation_hits"]
                else set()
            )
            | ({"causal_map_claim"} if validation["causal_claim_hits"] else set())
        ),
        "blocked": bool(validation["blocked"]),
        "map_rule_violations": xi.map_rule_violations(dumped, block),
        "value_fidelity_errors": len(xi.value_fidelity_errors(sentences, block)),
        "mentions_map_check": xi.mentions_map_check(" ".join(sentences)),
    }


def smoke_test(arms: Mapping[str, Mapping[tuple, Mapping]]) -> list[dict[str, Any]]:
    """Validator and frozen checks on the synthetic outputs."""
    views = {
        "eligible": arms["M"][(xi.MATCH_PREFIX + SMOKE_ELIGIBLE[0], SMOKE_ELIGIBLE[1])],
        "not_eligible": arms["M"][
            (xi.MATCH_PREFIX + SMOKE_NOT_ELIGIBLE[0], SMOKE_NOT_ELIGIBLE[1])
        ],
    }
    seconds = views["eligible"]["view"]["map_awareness"]["seconds_back_in_play"]
    rows = []
    for case in smoke_cases(seconds):
        view = views[case["death"]]["view"]
        rows.append(_smoke_row(case, view, f"M ({case['death']})"))
        if case["case"] == "intended":
            rows.append(_smoke_row(case, _strip_claim(view), "M without claim"))
    return rows


def _strip_claim(view: Mapping[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(view))
    out["claim_basis"] = [
        e for e in out["claim_basis"] if e["claim_id"] != xi.EXPERIMENT_CLAIM_ID
    ]
    return out


def death_rows(
    design: Mapping[str, Any], arms: Mapping[str, Mapping[tuple, Mapping]]
) -> list[dict[str, Any]]:
    """Per death: derived facts, block and prompt sizes."""
    rows = []
    for death in design["deaths"]:
        key = _key(death)
        block = arms["M"][key]["view"]["map_awareness"] or {}
        rows.append(
            {
                "match": death["match"],
                "cid": key[1],
                "source": block.get("back_in_play_source", "null block"),
                "seconds_back_in_play": block.get("seconds_back_in_play"),
                "checks": block.get("map_checks_since_back_in_play"),
                "eligible": bool(block.get("eligible")),
                "A_chars": len(arms["A"][key]["prompt"]),
                "M_chars": len(arms["M"][key]["prompt"]),
            }
        )
    return rows


def plan_doc(out: Path, base: AppConfig) -> dict[str, Any]:
    """Dry run: staleness, invariants, smoke test, calls and sizes."""
    design = load_design(out)
    facts, arms = arm_inputs(design, base)
    files = input_files(design, arms)
    deaths = death_rows(design, arms)
    support = xd.temperature_support(xi.PROVIDER)
    return {
        "dry_run": True,
        "llm_calls_made": 0,
        "provider": xi.PROVIDER,
        "model": xi.MODEL,
        "repeats": xi.REPEATS,
        "temperature_support": support,
        "temperature_note": xd.SUPPORT_NOTES[xi.PROVIDER],
        "fact_problems": fact_problems(design, facts),
        "stale_inputs": staleness(out / "inputs", files),
        "invariant_problems": invariant_problems(design, arms),
        "amendments": list(xi.AMENDMENTS),
        "total_calls": len(design["deaths"]) * len(xi.ARMS) * xi.REPEATS,
        "eligible": sum(d["eligible"] for d in deaths),
        "system_chars": {arm: len(xi.arm_system_prompt(arm)) for arm in xi.ARMS},
        "smoke_test": smoke_test(arms),
        "deaths": deaths,
    }


def _listed(items: Sequence[str]) -> str:
    return "; ".join(items) if items else "none"


def plan_markdown(doc: Mapping[str, Any]) -> str:
    """Human-readable dry run."""
    lines = [
        "# Experiment I dry run (no LLM calls)",
        "",
        f"- Provider / model: `{doc['provider']}` / `{doc['model']}`; "
        f"temperature **{doc['temperature_support']}** ({doc['temperature_note']})",
        f"- Derived facts vs frozen design: {_listed(doc['fact_problems'])}",
        "- Stale inputs (byte comparison with a rebuild): "
        + _listed(doc["stale_inputs"]),
        "- Invariants (A = production = Experiment H arm A; M = A + map_awareness, "
        "claim only when eligible; system M = A + section): "
        + ("all hold" if not doc["invariant_problems"] else "")
        + "; ".join(doc["invariant_problems"]),
        f"- Calls: {doc['total_calls']} ({doc['repeats']} repeats); eligible deaths "
        f"{doc['eligible']} of {len(doc['deaths'])}; model calls made: "
        f"{doc['llm_calls_made']}",
        f"- System prompt chars: A {doc['system_chars']['A']}, "
        f"M {doc['system_chars']['M']}",
        "",
        "## Validator smoke test (synthetic outputs, frozen before any call)",
        "",
        "| case | view | production hits | R5 window exempt | adjusted | rules hit "
        "| map-rule violations | value errors | mentions map |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        *[
            f"| {r['case']} | {r['view']} | {r['validator_hits']} | "
            f"{r['r5_window_exempt']} | {r['adjusted']} | {', '.join(r['hit_rules'])} | "
            f"{', '.join(r['map_rule_violations']) or '-'} | "
            f"{r['value_fidelity_errors']} | {r['mentions_map_check']} |"
            for r in doc["smoke_test"]
        ],
        "",
        "## Amendments (before any call)",
        "",
        *[f"{a['id']}. ({a['date']}) {a['text']}" for a in doc["amendments"]],
        "",
        "## Deaths",
        "",
        "| match | death | back in play from | seconds back | alive checks | eligible "
        "| A chars | M chars |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        *[
            f"| {d['match']} | `{d['cid']}` | {d['source']} | "
            f"{d['seconds_back_in_play']} | {d['checks']} | "
            f"{'Y' if d['eligible'] else 'N'} | {d['A_chars']} | {d['M_chars']} |"
            for d in doc["deaths"]
        ],
    ]
    return "\n".join(lines) + "\n"


def plan(out: Path, config_path: Path) -> int:
    """Write ``dry_run.json`` / ``dry_run.md``; no model call."""
    doc = plan_doc(out, load_config(config_path))
    (out / "dry_run.json").write_text(json.dumps(doc, indent=1) + "\n", "utf-8")
    text = plan_markdown(doc)
    (out / "dry_run.md").write_text(text, "utf-8")
    sys.stdout.write(text)
    problems = doc["fact_problems"] or doc["stale_inputs"] or doc["invariant_problems"]
    return 1 if problems else 0
