#!/usr/bin/env python3
"""Re-score every analyzed match in the library and compare with its last ranking.

For each death-importance factor: how often it is active and how often it
lands in the LLM top N, under the current config and under the ranking
already on disk. For each match: which deaths entered or left the top N.

With ``--baseline-root``, the "before" side is instead the current config
scored against the same relative analysis under that root, which isolates
the effect of changed evidence (e.g. a re-analysis with ``player_count``).

Roster coverage is reported per match (in-match snapshots carrying counts)
and per death: a pre-death sample backed by a nearby observation, a sample
held across an observation gap (stale), or no sample.

Read-only against analyses: nothing under an analysis directory is written.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from splatoon3_ai_coach.analysis.pipeline import (
    SCENARIO_CONTEXTS_JSON_FILENAME,
    SCENARIOS_JSON_FILENAME,
)
from splatoon3_ai_coach.analysis.player_count_series import format_avb
from splatoon3_ai_coach.cli.coach_common import (
    COACH_INPUTS_DIRNAME,
    COACH_INPUTS_META_FILENAME,
    COACHING_INDEX_FILENAME,
)
from splatoon3_ai_coach.coach.claim_catalog import (
    CoachingUnitResult,
    DeathImportanceFactorId,
    RosterSample,
)
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.coaching_candidates import active_factor_ids
from splatoon3_ai_coach.coach.load_analysis import (
    CoachAnalysisBundle,
    load_coach_analysis_bundle,
)
from splatoon3_ai_coach.coach.match_scoring import MatchScoring, score_match
from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT, default_config_path

MANIFEST_NAME = "vision_manifest.json"
UNRECORDED = "unrecorded"
_BUILD_LIMIT = 50

ROSTER_SAMPLE = "sample"
ROSTER_STALE = "stale"
ROSTER_MISSING = "missing"
ROSTER_STATUSES = (ROSTER_SAMPLE, ROSTER_STALE, ROSTER_MISSING)


@dataclass(frozen=True)
class RankedDeath:
    """One death's ranking outcome on one side of the comparison."""

    candidate_id: str
    score: float
    selected: bool
    factors: tuple[str, ...]
    roster: str | None = None
    roster_status: str = ROSTER_MISSING
    roster_gap_seconds: float | None = None


@dataclass(frozen=True)
class SnapshotCoverage:
    """In-match fused snapshots and how many carry both roster counts."""

    in_match: int = 0
    with_counts: int = 0


@dataclass
class MatchComparison:
    """Old (on disk) vs new (current config) ranking for one match."""

    name: str
    old_hash: str
    new_hash: str
    old: dict[str, RankedDeath]
    new: dict[str, RankedDeath]
    snapshots: SnapshotCoverage = field(default_factory=SnapshotCoverage)

    def entered(self) -> list[str]:
        """Deaths selected for the LLM now but not before."""
        return sorted(
            cid
            for cid, d in self.new.items()
            if d.selected and not (cid in self.old and self.old[cid].selected)
        )

    def left(self) -> list[str]:
        """Deaths selected before but not now."""
        return sorted(
            cid
            for cid, d in self.old.items()
            if d.selected and not (cid in self.new and self.new[cid].selected)
        )


@dataclass
class FactorTally:
    """Active / top-N counts per factor across the library."""

    active: Counter[str] = field(default_factory=Counter)
    top_n: Counter[str] = field(default_factory=Counter)
    deaths: int = 0
    with_roster: int = 0

    def add(self, deaths: dict[str, RankedDeath]) -> None:
        """Count every death on one side of a comparison."""
        for death in deaths.values():
            self.deaths += 1
            self.with_roster += death.roster is not None
            for factor in death.factors:
                self.active[factor] += 1
                if death.selected:
                    self.top_n[factor] += 1


def find_analyses(root: Path, *, exclude: tuple[Path, ...] = ()) -> list[Path]:
    """Directories under ``root`` holding scenarios, contexts and a manifest.

    Anything inside an ``exclude`` directory is skipped.
    """
    skipped = tuple(path.resolve() for path in exclude)
    found = set()
    for path in root.rglob(SCENARIOS_JSON_FILENAME):
        folder = path.parent.resolve()
        if any(folder.is_relative_to(skip) for skip in skipped):
            continue
        if (folder / SCENARIO_CONTEXTS_JSON_FILENAME).is_file() and (
            folder / MANIFEST_NAME
        ).is_file():
            found.add(folder)
    return sorted(found)


