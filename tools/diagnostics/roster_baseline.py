"""Measure the unchanged live-gameplay HUD roster detector.

Reads stored ``vision_manifest.json`` frame results, re-fuses them in memory
with the current production config, and reports temporal consistency. It does
not decode video, use Review timeline lamps, change analyses, or judge detector
correctness (that requires the separately sealed hand-label set).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.player_count_series import (
    PlayerCountObservation,
    assess_roster_plausibility,
    compress_player_count_observations,
    observations_from_snapshots,
)
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.media.vision_manifest import load_vision_manifest
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    GameEventType,
    PlayerCountReading,
    VisionFrameResult,
    VisionManifest,
)
from splatoon3_ai_coach.vision.player_count import alive_counts_from_reading
from splatoon3_ai_coach.vision.state import fuse_game_state

SourceClass = Literal["live_gameplay", "replay_derived"]
CURRENT_LOGIC = "current_logic"
PRE_CURRENT_LOGIC = "pre_current_logic"


@dataclass(frozen=True)
class RawRosterPoint:
    """One scheduled player-count result, usable or unreadable."""

    video_time: float
    ally: int | None
    opponent: int | None
    scores: tuple[float, ...]
    visible: bool
    confidence: float

    @property
    def avb(self) -> tuple[int, int] | None:
        """Raw AvB when the reading is usable."""
        if self.ally is None or self.opponent is None:
            return None
        return self.ally, self.opponent


class MatchBaseline(BaseModel):
    """Persisted consistency metrics for one source video."""

    match: str
    video_identity: str
    source_class: SourceClass
    language: str
    battle_mode_id: str | None = None
    detector_version: str | None = None
    detector_logic: str
    threshold_classification_mismatches: int = 0
    scheduled_readings: int = 0
    usable_readings: int = 0
    visible_readings: int = 0
    visibility_rate: float = 0.0
    margin_slot_scores: int = 0
    scored_slots: int = 0
    consecutive_usable_pairs: int = 0
    changed_pairs: int = 0
    changed_pair_share: float = 0.0
    change_magnitude_1: int = 0
    change_magnitude_2plus: int = 0
    one_frame_reversals: int = 0
    fused_changes_per_minute: float = 0.0
    unreliable_interval_count: int = 0
    unreliable_time_share: float = 0.0
    deaths: int = 0
    deaths_in_unreliable_interval: int = 0
    pre_death_samples: int = 0
    pre_death_missing: int = 0
    pre_death_unreliable: int = 0
    phase_comparable_deaths: int = 0
    phase_disagreements: int = 0
    phase_disagreement_magnitude_1: int = 0
    phase_disagreement_magnitude_2plus: int = 0


class BaselineArtifact(BaseModel):
    """Versioned output of the offline consistency survey."""

    schema_version: int = 1
    roots: list[str]
    replay_roots: list[str]
    config_path: str
    config_sha256: str
    roster_config: dict[str, object]
    matches: list[MatchBaseline]
    summaries: dict[str, object]
    notes: list[str] = Field(default_factory=list)


def find_manifests(roots: Iterable[Path]) -> list[Path]:
    """Unique vision manifests below roots, keyed by video identity."""
    found: dict[str, Path] = {}
    for root in roots:
        for path in sorted(root.rglob("vision_manifest.json")):
            manifest = load_vision_manifest(path)
            found.setdefault(manifest.video_identity, path)
    return sorted(found.values())


def _player_result(frame: VisionFrameResult) -> DetectorResult | None:
    results = [
        result
        for result in frame.detections
        if result.detector_name == "player_count"
        and isinstance(result.reading, PlayerCountReading)
    ]
    return max(results, key=lambda item: item.confidence) if results else None


def raw_points(
    frames: Sequence[VisionFrameResult], config: AppConfig
) -> list[RawRosterPoint]:
    """Extract scheduled raw roster observations without reclassification."""
    minimum = config.vision.player_count.min_usable_confidence
    points: list[RawRosterPoint] = []
    for frame in sorted(frames, key=lambda item: item.timestamp):
        result = _player_result(frame)
        if result is None:
            continue
        reading = result.reading
        assert isinstance(reading, PlayerCountReading)
        scores = (*reading.ally_slot_scores, *reading.opponent_slot_scores)
        usable = reading.roster_visible and result.confidence >= minimum
        ally, opponent = alive_counts_from_reading(reading) if usable else (None, None)
        points.append(
            RawRosterPoint(
                video_time=float(frame.timestamp),
                ally=ally,
                opponent=opponent,
                scores=tuple(float(value) for value in scores),
                visible=bool(reading.roster_visible),
                confidence=float(result.confidence),
            )
        )
    return points


def fuse(frames: list[VisionFrameResult], config: AppConfig):
    """Production roster fusion in memory, with unrelated detections removed."""
    vision = config.vision
    roster_frames = [
        frame.model_copy(
            update={
                "detections": [
                    item
                    for item in frame.detections
                    if item.detector_name == "player_count"
                ]
            }
        )
        for frame in frames
    ]
    return fuse_game_state(
        roster_frames,
        vision.timer,
        vision.state_fusion,
        vision.death,
        vision.splat,
        vision.respawn,
        vision.active_gameplay,
        vision.lifecycle,
        vision.map_overlay,
        vision.player_count,
        vision.low_ink,
        vision.score,
        vision.zone_control,
    )


def phase_frames(
    frames: Sequence[VisionFrameResult], parity: int
) -> list[VisionFrameResult]:
    """Keep alternating player-count results; retain all other detections."""
    out: list[VisionFrameResult] = []
    count_index = 0
    for frame in sorted(frames, key=lambda item: item.timestamp):
        detections = []
        for result in frame.detections:
            if result.detector_name != "player_count":
                detections.append(result)
            elif count_index % 2 == parity:
                detections.append(result)
        if _player_result(frame) is not None:
            count_index += 1
        out.append(frame.model_copy(update={"detections": detections}))
    return out


def _pre_death_point(
    observations: Sequence[PlayerCountObservation],
    death_time: float,
    config: AppConfig,
) -> PlayerCountObservation | None:
    """Production factor lookup over the scenario's roster context window."""
    window = compress_player_count_observations(
        [
            item
            for item in observations
            if (
                death_time - config.scenarios.context_lookback_seconds
                <= item.video_time
                <= death_time + config.scenarios.context_lookforward_seconds
            )
        ]
    )
    cutoff = (
        death_time - config.coach.death_factor_thresholds.roster_pre_death_offset_seconds
    )
    candidates = [item for item in window if item.video_time <= cutoff]
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.video_time)


