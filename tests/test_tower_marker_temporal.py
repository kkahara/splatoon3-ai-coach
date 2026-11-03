"""Tower marker endpoint comparisons preserve gaps, bounds and provenance."""

import pytest

from splatoon3_ai_coach.analysis.tower_marker_temporal import (
    build_tower_marker_comparisons,
)
from splatoon3_ai_coach.vision.models import (
    AnalysisIdentity,
    DetectorResult,
    GameEvent,
    GameEventType,
    GameStateSnapshot,
    MatchIntroReading,
    SourceFrameReference,
    TowerControlReading,
    TowerMarkerReading,
    VisionFrameResult,
    VisionManifest,
)


def _manifest(positions: list[float | None]) -> VisionManifest:
    """Build a manifest containing direct observations and explicit gaps."""
    intro = VisionFrameResult(
        frame_id="intro",
        timestamp=0,
        source="cadence",
        detections=[
            DetectorResult(
                id="intro-result",
                detector_name="match_intro",
                detector_version="test",
                confidence=1,
                reading=MatchIntroReading(
                    stage_id="makomart",
                    battle_mode_id="tower_control",
                    stage_template_score=0.99,
                    battle_mode_template_score=0.99,
                ),
            )
        ],
    )
    frames = [intro]
    snapshots = []
    for index, position in enumerate(positions, 1):
        frame_id, result_id = f"frame-{index}", f"result-{index}"
        marker = (
            None
            if position is None
            else TowerMarkerReading(
                center_x=position,
                lane_position=position,
                bbox=(0.3, 0.1, 0.4, 0.2),
                usable=True,
            )
        )
        detections = (
            []
            if marker is None
            else [
                DetectorResult(
                    id=result_id,
                    detector_name="tower_control",
                    detector_version="test",
                    confidence=0.8,
                    reading=TowerControlReading(marker=marker),
                )
            ]
        )
        frames.append(
            VisionFrameResult(
                frame_id=frame_id,
                timestamp=index,
                source="cadence",
                detections=detections,
            )
        )
        snapshots.append(
            GameStateSnapshot(
                timestamp=index,
                match_phase="in_match",
                tower_marker=marker,
                tower_evidence_ids=[result_id] if marker else [],
                source_frame=SourceFrameReference(frame_id=frame_id, timestamp=index),
            )
        )
    return VisionManifest(
        analysis=AnalysisIdentity(
            analysis_id="a",
            package_version="test",
            vision_config_sha256="v",
            video_identity="video",
            detector_versions={"tower_control": "test"},
        ),
        video_identity="video",
        frame_results=frames,
        state_snapshots=snapshots,
    )


def test_endpoint_changes_and_missing_observations() -> None:
    """A gap breaks comparison; equal endpoints are only no detectable change."""
    result = build_tower_marker_comparisons(
        _manifest([0.3, 0.31, 0.36, None, 0.4, 0.35]),
        position_tolerance=0.011,
    )
    assert [item.classification for item in result.comparisons] == [
        "no_detectable_change",
        "right",
        "left",
    ]
    assert [(x.earlier.video_time, x.later.video_time) for x in result.comparisons] == [
        (1, 2),
        (2, 3),
        (5, 6),
    ]
    assert result.comparisons[0].earlier.evidence_id == "result-1"
    assert result.comparisons[0].later.frame_id == "frame-2"


def test_gap_stale_source_and_match_end_are_not_bridged() -> None:
    """Time gaps, inconsistent evidence and match end prevent comparisons."""
    manifest = _manifest([0.1, 0.2, 0.3, 0.4, 0.5])
    manifest.state_snapshots[1].tower_evidence_ids = ["stale"]
    manifest.game_events = [
        GameEvent(
            event_type=GameEventType.MATCH_END,
            start_time=5,
            confidence=1,
        )
    ]
    result = build_tower_marker_comparisons(manifest)
    assert [(x.earlier.video_time, x.later.video_time) for x in result.comparisons] == [
        (3, 4)
    ]
    assert not build_tower_marker_comparisons(manifest, max_gap_seconds=0.5).comparisons


def test_mode_detector_and_duplicate_gates() -> None:
    """Unavailable modes and ambiguous frame identity cannot yield facts."""
    manifest = _manifest([0.3, 0.4])
    manifest.analysis.detector_versions.clear()
    assert (
        build_tower_marker_comparisons(manifest).unavailable_reason
        == "detector_not_enabled"
    )
    manifest.analysis.detector_versions["tower_control"] = "test"
    manifest.frame_results.append(manifest.frame_results[-1])
    with pytest.raises(ValueError, match="unique"):
        build_tower_marker_comparisons(manifest)
