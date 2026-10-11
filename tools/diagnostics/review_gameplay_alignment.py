#!/usr/bin/env python3
"""Review to gameplay clock alignment: tuning / validation diagnostics.

Contract: ``src/splatoon3_ai_coach/alignment/ALIGNMENT_CONTRACT.md``. For each
operator-declared pair this reports calibration quality and two independent
death references as **interval residuals** (gameplay bracket minus mapped
Review interval, seconds; positive = gameplay later):

- roster-X onsets (gameplay ``PlayerCountReading``) against Review death
  glyphs on the matching team row;
- local ``DEATH`` events against Review ally-row death glyphs ("some ally died
  here", never a slot assignment).

Association is fixed before any residual: candidates are mapped glyph
intervals intersecting the gameplay bracket widened by
``association_window_seconds``. One candidate is used; none is
``unassociated``; several is ``ambiguous`` and excluded. Never nearest by
residual. Deaths only test ``delta``; they never estimate ``c``. Read-only.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any, Literal

from splatoon3_ai_coach.alignment.review_alignment import (
    ReviewGameplayAlignment,
    align_pair,
    load_pair,
)
from splatoon3_ai_coach.analysis.match_bounds import before_match_end, match_end_time
from splatoon3_ai_coach.coach.game_clock import build_game_clock
from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.review.models import ReviewTimelineDataset, TimelineEventMarker
from splatoon3_ai_coach.vision.models import (
    GameEventType,
    PlayerCountReading,
    VisionManifest,
)

Row = Literal["top", "bottom"]
Interval = tuple[float, float]
MIN_USABLE_PER_REFERENCE = 3  # provisional sufficiency floor, frozen at freeze


def main() -> int:
    """Run every declared pair and write ``alignment_tuning.json`` / ``.md``."""
    args = _parse_args()
    config = load_config(args.config or default_config_path())
    pairs = [
        measure_pair(Path(r), Path(g), config, label=Path(r).name) for r, g in args.pair
    ]
    report = {"pairs": pairs, "delta_test": delta_test(pairs)}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "alignment_tuning.json").write_text(
        json.dumps(report, indent=2) + "\n", "utf-8"
    )
    (args.out / "alignment_tuning.md").write_text(render(report), "utf-8")
    print(json.dumps(report["delta_test"], indent=2))
    return 0


def measure_pair(
    review_dir: Path, gameplay_dir: Path, config: AppConfig, *, label: str
) -> dict[str, Any]:
    """Calibration, both death references and the delta=0 summary for one pair."""
    ds, manifest, mode, _, _ = load_pair(review_dir, gameplay_dir)
    alignment = align_pair(ds, manifest, mode, config)
    glyphs = mapped_death_glyphs(ds, alignment)
    window = config.alignment.association_window_seconds
    cadence = config.vision.hud_cadence_fps
    local = [
        associate(b, _ally_rows(ds), glyphs, window)
        for b in local_death_brackets(manifest, cadence)
    ]
    onsets, too_wide = roster_x_onsets(
        manifest, config.alignment.max_onset_bracket_seconds
    )
    roster = [
        associate(b, _team_rows(ds, team), glyphs, window, split_rows=True)
        for team, b in onsets
    ]
    clock = _clock(manifest, config)
    for item in local + roster:
        item["nearest_observed_game_clock"] = nearest_observed_game_clock(
            clock, item, config.alignment.nearest_clock_max_gap_seconds
        )
    return {
        "label": label,
        "pair": alignment.pair.model_dump(mode="json"),
        "calibration": calibration_summary(alignment),
        "glyph_status": dict(Counter(g["status"] for g in glyphs)),
        "local_death": {"items": local, "summary": summarize(local)},
        "roster_x": {
            "items": roster,
            "summary": summarize(roster),
            "onsets_excluded_wide_bracket": too_wide,
        },
    }


def calibration_summary(alignment: ReviewGameplayAlignment) -> dict[str, Any]:
    """Layer-1 quality figures (HUD only)."""
    cal = alignment.calibration
    c = cal.c_interval
    return {
        "fit_status": cal.fit_status,
        "reason": cal.reason,
        "c_interval": c,
        "c_width": None if c is None else round(c[1] - c[0], 4),
        "edge_count": cal.edge_count,
        "consistent_edge_count": cal.consistent_edge_count,
        "inconsistent_edge_share": cal.inconsistent_edge_share,
        "playback_status": cal.playback_status,
        "free_slope_game_seconds_per_video_second": cal.game_seconds_per_video_second,
        "e_axis": alignment.e_axis,
        "delta": alignment.delta,
    }


def mapped_death_glyphs(
    ds: ReviewTimelineDataset, alignment: ReviewGameplayAlignment
) -> list[dict[str, Any]]:
    """Every Review death glyph with its mapped video interval (or unknown)."""
    out = []
    for marker in ds.match_history_events:
        if marker.kind != "death" or marker.team_row not in ("top", "bottom"):
            continue
        r = _remaining(ds, marker)
        mapped = None if r is None else alignment.map_review_remaining(r)
        out.append(
            {
                "marker_id": marker.marker_id,
                "team_row": marker.team_row,
                "review_remaining": r,
                "video_interval": None if mapped is None else mapped.video_interval,
                "status": "unknown" if mapped is None else mapped.alignment_status,
                "reason": "no_estimate" if mapped is None else mapped.reason,
            }
        )
    return out


def _remaining(ds: ReviewTimelineDataset, marker: TimelineEventMarker) -> float | None:
    if ds.axis_calibration is None:
        return marker.elapsed_estimate
    axis = ds.axis_calibration
    return axis.slope * marker.x_norm + axis.intercept


def local_death_brackets(manifest: VisionManifest, cadence_fps: float) -> list[Interval]:
    """Local DEATH bracket ``(start - 1/cadence, start]`` before MATCH_END."""
    end = match_end_time(manifest.game_events)
    deaths = [e for e in manifest.game_events if e.event_type is GameEventType.DEATH]
    return [
        (e.start_time - 1.0 / cadence_fps, e.start_time)
        for e in before_match_end(deaths, lambda e: e.start_time, end)
    ]


def roster_x_onsets(
    manifest: VisionManifest, max_bracket_seconds: float
) -> tuple[list[tuple[str, Interval]], int]:
    """Roster-X onsets ``(team, (t_absent, t_present])`` and the too-wide count.

    A slot's X must be absent on one visible frame and present on the next two
    visible frames. ``team`` is ``ally`` or ``opponent`` (HUD side).
    """
    series = _visible_roster(manifest)
    onsets: list[tuple[str, Interval]] = []
    too_wide = 0
    for team in ("ally", "opponent"):
        for slot in range(1, 5):
            marks = [(t, slot in present[team]) for t, present in series]
            for (t0, x0), (t1, x1), (_, x2) in zip(
                marks, marks[1:], marks[2:], strict=False
            ):
                if x0 or not (x1 and x2):
                    continue
                if t1 - t0 > max_bracket_seconds:
                    too_wide += 1
                else:
                    onsets.append((team, (t0, t1)))
    onsets.sort(key=lambda item: item[1][0])
    return onsets, too_wide


def _visible_roster(manifest: VisionManifest) -> list[tuple[float, dict[str, set[int]]]]:
    end = match_end_time(manifest.game_events)
    out = []
    for frame in before_match_end(manifest.frame_results, lambda f: f.timestamp, end):
        for det in frame.detections:
            reading = det.reading
            if isinstance(reading, PlayerCountReading) and reading.roster_visible:
                present = {
                    "ally": set(reading.ally_dead_slots),
                    "opponent": set(reading.opponent_dead_slots),
                }
                out.append((frame.timestamp, present))
                break
    return out


def _ally_rows(ds: ReviewTimelineDataset) -> list[Row]:
    ally = ds.team_identity.ally_row
    return [ally] if ally else []


def _team_rows(ds: ReviewTimelineDataset, team: str) -> list[Row]:
    ally = ds.team_identity.ally_row
    if ally is None:
        return ["top", "bottom"]
    other: Row = "bottom" if ally == "top" else "top"
    return [ally] if team == "ally" else [other]


def associate(
    bracket: Interval,
    rows: list[Row],
    glyphs: list[dict[str, Any]],
    window: float,
    *,
    split_rows: bool = False,
) -> dict[str, Any]:
    """Apply the pre-committed association rule to one gameplay bracket."""
    item: dict[str, Any] = {"gameplay_bracket": bracket, "rows": rows}
    if not rows:
        return item | {"status": "no_ally_row"}
    lo, hi = bracket[0] - window, bracket[1] + window
    candidates = [
        g
        for g in glyphs
        if g["team_row"] in rows
        and g["video_interval"] is not None
        and g["video_interval"][0] <= hi
        and g["video_interval"][1] >= lo
    ]
    item["candidate_marker_ids"] = [g["marker_id"] for g in candidates]
    if not candidates:
        return item | {"status": "unassociated"}
    if len(candidates) > 1:
        return item | {"status": "ambiguous"}
    return item | {"status": "used"} | residual(bracket, candidates[0])


def residual(bracket: Interval, glyph: dict[str, Any]) -> dict[str, Any]:
    """Interval residual ``gameplay - review`` (positive = gameplay later)."""
    v_lo, v_hi = glyph["video_interval"]
    lo, hi = bracket[0] - v_hi, bracket[1] - v_lo
    return {
        "marker_id": glyph["marker_id"],
        "review_video_interval": glyph["video_interval"],
        "residual_interval": (round(lo, 4), round(hi, 4)),
        "residual_midpoint": round((lo + hi) / 2, 4),
        "residual_half_width": round((hi - lo) / 2, 4),
        "contains_zero": lo <= 0 <= hi,
    }


def _clock(manifest: VisionManifest, config: AppConfig) -> list[tuple[float, int]]:
    clock = build_game_clock(
        manifest.frame_results,
        min_usable_confidence=config.vision.timer.min_usable_confidence,
    )
    end = match_end_time(manifest.game_events)
    return [
        (o.video_time, o.seconds_remaining)
        for o in before_match_end(clock.observations, lambda o: o.video_time, end)
    ]


def nearest_observed_game_clock(
    clock: list[tuple[float, int]], item: dict[str, Any], max_gap: float
) -> dict[str, Any] | None:
    """Raw HUD read nearest the mapped glyph midpoint (validation evidence only)."""
    interval = item.get("review_video_interval")
    if interval is None or not clock:
        return None
    mid = (interval[0] + interval[1]) / 2
    t, value = min(clock, key=lambda o: abs(o[0] - mid))
    if abs(t - mid) > max_gap:
        return None
    return {"video_time": t, "seconds_remaining": value}


def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts plus residual statistics over ``used`` associations only."""
    counts = Counter(i["status"] for i in items)
    used = [i for i in items if i["status"] == "used"]
    out: dict[str, Any] = {"counts": dict(counts), "used": len(used)}
    if not used:
        return out | {"direction": "insufficient"}
    mids = [i["residual_midpoint"] for i in used]
    half = median(i["residual_half_width"] for i in used)
    med = median(mids)
    out |= {
        "contains_zero_share": round(
            sum(i["contains_zero"] for i in used) / len(used), 4
        ),
        "median_residual_midpoint": round(med, 4),
        "median_abs_residual_midpoint": round(median(abs(m) for m in mids), 4),
        "median_half_width": round(half, 4),
    }
    if len(used) < MIN_USABLE_PER_REFERENCE:
        return out | {"direction": "insufficient"}
    if abs(med) <= half:
        return out | {"direction": "within_uncertainty"}
    return out | {"direction": "positive" if med > 0 else "negative"}


