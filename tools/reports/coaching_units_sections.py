"""Markdown sections for the coaching-unit research report (``coaching_units.py``)."""

from __future__ import annotations

import json
import statistics
from collections.abc import Sequence
from typing import Any

from coaching_units import (
    DEFERRED_SETTINGS,
    POLICIES,
    STABILITY_TOLERANCE_SECONDS,
    MatchData,
    gap_sweep,
    multi_scoring_episodes,
    policy_metrics,
    pool,
    redundant_units,
    references,
    select,
    stability,
)

from splatoon3_ai_coach.config.models import AppConfig

HUMAN_MISSING = (
    "**Human-rated usefulness: missing data — rating sheet has not yet been completed.**"
)
MODEL_MISSING = (
    "**Model effects: missing data — Experiment D inputs are built, but no LLM call "
    "has been made.**"
)
STABILITY_PAIRS = (
    ("_rescore_v2", "_rescore_v2_1hz"),
    ("_rescore_1hz_even", "_rescore_1hz_odd"),
)
SHORT = {
    "death_quick_redeath": "re-death",
    "death_own_splat_before": "own-splat",
    "death_final_30s": "final30",
    "death_first_30s": "first30",
    "death_map_overlay_before_false": "no-map",
}


def _short(factors: frozenset[str]) -> str:
    return "/".join(sorted(SHORT.get(f, f) for f in factors)) or "–"


def _member(d: Any) -> str:
    return f"{d.t:.1f}s {_short(d.factors)} = {d.score:g}"


def build_report(
    matches: Sequence[MatchData],
    roots: dict[str, Sequence[MatchData]],
    config: AppConfig,
    deferred: dict[str, str],
    sheet: str,
) -> str:
    """Whole report."""
    coach = config.coach
    gap = coach.death_factor_thresholds.redeath_max_gap_seconds
    modifiers = frozenset(coach.death_modifier_factors)
    top = coach.max_llm_units
    metrics = [policy_metrics(matches, p, gap, top, modifiers) for p in POLICIES]
    parts = [
        header(matches, metrics[0], gap, top),
        experiment_a(matches),
        experiment_b(matches, gap),
        experiment_c(matches, roots, metrics, gap, top, modifiers),
        experiment_d(deferred),
        experiment_e(),
        rubric(sheet),
        examples(matches, gap, top, modifiers),
        complexity(),
        recommendation(metrics, matches, gap),
    ]
    return "\n".join(line for part in parts for line in part) + "\n"


def header(matches: Sequence[MatchData], base: dict[str, Any], gap: float, top: int):
    """Title, status banners and data summary."""
    return [
        "# Coaching units: deaths, episodes, episode-by-factor (research only)",
        "",
        "Research artifact for Assignment 4. Nothing here changes production: "
        "scores, factors, CoachInputs, views and prompts come from production "
        "code; grouping exists only in `tools/reports/coaching_units.py`.",
        "",
        HUMAN_MISSING,
        "",
        MODEL_MISSING,
        "",
        f"- Matches (unique source videos): {len(matches)}",
        f"- Deaths: {sum(len(m.deaths) for m in matches)}; "
        f"positive-scoring: {base['positive_total']}",
        f"- Budget: top {top} positive-score units per match "
        f"(`coach.max_llm_units`); episode link gap: {gap:g}s "
        "(`redeath_max_gap_seconds`)",
        "- Roster capability off (`coach.roster_evidence=false`), current weights.",
        "- Reproduce: `python tools/reports/coaching_units.py --root "
        "analysis/_rescore_v2`",
        "",
    ]


def _engagement_stats(matches: Sequence[MatchData]) -> dict[str, Any]:
    rows = [e for m in matches for e in m.engagements]
    durations = [float(e["end_time"]) - float(e["start_time"]) for e in rows]
    splats = [int((e.get("context") or {}).get("splat_count") or 0) for e in rows]
    linked = sum(1 for e in rows if (e.get("context") or {}).get("following_death_id"))
    return {
        "count": len(rows),
        "median_duration": statistics.median(durations) if durations else 0.0,
        "single_splat": sum(1 for s in splats if s <= 1),
        "linked_to_death": linked,
    }