def snapshot_coverage(analysis: Path) -> SnapshotCoverage:
    """Count in-match fused snapshots and those with both alive counts."""
    manifest = json.loads((analysis / MANIFEST_NAME).read_text(encoding="utf-8"))
    in_match = [
        snap
        for snap in manifest.get("state_snapshots") or ()
        if snap.get("match_phase") == "in_match"
    ]
    with_counts = sum(
        snap.get("ally_alive_count") is not None
        and snap.get("opponent_alive_count") is not None
        for snap in in_match
    )
    return SnapshotCoverage(in_match=len(in_match), with_counts=with_counts)


def read_old_ranking(analysis: Path) -> tuple[str, dict[str, RankedDeath]]:
    """The ranking already on disk, and the config hash it recorded."""
    inputs = analysis / COACH_INPUTS_DIRNAME
    index_path = inputs / COACHING_INDEX_FILENAME
    if not index_path.is_file():
        return UNRECORDED, {}
    rows = json.loads(index_path.read_text(encoding="utf-8"))
    deaths = {
        str(row["candidate_id"]): RankedDeath(
            candidate_id=str(row["candidate_id"]),
            score=float(row.get("importance_score") or 0.0),
            selected=bool(row.get("selected_for_llm")),
            factors=tuple(row.get("active_factors") or ()),
        )
        for row in rows
        if isinstance(row, dict) and row.get("candidate_id")
    }
    meta_path = inputs / COACH_INPUTS_META_FILENAME
    meta = (
        json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    )
    return str(meta.get("importance_config_hash") or UNRECORDED), deaths


def new_ranking(
    scoring: MatchScoring,
    bundle: CoachAnalysisBundle | None = None,
    app_config: AppConfig | None = None,
) -> dict[str, RankedDeath]:
    """Project a fresh :class:`MatchScoring` onto comparable rows.

    With ``bundle`` and ``app_config``, each death also gets a roster status.
    """
    rows = {}
    for unit in scoring.units:
        status, gap = ROSTER_MISSING, None
        if bundle is not None and app_config is not None:
            death_time = _death_time(scoring.coach_inputs.get(unit.candidate_id))
            status, gap = roster_status(unit, death_time, bundle, app_config)
        elif unit.roster_before_death is not None:
            status = ROSTER_SAMPLE
        rows[unit.candidate_id] = RankedDeath(
            candidate_id=unit.candidate_id,
            score=float(unit.importance_score),
            selected=bool(unit.selected_for_llm),
            factors=tuple(active_factor_ids(unit.to_candidate())),
            roster=_roster_label(unit.roster_before_death),
            roster_status=status,
            roster_gap_seconds=gap,
        )
    return rows


def roster_status(
    unit: CoachingUnitResult,
    death_time: float | None,
    bundle: CoachAnalysisBundle,
    app_config: AppConfig,
) -> tuple[str, float | None]:
    """Classify a death's pre-death roster evidence.

    The trajectory is compressed to count changes, so a sample can be held
    across a stretch where the HUD was not read. ``stale`` means the last
    raw observation at or before the cutoff is further back than
    ``player_count_max_lookup_gap_seconds``; the gap is returned in seconds.
    """
    sample = unit.roster_before_death
    if sample is None:
        return ROSTER_MISSING, None
    coach = app_config.coach
    if death_time is None:
        return ROSTER_SAMPLE, None
    cutoff = death_time - coach.death_factor_thresholds.roster_pre_death_offset_seconds
    before = [
        obs.video_time
        for obs in bundle.player_count_clock.observations
        if obs.video_time <= cutoff
    ]
    gap = cutoff - max(before) if before else None
    if gap is None or gap > coach.player_count_max_lookup_gap_seconds:
        return ROSTER_STALE, gap
    return ROSTER_SAMPLE, gap


def _death_time(coach_input: CoachInput | None) -> float | None:
    if coach_input is None:
        return None
    death = coach_input.primary_context.death_episode
    if death is None or death.death_time is None:
        return None
    return float(death.death_time)


def _roster_label(sample: RosterSample | None) -> str | None:
    if sample is None:
        return None
    return format_avb(sample.ally_alive_count, sample.opponent_alive_count)


