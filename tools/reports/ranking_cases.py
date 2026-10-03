"""Case analysis of the death-importance factors, one death at a time.

Describes what the current ranking does; proposes nothing. For every death that
carries a re-death, outnumbered, no-map-check or special-ready factor, lists
the evidence around the death (roster, splats, map, special gauge, clock),
not just the score. Re-deaths are grouped into chains.

Analyses of the same source video (same ``video_identity``) count once.

    python tools/reports/ranking_cases.py --root analysis/_rescore_v2
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from importance_report import MANIFEST_NAME, PROJECT_ROOT, _score, find_analyses
from loguru import logger
from ranking_competition import _hidden_roster_times

from splatoon3_ai_coach.analysis.scenario_models import ScenarioType
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.coaching_candidates import active_factor_ids
from splatoon3_ai_coach.coach.death_importance import pre_death_roster
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig

REDEATH = "death_redeath_le_10s"
OUTNUMBERED = "death_while_outnumbered"
AHEAD = "death_while_ahead_in_numbers"
MAP_FALSE = "death_map_overlay_before_false"
SPECIAL = "death_special_ready"
SHORT = {
    REDEATH: "re-death",
    OUTNUMBERED: "outnumbered",
    AHEAD: "ahead",
    MAP_FALSE: "no-map",
    SPECIAL: "special",
    "death_final_30s": "final30",
    "death_first_30s": "first30",
}
LOOKBACK = 20.0


@dataclass
class Timeline:
    """Per-match raw and fused series the cases are read from."""

    roster: list[tuple[float, int | None, int | None, str]]
    hidden_roster: list[float]
    map_present: list[float]
    special: list[tuple[float, bool, float | None, bool]]
    engagements: list[tuple[float, float, int]]
    match_start: float | None


@dataclass
class Case:
    """One death with its score and the evidence around it."""

    match: str
    cid: str
    t: float
    factors: frozenset[str]
    score: float
    rank: int
    selected: bool
    since_prev_death: float | None
    alive_for: float | None
    lines: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """Factor set in short names."""
        return " + ".join(SHORT.get(f, f) for f in sorted(self.factors)) or "–"

    @property
    def status(self) -> str:
        """Rank and selection."""
        return f"rank {self.rank}" + (" **selected**" if self.selected else "")


def load_timeline(manifest: dict, contexts: list, scenarios: list) -> Timeline:
    """Series used by every case in one match."""
    roster = [
        (
            float(s["timestamp"]),
            s.get("ally_alive_count"),
            s.get("opponent_alive_count"),
            str(s.get("player_lifecycle")),
        )
        for s in manifest.get("state_snapshots") or ()
        if s.get("match_phase") == "in_match"
    ]
    map_present, special = [], []
    for frame in manifest.get("frame_results") or ():
        t = float(frame["timestamp"])
        for det in frame["detections"]:
            reading = det["reading"]
            if det["detector_name"] == "map_overlay" and reading.get("present"):
                map_present.append(t)
            if det["detector_name"] == "special_gauge":
                special.append(
                    (
                        t,
                        bool(reading.get("visible")),
                        reading.get("fill_fraction"),
                        bool(reading.get("ready")),
                    )
                )
    kinds = {s.scenario_id: s.scenario_type for s in scenarios}
    engagements = [
        (
            float(c.combat.first_splat_time),
            float(c.combat.last_splat_time),
            int(c.combat.splat_count),
        )
        for c in contexts
        if kinds.get(c.scenario_id) is ScenarioType.ENGAGEMENT
        and c.combat is not None
        and c.combat.first_splat_time is not None
    ]
    return Timeline(
        roster=roster,
        hidden_roster=_hidden_roster_times(manifest),
        map_present=map_present,
        special=special,
        engagements=sorted(engagements),
        match_start=roster[0][0] if roster else None,
    )


def load_cases(root: Path, config: AppConfig) -> tuple[list[Case], dict[str, float]]:
    """Every death in every unique analysis, with evidence lines."""
    seen: set[str] = set()
    cases: list[Case] = []
    limits = config.coach.death_factor_thresholds
    for folder in find_analyses(root):
        manifest = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
        identity = manifest["analysis"]["video_identity"]
        if identity in seen:
            continue
        seen.add(identity)
        bundle, scoring = _score(folder, config)
        timeline = load_timeline(manifest, bundle.contexts, bundle.scenarios)
        name = str(folder.relative_to(root))
        units = sorted(scoring.units, key=lambda u: float(u.video_time))
        prev_active: float | None = timeline.match_start
        for unit in units:
            ci = scoring.coach_inputs[unit.candidate_id]
            episode = ci.primary_context.death_episode
            t = float(unit.video_time)
            timeline_ctx = ci.primary_context.timeline
            case = Case(
                match=name,
                cid=unit.candidate_id,
                t=t,
                factors=frozenset(active_factor_ids(unit.to_candidate())),
                score=float(unit.importance_score),
                rank=int(unit.rank or 0),
                selected=bool(unit.selected_for_llm),
                since_prev_death=(
                    float(timeline_ctx.time_since_previous_death)
                    if timeline_ctx and timeline_ctx.time_since_previous_death
                    else None
                ),
                alive_for=t - prev_active if prev_active is not None else None,
            )
            case.lines = evidence_lines(ci, timeline, t, prev_active, limits)
            cases.append(case)
            if episode is not None and episode.active_again_time is not None:
                prev_active = float(episode.active_again_time)
    return cases, dict(config.coach.death_importance_weights)


def evidence_lines(
    ci: CoachInput,
    timeline: Timeline,
    t: float,
    life_start: float | None,
    limits,
) -> dict[str, str]:
    """Readable evidence around one death."""
    return {
        "clock": _clock_line(ci),
        "roster": _roster_line(ci, timeline, t, limits.roster_pre_death_offset_seconds),
        "splats": _splat_line(timeline, t, life_start),
        "map": _map_line(ci, timeline, t, life_start),
        "special": _special_line(timeline, t),
        "low_ink": _low_ink_line(ci),
    }


def _clock_line(ci: CoachInput) -> str:
    sample = next((s for s in ci.game_clock_samples if s.label == "death"), None)
    if sample is None or sample.observation is None:
        return "no game-clock reading near the death"
    rem = int(sample.observation.seconds_remaining)
    gap = float(sample.gap_seconds or 0)
    return (
        f"{rem // 60}:{rem % 60:02d} remaining (reading {gap:.1f}s "
        f"from the death, conf {float(sample.observation.confidence):.2f})"
    )


def _roster_line(ci: CoachInput, timeline: Timeline, t: float, offset: float) -> str:
    used = pre_death_roster(ci, offset_seconds=offset)
    head = (
        f"factor sample {used.ally_alive_count}v{used.opponent_alive_count} at "
        f"{used.video_time - t:+.1f}s"
        if used
        else "no roster sample for the factor"
    )
    trace, last = [], None
    for ts, ally, opp, life in timeline.roster:
        if ts < t - LOOKBACK or ts > t + 0.5:
            continue
        state = f"{ally}v{opp}" if ally is not None and opp is not None else "?"
        if life != "alive":
            state += f" ({life})"
        if state != last:
            trace.append(f"{ts - t:+.1f}s {state}")
            last = state
    return f"{head}; fused trace (ally v opp): " + (" → ".join(trace) or "none")


def _splat_line(timeline: Timeline, t: float, life_start: float | None) -> str:
    start = max(t - LOOKBACK, life_start or 0.0)
    near = [e for e in timeline.engagements if start <= e[1] <= t]
    if not near:
        return f"no splats by the player in this life's last {t - start:.0f}s"
    return "; ".join(
        f"{n} splat(s) {first - t:+.1f}s…{last - t:+.1f}s" for first, last, n in near
    )


def _map_line(
    ci: CoachInput, timeline: Timeline, t: float, life_start: float | None
) -> str:
    ctx = ci.primary_context.map
    frames_before = sum(1 for x in timeline.map_present if x < t)
    life_hidden = [x for x in timeline.hidden_roster if (life_start or 0.0) <= x < t]
    base = (
        f"map_check_before_death={ctx.map_check_before_death}, "
        f"{ctx.map_check_count} overlay event(s) in context"
        if ctx
        else "no map context"
    )
    if ctx and ctx.seconds_since_map_check_before_death is not None:
        base += f", last {float(ctx.seconds_since_map_check_before_death):.1f}s before"
    base += (
        f"; raw map-overlay frames before death in match: {frames_before} "
        f"(whole match: {len(timeline.map_present)})"
    )
    if life_hidden:
        base += (
            f"; roster hidden while alive this life: {len(life_hidden)} frame(s), "
            f"latest {life_hidden[-1] - t:+.1f}s"
        )
    return base


def _special_line(timeline: Timeline, t: float) -> str:
    window = [r for r in timeline.special if t - LOOKBACK <= r[0] <= t]
    if not window:
        return "no special-gauge readings in the window"
    runs: list[list] = []
    for ts, visible, fill, ready in window:
        kind = "READY" if ready else ("gauge" if visible else "hidden")
        if runs and runs[-1][0] == kind:
            runs[-1][2] = ts
            if fill is not None:
                runs[-1][3].append(float(fill))
            runs[-1][4] += 1
        else:
            runs.append([kind, ts, ts, [float(fill)] if fill is not None else [], 1])
    parts = []
    for kind, a, b, fills, n in runs:
        span = f"{a - t:+.1f}…{b - t:+.1f}s"
        fill = (
            f" fill {min(fills):.2f}–{max(fills):.2f}"
            if fills and kind == "gauge"
            else ""
        )
        parts.append(f"{span} {kind} ×{n}{fill}")
    return " → ".join(parts)


def _low_ink_line(ci: CoachInput) -> str:
    low = ci.primary_context.low_ink
    if low is None or not low.intervals:
        return "none overlapping"
    return f"{len(low.intervals)} LOW_INK interval(s) overlapping the episode"


def case_block(case: Case, keys: tuple[str, ...]) -> list[str]:
    """Markdown bullet block for one death."""
    alive = f"{case.alive_for:.1f}s" if case.alive_for is not None else "?"
    gap = (
        f", {case.since_prev_death:.1f}s after previous death"
        if case.since_prev_death is not None
        else ""
    )
    lines = [
        f"- **{case.match[:44]}** {case.t:.0f}s — {case.label} = {case.score:g}, "
        f"{case.status}; in control {alive} before dying{gap}"
    ]
    lines += [f"  - {key}: {case.lines[key]}" for key in keys]
    return lines


def chains(cases: list[Case], gap: float) -> list[list[Case]]:
    """Consecutive deaths within ``gap`` of the previous one, per match."""
    out: list[list[Case]] = []
    by_match: dict[str, list[Case]] = {}
    for case in cases:
        by_match.setdefault(case.match, []).append(case)
    for deaths in by_match.values():
        current: list[Case] = []
        for case in sorted(deaths, key=lambda c: c.t):
            if (
                current
                and case.since_prev_death is not None
                and case.since_prev_death <= gap
            ):
                current.append(case)
                continue
            if len(current) > 1:
                out.append(current)
            current = [case]
        if len(current) > 1:
            out.append(current)
    return out


def redeath_section(cases: list[Case], gap: float) -> list[str]:
    """Re-death chains and how many slots each takes."""
    groups = chains(cases, gap)
    lines = [
        "## 1. Re-death chains",
        "",
        f"A chain is consecutive deaths each within {gap:g}s of the previous one. The "
        'first death of a chain has no re-death factor itself. "In control" is time '
        "from the previous ACTIVE_AGAIN (or match start) to this death.",
        "",
        "| match | chain (time: factors = score, rank) | units selected from chain |",
        "| --- | --- | ---: |",
    ]
    for group in groups:
        members = "; ".join(
            f"{c.t:.0f}s: {c.label} = {c.score:g}, r{c.rank}{'✓' if c.selected else ''}"
            for c in group
        )
        lines.append(
            f"| {group[0].match[:36]} | {members} | {sum(c.selected for c in group)} |"
        )
    lines += ["", "Evidence per re-death:", ""]
    for case in (c for c in cases if REDEATH in c.factors):
        lines += case_block(case, ("clock", "roster", "splats"))
    lines.append("")
    return lines


def competitors(case: Case, cases: list[Case]) -> str:
    """The other selected deaths of the same match."""
    others = [
        c for c in cases if c.match == case.match and c.selected and c.cid != case.cid
    ]
    return (
        "; ".join(
            f"{c.t:.0f}s {c.label} = {c.score:g} (r{c.rank})"
            for c in sorted(others, key=lambda c: c.rank)
        )
        or "none"
    )


def factor_section(
    title: str, factor: str, cases: list[Case], keys: tuple[str, ...]
) -> list[str]:
    """Every death with ``factor``: its evidence and what it competes with."""
    hits = [c for c in cases if factor in c.factors]
    first = sum(1 for c in hits if c.since_prev_death is None)
    lines = [
        title,
        "",
        f"{len(hits)} deaths, {sum(c.selected for c in hits)} selected; "
        f"{first} are the first death of their match.",
        "",
    ]
    for case in hits:
        lines += case_block(case, keys)
        lines.append(f"  - competing selected deaths: {competitors(case, cases)}")
    lines.append("")
    return lines


def build(cases: list[Case], weights: dict[str, float], config: AppConfig) -> str:
    """Whole report."""
    gap = config.coach.death_factor_thresholds.redeath_max_gap_seconds
    header = [
        "# Ranking case analysis",
        "",
        "Describes current behaviour only. Weights: "
        + ", ".join(
            f"{SHORT.get(k, k)} {v:g}"
            for k, v in sorted(weights.items(), key=lambda kv: -kv[1])
        )
        + f". Evidence windows look back {LOOKBACK:g}s from the death.",
        "",
    ]
    return "\n".join(
        header
        + redeath_section(cases, gap)
        + factor_section(
            "## 2. Outnumbered", OUTNUMBERED, cases, ("clock", "roster", "splats", "map")
        )
        + factor_section(
            "## 3. No map check", MAP_FALSE, cases, ("clock", "map", "roster", "splats")
        )
        + factor_section(
            "## 4. Special ready",
            SPECIAL,
            cases,
            ("clock", "special", "roster", "splats"),
        )
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
        default=PROJECT_ROOT / "analysis" / "importance_report" / "cases.md",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    cases, weights = load_cases(args.root.resolve(), config)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(cases, weights, config) + "\n", encoding="utf-8")
    logger.info("Wrote {}", args.out)


if __name__ == "__main__":
    main()