def _overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def _low_ink_stats(matches: Sequence[MatchData]) -> dict[str, Any]:
    total = free = 0
    for m in matches:
        for interval in m.low_ink:
            total += 1
            if not any(_overlaps(interval, d) for d in m.death_intervals):
                free += 1
    return {"count": total, "without_death": free}


def experiment_a(matches: Sequence[MatchData]) -> list[str]:
    """Unit definitions and feasibility on existing data."""
    eng, ink = _engagement_stats(matches), _low_ink_stats(matches)
    return [
        "## Experiment A: candidate units",
        "",
        "| unit | definition | evaluable with existing data? |",
        "| --- | --- | --- |",
        "| Individual death | One `DEATH_EPISODE` scenario = one CoachInput = one "
        "LLM call (production). | Yes |",
        "| Death episode | Consecutive deaths each within the link gap of the "
        "previous (transitive, unbounded span); one unit per episode holding its "
        "positive-scoring deaths, ranked by its best member. | Yes |",
        "| Episode-by-factor | Within an episode, positive deaths sharing a primary "
        "(non-modifier) factor set form one unit; different sets stay separate. "
        "| Yes |",
        "| Engagement | Existing `ENGAGEMENT` scenario (owns own SPLAT events). "
        "| Counted only: see below |",
        "| Non-death opportunity | A coachable situation without a death. "
        "| No: requires new definitions |",
        "",
        f"**Engagement.** {eng['count']} engagements; median duration "
        f"{eng['median_duration']:g}s; {eng['single_splat']} hold a single splat; "
        f"{eng['linked_to_death']} link to a following death. An engagement owns "
        "only the player's own splats: there are no fight boundaries, opponents, "
        "damage or positions, and no importance factors are defined for it. It "
        "cannot be ranked or coached without new detection, so it is excluded "
        "from Experiments C and D.",
        "",
        f"**Non-death opportunity.** {ink['count']} LOW_INK intervals, "
        f"{ink['without_death']} not overlapping any death episode (the closed-"
        "interval rule of `analysis/low_ink_context.py`). These are facts, but "
        "there is no importance contract for them, and the other candidates "
        "(held special, long gaps without a map check) fall under undetermined "
        "topics (special usage) or need map coverage this corpus lacks. Feasibility "
        "count only; requires a new evidence and claim definition before any test.",
        "",
    ]


def _contradictions(group: Sequence[Any]) -> list[str]:
    scoring = [d for d in group if d.score > 0]
    notes = []
    for label, attr in (("special", "special"), ("map", "map_status")):
        states = {getattr(d, attr) for d in scoring}
        if len(states) > 1:
            notes.append(
                f"{label} differs: "
                + ", ".join(f"{d.t:.1f}s={getattr(d, attr)}" for d in scoring)
            )
    return notes