def compare_match(
    analysis: Path,
    app_config: AppConfig,
    root: Path,
    *,
    baseline_root: Path | None = None,
) -> MatchComparison:
    """Re-score one analysis and pair it with the ranking on disk.

    With ``baseline_root``, the "before" side is the current config scored
    against the same relative analysis under that root (empty if absent).
    """
    bundle, scoring = _score(analysis, app_config)
    name = (
        str(analysis.relative_to(root))
        if analysis.is_relative_to(root)
        else str(analysis)
    )
    if baseline_root is None:
        old_hash, old = read_old_ranking(analysis)
    else:
        old_hash, old = _baseline_ranking(baseline_root / name, app_config)
    return MatchComparison(
        name=name,
        old_hash=old_hash,
        new_hash=scoring.importance_config_hash,
        old=old,
        new=new_ranking(scoring, bundle, app_config),
        snapshots=snapshot_coverage(analysis),
    )


def _score(
    analysis: Path, app_config: AppConfig
) -> tuple[CoachAnalysisBundle, MatchScoring]:
    bundle = load_coach_analysis_bundle(
        analysis, min_usable_confidence=app_config.vision.timer.min_usable_confidence
    )
    return bundle, score_match(bundle, app_config, limit=_BUILD_LIMIT)


def _baseline_ranking(
    baseline: Path, app_config: AppConfig
) -> tuple[str, dict[str, RankedDeath]]:
    if not (baseline / SCENARIOS_JSON_FILENAME).is_file():
        return UNRECORDED, {}
    bundle, scoring = _score(baseline, app_config)
    return scoring.importance_config_hash, new_ranking(scoring, bundle, app_config)