def _latest_pre_death(
    observations: Sequence[PlayerCountObservation],
    death_time: float,
    config: AppConfig,
) -> tuple[int, int] | None:
    point = _pre_death_point(observations, death_time, config)
    if point is None:
        return None
    return point.ally_alive_count, point.opponent_alive_count


def _intervals(observations: Sequence[PlayerCountObservation], config: AppConfig):
    plausibility = config.scenarios.roster_plausibility
    return assess_roster_plausibility(
        observations,
        min_respawn_seconds=plausibility.min_respawn_seconds,
        window_seconds=plausibility.window_seconds,
    )


def _death_times(manifest: VisionManifest) -> list[float]:
    return [
        float(event.start_time)
        for event in manifest.game_events
        if event.event_type is GameEventType.DEATH
    ]


def _change_stats(points: Sequence[RawRosterPoint]) -> dict[str, int]:
    pairs = changed = mag1 = mag2 = 0
    reversals = 0
    for before, after in zip(points, points[1:], strict=False):
        if before.avb is None or after.avb is None:
            continue
        pairs += 1
        magnitude = sum(abs(a - b) for a, b in zip(before.avb, after.avb, strict=True))
        if magnitude:
            changed += 1
            mag1 += magnitude == 1
            mag2 += magnitude >= 2
    for before, middle, after in zip(points, points[1:], points[2:], strict=False):
        if None not in (before.avb, middle.avb, after.avb):
            reversals += before.avb == after.avb and before.avb != middle.avb
    return {
        "pairs": pairs,
        "changed": changed,
        "magnitude_1": mag1,
        "magnitude_2plus": mag2,
        "reversals": reversals,
    }


def _union_duration(intervals, duration: float) -> float:
    ranges = sorted((max(0.0, item.start), min(duration, item.end)) for item in intervals)
    merged: list[list[float]] = []
    for start, end in ranges:
        if end < start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return sum(end - start for start, end in merged)