def experiment_b(matches: Sequence[MatchData], gap: float) -> list[str]:
    """Grouping semantics, answered with corpus examples."""
    lines = [
        "## Experiment B: grouping semantics",
        "",
        f"Episodes with two or more positive-scoring deaths (link gap {gap:g}s):",
        "",
        "| match | members (time factors = score) | distinct primary sets "
        "| span | member disagreement |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for m in matches:
        for group in multi_scoring_episodes(m, gap):
            scoring = [d for d in group if d.score > 0]
            distinct = len(
                {d.factors - {"death_final_30s", "death_first_30s"} for d in scoring}
            )
            lines.append(
                f"| {m.name[:36]} | {'; '.join(_member(d) for d in group)} | "
                f"{distinct} | {group[-1].t - group[0].t:.1f}s | "
                f"{'; '.join(_contradictions(group)) or 'none'} |"
            )
    refs = [(m, d, p) for m in matches for d, p in references(m)]
    lines += [
        "",
        *_semantics_answers(gap, len(refs), _episode_examples(matches, gap)),
        "",
    ]
    lines += [
        "Zero-score deaths referenced as the previous death (reference only, "
        "never a member):",
        "",
    ]
    lines += [
        f"- {m.name[:36]}: {d.t:.1f}s (score {d.score:g}) references {p.t:.1f}s (score 0)"
        for m, d, p in refs
    ] or ["- none"]
    lines.append("")
    return lines


def _label(match: MatchData, group: Sequence[Any]) -> str:
    scoring = [d for d in group if d.score > 0]
    return f"{match.name[:36]} ({', '.join(_member(d) for d in scoring)})"


def _primary_sets(group: Sequence[Any]) -> set[frozenset[str]]:
    clock = {"death_final_30s", "death_first_30s"}
    return {d.factors - clock for d in group if d.score > 0}


def _episode_examples(matches: Sequence[MatchData], gap: float) -> dict[str, str]:
    """Corpus examples for the semantics answers, derived from the data."""
    found = [(m, g) for m in matches for g in multi_scoring_episodes(m, gap)]
    longest = max(found, key=lambda mg: mg[1][-1].t - mg[1][0].t, default=None)
    mixed = [_label(m, g) for m, g in found if len(_primary_sets(g)) > 1]
    same = [_label(m, g) for m, g in found if len(_primary_sets(g)) == 1]
    span = (
        f"{longest[0].name[:36]} links {len(longest[1])} deaths across "
        f"{longest[1][-1].t - longest[1][0].t:.1f}s"
        if longest
        else "no multi-death episode exists"
    )
    return {
        "span": span,
        "mixed": "; ".join(mixed) or "none in this corpus",
        "same": "; ".join(same) or "none in this corpus",
    }


def _semantics_answers(gap: float, n_refs: int, ex: dict[str, str]) -> list[str]:
    return [
        "- **What links two deaths.** Only video-time proximity: death B joins A's "
        f"episode when B - A <= {gap:g}s. This is the same threshold as "
        "`death_quick_redeath`, so every later member of an episode carries the "
        "re-death factor by construction. An episode is therefore a re-death "
        "chain, not independent evidence that the deaths are one situation.",
        f"- **Transitive links.** Chaining is unbounded: {ex['span']}, although "
        "first and last are further apart than the gap. A span cap would be a new "
        "policy; the report only measures spans.",
        "- **Many opportunities in one episode.** Yes, when members carry different "
        f"primary factors and coachable claims: {ex['mixed']}.",
        "- **One opportunity across deaths.** Yes, when members repeat the same "
        f"primary factor and recommendation template: {ex['same']}.",
        "- **Contradictory evidence.** Members can disagree (special `not_ready` "
        "at one death, `not_observed` at the next). Each claim is true only at its "
        "own anchor; a grouped unit must keep per-member values and never merge "
        "them into one state.",
        "- **Boundary uncertainty.** Membership is sensitive to the gap (see the "
        "sweep in Experiment C). Uncertainty should be represented by keeping each "
        "member's own anchor and gap value, not by a confidence on the group.",
        f"- **Zero-score references.** {n_refs} positive deaths reference a "
        "zero-score previous death through `time_since_previous_death`. The "
        "reference is preserved as a value on the positive death; the zero-score "
        "death never becomes a member, factor, claim or observation.",
    ]


def _metric_rows(metrics: Sequence[dict[str, Any]]) -> list[str]:
    rows = (
        ("Units in research pool", "pool_units"),
        ("Units selected (budget)", "selected_units"),
        ("Matches covered", "matches_covered"),
        ("Positive deaths retained as observations", "positive_as_observations"),
        ("Positive deaths inside selected units", "positive_in_selected"),
        ("Zero-score deaths promoted", "zero_promoted"),
        ("Redundant selected units", "redundant_units"),
        ("Distinct opportunities (primary factor sets)", "distinct_opportunities"),
        ("Coachable claims per selected unit", "context_claims_per_unit"),
        ("Claims lost if only the lead's view were sent", "claims_lost_lead_only"),
        ("Anchors lost if only the lead's anchor were shown", "anchors_lost_lead_only"),
        ("Prompt characters, all selected units", "prompt_chars_total"),
        ("Prompt characters per selected unit", "prompt_chars_per_unit"),
        ("Max calls per match", "max_calls_per_match"),
    )
    total = metrics[0]["positive_total"]
    out = []
    for label, key in rows:
        cells = []
        for m in metrics:
            value = m[key]
            cells.append(
                f"{value} / {total}" if key.startswith("positive_") else str(value)
            )
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    out.append("| Human-rated usefulness | " + " | ".join(["missing data"] * 3) + " |")
    return out


def experiment_c(
    matches: Sequence[MatchData],
    roots: dict[str, Sequence[MatchData]],
    metrics: Sequence[dict[str, Any]],
    gap: float,
    top: int,
    modifiers: frozenset[str],
) -> list[str]:
    """Measured comparison, gap sweep and stability."""
    lines = [
        "## Experiment C: selection and ranking (measured)",
        "",
        "| metric | " + " | ".join(f"`{p}`" for p in POLICIES) + " |",
        "| --- | ---: | ---: | ---: |",
        *_metric_rows(metrics),
        "",
        "- Observations: every positive death stays in exactly one pool unit under "
        "every policy, whatever the budget selects. Fewer deaths inside selected "
        "units is a budget effect, not grouping losing deaths.",
        "- Redundant = a later pick in the same match that shares an episode and a "
        "primary factor set with an earlier pick, or repeats its non-empty "
        "recommendation-template set.",
        "- Grouped prompt size = lead's user prompt plus other members' view JSON "
        "appended verbatim (Experiment D construction).",
        "- Map evidence: no death in this corpus has a map-coverage status, so "
        "`death_map_overlay_before_false` never scores here.",
        "",
        f"### Link-gap sensitivity (research only; production stays {gap:g}s)",
        "",
        "| gap | multi-death episodes | multi-scoring episodes | longest span "
        "| " + " | ".join(POLICIES) + " |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in gap_sweep(matches, top, modifiers):
        lines.append(
            f"| {row['gap']:g}s | {row['episodes_multi_death']} | "
            f"{row['episodes_multi_scoring']} | {row['longest_span']:.1f}s | "
            + " | ".join(str(row[p]) for p in POLICIES)
            + " |"
        )
    lines += ["", *_stability_lines(roots, top, gap, modifiers), ""]
    return lines


def _stability_lines(
    roots: dict[str, Sequence[MatchData]], top: int, gap: float, modifiers: frozenset[str]
) -> list[str]:
    rows = stability(roots, STABILITY_PAIRS, top, gap, modifiers)
    lines = [
        "### Ranking stability",
        "",
        "Jaccard overlap of selected deaths between re-analyses of the same videos "
        f"(deaths matched by video identity and time within "
        f"{STABILITY_TOLERANCE_SECONDS:g}s).",
        "",
        "| pair | shared matches | " + " | ".join(POLICIES) + " |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    lines += [
        f"| {r['pair']} | {r['shared_matches']} | "
        + " | ".join(str(r[p]) for p in POLICIES)
        + " |"
        for r in rows
    ] or ["| (stability roots not present) | | | | |"]
    lines += [
        "",
        "Identical selections across re-analyses mean the policies are equally "
        "stable on this corpus; the test does not discriminate between them.",
    ]
    return lines


def experiment_d(deferred: dict[str, str]) -> list[str]:
    """Deferred LLM experiment: settings and measured input sizes. No calls."""
    manifest = json.loads(deferred["manifest.json"])
    lines = [
        "## Experiment D: LLM efficiency (inputs built, NO calls)",
        "",
        MODEL_MISSING,
        "",
        "| field | value |",
        "| --- | --- |",
        *[f"| {k} | {v} |" for k, v in DEFERRED_SETTINGS.items()],
        "| inputs | `analysis/importance_report/coaching_units_deferred/` |",
        "",
        "Grouped arms append the other members' existing CoachLlmView JSON "
        "verbatim after the lead's user prompt, so no evidence is re-worded: a "
        "later difference between arms is attributable to grouping and context, "
        "not wording. System prompt: "
        f"{manifest['system_prompt_chars']} characters, identical in every arm "
        "and sent once per call.",
        "",
        "Scope: every arm contains only the episode's positive-scoring deaths; "
        "zero-score members are references, not evidence, and are excluded. "
        "Experiment D therefore tests how to package multiple positive-scoring "
        "deaths, not how an episode's complete temporal context affects coaching.",
        "",
        "Cells: calls / user-prompt characters / total input characters "
        "(user prompts plus one system prompt per call).",
        "",
        "| episode | individual | episode | episode-by-factor |",
        "| --- | --- | --- | --- |",
    ]
    system = manifest["system_prompt_chars"]
    for ep in manifest["episodes"]:
        cells = []
        for arm in ("individual", "episode", "episode_by_factor"):
            calls = ep["arms"][arm]
            user = sum(c["chars"] for c in calls)
            cells.append(f"{len(calls)} / {user} / {user + system * len(calls)}")
        lines.append(f"| {ep['episode']} | " + " | ".join(cells) + " |")
    lines += [
        "",
        f"Smallest run that resolves model effects: these {len(manifest['episodes'])} "
        "episodes x 3 arms, one model, temperature 0, outputs validated with "
        "`validate_assessment`, rule hits as the unsupported-claim metric, records "
        "saved under `coach_experiments/` with `experiment: true`, then the same "
        "blind rating.",
        "",
    ]
    return lines


def experiment_e() -> list[str]:
    """Presentation mapping and required schema changes."""
    return [
        "## Experiment E: presentation implications",
        "",
        "| layer | individual death (today) | death episode / episode-by-factor |",
        "| --- | --- | --- |",
        "| CoachInput | One primary `DEATH_EPISODE` + relations | No home: one "
        "primary scenario per CoachInput; members would need a new list "
        "(Scenario ownership stays frozen) |",
        "| CoachLlmView | One `unit`, one `claim_basis`, one anchor | Needs per-member "
        "blocks (the research composer appends them verbatim) |",
        "| LLM output | `claims[]` by `claim_id` | `claims[]` cannot say which death "
        "a claim belongs to; needs member attribution |",
        "| CoachingMoment | One `video_time`, marks for one death lifecycle | One "
        "anchor cannot show two deaths; needs multi-anchor marks |",
        "| Public timestamps | Seek to the death | Which death to seek to is "
        "undefined; members' evidence would be hidden behind the lead |",
        "| Human review | One clip, one judgment | Reviewer must watch a span; "
        "judgment mixes members |",
        "",
        "Required schema changes before any multi-death moment: a member list with "
        "per-member anchors and values; per-member `claim_basis`; claim-to-member "
        "attribution in the output and validator; multi-anchor marks and seek "
        "targets in `CoachingMoment` / `tools/public_site/results.py`; a "
        "`LLM_VIEW_SCHEMA_VERSION` and `COACH_EVIDENCE_VERSION` bump with "
        "staleness handling for existing artifacts.",
        "",
        "**Can one moment faithfully show several deaths?** Not with the current "
        "schema: a single anchor and unattributed claims would present one death's "
        "evidence as if it described the group (the lead-only loss in Experiment "
        "C). Faithful display needs the schema changes above.",
        "",
    ]


def rubric(sheet: str) -> list[str]:
    """Human review rubric and rating-sheet status."""
    rows = max(sheet.count("\n") - 1, 0)
    return [
        "## Human review rubric",
        "",
        HUMAN_MISSING,
        "",
        f"- Sheet: `coaching_units_rating_sheet.csv` ({rows} units, anonymous IDs "
        "`unit_001`..., seeded shuffled order). It shows match, anchors and the "
        "deterministic factor statements only; it never names the policy, model "
        "or experiment arm. `coaching_units_rating_key.csv` unblinds it.",
        "- Units selected by more than one policy appear once. The anchor count "
        "can hint at grouping; rate the content, not the count.",
        "- Score each unit 1-5 (5 best) on: usefulness to the player; redundancy "
        "with other units of the same match (5 = not redundant); whether the "
        "anchor(s) show the situation; missing context (5 = nothing missing); "
        "plus supported yes/no (every statement backed by the evidence shown).",
        "- No recommendation is calculated from human usefulness until the sheet "
        "is completed.",
        "",
    ]


def _redundancy_examples(
    matches: Sequence[MatchData],
    policy: str,
    gap: float,
    top: int,
    modifiers: frozenset[str],
) -> list[str]:
    out = []
    for m in matches:
        picks = select(pool(m, policy, gap, modifiers), top)
        for unit in redundant_units(m, picks, gap, modifiers):
            out.append(f"{m.name[:36]} {' + '.join(_member(d) for d in unit.members)}")
    return out


def examples(
    matches: Sequence[MatchData], gap: float, top: int, modifiers: frozenset[str]
) -> list[str]:
    """Where each representation succeeds and fails, from the corpus."""
    ex = _episode_examples(matches, gap)
    redundant = _redundancy_examples(matches, "deaths", gap, top, modifiers)
    return [
        "## Successes and failures",
        "",
        "- **Individual death succeeds** when deaths are isolated (most of the "
        "corpus: every positive death outside the multi-scoring episodes), and "
        "when an episode mixes opportunities, since each keeps its own anchor "
        f"and claims ({ex['mixed']}).",
        "- **Individual death fails** by spending budget on repeats: "
        + ("; ".join(redundant) or "no redundant picks in this corpus")
        + ".",
        "- **Death episode succeeds** on repeated re-death chains, where one unit "
        f"covers the repeat ({ex['same']}).",
        "- **Death episode fails** on mixed episodes: distinct opportunities share "
        "one unit, and under today's single-anchor moment the members' anchors "
        "and claims would be hidden behind the lead (Experiment C, claims and "
        "anchors lost). The research composer keeps them only by appending "
        "member views, which no production schema can present.",
        "- **Episode-by-factor succeeds** on both patterns: repeats merge, mixed "
        "factors stay separate.",
        "- **Episode-by-factor fails** because the split depends on the factor "
        "catalog: the re-death factor is itself the episode link, so later "
        "members always differ from a non-re-death first member, and a weight "
        "or catalog change silently changes the units.",
        "- **Engagement** never succeeds as a unit with current evidence (single "
        "own splats, no fight boundaries).",
        "",
    ]


def complexity() -> list[str]:
    """Implementation complexity and migration risk."""
    return [
        "## Implementation complexity and migration risk",
        "",
        "| option | complexity | migration risk |",
        "| --- | --- | --- |",
        "| Retain individual death | None | None |",
        "| Episode or episode-by-factor as the LLM unit | High: the 1:1 chain "
        "unit = scenario = CoachInput = LLM prompt = VMV block = public moment "
        "(`coach/llm_view.py`, `coach/vmv.py`, `tools/public_site/results.py`) "
        "must carry members; validator needs claim attribution | High: artifact "
        "and view versions bump, every analysis goes stale, public pages and "
        "translation caches change shape |",
        "| Selection-only de-duplication (keep death units, skip a pick that "
        "repeats an earlier pick's episode and factor) | Low: ranking only | "
        "Medium: changes which deaths are coached; a selection-policy change that "
        "needs its own decision |",
        "",
    ]


def recommendation(
    metrics: Sequence[dict[str, Any]], matches: Sequence[MatchData], gap: float
) -> list[str]:
    """Recommendation from deterministic measurements only."""
    by = {m["policy"]: m for m in metrics}
    d, e, f = by["deaths"], by["episode"], by["episode_by_factor"]
    n_multi = sum(len(multi_scoring_episodes(m, gap)) for m in matches)
    return [
        "## Recommendation",
        "",
        "**Retain the individual death as the production unit.** Supported by the "
        "measurements above; model and human evidence are still missing.",
        "",
        f"- Grouping changes units only in {n_multi} episodes. In the matches "
        "holding them, the budget it frees selects other deaths instead "
        f"({e['positive_in_selected']} versus {d['positive_in_selected']} "
        "positive deaths inside selected units).",
        f"- Redundant picks: {d['redundant_units']} (deaths) versus "
        f"{e['redundant_units']} (episode) and {f['redundant_units']} "
        "(episode-by-factor). This is the only deterministic gain from grouping.",
        f"- Episode grouping costs {e['anchors_lost_lead_only']} anchors and "
        f"{e['claims_lost_lead_only']} claims under a single-anchor moment, and "
        f"{e['prompt_chars_per_unit']} versus {d['prompt_chars_per_unit']} prompt "
        "characters per unit. Episode-by-factor costs "
        f"{f['anchors_lost_lead_only']} anchors and {f['claims_lost_lead_only']} "
        "claims.",
        "- Matches covered, positive observations retained and zero-score "
        "exclusion are identical across policies; stability does not discriminate.",
        "- The episode link is the re-death factor, so grouping re-encodes an "
        "existing factor rather than adding evidence.",
        "",
        "**Do not replace** the unit: no measured benefit justifies the schema "
        "changes in Experiment E. **Supplement candidate:** episode-by-factor (or "
        "the equivalent selection-only de-duplication) is the lowest-risk option "
        "to test next, because it removes repeats without merging distinct "
        "opportunities.",
        "",
        "Not yet decidable, and the smallest experiment for each:",
        "",
        f"- Model effects: {MODEL_MISSING} Run the Experiment D inputs (one model, "
        "temperature 0) and compare validator rule hits and redundant "
        "recommendations per call.",
        f"- Usefulness: {HUMAN_MISSING} Complete the blind sheet; then compare mean "
        "usefulness and redundancy by policy using the key.",
        "",
    ]
