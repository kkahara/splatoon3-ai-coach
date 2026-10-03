"""Selection unit analysis: deaths versus coaching episodes.

Describes what the top-N selection would become if nearby deaths were grouped
into episodes before selection; changes nothing in production. An episode is a
run of consecutive deaths each within the re-death gap of the previous one (a
lone death is its own episode). Linking is transitive and unbounded in total
span: death C joins because it is close to B, even if C is far from A. Scores
and factors come from production scoring. Episode membership is not evidence
membership — a non-scoring death inside or starting an episode never becomes
part of a grouped candidate's evidence.

Policies compared:

- ``deaths``: production — every death competes on its own.
- ``episode``: one candidate per episode, scored by its best member.
- ``episode_by_factor``: within an episode, members sharing the same primary
  (non-modifier) factor set merge into one candidate; members with different
  primary factors stay separate.

Analyses of the same source video (same ``video_identity``) count once.

    python tools/reports/ranking_episodes.py --root analysis/_rescore_v2
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from importance_report import PROJECT_ROOT
from loguru import logger
from ranking_competition import Death, Match, load_matches

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig

SHORT = {
    "death_redeath_le_10s": "re-death",
    "death_while_outnumbered": "outnumbered",
    "death_while_ahead_in_numbers": "ahead",
    "death_final_30s": "final30",
    "death_first_30s": "first30",
}
POLICIES = ("deaths", "episode", "episode_by_factor")


@dataclass
class Candidate:
    """One selectable unit: one or more deaths presented together."""

    members: list[Death]

    @property
    def lead(self) -> Death:
        """Best-scoring member (earliest on ties), which sets the rank."""
        return min(self.members, key=lambda d: (-d.score, d.t, d.cid))

    @property
    def score(self) -> float:
        """Score of the lead member."""
        return self.lead.score

    def label(self, modifiers: frozenset[str]) -> str:
        """Members with their factors, e.g. ``285s re-death + 298s re-death``."""
        return " + ".join(
            f"{d.t:.0f}s {_short(d.factors, modifiers)}"
            for d in sorted(self.members, key=lambda d: d.t)
        )


def _short(factors: frozenset[str], modifiers: frozenset[str]) -> str:
    primary = sorted(SHORT.get(f, f) for f in factors if f not in modifiers)
    extra = sorted(SHORT.get(f, f) for f in factors if f in modifiers)
    return "/".join(primary) + (f" (+{'/'.join(extra)})" if extra else "") or "–"


def primary(death: Death, modifiers: frozenset[str]) -> frozenset[str]:
    """Scoring factors that are not modifiers."""
    return frozenset(f for f in death.factors if f not in modifiers)


def episodes(match: Match, gap: float) -> list[list[Death]]:
    """Consecutive deaths each within ``gap`` seconds of the previous one.

    Chaining is transitive and has no maximum span: death C joins the episode
    because it is within ``gap`` of B, even if C is more than ``gap`` after A.
    A run of closely spaced deaths can therefore cover much more than ``gap``
    seconds in total (e.g. Tenta Missiles: 41s across four linked deaths).
    """
    groups: list[list[Death]] = []
    for death in sorted(match.deaths, key=lambda d: d.t):
        if groups and death.t - groups[-1][-1].t <= gap:
            groups[-1].append(death)
        else:
            groups.append([death])
    return groups


def candidates(
    match: Match, policy: str, gap: float, modifiers: frozenset[str]
) -> list[Candidate]:
    """Selectable units of one match under ``policy``.

    Episode membership is not evidence membership: a zero-score death that
    starts or sits inside an episode never joins a grouped candidate. Grouping
    only decides which scoring deaths are presented together; it never turns a
    non-scoring death into evidence.
    """
    if policy == "deaths":
        return [Candidate([d]) for d in match.deaths]
    out: list[Candidate] = []
    for group in episodes(match, gap):
        scoring = [d for d in group if d.score > 0]
        if not scoring:
            out.extend(Candidate([d]) for d in group)
        elif policy == "episode":
            out.append(Candidate(scoring))
        else:
            by_factor: dict[frozenset[str], list[Death]] = {}
            for death in scoring:
                by_factor.setdefault(primary(death, modifiers), []).append(death)
            out.extend(Candidate(members) for members in by_factor.values())
    return out


def select(pool: list[Candidate], top: int) -> list[Candidate]:
    """Production selection rule: top N among positive-score candidates."""
    ranked = sorted(pool, key=lambda c: (-c.score, c.lead.t, c.lead.cid))
    return [c for c in ranked if c.score > 0][:top]


def summary_section(
    matches: list[Match], picks: dict[str, dict[str, list[Candidate]]]
) -> list[str]:
    """Units, deaths covered and matches covered per policy."""
    lines = [
        "## 1. Summary",
        "",
        "| policy | units | deaths inside units | matches with a unit "
        "| units per match |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for policy in POLICIES:
        chosen = picks[policy]
        dist = Counter(len(chosen[m.name]) for m in matches)
        lines.append(
            f"| `{policy}` | {sum(len(v) for v in chosen.values())} | "
            f"{sum(len(c.members) for v in chosen.values() for c in v)} | "
            f"{sum(1 for v in chosen.values() if v)} | "
            + "; ".join(f"{n}: {k}" for n, k in sorted(dist.items()))
            + " |"
        )
    lines.append("")
    return lines


def episode_section(
    matches: list[Match], gap: float, modifiers: frozenset[str]
) -> list[str]:
    """Every episode with two or more scoring deaths, and its factor diversity."""
    lines = [
        "## 2. Episodes with more than one scoring death",
        "",
        f"Episode = consecutive deaths each within {gap:g}s of the previous one. "
        "Primary factors exclude the clock modifiers.",
        "",
        "| match | deaths in episode (time: factors = score) | distinct primary "
        "factor sets | selected today |",
        "| --- | --- | ---: | ---: |",
    ]
    for match in matches:
        for group in episodes(match, gap):
            scoring = [d for d in group if d.score > 0]
            if len(scoring) < 2:
                continue
            members = "; ".join(
                f"{d.t:.0f}s: {_short(d.factors, modifiers)} = {d.score:g}" for d in group
            )
            distinct = len({primary(d, modifiers) for d in scoring})
            lines.append(
                f"| {match.name[:36]} | {members} | {distinct} | "
                f"{sum(d.selected for d in group)} |"
            )
    lines.append("")
    return lines


def diff_section(
    matches: list[Match],
    picks: dict[str, dict[str, list[Candidate]]],
    modifiers: frozenset[str],
) -> list[str]:
    """Matches where the policies select differently."""
    lines = ["## 3. Matches where the policies differ", ""]
    for match in matches:
        views = {
            policy: [c.label(modifiers) for c in picks[policy][match.name]]
            for policy in POLICIES
        }
        if len({tuple(v) for v in views.values()}) == 1:
            continue
        lines.append(f"**{match.name}**")
        lines.append("")
        for policy in POLICIES:
            units = views[policy]
            shown = "; ".join(f"[{u}]" for u in units) if units else "none"
            lines.append(f"- `{policy}` ({len(units)}): {shown}")
        lines.append("")
    return lines


def build(matches: list[Match], config: AppConfig) -> str:
    """Whole report."""
    coach = config.coach
    gap = coach.death_factor_thresholds.redeath_max_gap_seconds
    modifiers = frozenset(coach.death_modifier_factors)
    picks = {
        policy: {
            m.name: select(candidates(m, policy, gap, modifiers), coach.max_llm_units)
            for m in matches
        }
        for policy in POLICIES
    }
    header = [
        "# Selection unit: deaths versus episodes",
        "",
        "Describes alternatives only; production selects deaths. Scores are the "
        f"production scores; the top {coach.max_llm_units} positive-score candidates "
        "are selected under every policy. A grouped candidate is ranked by its best "
        "member and lists every scoring death it contains.",
        "",
        "> **Research conclusion (not yet implemented).** Intended future selection "
        "policy: one coaching unit per 20-second-linked death episode, ranked by "
        "its best-scoring death, with every scoring death in that episode retained "
        "as an observation inside the unit. Blocked on redesigning the 1:1 chain "
        "unit = scenario = CoachInput = LLM prompt = VMV block = public-site moment "
        "(`coach/llm_view.py`, `coach/vmv.py`, `tools/public_site/results.py`) to "
        "present multiple death episodes as one unit. Do not implement until that "
        "presentation design is decided separately.",
        "",
    ]
    return "\n".join(
        header
        + summary_section(matches, picks)
        + episode_section(matches, gap, modifiers)
        + diff_section(matches, picks, modifiers)
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
        default=PROJECT_ROOT / "analysis" / "importance_report" / "episodes.md",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    matches = load_matches(args.root.resolve(), config)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(matches, config) + "\n", encoding="utf-8")
    logger.info("Wrote {}", args.out)


if __name__ == "__main__":
    main()
