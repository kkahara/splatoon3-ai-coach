"""Read-only roster/map observation provenance export.

This module intentionally consumes persisted artifacts only.  It is not part
of ScenarioContext or CoachInput construction.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.match_bounds import match_end_time
from splatoon3_ai_coach.analysis.scenario_evidence import scenario_window
from splatoon3_ai_coach.analysis.scenario_models import Scenario
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig
from splatoon3_ai_coach.vision.models import GameEvent

SCENARIO_OBSERVATION_EVIDENCE_FILENAME = "scenario_observation_evidence.json"


class SourceRecordRef(BaseModel):
    """Reference to an unmodified record in one hashed source artifact."""

    artifact_key: str
    artifact_sha256: str | None = None
    json_pointer: str | None = None
    result_id: str | None = None
    resolution: Literal["resolved", "missing", "ambiguous", "mismatched", "unavailable"]


class ObservationGap(BaseModel):
    """Coverage gap, never a gameplay event or continuous-state assertion."""

    stream: Literal["roster", "map_ink"]
    kind: Literal[
        "sampled_counts_unavailable", "no_recorded_frames", "between_map_samples"
    ]
    left_observation_video_time: float | None = None
    right_observation_video_time: float | None = None
    first_unavailable_video_time: float | None = None
    last_unavailable_video_time: float | None = None
    unavailable_sample_refs: list[SourceRecordRef] = Field(default_factory=list)
    censoring: Literal["none", "left", "right", "both"] = "none"


class RosterFrameEvidence(BaseModel):
    """One persisted frame/snapshot, retaining unavailable counts as null."""

    sample_video_time: float
    frame_record_ref: SourceRecordRef
    snapshot_ref: SourceRecordRef | None = None
    ally_alive_count: int | None = None
    opponent_alive_count: int | None = None
    snapshot_confidence: float | None = None
    snapshot_evidence_ids: list[str] = Field(default_factory=list)
    roster_support: list[SourceRecordRef] = Field(default_factory=list)
    roster_visible: bool | None = None
    paired_counts: Literal["present", "partial", "unavailable"]


class MapInkObservationRecord(BaseModel):
    """Raw map point values and source identity; no reclassification occurs."""

    video_time: float
    observation_ref: SourceRecordRef
    ally_share_of_detected_ink: float | None = None
    opponent_share_of_detected_ink: float | None = None
    visible_ink_fraction: float | None = None
    stage_id: str | None = None
    battle_mode_id: str | None = None
    geometry_battle_mode_id: str | None = None
    sample_mask_source: str | None = None
    sample_polygon_sha256: str | None = None
    pixel_totals: dict[str, int | None]
    source_evidence_ids: list[str] = Field(default_factory=list)


class ContextObservationEvidence(BaseModel):
    """Inspection-only evidence for one existing scenario."""

    scenario_id: str
    requested_start: float
    requested_end: float
    match_end_exclusive: float | None = None
    roster_samples: list[RosterFrameEvidence] = Field(default_factory=list)
    roster_gaps: list[ObservationGap] = Field(default_factory=list)
    map_samples: list[MapInkObservationRecord] = Field(default_factory=list)
    map_gaps: list[ObservationGap] = Field(default_factory=list)


class ObservationInspectionArtifact(BaseModel):
    """Versioned sidecar that is never loaded by ordinary coaching."""

    schema_version: Literal[1] = 1
    rule_id: Literal["roster_map_observation_provenance_v1"] = (
        "roster_map_observation_provenance_v1"
    )
    parameters: dict[str, float]
    sources: dict[str, dict[str, str | None]]
    contexts: list[ContextObservationEvidence]


def export_scenario_observation_evidence(
    analysis_dir: Path, config: ScenarioBuilderConfig, output_path: Path | None = None
) -> ObservationInspectionArtifact:
    """Export additive provenance sidecar from saved artifacts without mutation."""
    raw, sources = _load_sources(analysis_dir)
    manifest = raw.get("vision_manifest") or {}
    events = [GameEvent.model_validate(item) for item in manifest.get("game_events", [])]
    end = match_end_time(events)
    frames = manifest.get("frame_results", [])
    snapshots = manifest.get("state_snapshots", [])
    contexts: list[ContextObservationEvidence] = []
    for item in raw.get("scenarios") or []:
        scenario = Scenario.model_validate(item)
        start, finish = scenario_window(scenario, config)
        contexts.append(
            _context_for_scenario(
                scenario,
                start,
                finish,
                end,
                frames,
                snapshots,
                raw.get("map_observations"),
                sources,
            )
        )
    artifact = ObservationInspectionArtifact(
        parameters={
            "context_lookback_seconds": float(config.context_lookback_seconds),
            "context_lookforward_seconds": float(config.context_lookforward_seconds),
            "coverage_break_gap_seconds": float(config.context_max_gap_seconds),
        },
        sources=sources,
        contexts=contexts,
    )
    destination = output_path or analysis_dir / SCENARIO_OBSERVATION_EVIDENCE_FILENAME
    _atomic_write_new(destination, artifact.model_dump(mode="json"))
    return artifact


def _load_sources(
    directory: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, str | None]]]:
    names = {
        "vision_manifest": "vision_manifest.json",
        "map_observations": "map_observations.json",
        "match_identity": "match_identity.json",
        "scenario_contexts": "scenario_contexts.json",
        "scenarios": "scenarios.json",
    }
    raw: dict[str, Any] = {}
    sources: dict[str, dict[str, str | None]] = {}
    for key, name in names.items():
        path = directory / name
        if not path.is_file():
            sources[key] = {"filename": name, "sha256": None, "availability": "missing"}
            continue
        data = path.read_bytes()
        try:
            raw[key] = json.loads(data)
            status = "present"
        except json.JSONDecodeError:
            status = "invalid"
        sources[key] = {
            "filename": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "availability": status,
        }
    return raw, sources


def _context_for_scenario(
    scenario: Scenario,
    start: float,
    finish: float,
    end: float | None,
    frames: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    map_rows: Any,
    sources: dict[str, dict[str, str | None]],
) -> ContextObservationEvidence:
    limit = min(finish, end) if end is not None else finish
    snap_by_time: dict[float, list[tuple[int, dict[str, Any]]]] = {}
    for index, snapshot in enumerate(snapshots):
        snap_by_time.setdefault(float(snapshot.get("timestamp", -1)), []).append(
            (index, snapshot)
        )
    roster: list[RosterFrameEvidence] = []
    for fi, frame in enumerate(frames):
        time = float(frame.get("timestamp", -1))
        if not start <= time <= limit or (end is not None and time >= end):
            continue
        matches = snap_by_time.get(time, [])
        snap_i, snap = matches[0] if len(matches) == 1 else (None, {})
        ref = _ref("vision_manifest", sources, f"/frame_results/{fi}")
        snap_ref = (
            _ref("vision_manifest", sources, f"/state_snapshots/{snap_i}")
            if snap_i is not None
            else None
        )
        ally, opponent = snap.get("ally_alive_count"), snap.get("opponent_alive_count")
        paired = (
            "present"
            if ally is not None and opponent is not None
            else "partial"
            if ally is not None or opponent is not None
            else "unavailable"
        )
        readings = [
            d
            for d in frame.get("detections", [])
            if d.get("detector_name") == "player_count"
        ]
        support = [
            _ref(
                "vision_manifest",
                sources,
                f"/frame_results/{fi}/detections/{j}",
                d.get("id"),
            )
            for j, d in enumerate(frame.get("detections", []))
            if d.get("detector_name") == "player_count"
        ]
        visible = (
            readings[0].get("reading", {}).get("roster_visible")
            if len(readings) == 1
            else None
        )
        roster.append(
            RosterFrameEvidence(
                sample_video_time=time,
                frame_record_ref=ref,
                snapshot_ref=snap_ref,
                ally_alive_count=ally,
                opponent_alive_count=opponent,
                snapshot_confidence=snap.get("player_count_confidence"),
                snapshot_evidence_ids=snap.get("evidence_ids", []),
                roster_support=support,
                roster_visible=visible,
                paired_counts=paired,
            )
        )
    maps = _map_samples(map_rows, start, limit, end, sources)
    return ContextObservationEvidence(
        scenario_id=scenario.scenario_id,
        requested_start=start,
        requested_end=finish,
        match_end_exclusive=end,
        roster_samples=roster,
        roster_gaps=_roster_gaps(roster),
        map_samples=maps,
        map_gaps=_map_gaps(maps),
    )


def _ref(
    key: str,
    sources: dict[str, dict[str, str | None]],
    pointer: str,
    result_id: str | None = None,
) -> SourceRecordRef:
    return SourceRecordRef(
        artifact_key=key,
        artifact_sha256=sources[key]["sha256"],
        json_pointer=pointer,
        result_id=result_id,
        resolution="resolved",
    )


def _roster_gaps(samples: list[RosterFrameEvidence]) -> list[ObservationGap]:
    gaps: list[ObservationGap] = []
    run: list[RosterFrameEvidence] = []
    for sample in [*samples, None]:
        if sample is not None and sample.paired_counts != "present":
            run.append(sample)
            continue
        if run:
            left = (
                samples[samples.index(run[0]) - 1].sample_video_time
                if samples.index(run[0])
                else None
            )
            right = sample.sample_video_time if sample else None
            gaps.append(
                ObservationGap(
                    stream="roster",
                    kind="sampled_counts_unavailable",
                    left_observation_video_time=left,
                    right_observation_video_time=right,
                    first_unavailable_video_time=run[0].sample_video_time,
                    last_unavailable_video_time=run[-1].sample_video_time,
                    unavailable_sample_refs=[r.frame_record_ref for r in run],
                    censoring="left"
                    if left is None
                    else "right"
                    if right is None
                    else "none",
                )
            )
            run = []
    return gaps


def _map_samples(
    rows: Any,
    start: float,
    finish: float,
    end: float | None,
    sources: dict[str, dict[str, str | None]],
) -> list[MapInkObservationRecord]:
    if not isinstance(rows, list):
        return []
    out = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(
            row.get("video_time"), (int, float)
        ):
            continue
        t = float(row["video_time"])
        if not start <= t <= finish or (end is not None and t >= end):
            continue
        out.append(
            MapInkObservationRecord(
                video_time=t,
                observation_ref=_ref("map_observations", sources, f"/{i}"),
                ally_share_of_detected_ink=row.get(
                    "ally_share_of_detected_ink", row.get("ally_classified_fraction")
                ),
                opponent_share_of_detected_ink=row.get(
                    "opponent_share_of_detected_ink",
                    row.get("opponent_classified_fraction"),
                ),
                visible_ink_fraction=row.get(
                    "visible_ink_fraction", row.get("classified_fraction")
                ),
                stage_id=row.get("stage_id"),
                battle_mode_id=row.get("battle_mode_id"),
                geometry_battle_mode_id=row.get("geometry_battle_mode_id"),
                sample_mask_source=row.get("sample_mask_source"),
                sample_polygon_sha256=row.get(
                    "sample_polygon_sha256", row.get("polygon_sha256")
                ),
                pixel_totals={
                    k: row.get(k)
                    for k in (
                        "total_sample_pixels",
                        "classified_pixels",
                        "ally_ink_pixels",
                        "opponent_ink_pixels",
                        "neither_team_pixels",
                    )
                },
                source_evidence_ids=row.get("evidence_ids", []),
            )
        )
    return out


def _map_gaps(samples: list[MapInkObservationRecord]) -> list[ObservationGap]:
    return [
        ObservationGap(
            stream="map_ink",
            kind="between_map_samples",
            left_observation_video_time=a.video_time,
            right_observation_video_time=b.video_time,
        )
        for a, b in zip(samples, samples[1:], strict=False)
    ]


def _atomic_write_new(path: Path, payload: dict[str, Any]) -> None:
    """Write a new sidecar atomically and refuse to overwrite prior inspection."""
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite {path}")
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)
