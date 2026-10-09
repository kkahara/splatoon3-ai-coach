"""Research: alternative coaching units (deaths, episodes, episode-by-factor).

Compares candidate coaching units on existing analyses under the current
production scoring. Changes nothing in production: scores, factors, views
and prompts come from production code; grouping exists only here.

Rules kept by every policy:

- A positive-scoring death is always retained as an observation in exactly
  one unit of the pool, whether or not that unit is selected at the budget.
- A zero-score death never becomes a unit member of a scoring unit and never
  contributes a factor, claim or observation. It may still be *referenced*
  as the previous death behind ``time_since_previous_death``.

Experiment D inputs are built and written, but no model is ever invoked.

    python tools/reports/coaching_units.py --root analysis/_rescore_v2
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import random
import re
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from importance_report import MANIFEST_NAME, PROJECT_ROOT, _score, find_analyses
from loguru import logger
from ranking_episodes import Candidate, candidates, episodes, primary, select

from splatoon3_ai_coach.coach.coach import serialize_llm_view_user_prompt
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig

POLICIES = ("deaths", "episode", "episode_by_factor")
GAP_SWEEP = (10.0, 15.0, 20.0, 25.0)
STABILITY_TOLERANCE_SECONDS = 1.5
RATING_SEED = 20261007
OBSERVATIONS_HEADER = (
    "\n\nOther scoring deaths in this unit (their existing CoachLlmView JSON, "
    "verbatim, one per line):\n"
)
DEFERRED_SETTINGS = {
    "model": "deferred",
    "temperature": 0,
    "budget_calls_per_match": 3,
    "validation": "claim_validation.validate_assessment",
    "unsupported_claim_metric": "validator rule hits",
    "storage": "coach_experiments/ with experiment: true",
    "llm_calls_made": 0,
}
RATING_COLUMNS = (
    "usefulness_1to5",
    "redundancy_1to5",
    "anchor_shows_situation_1to5",
    "missing_context_1to5",
    "supported_yes_no",
    "notes",
)
REPORT_DIR = PROJECT_ROOT / "analysis" / "importance_report"


@dataclass(frozen=True)
class Obs:
    """One scored death as a research observation (never re-scored here)."""

    match: str
    cid: str
    t: float
    score: float
    factors: frozenset[str]
    observed: frozenset[str] = frozenset()
    prev_death_t: float | None = None
    selected: bool = False
    prompt: str = ""
    body: str = ""
    coachable: frozenset[str] = frozenset()
    templates: frozenset[str] = frozenset()
    statements: tuple[str, ...] = ()
    special: str | None = None
    map_status: str | None = None

    @property
    def key(self) -> str:
        """Match-qualified death identity."""
        return f"{self.match}|{self.cid}"


@dataclass
class MatchData:
    """Deaths and secondary scenarios of one unique source video."""

    name: str
    identity: str
    deaths: list[Obs]
    engagements: list[dict[str, Any]] = field(default_factory=list)
    low_ink: list[tuple[float, float]] = field(default_factory=list)
    death_intervals: list[tuple[float, float]] = field(default_factory=list)


# --------------------------------------------------------------------------- load


def load_root(root: Path, config: AppConfig) -> list[MatchData]:
    """Score every unique analysis under ``root`` (same video counts once)."""
    seen: set[str] = set()
    matches: list[MatchData] = []
    for folder in find_analyses(root):
        manifest = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
        identity = str(manifest["analysis"]["video_identity"])
        if identity in seen:
            continue
        seen.add(identity)
        matches.append(_load_match(folder, root, identity, manifest, config))
    return matches


def _load_match(
    folder: Path, root: Path, identity: str, manifest: dict[str, Any], config: AppConfig
) -> MatchData:
    _, scoring = _score(folder, config)
    name = str(folder.relative_to(root))
    thresholds = config.coach.death_factor_thresholds
    deaths = []
    for unit in scoring.units:
        coach_input = scoring.coach_inputs[unit.candidate_id]
        view = build_coach_llm_view(coach_input, unit, thresholds)
        deaths.append(observation(name, unit, coach_input, view))
    scenarios = json.loads((folder / "scenarios.json").read_text(encoding="utf-8"))
    rows = scenarios if isinstance(scenarios, list) else scenarios.get("scenarios", [])
    return MatchData(
        name=name,
        identity=identity,
        deaths=deaths,
        engagements=[r for r in rows if r["scenario_type"] == "engagement"],
        low_ink=[
            (float(e["start_time"]), float(e.get("end_time") or e["start_time"]))
            for e in manifest.get("game_events") or ()
            if e.get("event_type") == "low_ink"
        ],
        death_intervals=[
            (float(r["start_time"]), float(r["end_time"]))
            for r in rows
            if r["scenario_type"] == "death_episode"
        ],
    )


def observation(name: str, unit: Any, coach_input: Any, view: Any) -> Obs:
    """Observation from production unit, CoachInput and CoachLlmView."""
    payload = view.model_dump(mode="json")
    timeline = coach_input.primary_context.timeline
    gap = timeline.time_since_previous_death if timeline else None
    t = float(unit.video_time)
    map_block = (payload.get("map") or {}).get("map_check_before_death")
    basis = list(payload.get("claim_basis") or ())
    return Obs(
        match=name,
        cid=unit.candidate_id,
        t=t,
        score=float(unit.importance_score),
        factors=frozenset(f.factor_id for f in unit.factors if f.contribution > 0),
        observed=frozenset(f.factor_id for f in unit.factors if f.active),
        prev_death_t=None if gap is None else round(t - float(gap), 3),
        selected=bool(unit.selected_for_llm),
        prompt=serialize_llm_view_user_prompt(view),
        body=json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ),
        coachable=frozenset(e["claim_id"] for e in basis if e.get("coachable")),
        templates=frozenset(r for e in basis for r in e.get("rec_template_ids") or ()),
        statements=tuple(
            f.statement_player for f in unit.factors if f.active and f.statement_player
        ),
        special=(payload.get("special") or {}).get("state_at_anchor"),
        map_status=map_block.get("status") if isinstance(map_block, dict) else None,
    )


# -------------------------------------------------------------------------- units


def pool(
    match: Any, policy: str, gap: float, modifiers: frozenset[str]
) -> list[Candidate]:
    """Every unit of one match under ``policy`` (the research corpus)."""
    return candidates(match, policy, gap, modifiers)


def by_factor(scoring: Sequence[Obs], modifiers: frozenset[str]) -> list[Candidate]:
    """Scoring deaths grouped by primary (non-modifier) factor set."""
    groups: dict[frozenset[str], list[Obs]] = {}
    for death in scoring:
        groups.setdefault(primary(death, modifiers), []).append(death)
    return [Candidate(members) for members in groups.values()]


def multi_scoring_episodes(match: Any, gap: float) -> list[list[Obs]]:
    """Episodes holding two or more positive-scoring deaths."""
    return [g for g in episodes(match, gap) if sum(d.score > 0 for d in g) >= 2]


def unit_prompt(unit: Candidate) -> str:
    """Lead's user prompt plus other members' view JSON appended verbatim."""
    lead = unit.lead
    others = sorted((d for d in unit.members if d is not lead), key=lambda d: d.t)
    if not others:
        return lead.prompt
    return lead.prompt + OBSERVATIONS_HEADER + "\n".join(d.body for d in others)


def zero_score_members(units: Iterable[Candidate]) -> list[Obs]:
    """Zero-score deaths that sit inside a scoring or multi-member unit."""
    return [
        d
        for u in units
        if u.score > 0 or len(u.members) > 1
        for d in u.members
        if d.score <= 0
    ]


def references(match: Any) -> list[tuple[Obs, Obs]]:
    """(positive death, zero-score previous death it references via the timeline)."""
    by_t = {round(d.t, 3): d for d in match.deaths}
    pairs = []
    for death in match.deaths:
        if death.score <= 0 or death.prev_death_t is None:
            continue
        prev = by_t.get(death.prev_death_t)
        if prev is not None and prev.score <= 0:
            pairs.append((death, prev))
    return pairs


# ------------------------------------------------------------------------ metrics


def _episode_index(match: Any, gap: float) -> dict[str, int]:
    return {d.cid: i for i, group in enumerate(episodes(match, gap)) for d in group}


def _signature(unit: Candidate, modifiers: frozenset[str]) -> frozenset[frozenset[str]]:
    return frozenset(primary(d, modifiers) for d in unit.members)


def redundant_units(
    match: Any, picks: Sequence[Candidate], gap: float, modifiers: frozenset[str]
) -> list[Candidate]:
    """Selected units repeating an earlier pick's opportunity in one match.

    Repeat = same episode and a shared primary factor set, or the same
    non-empty recommendation-template set.
    """
    index = _episode_index(match, gap)
    flagged: list[Candidate] = []
    for i, unit in enumerate(picks):
        templates = frozenset().union(*(d.templates for d in unit.members))
        for earlier in picks[:i]:
            same_episode = index[earlier.lead.cid] == index[unit.lead.cid]
            shared = _signature(unit, modifiers) & _signature(earlier, modifiers)
            earlier_templates = frozenset().union(*(d.templates for d in earlier.members))
            if (same_episode and shared) or (
                templates and templates == earlier_templates
            ):
                flagged.append(unit)
                break
    return flagged


def _unit_context(unit: Candidate) -> tuple[int, int, int]:
    """(coachable claims in unit, claims missing from lead-only view, anchors lost)."""
    claims = frozenset().union(*(d.coachable for d in unit.members))
    return len(claims), len(claims - unit.lead.coachable), len(unit.members) - 1


def policy_metrics(
    matches: Sequence[Any], policy: str, gap: float, top: int, modifiers: frozenset[str]
) -> dict[str, Any]:
    """Every Experiment C metric for one policy."""
    pools = {m.name: pool(m, policy, gap, modifiers) for m in matches}
    picks = {name: select(units, top) for name, units in pools.items()}
    positive = {d.key for m in matches for d in m.deaths if d.score > 0}
    in_pool = {d.key for units in pools.values() for u in units for d in u.members}
    selected = [u for units in picks.values() for u in units]
    in_selected = {d.key for u in selected for d in u.members}
    context = [_unit_context(u) for u in selected]
    prompts = [len(unit_prompt(u)) for u in selected]
    by_name = {m.name: m for m in matches}
    return {
        "policy": policy,
        "pool_units": sum(len(v) for v in pools.values()),
        "selected_units": len(selected),
        "matches_covered": sum(1 for v in picks.values() if v),
        "positive_total": len(positive),
        "positive_as_observations": len(positive & in_pool),
        "positive_in_selected": len(positive & in_selected),
        "zero_promoted": len(
            zero_score_members(u for units in pools.values() for u in units)
        ),
        "redundant_units": sum(
            len(redundant_units(by_name[n], v, gap, modifiers)) for n, v in picks.items()
        ),
        "distinct_opportunities": sum(
            len({primary(d, modifiers) for u in v for d in u.members} - {frozenset()})
            for v in picks.values()
        ),
        "context_claims_per_unit": _mean([c[0] for c in context]),
        "claims_lost_lead_only": sum(c[1] for c in context),
        "anchors_lost_lead_only": sum(c[2] for c in context),
        "prompt_chars_total": sum(prompts),
        "prompt_chars_per_unit": _mean(prompts),
        "max_calls_per_match": max((len(v) for v in picks.values()), default=0),
    }


def _mean(values: Sequence[float]) -> float:
    return round(statistics.fmean(values), 2) if values else 0.0


def gap_sweep(
    matches: Sequence[Any], top: int, modifiers: frozenset[str]
) -> list[dict[str, Any]]:
    """Episode counts and selections at each research link gap (not a policy)."""
    rows = []
    for gap in GAP_SWEEP:
        groups = [g for m in matches for g in episodes(m, gap)]
        multi = [g for g in groups if len(g) >= 2]
        row: dict[str, Any] = {
            "gap": gap,
            "episodes_multi_death": len(multi),
            "episodes_multi_scoring": sum(
                1 for g in multi if sum(d.score > 0 for d in g) >= 2
            ),
            "longest_span": max((g[-1].t - g[0].t for g in multi), default=0.0),
        }
        for policy in POLICIES:
            row[policy] = sum(
                len(select(pool(m, policy, gap, modifiers), top)) for m in matches
            )
        rows.append(row)
    return rows


# ---------------------------------------------------------------------- stability


def _selected_times(
    matches: Sequence[Any], policy: str, gap: float, top: int, modifiers: frozenset[str]
) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for m in matches:
        picks = select(pool(m, policy, gap, modifiers), top)
        out[m.identity] = sorted(d.t for u in picks for d in u.members)
    return out


def jaccard_by_time(
    left: dict[str, list[float]], right: dict[str, list[float]], tolerance: float
) -> float | None:
    """Selection overlap; deaths match by video identity and time within tolerance."""
    shared = sorted(set(left) & set(right))
    union = inter = 0
    for identity in shared:
        a, b = list(left[identity]), list(right[identity])
        hits = 0
        for t in a:
            near = [x for x in b if abs(x - t) <= tolerance]
            if near:
                b.remove(min(near, key=lambda x: abs(x - t)))
                hits += 1
        inter += hits
        union += len(left[identity]) + len(right[identity]) - hits
    return round(inter / union, 3) if union else None


def stability(
    roots: dict[str, Sequence[Any]],
    pairs: Sequence[tuple[str, str]],
    top: int,
    gap: float,
    modifiers: frozenset[str],
) -> list[dict[str, Any]]:
    """Jaccard of selected deaths between root pairs, per policy."""
    rows = []
    for a, b in pairs:
        if a not in roots or b not in roots:
            continue
        row: dict[str, Any] = {"pair": f"{a} vs {b}"}
        shared = {m.identity for m in roots[a]} & {m.identity for m in roots[b]}
        row["shared_matches"] = len(shared)
        for policy in POLICIES:
            left = _selected_times(roots[a], policy, gap, top, modifiers)
            right = _selected_times(roots[b], policy, gap, top, modifiers)
            row[policy] = jaccard_by_time(left, right, STABILITY_TOLERANCE_SECONDS)
        rows.append(row)
    return rows


# --------------------------------------------------------- Experiment D (no calls)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")[:48]


def deferred_arms(scoring: Sequence[Obs], modifiers: frozenset[str]) -> dict[str, list]:
    """The three arms for one multi-scoring-death episode."""
    return {
        "individual": [Candidate([d]) for d in scoring],
        "episode": [Candidate(list(scoring))],
        "episode_by_factor": by_factor(scoring, modifiers),
    }


def deferred_inputs(
    matches: Sequence[Any], gap: float, modifiers: frozenset[str], system_prompt: str
) -> dict[str, str]:
    """Exact Experiment D input files (relative path -> text). Never calls a model."""
    files: dict[str, str] = {"system_prompt.txt": system_prompt}
    episodes_meta = []
    for match in matches:
        for i, group in enumerate(multi_scoring_episodes(match, gap), start=1):
            scoring = [d for d in group if d.score > 0]
            slug = f"{_slug(match.name)}__ep{i}"
            arms_meta: dict[str, list[dict[str, Any]]] = {}
            for arm, units in deferred_arms(scoring, modifiers).items():
                arms_meta[arm] = []
                for j, unit in enumerate(sorted(units, key=lambda u: u.lead.t), start=1):
                    path = f"{slug}/{arm}/call_{j}.user_prompt.txt"
                    files[path] = unit_prompt(unit)
                    arms_meta[arm].append(
                        {
                            "file": path,
                            "chars": len(files[path]),
                            "lead": unit.lead.cid,
                            "members": [
                                d.cid for d in sorted(unit.members, key=lambda d: d.t)
                            ],
                        }
                    )
            episodes_meta.append(
                {"match": match.name, "episode": slug, "arms": arms_meta}
            )
    manifest = {
        "settings": DEFERRED_SETTINGS,
        "system_prompt_chars": len(system_prompt),
        "episodes": episodes_meta,
    }
    files["manifest.json"] = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    return files


def write_files(out_dir: Path, files: dict[str, str]) -> None:
    """Replace ``out_dir`` contents with ``files`` (deterministic byte output)."""
    if out_dir.is_dir():
        for path in sorted(out_dir.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
    for rel, text in sorted(files.items()):
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


# ------------------------------------------------------------------ rating sheet


def rating_units(
    matches: Sequence[Any], gap: float, top: int, modifiers: frozenset[str]
) -> dict[tuple[str, ...], dict[str, Any]]:
    """Selected units of every policy, deduplicated by member set."""
    units: dict[tuple[str, ...], dict[str, Any]] = {}
    for policy in POLICIES:
        for match in matches:
            for unit in select(pool(match, policy, gap, modifiers), top):
                key = tuple(sorted(d.key for d in unit.members))
                entry = units.setdefault(key, {"unit": unit, "policies": []})
                entry["policies"].append(policy)
    return units


def _evidence_text(unit: Candidate) -> str:
    return " || ".join(
        f"{d.t:.1f}s: " + (" ".join(d.statements) or "(no factor statement)")
        for d in sorted(unit.members, key=lambda d: d.t)
    )


def rating_sheet(
    matches: Sequence[Any],
    gap: float,
    top: int,
    modifiers: frozenset[str],
    seed: int = RATING_SEED,
) -> tuple[str, str]:
    """(blind sheet CSV, key CSV). The sheet never names policy, model or arm."""
    units = rating_units(matches, gap, top, modifiers)
    keys = sorted(units)
    random.Random(seed).shuffle(keys)
    sheet, key = io.StringIO(), io.StringIO()
    sheet_w = csv.writer(sheet, lineterminator="\n")
    key_w = csv.writer(key, lineterminator="\n")
    sheet_w.writerow(("unit_id", "match", "anchors", "evidence", *RATING_COLUMNS))
    key_w.writerow(("unit_id", "match", "members", "lead", "policies"))
    for n, members in enumerate(keys, start=1):
        unit = units[members]["unit"]
        unit_id = f"unit_{n:03d}"
        anchors = "; ".join(
            f"{d.t:.1f}s" for d in sorted(unit.members, key=lambda d: d.t)
        )
        sheet_w.writerow(
            (unit_id, unit.lead.match, anchors, _evidence_text(unit), *[""] * 6)
        )
        key_w.writerow(
            (
                unit_id,
                unit.lead.match,
                ";".join(d.cid for d in unit.members),
                unit.lead.cid,
                ";".join(units[members]["policies"]),
            )
        )
    return sheet.getvalue(), key.getvalue()


# --------------------------------------------------------------------------- CLI


def main() -> None:
    """CLI entry point: report, rating sheet, key and deferred inputs."""
    from coaching_units_sections import build_report

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root", type=Path, default=PROJECT_ROOT / "analysis/_rescore_v2"
    )
    parser.add_argument(
        "--stability-roots",
        nargs="*",
        default=[
            "analysis/_rescore_v2_1hz",
            "analysis/_rescore_1hz_even",
            "analysis/_rescore_1hz_odd",
        ],
    )
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--out-dir", type=Path, default=REPORT_DIR)
    args = parser.parse_args()
    config = load_config(args.config)
    coach = config.coach
    gap = coach.death_factor_thresholds.redeath_max_gap_seconds
    modifiers = frozenset(coach.death_modifier_factors)
    top = coach.max_llm_units
    matches = load_root(args.root.resolve(), config)
    roots = {args.root.name: matches}
    for extra in args.stability_roots:
        path = (PROJECT_ROOT / extra).resolve()
        if path.is_dir():
            roots[path.name] = load_root(path, config)
    system_prompt = load_system_prompt(roster_evidence=coach.roster_evidence)
    deferred = deferred_inputs(matches, gap, modifiers, system_prompt)
    sheet, key = rating_sheet(matches, gap, top, modifiers)
    out = args.out_dir
    write_files(out / "coaching_units_deferred", deferred)
    (out / "coaching_units_rating_sheet.csv").write_text(sheet, encoding="utf-8")
    (out / "coaching_units_rating_key.csv").write_text(key, encoding="utf-8")
    report = build_report(matches, roots, config, deferred, sheet)
    (out / "coaching_units.md").write_text(report, encoding="utf-8")
    logger.info("Wrote {}", out / "coaching_units.md")


if __name__ == "__main__":
    main()