def _mode(folder: Path) -> str | None:
    path = folder / "match_identity.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("battle_mode_id")


def _source_class(
    manifest: VisionManifest, override: SourceClass | None = None
) -> SourceClass:
    if override is not None:
        return override
    if manifest.video is not None and manifest.video.source.value == "review":
        return "replay_derived"
    return "live_gameplay"


def _logic_status(points: Sequence[RawRosterPoint], threshold: float) -> tuple[str, int]:
    mismatches = 0
    for point in points:
        if point.avb is None or len(point.scores) != 8:
            continue
        expected = (
            4 - sum(score <= threshold for score in point.scores[:4]),
            4 - sum(score <= threshold for score in point.scores[4:]),
        )
        mismatches += expected != point.avb
    return (CURRENT_LOGIC if mismatches == 0 else PRE_CURRENT_LOGIC), mismatches


def measure_match(
    path: Path, config: AppConfig, source_override: SourceClass | None = None
) -> MatchBaseline:
    """Measure one persisted gameplay-HUD roster stream."""
    manifest = load_vision_manifest(path)
    frames = list(manifest.frame_results)
    raw = raw_points(frames, config)
    snapshots = fuse(frames, config)
    all_observations = observations_from_snapshots(snapshots)
    observations = compress_player_count_observations(all_observations)
    intervals = _intervals(observations, config)
    deaths = _death_times(manifest)
    even = observations_from_snapshots(fuse(phase_frames(frames, 0), config))
    odd = observations_from_snapshots(fuse(phase_frames(frames, 1), config))
    return _match_row(
        path,
        manifest,
        config,
        raw,
        all_observations,
        observations,
        intervals,
        deaths,
        even,
        odd,
        source_override,
    )


def _match_row(
    path,
    manifest,
    config,
    raw,
    all_observations,
    observations,
    intervals,
    deaths,
    even,
    odd,
    source_override,
):
    changes = _change_stats(raw)
    duration = (
        float(manifest.timing.video_duration_seconds)
        if manifest.timing
        else (max((frame.timestamp for frame in manifest.frame_results), default=0.0))
    )
    phases = _phase_stats(deaths, even, odd, config)
    logic, mismatches = _logic_status(
        raw, config.vision.player_count.sqdiff_match_threshold
    )
    scored = [score for point in raw for score in point.scores]
    margin = sum(
        abs(score - config.vision.player_count.sqdiff_match_threshold) <= 0.01
        for score in scored
    )
    fused_changes = max(0, len(observations) - 1)
    pre = [_pre_death_status(t, all_observations, intervals, config) for t in deaths]
    return MatchBaseline(
        match=str(path.parent),
        video_identity=manifest.video_identity,
        source_class=_source_class(manifest, source_override),
        language=manifest.analysis.language,
        battle_mode_id=_mode(path.parent),
        detector_version=manifest.analysis.detector_versions.get("player_count"),
        detector_logic=logic,
        threshold_classification_mismatches=mismatches,
        scheduled_readings=len(raw),
        usable_readings=sum(point.avb is not None for point in raw),
        visible_readings=sum(point.visible for point in raw),
        visibility_rate=_ratio(sum(point.visible for point in raw), len(raw)),
        margin_slot_scores=margin,
        scored_slots=len(scored),
        consecutive_usable_pairs=changes["pairs"],
        changed_pairs=changes["changed"],
        changed_pair_share=_ratio(changes["changed"], changes["pairs"]),
        change_magnitude_1=changes["magnitude_1"],
        change_magnitude_2plus=changes["magnitude_2plus"],
        one_frame_reversals=changes["reversals"],
        fused_changes_per_minute=fused_changes / max(duration / 60.0, 1e-9),
        unreliable_interval_count=len(intervals),
        unreliable_time_share=_ratio(_union_duration(intervals, duration), duration),
        deaths=len(deaths),
        deaths_in_unreliable_interval=sum(
            any(item.contains(t) for item in intervals) for t in deaths
        ),
        pre_death_samples=pre.count("sample"),
        pre_death_missing=pre.count("missing"),
        pre_death_unreliable=pre.count("unreliable"),
        **phases,
    )


def _pre_death_status(death, observations, intervals, config) -> str:
    point = _pre_death_point(observations, death, config)
    if point is None:
        return "missing"
    if any(item.contains(death) or item.contains(point.video_time) for item in intervals):
        return "unreliable"
    return "sample"


