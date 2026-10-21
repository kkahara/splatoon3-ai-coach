"""Markdown for the N2 citation-compliance experiment (design, dry run, score)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _units_table(design: Mapping[str, Any]) -> list[str]:
    rows = [
        "| # | match | death | group | why selected | families | view sha256 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for i, u in enumerate(design["units"], start=1):
        f = u["features"]
        fams = [k for k in ("clock", "timeline", "special", "map", "low_ink") if f[k]]
        fams += [f"related {f['related']}", f"claims {f['claim_basis']}"]
        fams += [f"source_path {f['source_path']}"]
        fams += [k for k in ("score", "zone_control") if f[k]]
        rows.append(
            f"| {i} | {u['match']} | `{u['cid']}` | {u['group']} | {u['reason']} | "
            f"{', '.join(fams)} | `{u['view_sha256'][:16]}` |"
        )
    return rows


def _scoring_lines(scoring: Mapping[str, Any]) -> list[str]:
    lines = ["Citation classes (one per `path = value` segment of an entry):", ""]
    lines += [f"- `{c}`" for c in scoring["classes"]]
    lines += ["", "Gate (frozen; PASS only when every rule holds):", ""]
    lines += [f"- `{k}`: {v}" for k, v in scoring["gate_rules"].items()]
    lines += ["", "Gate groups:", ""]
    lines += [f"- `{g}`: {', '.join(c)}" for g, c in scoring["gate_groups"].items()]
    lines += [
        "",
        f"Value rule: {scoring['value_rule']}.",
        "",
        "Secondary metrics (no gate):",
        "",
    ]
    lines += [f"- {m}" for m in scoring["secondary_metrics"]]
    return lines


def design_markdown(design: Mapping[str, Any]) -> str:
    """Human-readable pre-registered design."""
    eq = design["cli_equivalence"]
    lines = [
        "# N2 citation-compliance experiment: design (pre-registered)",
        "",
        f"Question: {design['question']}",
        "",
        "Citation-interface experiment only: no usefulness or value rating. "
        "Experiment H / I artifacts, ratings, validations and determinations "
        "are not used or modified. The validator's legacy label fallback is "
        "unchanged; it is measured, never counted as compliance.",
        "",
        "## Frozen setup",
        "",
        f"- Code: design computed at `{design['git_head']}`; production paths "
        f"(`src/`, `configs/default.yaml`) unchanged since prompt commit "
        f"`{design['prompt_commit']}`",
        f"- Provider / model: `{design['provider']}` / `{design['model']}` "
        f"(production: `S3_COACH_LLM_PROVIDER=cursor`, config `cursor_model`); "
        f"temperature {design['temperature_support']} ({design['temperature_note']})",
        f"- System prompt sha256 `{design['system_prompt_sha256']}` "
        f"({design['system_prompt_chars']} chars, `roster_evidence="
        f"{str(design['roster_evidence']).lower()}`)",
        f"- User preamble sha256 `{design['user_preamble_sha256']}`; config sha256 "
        f"`{design['config_sha256']}`",
        f"- `LLM_VIEW_SCHEMA_VERSION` {design['llm_view_schema_version']}, "
        f"`COACH_EVIDENCE_VERSION` {design['coach_evidence_version']}",
        f"- Views equal `s3-coach coach-inputs` output: {eq['matched']} / {eq['total']}",
        f"- Calls: {len(design['units'])} units x {design['repeats']} repeats = "
        f"{design['calls']}",
        "",
        "## Selection",
        "",
        f"Rule: {design['selection_rule']} Census: {design['census']['death_views']} "
        f"death views over {design['census']['matches']} local matches.",
        "",
        *_units_table(design),
        "",
        "Full hashes (view and user prompt) are in `design.json`.",
        "",
        "## Scoring and gate",
        "",
        *_scoring_lines(design["scoring"]),
    ]
    return "\n".join(lines) + "\n"


def dry_run_markdown(doc: Mapping[str, Any]) -> str:
    """Human-readable dry run."""

    def listed(items: Any) -> str:
        return "; ".join(items) if items else "none"

    lines = [
        "# N2 citation-compliance experiment: dry run (no LLM calls)",
        "",
        f"- Provider / model: `{doc['provider']}` / `{doc['model']}`; planned calls "
        f"{doc['planned_calls']} ({doc['repeats']} repeats); model calls made: "
        f"{doc['llm_calls_made']}",
        f"- Two rebuilds byte-identical: {doc['rebuild_deterministic']}",
        f"- Stale inputs: {listed(doc['stale_inputs'])}",
        f"- Hash problems: {listed(doc['hash_problems'])}",
        f"- Production since prompt commit: {listed(doc['prompt_commit_problems'])}",
        f"- Scorer smoke failures: {listed(doc['smoke_failures'])}",
        "",
        f"## Scorer smoke test (`{doc['smoke_unit'][0]}` `{doc['smoke_unit'][1]}`)",
        "",
        "| citation | expected | strict | form | validator | validator accepts |",
        "| --- | --- | --- | --- | --- | --- |",
        *[
            f"| `{r['text']}` | {', '.join(r['expected'])} | {', '.join(r['got'])} | "
            f"{', '.join(str(f) for f in r['value_forms'])} | {r['validator_kind']} | "
            f"{r['validator_accepts']} |"
            for r in doc["smoke_test"]
        ],
    ]
    return "\n".join(lines) + "\n"


def score_markdown(result: Mapping[str, Any]) -> str:
    """Human-readable automatic citation scoring (after the sealed calls)."""
    s = result["summary"]
    g = s["gate"]
    lines = [
        "# N2 citation-compliance experiment: automatic scoring",
        "",
        f"**Gate: {'PASS' if g['passed'] else 'FAIL'}**",
        "",
        "| rule | holds |",
        "| --- | --- |",
        *[f"| `{k}` | {v} |" for k, v in g["checks"].items()],
        "",
        f"- Outputs: {s['outputs']['compliant']} / {s['outputs']['total']} compliant "
        f"({s['outputs']['parsed']} parsed); units compliant on every repeat: "
        f"{s['units_compliant_every_repeat']} / {s['units']}",
        f"- Entries: {s['entries']['compliant']} / {s['entries']['total']} compliant; "
        f"prose before the path {s['entries']['prose_prefix']}, several facts "
        f"{s['entries']['multi_fact']}",
        f"- Citations: {s['citations']['compliant']} / {s['citations']['total']} "
        "n2_scalar_correct",
        f"- Value forms: {s['value_forms']}; malformed reasons: {s['malformed_reasons']}",
        f"- Families cited: {s['families']}",
        "- Selected claims with an evidence path cited: "
        f"{s['selected_claims']['evidence_path_cited']} / "
        f"{s['selected_claims']['total']}",
        f"- Outputs without citations: {s['outputs_without_citations']}",
        f"- Validator status: {s['validator_status']}",
        f"- Validator vs strict disagreements: {len(s['validator_disagreements'])}",
        "",
        "## Citation classes",
        "",
        "| class | count |",
        "| --- | --- |",
        *[f"| `{c}` | {n} |" for c, n in g["class_counts"].items()],
        "",
        "## Non-compliant citations and disagreements",
        "",
        *[
            f"- `{r['call_id']}`: `{r['text']}` ({r['class']})"
            for r in result["noncompliant"]
        ],
        *[
            f"- disagreement `{d['call_id']}`: `{d['text']}` (validator "
            f"{d['validator_kind']}, accepts {d['validator_accepts']}; strict "
            f"{d['strict_compliant']})"
            for d in s["validator_disagreements"]
        ],
    ]
    return "\n".join(lines) + "\n"