def delta_test(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Pre-registered rule: delta stays 0 unless every condition holds."""
    per_match = [
        {
            "label": p["label"],
            "roster_x": p["roster_x"]["summary"]["direction"],
            "local_death": p["local_death"]["summary"]["direction"],
        }
        for p in pairs
    ]
    usable = [
        m for m in per_match if "insufficient" not in (m["roster_x"], m["local_death"])
    ]
    directions = {d for m in usable for d in (m["roster_x"], m["local_death"])}
    supported = (
        len(usable) >= 2
        and len(usable) == len(per_match)
        and len(directions) == 1
        and directions <= {"positive", "negative"}
    )
    return {
        "per_match": per_match,
        "matches_with_both_references_usable": len(usable),
        "nonzero_delta_supported": supported,
        "decision": "evidence_for_nonzero_delta" if supported else "delta_stays_0",
    }


def render(report: dict[str, Any]) -> str:
    """Markdown summary for the STOP report."""
    lines = ["# Review to gameplay alignment diagnostics", ""]
    for p in report["pairs"]:
        cal, pair = p["calibration"], p["pair"]
        lines += [
            f"## {p['label']}",
            "",
            f"- pair: {pair['pair_status']} {pair['reasons']}",
            f"- fit: {cal['fit_status']} ({cal['reason']}); c {cal['c_interval']} "
            f"width {cal['c_width']}; edges {cal['consistent_edge_count']}/"
            f"{cal['edge_count']}; free slope "
            f"{cal['free_slope_game_seconds_per_video_second']}",
            f"- glyphs: {p['glyph_status']}",
        ]
        for ref in ("roster_x", "local_death"):
            lines.append(f"- {ref}: {json.dumps(p[ref]['summary'])}")
        lines += [
            f"- roster-X onsets excluded (wide bracket): "
            f"{p['roster_x']['onsets_excluded_wide_bracket']}",
            "",
        ]
    lines += ["## delta = 0 test", "", "```json"]
    lines += [json.dumps(report["delta_test"], indent=2), "```", ""]
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pair",
        nargs=2,
        action="append",
        required=True,
        metavar=("REVIEW_IMPORT", "GAMEPLAY_ANALYSIS"),
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=None)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