def _phase_stats(deaths, even, odd, config) -> dict[str, int]:
    comparable = disagreements = mag1 = mag2 = 0
    for death in deaths:
        left = _latest_pre_death(even, death, config)
        right = _latest_pre_death(odd, death, config)
        if left is None or right is None:
            continue
        comparable += 1
        magnitude = sum(abs(a - b) for a, b in zip(left, right, strict=True))
        disagreements += magnitude > 0
        mag1 += magnitude == 1
        mag2 += magnitude >= 2
    return {
        "phase_comparable_deaths": comparable,
        "phase_disagreements": disagreements,
        "phase_disagreement_magnitude_1": mag1,
        "phase_disagreement_magnitude_2plus": mag2,
    }


def _ratio(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def summarize(rows: Sequence[MatchBaseline]) -> dict[str, object]:
    """Separate primary summaries for live and replay-derived footage."""
    by_source = {
        "live_gameplay": [row for row in rows if row.source_class == "live_gameplay"],
        "replay_derived": [row for row in rows if row.source_class == "replay_derived"],
    }
    languages = sorted({row.language for row in rows})
    modes = sorted({row.battle_mode_id or "unknown" for row in rows})
    return {
        "by_source": {name: _summary(items) for name, items in by_source.items()},
        "by_source_language": {
            f"{source}/{language}": _summary(
                [row for row in items if row.language == language]
            )
            for source, items in by_source.items()
            for language in languages
        },
        "by_source_mode": {
            f"{source}/{mode}": _summary(
                [row for row in items if (row.battle_mode_id or "unknown") == mode]
            )
            for source, items in by_source.items()
            for mode in modes
        },
    }


def _summary(rows: Sequence[MatchBaseline]) -> dict[str, object]:
    sums = Counter()
    keys = (
        "scheduled_readings",
        "usable_readings",
        "visible_readings",
        "consecutive_usable_pairs",
        "changed_pairs",
        "change_magnitude_1",
        "change_magnitude_2plus",
        "one_frame_reversals",
        "unreliable_interval_count",
        "deaths",
        "deaths_in_unreliable_interval",
        "pre_death_samples",
        "pre_death_missing",
        "pre_death_unreliable",
        "phase_comparable_deaths",
        "phase_disagreements",
        "phase_disagreement_magnitude_1",
        "phase_disagreement_magnitude_2plus",
    )
    for row in rows:
        for key in keys:
            sums[key] += getattr(row, key)
    for key in keys:
        sums[key] += 0
    return {
        "matches": len(rows),
        **dict(sums),
        "visibility_rate": _ratio(sums["visible_readings"], sums["scheduled_readings"]),
        "changed_pair_share": _ratio(
            sums["changed_pairs"], sums["consecutive_usable_pairs"]
        ),
        "phase_disagreement_share": _ratio(
            sums["phase_disagreements"], sums["phase_comparable_deaths"]
        ),
        "matches_with_pre_current_logic": sum(
            row.detector_logic == PRE_CURRENT_LOGIC for row in rows
        ),
    }


def markdown(artifact: BaselineArtifact) -> str:
    """Human-readable report with source classes kept separate."""
    lines = [
        "# Gameplay HUD roster consistency baseline",
        "",
        "No labels are used here. Changes and disagreements are not called errors.",
        "Live gameplay and replay-derived footage are never aggregated.",
        "",
    ]
    by_source = artifact.summaries["by_source"]
    assert isinstance(by_source, dict)
    for source in ("live_gameplay", "replay_derived"):
        row = by_source[source]
        assert isinstance(row, dict)
        lines.extend(_summary_markdown(source, row))
    lines.extend(
        [
            "## Per match",
            "",
            "| source | match | mode | readings | visible | changed pairs | "
            "reversals | implausible intervals | deaths unreliable | "
            "1 Hz disagreements |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in artifact.matches:
        lines.append(
            f"| {row.source_class} | `{Path(row.match).name}` | "
            f"{row.battle_mode_id or 'unknown'} | {row.scheduled_readings} | "
            f"{row.visibility_rate:.1%} | {row.changed_pairs}/"
            f"{row.consecutive_usable_pairs} | {row.one_frame_reversals} | "
            f"{row.unreliable_interval_count} | "
            f"{row.deaths_in_unreliable_interval}/{row.deaths} | "
            f"{row.phase_disagreements}/{row.phase_comparable_deaths} |"
        )
    return "\n".join(lines) + "\n"


def _summary_markdown(source: str, row: dict[str, object]) -> list[str]:
    return [
        f"## {source.replace('_', ' ').title()}",
        "",
        f"- Matches: {row['matches']}",
        f"- Scheduled / usable readings: {row['scheduled_readings']} / "
        f"{row['usable_readings']}",
        f"- Visibility rate: {row['visibility_rate']:.1%}",
        f"- Consecutive raw changes: {row['changed_pairs']} / "
        f"{row['consecutive_usable_pairs']} ({row['changed_pair_share']:.1%})",
        f"- Change magnitude 1 / 2+: {row['change_magnitude_1']} / "
        f"{row['change_magnitude_2plus']}",
        f"- One-frame A-B-A reversals: {row['one_frame_reversals']}",
        f"- Implausible intervals: {row['unreliable_interval_count']}",
        f"- Deaths in implausible intervals: {row['deaths_in_unreliable_interval']} / "
        f"{row['deaths']}",
        f"- Pre-death sample / missing / unreliable: {row['pre_death_samples']} / "
        f"{row['pre_death_missing']} / {row['pre_death_unreliable']}",
        f"- Even/odd 1 Hz disagreements: {row['phase_disagreements']} / "
        f"{row['phase_comparable_deaths']} ({row['phase_disagreement_share']:.1%})",
        f"- Disagreement magnitude 1 / 2+: {row['phase_disagreement_magnitude_1']} / "
        f"{row['phase_disagreement_magnitude_2plus']}",
        f"- Matches with readings inconsistent with the current 0.03 rule: "
        f"{row['matches_with_pre_current_logic']}",
        "",
    ]


def build_artifact(
    roots: list[Path], config_path: Path, replay_roots: list[Path] | None = None
) -> BaselineArtifact:
    """Measure unique source videos below all requested roots."""
    config = load_config(config_path)
    replay_roots = replay_roots or []
    paths: dict[str, tuple[Path, SourceClass]] = {}
    for path in find_manifests(roots):
        manifest = load_vision_manifest(path)
        paths[manifest.video_identity] = (path, "live_gameplay")
    for path in find_manifests(replay_roots):
        manifest = load_vision_manifest(path)
        paths[manifest.video_identity] = (path, "replay_derived")
    rows = [measure_match(path, config, source) for path, source in paths.values()]
    player = config.vision.player_count
    return BaselineArtifact(
        roots=[str(root) for root in roots],
        replay_roots=[str(root) for root in replay_roots],
        config_path=str(config_path),
        config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
        roster_config={
            "sqdiff_match_threshold": player.sqdiff_match_threshold,
            "min_usable_confidence": player.min_usable_confidence,
            "min_slot_edge_energy": player.min_slot_edge_energy,
            "confirm_readings": player.confirm_readings,
            "hold_seconds": player.hold_seconds,
            "hud_cadence_fps": config.vision.hud_cadence_fps,
        },
        matches=rows,
        summaries=summarize(rows),
        notes=[
            "Consistency is not correctness; hand labels are scored separately.",
            "Review timeline lamps and alignment are not inputs.",
        ],
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        action="append",
        type=Path,
        default=[],
        help="Analysis root (repeatable)",
    )
    parser.add_argument(
        "--replay-root",
        action="append",
        type=Path,
        default=[],
        help="Known replay/Review-derived gameplay analysis root (repeatable)",
    )
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    roots = args.root or [
        PROJECT_ROOT / "analysis" / "_rescore_v2",
        PROJECT_ROOT / "analysis" / "_sz_corpus_v1",
    ]
    default_replay = (
        PROJECT_ROOT / "analysis" / "en-preview-full-fast-2026-10-04 21-32-55"
    )
    replay_roots = args.replay_root or (
        [default_replay] if default_replay.exists() else []
    )
    artifact = build_artifact(
        [root.resolve() for root in roots],
        args.config.resolve(),
        [root.resolve() for root in replay_roots],
    )
    text = markdown(artifact)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(artifact.model_dump(mode="json"), indent=2) + "\n", "utf-8"
        )
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(text, "utf-8")
    if not args.markdown:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
