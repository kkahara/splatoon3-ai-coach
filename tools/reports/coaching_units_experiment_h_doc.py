"""DESIGN.md renderer for Experiment H (pre-registration text + computed sets)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def _short(match: str) -> str:
    return match.removeprefix("en-live-playerview-")


def _avb(pair: Sequence[int] | None) -> str:
    return "none" if pair is None else f"{pair[0]}v{pair[1]}"


def _death_rows(doc: dict[str, Any], keys: Sequence[Sequence[str]]) -> list[str]:
    by_key = {(r["match"], r["cid"]): r for r in doc["deaths"]}
    lines = [
        "| match | death | change type | before death B -> C | score A / B / C "
        "| selected A / B / C |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for match, cid in keys:
        r = by_key[(match, cid)]
        lines.append(
            f"| {_short(match)} | `{cid}` | {r['change_type']} | "
            f"{_avb(r['before_death_b'])} -> {_avb(r['before_death_c'])} | "
            f"{r['A_score']:g} / {r['B_score']:g} / {r['C_score']:g} | "
            f"{'Y' if r['A_selected'] else 'N'} / {'Y' if r['B_selected'] else 'N'} / "
            f"{'Y' if r['C_selected'] else 'N'} |"
        )
    return lines


def _selection_effect(doc: dict[str, Any]) -> list[str]:
    changed = [
        r
        for r in doc["deaths"]
        if len({(r[f"{a}_rank"], r[f"{a}_selected"]) for a in ("A", "B", "C")}) > 1
    ]
    lines = [
        "| match | death | change type | rank A / B / C | selected A / B / C |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in changed:
        lines.append(
            f"| {_short(r['match'])} | `{r['cid']}` | {r['change_type']} | "
            f"{r['A_rank']} / {r['B_rank']} / {r['C_rank']} | "
            f"{'Y' if r['A_selected'] else 'N'} / {'Y' if r['B_selected'] else 'N'} / "
            f"{'Y' if r['C_selected'] else 'N'} |"
        )
    return lines


def _counts(doc: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in doc["deaths"]:
        out[r["change_type"]] = out.get(r["change_type"], 0) + 1
    return out


def _header(doc: dict[str, Any]) -> list[str]:
    eq = doc["production_equivalence"]
    counts = _counts(doc)
    return [
        "# Experiment H: roster evidence in coaching (pre-registration)",
        "",
        "Research only. Frozen before any input is built or any model call is",
        "made. Production code, prompts, catalog and config are unchanged",
        "(`coach.roster_evidence=false` in production). A1/O3 detector tuning",
        "stays closed whatever this experiment shows. **Gates and thresholds are",
        "not adjusted after the outputs are seen**, including results close to a",
        "boundary.",
        "",
        f"Design computed at `{doc['git_head'][:7]}`; config sha256",
        f"`{doc['config_sha256'][:12]}`. Machine-readable: `design.json`.",
        "",
        "## Questions",
        "",
        "- **Q1 correction effect:** with roster evidence on, does refreshed",
        "  evidence produce appropriately different coaching than pre-refresh",
        "  evidence? B vs C on the treatment deaths.",
        "- **Q2 value:** does exposing roster evidence improve coaching over",
        "  production today (roster off)? A vs C.",
        "",
        "## Arms",
        "",
        "Each arm is the production pipeline with only the production switch",
        "set: `score_match` + `build_coach_llm_view` + `load_system_prompt`. No",
        "experiment-specific prompt or view manipulation.",
        "",
        "| arm | analysis | coach.roster_evidence | system prompt sha256 |",
        "| --- | --- | --- | --- |",
        *[
            f"| {arm} | {a['analysis']} | {str(a['roster_evidence']).lower()} | "
            f"`{a['system_prompt_sha256'][:12]}` |"
            for arm, a in doc["arms"].items()
        ],
        "",
        "Production equivalence: every arm's LLM view was compared with the",
        "`s3-coach coach-inputs` output for the same analysis, using the",
        "production config with only `roster_evidence` set:",
        ", ".join(f"{arm} {v['matched']}/{v['total']}" for arm, v in eq.items())
        + " identical.",
        "",
        "Arm A carries no roster values: `roster` is null; the death block's",
        "roster fields (`numbers_state_at_death`, `roster_changed_before_death`,",
        "`seconds_since_roster_change`) are null; the `roster_evidence_disabled`",
        "limit and the `roster` undetermined topic are present. Its",
        "`importance.factor_thresholds` still lists the two roster factors'",
        "configured thresholds (config values, not evidence); that is production",
        "behavior and is left as is.",
        "",
        f"Model: {doc['provider']} `{doc['model']}` (temperature not honored),",
        f"one death per call, {doc['repeats']} repeats.",
        "",
        "## Deaths",
        "",
        f"{len(doc['deaths'])} death units in {len(doc['matches'])} matches,",
        "classified by how the arm-B view differs from the arm-C view:",
        ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + ".",
        "",
        "- `at_or_before_death`: an evidence path outside the roster window",
        "  differs (roster before/at death, death roster fields, roster context,",
        "  supporting evidence, limits or claim basis).",
        "- `window_only`: outside `importance`, only the roster window differs.",
        "- `rank_only`: only `importance` differs (selection side effect).",
        "- `unchanged`: B and C views (and prompts) are identical.",
        "",
    ]


def _sets(doc: dict[str, Any]) -> list[str]:
    n_t, n_c = len(doc["treatment"]), len(doc["controls"])
    return [
        f"### Treatment ({n_t}): every death whose roster evidence differs",
        "",
        "All `at_or_before_death` and `window_only` deaths, regardless of",
        "production selection. Change type, count change and selection are",
        "covariates.",
        "",
        *_death_rows(doc, doc["treatment"]),
        "",
        f"### Controls ({n_c}): seeded draw from the `unchanged` deaths",
        "",
        f"Seed {doc['control_seed']}, stratified to the treatment's arm-C",
        "selection mix. B and C prompts are byte-identical, so B-vs-C",
        "differences on controls are model variation (the noise floor).",
        "",
        *_death_rows(doc, doc["controls"]),
        "",
        "## Selection effect (no model calls)",
        "",
        "Deaths whose rank or selection differs between arms. Reported",
        "separately from the coaching text.",
        "",
        *_selection_effect(doc),
        "",
        f"Calls: {n_t + n_c} deaths x 3 arms x {doc['repeats']} repeats = "
        f"{(n_t + n_c) * 3 * doc['repeats']}.",
        "",
    ]


def _measures(doc: dict[str, Any]) -> list[str]:
    return [
        "## Measures",
        "",
        "Automatic, per output, implemented and frozen in",
        "`tools/reports/coaching_units_experiment_h.py` before any output:",
        "",
        "- **Validator:** `claim_validation.validate_assessment` rule hits and",
        "  blocked outputs, each arm validated under its own roster setting.",
        "- **Roster mention** (`mentions_roster`): alive counts, numbers state,",
        "  outnumbered / advantage wording, roster or player count.",
        "- **Count fidelity** (`count_fidelity`): every cited ally-vs-opponent",
        "  pair and every named numbers state (even / advantage / disadvantage)",
        "  is present in that arm's own roster block (before death, at death or",
        "  window). A stale pre-refresh value cited in C is an error.",
        "- **Roster-rule violations** (`roster_violations`, from",
        "  `coach_roster.txt`): a verdict in a roster sentence (should have",
        "  retreated / pushed / played passively ...); fight framing",
        "  (outnumbered fight); teammate-death or trade inference in a roster",
        "  sentence; turning a presence bound into continuous time.",
        "- **Divergence** (`divergence`): 1 - difflib ratio between B and C",
        "  output text, repeat 1 with repeat 1 and 2 with 2, mean per death.",
        "",
        "Blind rating: all outputs in one sheet, shuffled with seed",
        f"{doc['rating_seed']}. Columns: usefulness, redundancy,",
        "anchor_shows_situation, missing_context (1-5), supported (yes/no),",
        "**roster_use** (correct / incorrect / not_mentioned), notes. Ratings",
        "are sealed in a commit before the key is opened.",
        "",
    ]


def _rule(doc: dict[str, Any]) -> list[str]:
    t = doc["thresholds"]
    pct = round(t["fidelity_min"] * 100)
    by_key = {(r["match"], r["cid"]): r for r in doc["deaths"]}
    n_death = sum(
        1
        for m, c in doc["treatment"]
        if by_key[(m, c)]["change_type"] == "at_or_before_death"
    )
    reps = doc["repeats"]
    return [
        "## Decision rule",
        "",
        "1. **Safety gate (arm C).** 0 roster-rule violations; 0 count-fidelity",
        "   errors; blocked outputs no more than arm A; validator hits per call",
        f"   no more than arm A + {t['validator_margin']}. Failure: keep roster",
        "   evidence off, record the failures.",
        '2. **Q1 correction effect ("appropriately different").**',
        "   - Among C outputs on the `at_or_before_death` deaths that mention",
        f"     roster, at least {pct}% pass count fidelity against C's (refreshed)",
        f"     evidence. **If none of the C outputs on those deaths ({n_death} deaths",
        f"     x {reps} repeats = {n_death * reps}) mentions roster, Q1 fails.** "
        "Roster-mention",
        "     frequency for B and C is reported explicitly and considered when",
        "     interpreting Q1; no minimum mention rate beyond that.",
        "   - Mean B-vs-C divergence on treatment deaths exceeds the mean on",
        "     controls.",
        "   - On treatment deaths, C is rated `roster_use=correct` at least as",
        "     often as B.",
        "3. **Q2 value.** Over all treatment and control deaths, mean usefulness",
        "   C - A exceeds the noise floor: the mean over controls of",
        "   |mean usefulness B - mean usefulness C| (per death, over repeats).",
        "   C's supported rate is not lower than A's. Missing context is",
        "   reported alongside.",
        "4. **Outcomes.**",
        "   - Passes 1, 2 and 3: recommend enabling `coach.roster_evidence`.",
        "   - Passes 1 and 2, fails 3: evidence handled correctly but no",
        "     demonstrated value; keep off.",
        "   - Fails 1 or 2: keep off; investigate the coach's roster handling.",
        "     The detector is not reopened.",
        "   Directional only (one rater, small n). Any production change remains",
        "   a separate decision.",
        "5. **Covariates (descriptive, not a gate).** Whether effects track",
        "   change type, the size of the count change, or production selection.",
        "",
        "## Stop points",
        "",
        "1. This design (tool, tests, `DESIGN.md`, `design.json`). Review.",
        "2. Built inputs and dry run, sealed. Go-ahead before any call.",
        "3. Outputs, automatic summary, blind sheet.",
        "4. Sealed ratings, then key and `analyze` report.",
        "",
    ]


def amendments_markdown(amendments: Sequence[dict[str, Any]]) -> list[str]:
    """Dated pre-output amendments, in order."""
    if not amendments:
        return []
    lines = ["## Pre-output amendments", ""]
    for a in amendments:
        lines += [f"**Amendment {a['id']} ({a['date']}, after {a['after']}).**", ""]
        lines += [a["text"], ""]
    return lines


def design_markdown(doc: dict[str, Any]) -> str:
    """Pre-registration document for ``doc`` (from ``design_doc``)."""
    return "\n".join(
        _header(doc)
        + _sets(doc)
        + _measures(doc)
        + _rule(doc)
        + amendments_markdown(doc.get("amendments", ()))
    )