def coverage_section(matches: list[MatchComparison], app_config: AppConfig) -> list[str]:
    """Markdown: roster evidence coverage by match and by death, current side."""
    gap_limit = app_config.coach.player_count_max_lookup_gap_seconds
    totals: Counter[str] = Counter()
    for match in matches:
        totals.update(d.roster_status for d in match.new.values())
    usable = [m for m in matches if m.snapshots.with_counts]
    lines = [
        f"Matches with roster counts on in-match snapshots: {len(usable)} of "
        f"{len(matches)}.",
        f"Deaths: {sum(totals.values())}. With a pre-death sample: "
        f"{totals[ROSTER_SAMPLE]}. Stale (last observation more than "
        f"{gap_limit:g}s before the cutoff): {totals[ROSTER_STALE]}. "
        f"No sample: {totals[ROSTER_MISSING]}.",
        *freshness_lines(matches, app_config),
        "",
        "| match | in-match snapshots with counts | deaths | sample | stale | missing |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    lines.extend(_coverage_row(match) for match in matches)
    return lines


def freshness_lines(matches: list[MatchComparison], app_config: AppConfig) -> list[str]:
    """Markdown: how old the last raw roster reading is at each death's cutoff."""
    gaps = sorted(
        d.roster_gap_seconds
        for m in matches
        for d in m.new.values()
        if d.roster_gap_seconds is not None
    )
    if not gaps:
        return []
    offset = app_config.coach.death_factor_thresholds.roster_pre_death_offset_seconds
    within = sum(gap <= 1.0 for gap in gaps)
    return [
        f"Last raw reading before the cutoff ({offset:g}s before death), over "
        f"{len(gaps)} deaths: median {_quantile(gaps, 0.5):.2f}s, "
        f"p90 {_quantile(gaps, 0.9):.2f}s, max {gaps[-1]:.2f}s; within 1s of the "
        f"cutoff: {within} ({100 * within / len(gaps):.0f}%).",
    ]


def _quantile(values: list[float], q: float) -> float:
    """Nearest-rank quantile of an already sorted list."""
    index = min(len(values) - 1, max(0, round(q * (len(values) - 1))))
    return values[index]


def _coverage_row(match: MatchComparison) -> str:
    snaps = match.snapshots
    share = f"{snaps.with_counts}/{snaps.in_match}"
    if snaps.in_match:
        share += f" ({100 * snaps.with_counts / snaps.in_match:.0f}%)"
    counts = Counter(d.roster_status for d in match.new.values())
    cells = " | ".join(str(counts[status]) for status in ROSTER_STATUSES)
    return f"| {match.name} | {share} | {len(match.new)} | {cells} |"


def factor_table(old: FactorTally, new: FactorTally) -> list[str]:
    """Markdown rows: factor frequency and top-N share, old vs new."""
    lines = [
        f"Deaths scored: {new.deaths} now, {old.deaths} before.",
        f"Deaths with a pre-death roster sample: {new.with_roster} of {new.deaths}.",
        "",
        "| factor | active now | in top N now | active before | in top N before |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    names = [factor.value for factor in DeathImportanceFactorId]
    names += sorted(set(old.active) - set(names))
    for name in names:
        lines.append(
            f"| `{name}` | {new.active[name]} | {new.top_n[name]} | "
            f"{old.active[name]} | {old.top_n[name]} |"
        )
    return lines


def match_section(match: MatchComparison) -> list[str]:
    """Markdown for one match: config hashes and top-N movement."""
    selected = sum(d.selected for d in match.new.values())
    lines = [
        f"### {match.name}",
        "",
        f"Config hash: before `{match.old_hash}`, now `{match.new_hash}`. "
        f"{len(match.new)} deaths, {selected} in top N.",
    ]
    if selected == len(match.new):
        lines.append("Every death is in the top N, so ranking has no effect here.")
    if not match.old:
        lines.append("No ranking on disk. Top N now:")
        lines.extend(
            _death_line("Top N", match.new[cid])
            for cid in sorted(match.new)
            if match.new[cid].selected
        )
    else:
        lines.extend(_death_line("Entered top N", match.new[c]) for c in match.entered())
        lines.extend(_death_line("Left top N", match.old[c]) for c in match.left())
        if not match.entered() and not match.left():
            lines.append("- Top N unchanged.")
    lines.append("")
    return lines


def _death_line(label: str, death: RankedDeath) -> str:
    factors = ", ".join(death.factors) or "none"
    roster = f"; roster before death {death.roster}" if death.roster else ""
    if death.roster_status == ROSTER_STALE:
        gap = death.roster_gap_seconds
        roster += f" (stale, {gap:.1f}s gap)" if gap is not None else " (stale)"
    return (
        f"- {label}: `{death.candidate_id}` (score {death.score:.1f}; {factors}{roster})"
    )


def build_report(
    matches: list[MatchComparison],
    new_hash: str,
    app_config: AppConfig,
    *,
    baseline_root: Path | None = None,
) -> str:
    """Whole report as markdown."""
    old, new = FactorTally(), FactorTally()
    for match in matches:
        old.add(match.old)
        new.add(match.new)
    old_hashes = sorted({m.old_hash for m in matches})
    before = (
        f"Before = current config on the analyses under `{baseline_root}`."
        if baseline_root is not None
        else "Before = the rankings on disk."
    )
    lines = [
        "# Importance re-scoring report",
        "",
        f"Matches: {len(matches)}. Current config hash: `{new_hash}`. "
        f"Before hashes: {', '.join(f'`{h}`' for h in old_hashes) or 'none'}. "
        f"{before}",
        "",
        "## Roster coverage",
        "",
        *coverage_section(matches, app_config),
        "",
        "## Factors",
        "",
        *factor_table(old, new),
        "",
        "## Top N movement by match",
        "",
    ]
    for match in matches:
        lines.extend(match_section(match))
    return "\n".join(lines)


def run(
    root: Path,
    config_path: Path,
    *,
    baseline_root: Path | None = None,
    exclude: tuple[Path, ...] = (),
) -> tuple[str, list[MatchComparison], str]:
    """Score every analysis under ``root``; return the report and its parts."""
    app_config = load_config(config_path)
    matches: list[MatchComparison] = []
    for analysis in find_analyses(root, exclude=exclude):
        try:
            matches.append(
                compare_match(analysis, app_config, root, baseline_root=baseline_root)
            )
        except (FileNotFoundError, ValueError, KeyError) as exc:
            logger.warning("Skipping {}: {}", analysis, exc)
    new_hash = matches[0].new_hash if matches else UNRECORDED
    report = build_report(matches, new_hash, app_config, baseline_root=baseline_root)
    return report, matches, new_hash


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT / "analysis")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument(
        "--out",
        type=Path,
        default=PROJECT_ROOT / "analysis" / "importance_report",
        help="Directory for the markdown report (named by config hash).",
    )
    parser.add_argument(
        "--baseline-root",
        type=Path,
        default=None,
        help="Compare against the same relative analyses under this root.",
    )
    parser.add_argument(
        "--exclude",
        type=Path,
        action="append",
        default=[],
        help="Skip analyses inside this directory (repeatable).",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    baseline = args.baseline_root.resolve() if args.baseline_root else None
    report, matches, new_hash = run(
        root, args.config, baseline_root=baseline, exclude=tuple(args.exclude)
    )
    args.out.mkdir(parents=True, exist_ok=True)
    suffix = "-vs-baseline" if baseline is not None else ""
    path = args.out / f"report-{new_hash}{suffix}.md"
    path.write_text(report + "\n", encoding="utf-8")
    logger.info("Scored {} matches; wrote {}", len(matches), path)


if __name__ == "__main__":
    main()
