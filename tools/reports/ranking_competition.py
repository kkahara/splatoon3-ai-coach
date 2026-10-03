"""Competition among death candidates in the importance ranking.

Answers, per library: how the score is built, which factors fill the top N,
what each factor displaces when it gets a unit selected, how far unselected
factor-bearing deaths sit from the cutoff, whether scale rather than intent
decides orderings, and how well the evidence behind each rank-1 pick holds up.

Analyses of the same source video (same ``video_identity``) count once.

    python tools/reports/ranking_competition.py --root analysis/_rescore_v2
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

from importance_report import MANIFEST_NAME, PROJECT_ROOT, _score, find_analyses
from loguru import logger

from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.coaching_candidates import (
    active_factor_ids,
    scoring_factor_ids,
)
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig

OUTNUMBERED = "death_while_outnumbered"
MAP_FALSE = "death_map_overlay_before_false"
SPECIAL = "death_special_ready"
CLOCK_FACTORS = ("death_final_30s", "death_first_30s")
SPLAT_ZONES = "splat_zones"
COUNT_FACTORS = (
    "death_while_behind_in_count",
    "death_while_ahead_in_count",
    "death_opponent_counter_ticked",
)
# Existing factors whose overlap with the count factors the plan asks about.
OVERLAP_FACTORS = (
    "death_final_30s",
    OUTNUMBERED,
    "death_while_ahead_in_numbers",
    "death_redeath_le_10s",
)
NONE = "(no factor)"
EMPTY = "(slot left empty)"

# How each factor's evidence is established, for the evidence-strength section.
EVIDENCE_KIND = {
    "death_while_outnumbered": "debounced roster readings (>= 2 agreeing frames)",
    "death_while_ahead_in_numbers": "debounced roster readings (>= 2 agreeing frames)",
    "death_redeath_le_10s": "two DEATH events (video clock)",
    "death_special_ready": "consecutive detector readings classified as ready",
    "death_final_30s": "game-clock OCR at the death",
    "death_first_30s": "game-clock OCR at the death",
    "death_map_overlay_before_false": "absence: no MAP_OVERLAY event detected",
    "death_while_behind_in_count": "fused Splat Zones counts, pre-death sample",
    "death_while_ahead_in_count": "fused Splat Zones counts, pre-death sample",
    "death_opponent_counter_ticked": "two fused Splat Zones samples ~5s apart",
}


@dataclass
class Death:
    """One scored death candidate and the evidence details we audit."""

    match: str
    cid: str
    t: float
    # Factors that contributed weight. Ranking-excluded factors never appear.
    factors: frozenset[str]
    score: float
    rank: int
    selected: bool
    # Every factor detected as a fact, including ranking-excluded ones.
    observed: frozenset[str] = frozenset()
    notes: dict[str, str] = field(default_factory=dict)
    # Splat Zones pre-death remaining_diff (opponent - ally); None if unavailable.
    remaining_diff: int | None = None


@dataclass
class Match:
    """Deaths of one unique source video."""

    name: str
    deaths: list[Death]
    battle_mode_id: str | None = None


def signature(factors: frozenset[str] | set[str]) -> str:
    """Stable label for a factor set."""
    return " + ".join(sorted(factors)) if factors else NONE


MODIFIERS: frozenset[str] = frozenset()


def death_score(factors: frozenset[str], weights: dict[str, float]) -> float:
    """Production score: modifier factors count only beside another factor."""
    supported = any(f not in MODIFIERS for f in factors)
    return sum(weights.get(f, 0.0) for f in factors if supported or f not in MODIFIERS)


def select(
    deaths: list[Death], weights: dict[str, float], top: int, positive: bool
) -> list[str]:
    """Candidate IDs selected under ``weights`` with the production tie-break."""
    scored = sorted(
        deaths,
        key=lambda d: (-death_score(d.factors, weights), d.t, d.cid),
    )
    if positive:
        scored = [death for death in scored if death_score(death.factors, weights) > 0]
    return [death.cid for death in scored[:top]]


def load_matches(root: Path, config: AppConfig) -> list[Match]:
    """Score every unique analysis under ``root``."""
    seen: set[str] = set()
    matches: list[Match] = []
    for folder in find_analyses(root):
        manifest = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
        identity = manifest["analysis"]["video_identity"]
        if identity in seen:
            continue
        seen.add(identity)
        bundle, scoring = _score(folder, config)
        hidden = _hidden_roster_times(manifest)
        name = str(folder.relative_to(root))
        deaths = [
            Death(
                match=name,
                cid=u.candidate_id,
                t=float(u.video_time),
                factors=frozenset(scoring_factor_ids(u.to_candidate())),
                score=float(u.importance_score),
                rank=int(u.rank or 0),
                selected=bool(u.selected_for_llm),
                observed=frozenset(active_factor_ids(u.to_candidate())),
                notes=_evidence_notes(scoring.coach_inputs[u.candidate_id], hidden),
                remaining_diff=_remaining_diff(scoring.coach_inputs[u.candidate_id]),
            )
            for u in scoring.units
        ]
        matches.append(
            Match(name=name, deaths=deaths, battle_mode_id=bundle.battle_mode_id)
        )
    return matches


def _remaining_diff(ci: CoachInput) -> int | None:
    facts = ci.score_facts
    if facts is None or facts.sample_label != "pre_death":
        return None
    return facts.remaining_diff


def _hidden_roster_times(manifest: dict) -> list[float]:
    """Alive, in-match frames where the roster HUD was hidden: a proxy for map open."""
    snaps = {s["timestamp"]: s for s in manifest.get("state_snapshots") or ()}
    times = []
    for frame in manifest.get("frame_results") or ():
        snap = snaps.get(frame["timestamp"]) or {}
        if (
            snap.get("match_phase") != "in_match"
            or snap.get("player_lifecycle") != "alive"
        ):
            continue
        for det in frame["detections"]:
            if (
                det["detector_name"] == "player_count"
                and det["reading"].get("roster_visible") is False
            ):
                times.append(float(frame["timestamp"]))
    return times


def _evidence_notes(ci: CoachInput, hidden_roster: list[float]) -> dict[str, str]:
    """Per-factor evidence details for this death."""
    notes: dict[str, str] = {}
    ctx = ci.primary_context
    death_time = ctx.death_episode.death_time if ctx.death_episode else None
    clock = next((s for s in ci.game_clock_samples if s.label == "death"), None)
    if clock is not None and clock.observation is not None:
        notes["clock"] = (
            f"gap {float(clock.gap_seconds or 0):.1f}s "
            f"conf {float(clock.observation.confidence):.2f}"
        )
    if (
        ctx.special is not None
        and ctx.special.nearest_before_anchor is not None
        and death_time is not None
    ):
        ready_run = 0
        for reading in sorted(ctx.special.observations, key=lambda r: -r.video_time):
            if reading.video_time > death_time:
                continue
            if not reading.ready:
                break
            ready_run += 1
        near = ctx.special.nearest_before_anchor
        notes["special"] = (
            f"{ready_run} consecutive ready reading(s) before death; nearest "
            f"{death_time - near.video_time:.1f}s before"
        )
    if death_time is not None:
        before = [t for t in hidden_roster if t < death_time]
        notes["map_proxy"] = str(len(before))
    return notes


def score_section(config: AppConfig) -> list[str]:
    """Current weights and the scoring rule."""
    coach = config.coach
    weights = dict(coach.death_importance_weights)
    lines = [
        "## 1. Weights and score",
        "",
        "| factor | weight | evidence |",
        "| --- | ---: | --- |",
    ]
    for name, weight in sorted(weights.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{name}` | {weight:g} | {EVIDENCE_KIND.get(name, '')} |")
    lines += [
        "",
        "Score = sum of the weights of the active factors (each factor is on/off; "
        "no magnitudes). Deaths are ranked by score, ties broken by earlier video "
        f"time. The top {coach.max_llm_units} are selected"
        + (
            ", but only if they score above zero."
            if coach.llm_units_require_positive_score
            else "."
        ),
        "",
    ]
    return lines


def occupancy_section(matches: list[Match], weights: dict[str, float]) -> list[str]:
    """Which factors and factor sets fill ranks 1..N."""
    by_rank: dict[int, Counter[str]] = defaultdict(Counter)
    sig_selected: Counter[str] = Counter()
    sig_all: Counter[str] = Counter()
    observed_all: Counter[str] = Counter()
    scoring_all: Counter[str] = Counter()
    scoring_sel: Counter[str] = Counter()
    for match in matches:
        for d in match.deaths:
            sig_all[signature(d.factors)] += 1
            observed_all.update(d.observed)
            scoring_all.update(d.factors)
            if d.selected:
                by_rank[d.rank].update(d.factors or {NONE})
                sig_selected[signature(d.factors)] += 1
                scoring_sel.update(d.factors)
    ranks = sorted(by_rank)
    lines = [
        "## 2. Who occupies the selected ranks",
        "",
        "`observed` counts deaths where the factor was detected as a fact; "
        "`scoring` counts deaths where it actually added weight. They differ for "
        "ranking-excluded factors and for clock modifiers with nothing to modify.",
        "",
        "| factor | weight | observed | scoring | selected | selection rate | "
        + " | ".join(f"at rank {r}" for r in ranks)
        + " |",
        "| --- | ---: | ---: | ---: | ---: | ---: | "
        + " | ".join("---:" for _ in ranks)
        + " |",
    ]
    for name in sorted(weights, key=lambda n: -weights[n]):
        rate = (
            f"{100 * scoring_sel[name] / scoring_all[name]:.0f}%"
            if scoring_all[name]
            else "–"
        )
        cells = " | ".join(str(by_rank[r][name]) for r in ranks)
        lines.append(
            f"| `{name}` | {weights[name]:g} | {observed_all[name]} | "
            f"{scoring_all[name]} | {scoring_sel[name]} | {rate} | {cells} |"
        )
    lines += [
        "",
        "Factor sets that scored (score, selected / occurring):",
        "",
        "| factor set | score | selected | occurring |",
        "| --- | ---: | ---: | ---: |",
    ]
    for sig, n in sorted(sig_all.items(), key=lambda kv: -_sig_score(kv[0], weights)):
        lines.append(
            f"| {sig} | {_sig_score(sig, weights):g} | {sig_selected[sig]} | {n} |"
        )
    empty_with_evidence = sum(
        1 for m in matches for d in m.deaths if not d.factors and d.observed
    )
    lines += [
        "",
        f"Of the {sig_all[NONE]} deaths that scored nothing, {empty_with_evidence} did "
        f"carry observed evidence (ranking-excluded factors, or a clock window with no "
        f"factor to modify) and {sig_all[NONE] - empty_with_evidence} carried none.",
        "",
    ]
    return lines


def _sig_score(sig: str, weights: dict[str, float]) -> float:
    if sig == NONE:
        return 0.0
    return death_score(frozenset(sig.split(" + ")), weights)


def displacement_section(
    matches: list[Match], weights: dict[str, float], top: int, positive: bool
) -> list[str]:
    """For each factor: what its weight pushes out of the top N, and near misses."""
    lines = [
        "## 3. Displacement: what each factor pushes out",
        "",
        "For each factor, its weight is set to 0 and the selection recomputed. "
        "Deaths that enter are what the factor was displacing; `(slot left empty)` "
        "means removing the factor frees a slot nobody else qualifies for.",
        "",
        "| factor | selections that depend on it | displaced deaths (factor sets) |",
        "| --- | ---: | --- |",
    ]
    for factor in sorted(weights, key=lambda n: -weights[n]):
        without = {**weights, factor: 0.0}
        dependent = 0
        displaced: Counter[str] = Counter()
        for match in matches:
            base = select(match.deaths, weights, top, positive)
            alt = select(match.deaths, without, top, positive)
            lost = [c for c in base if c not in alt]
            dependent += len(lost)
            entered = [c for c in alt if c not in base]
            by_id = {d.cid: d for d in match.deaths}
            for cid in entered:
                displaced[signature(by_id[cid].factors)] += 1
            displaced[EMPTY] += max(0, len(lost) - len(entered))
        text = "; ".join(f"{sig} ×{n}" for sig, n in displaced.most_common()) or "–"
        lines.append(f"| `{factor}` | {dependent} | {text} |")
    lines += ["", *near_miss_lines(matches, weights, top)]
    return lines


def near_miss_lines(
    matches: list[Match], weights: dict[str, float], top: int
) -> list[str]:
    """Unselected deaths carrying a factor: margin to the cutoff score."""
    rows: dict[str, list[str]] = defaultdict(list)
    for match in matches:
        selected = [d for d in match.deaths if d.selected]
        if len(selected) < top:
            continue
        cutoff = min(d.score for d in selected)
        for d in match.deaths:
            if d.selected or not d.factors:
                continue
            gap = cutoff - d.score
            how = "tie, lost on time" if gap == 0 else f"{gap:g} below"
            for factor in d.factors:
                rows[factor].append(f"{d.score:g} vs cutoff {cutoff:g} ({how})")
    lines = [
        "Unselected deaths that carry a factor, against their match's cutoff "
        "(score of the last selected death):",
        "",
        "| factor | unselected | margins |",
        "| --- | ---: | --- |",
    ]
    for factor in sorted(weights, key=lambda n: -weights[n]):
        margins = rows.get(factor, [])
        shown = (
            "; ".join(sorted(margins)) if margins else "– (always selected when active)"
        )
        lines.append(f"| `{factor}` | {len(margins)} | {shown} |")
    lines.append("")
    return lines


def scale_section(
    matches: list[Match], weights: dict[str, float], top: int, positive: bool
) -> list[str]:
    """Where numeric scale, not intended importance, decides the ordering."""
    lines = [
        "## 4. Scale effects",
        "",
        "Orderings the weights imply between a single factor and a pair of others "
        "(only pairs that actually co-occur in the library):",
        "",
    ]
    pairs = Counter()
    for match in matches:
        for d in match.deaths:
            for pair in combinations(sorted(d.factors), 2):
                pairs[pair] += 1
    for (a, b), n in pairs.most_common():
        pair_score = death_score(frozenset((a, b)), weights)
        beaten = [
            f
            for f in weights
            if f not in (a, b) and death_score(frozenset({f}), weights) > pair_score
        ]
        tied = [
            f
            for f in weights
            if f not in (a, b) and death_score(frozenset({f}), weights) == pair_score
        ]
        lines.append(
            f"- `{a}` + `{b}` (occurs {n}×) = {pair_score:g}: outranked by "
            f"{', '.join(f'`{f}`' for f in beaten) or 'nothing alone'}"
            + (f"; tied with {', '.join(f'`{f}`' for f in tied)}" if tied else "")
        )
    uniform = {f: 1.0 for f in weights}
    rank_only = {
        f: float(len(weights) - i)
        for i, f in enumerate(sorted(weights, key=lambda n: -weights[n]))
    }
    changes = {}
    for label, alt in (
        ("every weight 1", uniform),
        ("weights = rank order 7..1", rank_only),
    ):
        changes[label] = sum(
            set(select(m.deaths, weights, top, positive))
            != set(select(m.deaths, alt, top, positive))
            for m in matches
        )
    contested = sum(1 for m in matches if sum(1 for d in m.deaths if d.score > 0) > top)
    lines += [
        "",
        f"Matches with more than {top} scoring deaths (where weights can matter at all): "
        f"{contested} of {len(matches)}.",
        "Matches whose selection changes if the magnitudes are replaced: "
        + "; ".join(f"{k}: {v}" for k, v in changes.items())
        + ".",
        "",
    ]
    return lines


def evidence_section(matches: list[Match]) -> list[str]:
    """Rank-1 picks: margin over rank 2 and how their evidence holds up."""
    lines = [
        "## 5. Rank-1 picks: evidence versus score",
        "",
        "| match | rank-1 factors | score | margin over rank 2 | evidence notes |",
        "| --- | --- | ---: | --- | --- |",
    ]
    tally: Counter[str] = Counter()
    for match in matches:
        ordered = sorted(match.deaths, key=lambda d: d.rank)
        if not ordered or not ordered[0].selected:
            continue
        first = ordered[0]
        second = ordered[1].score if len(ordered) > 1 else None
        if second is None:
            margin = "only death"
        elif first.score == second:
            margin = "tie (won on earlier time)"
            tally["tie"] += 1
        else:
            margin = f"+{first.score - second:g}"
        if first.observed - first.factors:
            tally["rank-1 also carrying non-scoring evidence"] += 1
        notes = _notes_for(first)
        lines.append(
            f"| {match.name[:40]} | {signature(first.factors)} | {first.score:g} | "
            f"{margin} | {notes} |"
        )
    lines += ["", *(f"- {k}: {v}" for k, v in tally.items()), ""]
    return lines


def _notes_for(death: Death) -> str:
    """Evidence notes, including factors observed but excluded from scoring."""
    parts = []
    if death.observed & set(CLOCK_FACTORS) and "clock" in death.notes:
        parts.append(f"clock {death.notes['clock']}")
    if SPECIAL in death.observed and "special" in death.notes:
        parts.append(f"special (not scored): {death.notes['special']}")
    if MAP_FALSE in death.observed:
        n = int(death.notes.get("map_proxy", "0"))
        parts.append(
            f"map absence (not scored): {n} hidden-roster frame(s) while alive "
            "before death" + (" (map may have been open)" if n else "")
        )
    return "; ".join(parts) or "–"


def map_absence_audit(matches: list[Match]) -> list[str]:
    """How many 'no map check' deaths have independent signs the map was open."""
    flagged = [d for m in matches for d in m.deaths if MAP_FALSE in d.observed]
    contradicted = [d for d in flagged if int(d.notes.get("map_proxy", "0")) > 0]
    specials = [d for m in matches for d in m.deaths if SPECIAL in d.observed]
    lines = [
        "## 6. Evidence audit for the weaker factors",
        "",
        "Counts are observed detections. Both factors are ranking-excluded, so "
        "none of these deaths scored on them.",
        "",
        f"- `{MAP_FALSE}`: {len(flagged)} deaths. {len(contradicted)} of them have alive "
        "frames before the death where the roster HUD was hidden, which is how an open "
        "map looks to the roster detector — the map detector may have missed a glance.",
    ]
    lines += [
        f"  - {d.match[:40]} `{d.cid}`: {d.notes.get('map_proxy')} frame(s), "
        f"{'selected' if d.selected else 'not selected'}"
        for d in contradicted
    ]
    lines.append(f"- `{SPECIAL}`: {len(specials)} deaths.")
    lines += [
        f"  - {d.match[:40]} `{d.cid}`: {d.notes.get('special', 'no reading')}"
        for d in specials
    ]
    lines.append("")
    return lines


def splat_zones_section(matches: list[Match], config: AppConfig) -> list[str]:
    """Count factors: observed / scoring / selected and overlap (Splat Zones only)."""
    sz = [m for m in matches if m.battle_mode_id == SPLAT_ZONES]
    deaths = [d for m in sz for d in m.deaths]
    with_diff = [d for d in deaths if d.remaining_diff is not None]
    min_diff = config.coach.death_factor_thresholds.count_min_diff
    excluded = set(config.coach.death_ranking_excluded_factors)
    lines = [
        "## 7. Splat Zones count factors (observed-but-excluded)",
        "",
        f"Splat Zones matches: {len(sz)} of {len(matches)}; deaths: {len(deaths)}; "
        f"with both counts observed at the pre-death sample: {len(with_diff)}. "
        f"`count_min_diff` = {min_diff}. Other modes never carry these factors.",
        "",
        "| factor | ranking-excluded | observed | scoring | selected (on other "
        "factors) | observed but scoring 0 today |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for name in COUNT_FACTORS:
        observed = [d for d in deaths if name in d.observed]
        lines.append(
            f"| `{name}` | {'yes' if name in excluded else 'no'} | {len(observed)} | "
            f"{sum(name in d.factors for d in observed)} | "
            f"{sum(d.selected for d in observed)} | "
            f"{sum(d.score == 0 for d in observed)} |"
        )
    lines += [
        "",
        "`death_opponent_counter_ticked` is under-observed by construction: the "
        "ticking team's pod is highlighted, and highlighted-pod reads are withheld "
        "by fusion (Stage 3.2 known detector gap).",
        "",
        *_overlap_lines(deaths),
        "",
        *_diff_distribution(with_diff),
        "",
    ]
    return lines


def _overlap_lines(deaths: list[Death]) -> list[str]:
    """How often each count factor co-occurs with the existing factors."""
    lines = [
        "Overlap with existing factors (deaths where both are observed):",
        "",
        "| count factor | observed | "
        + " | ".join(f"+ `{f}`" for f in OVERLAP_FACTORS)
        + " | none of these |",
        "| --- | ---: | " + " | ".join("---:" for _ in OVERLAP_FACTORS) + " | ---: |",
    ]
    for name in COUNT_FACTORS:
        observed = [d for d in deaths if name in d.observed]
        cells = " | ".join(
            str(sum(f in d.observed for d in observed)) for f in OVERLAP_FACTORS
        )
        alone = sum(not (d.observed & set(OVERLAP_FACTORS)) for d in observed)
        lines.append(f"| `{name}` | {len(observed)} | {cells} | {alone} |")
    return lines


def _diff_distribution(deaths: list[Death]) -> list[str]:
    """Pre-death remaining_diff buckets (positive: ally team needs fewer)."""
    buckets = (
        ("<= -30", lambda v: v <= -30),
        ("-29..-10", lambda v: -29 <= v <= -10),
        ("-9..-1", lambda v: -9 <= v <= -1),
        ("0", lambda v: v == 0),
        ("1..9", lambda v: 1 <= v <= 9),
        ("10..29", lambda v: 10 <= v <= 29),
        (">= 30", lambda v: v >= 30),
    )
    values = [int(d.remaining_diff) for d in deaths if d.remaining_diff is not None]
    return [
        "Pre-death `remaining_diff` = opponent_remaining − ally_remaining "
        "(negative: ally team needs more):",
        "",
        "| bucket | deaths |",
        "| --- | ---: |",
        *(f"| {label} | {sum(test(v) for v in values)} |" for label, test in buckets),
    ]


def _units_per_match(matches: list[Match]) -> str:
    """Distribution of selected units per match, e.g. ``0 units: 9; 1 unit: 7``."""
    dist = Counter(sum(d.selected for d in m.deaths) for m in matches)
    return "; ".join(
        f"{n} unit{'' if n == 1 else 's'}: {count}" for n, count in sorted(dist.items())
    )


def build(matches: list[Match], config: AppConfig) -> str:
    """Whole report."""
    coach = config.coach
    weights = dict(coach.death_importance_weights)
    top, positive = coach.max_llm_units, coach.llm_units_require_positive_score
    deaths = sum(len(m.deaths) for m in matches)
    header = [
        "# Ranking competition",
        "",
        f"Unique matches: {len(matches)}. Deaths: {deaths}. Selected: "
        f"{sum(d.selected for m in matches for d in m.deaths)} units across "
        f"{sum(1 for m in matches if any(d.selected for d in m.deaths))} matches "
        f"({_units_per_match(matches)}).",
        "",
    ]
    return "\n".join(
        header
        + score_section(config)
        + occupancy_section(matches, weights)
        + displacement_section(matches, weights, top, positive)
        + scale_section(matches, weights, top, positive)
        + evidence_section(matches)
        + map_absence_audit(matches)
        + splat_zones_section(matches, config)
    )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root", type=Path, default=PROJECT_ROOT / "analysis" / "_rescore_v2"
    )
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "analysis" / "importance_report" / "competition.md",
    )
    parser.add_argument(
        "--splat-zones",
        action="store_true",
        help="Write only the Splat Zones count-factor section.",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    global MODIFIERS
    MODIFIERS = frozenset(config.coach.death_modifier_factors)
    matches = load_matches(args.root.resolve(), config)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.splat_zones:
        text = "\n".join(
            ["# Ranking competition", "", *splat_zones_section(matches, config)]
        )
    else:
        text = build(matches, config)
    args.out.write_text(text + "\n", encoding="utf-8")
    logger.info("Wrote {}", args.out)


if __name__ == "__main__":
    main()
