"""Bounded comparisons of consecutive observed Tower HUD marker positions.

These are endpoint comparisons, not continuous movement or gameplay episodes.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.match_bounds import match_end_time
from splatoon3_ai_coach.vision.canonical import canonical_json, sha256_hex
from splatoon3_ai_coach.vision.models import (
    GameStateSnapshot,
    TowerControlReading,
    VisionFrameResult,
    VisionManifest,
)
from splatoon3_ai_coach.vision.score_fusion import resolve_battle_mode


class TowerMarkerSample(BaseModel):
    """A single observed marker position with canonical source provenance."""

    video_time: float = Field(ge=0)
    frame_id: str
    evidence_id: str
    lane_position: float = Field(ge=0, le=1)


class TowerMarkerComparison(BaseModel):
    """A pair of observations, making no claim about the interval between them."""

    earlier: TowerMarkerSample
    later: TowerMarkerSample
    delta: float
    classification: Literal["left", "right", "no_detectable_change"]


class TowerMarkerTemporalResult(BaseModel):
    """Observed adjacent-frame comparisons and their explicit parameters."""

    schema_version: Literal[1] = 1
    analysis_id: str
    source_manifest_sha256: str
    battle_mode_id: str | None
    status: Literal["available", "unavailable"]
    unavailable_reason: (
        Literal["mode_not_tower_control", "detector_not_enabled"] | None
    ) = None
    max_gap_seconds: float = Field(gt=0)
    position_tolerance: float = Field(ge=0, le=1)
    comparisons: list[TowerMarkerComparison] = Field(default_factory=list)


def write_tower_marker_comparisons(result: TowerMarkerTemporalResult, path: Path) -> None:
    """Write a comparison result to a caller-selected JSON artifact path."""
    path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")


def build_tower_marker_comparisons(
    manifest: VisionManifest,
    *,
    max_gap_seconds: float = 1.0,
    position_tolerance: float = 0.01,
) -> TowerMarkerTemporalResult:
    """Compare consecutive accepted frames without bridging missing evidence.

    Tolerance and maximum gap are development parameters in HUD lane units
    and video seconds. Results describe endpoints only; they do not establish
    continuous stationarity, duration, or gameplay distance.
    """
    base = dict(
        analysis_id=manifest.analysis.analysis_id,
        source_manifest_sha256=sha256_hex(
            canonical_json(manifest.model_dump(mode="json"))
        ),
        battle_mode_id=resolve_battle_mode(manifest.frame_results),
        max_gap_seconds=max_gap_seconds,
        position_tolerance=position_tolerance,
    )
    if base["battle_mode_id"] != "tower_control":
        return TowerMarkerTemporalResult(
            **base, status="unavailable", unavailable_reason="mode_not_tower_control"
        )
    if "tower_control" not in manifest.analysis.detector_versions:
        return TowerMarkerTemporalResult(
            **base, status="unavailable", unavailable_reason="detector_not_enabled"
        )
    # Validate parameters even if no comparable samples exist.
    result = TowerMarkerTemporalResult(**base, status="available")
    frames = sorted(manifest.frame_results, key=lambda frame: frame.timestamp)
    if len({frame.frame_id for frame in frames}) != len(frames) or len(
        {frame.timestamp for frame in frames}
    ) != len(frames):
        raise ValueError("Tower comparison requires unique frame IDs and timestamps")
    snapshots = _snapshots_by_frame(manifest.state_snapshots)
    end = match_end_time(manifest.game_events)
    previous: TowerMarkerSample | None = None
    for frame in frames:
        if end is not None and frame.timestamp >= end:
            break
        sample = _sample(frame, snapshots.get(frame.frame_id))
        if sample is None:
            previous = None
            continue
        if (
            previous is not None
            and sample.video_time - previous.video_time <= max_gap_seconds
        ):
            delta = sample.lane_position - previous.lane_position
            classification = (
                "right"
                if delta > position_tolerance
                else "left"
                if delta < -position_tolerance
                else "no_detectable_change"
            )
            result.comparisons.append(
                TowerMarkerComparison(
                    earlier=previous,
                    later=sample,
                    delta=delta,
                    classification=classification,
                )
            )
        previous = sample
    return result


def _snapshots_by_frame(
    snapshots: list[GameStateSnapshot],
) -> dict[str, GameStateSnapshot]:
    """Reject ambiguous snapshot identities rather than selecting one."""
    result: dict[str, GameStateSnapshot] = {}
    for snapshot in snapshots:
        source = snapshot.source_frame
        if source is None:
            continue
        if source.frame_id in result:
            raise ValueError("duplicate Tower snapshot source frame")
        result[source.frame_id] = snapshot
    return result


def _sample(
    frame: VisionFrameResult, snapshot: GameStateSnapshot | None
) -> TowerMarkerSample | None:
    """Accept only same-frame, in-match, direct detector evidence."""
    if (
        snapshot is None
        or snapshot.source_frame is None
        or snapshot.source_frame.timestamp != frame.timestamp
        or snapshot.timestamp != frame.timestamp
        or snapshot.match_phase != "in_match"
        or snapshot.map_overlay_present is True
        or snapshot.tower_marker is None
        or not snapshot.tower_marker.usable
        or len(snapshot.tower_evidence_ids) != 1
    ):
        return None
    evidence_id = snapshot.tower_evidence_ids[0]
    for result in frame.detections:
        if (
            result.id == evidence_id
            and result.detector_name == "tower_control"
            and isinstance(result.reading, TowerControlReading)
            and result.reading.marker == snapshot.tower_marker
        ):
            return TowerMarkerSample(
                video_time=frame.timestamp,
                frame_id=frame.frame_id,
                evidence_id=evidence_id,
                lane_position=snapshot.tower_marker.lane_position,
            )
    return None
