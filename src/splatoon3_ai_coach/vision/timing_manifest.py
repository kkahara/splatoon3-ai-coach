"""Assemble vision timing metrics and persisted manifests."""

from __future__ import annotations

from loguru import logger

from splatoon3_ai_coach.media.video_source import VideoRunMetadata
from splatoon3_ai_coach.vision.cadence_scan import CadenceScanStats
from splatoon3_ai_coach.vision.models import (
    AnalysisIdentity,
    GameEvent,
    GameStateSnapshot,
    VisionFrameResult,
    VisionManifest,
    VisionTimingMetrics,
)
from splatoon3_ai_coach.vision.provenance import package_version

# Cadence-only runs keep the existing three-input analysis ID scheme
# (video, extraction hash, vision config) with an empty extraction payload.
CADENCE_EXTRACTION_SHA256 = ""


def build_timing(
    video_duration: float,
    stats: CadenceScanStats,
    *,
    temporal_seconds: float,
    total_seconds: float,
) -> VisionTimingMetrics:
    """Assemble persisted timing metrics for a cadence analysis run."""
    detector_times = stats.detector_seconds
    invocations = stats.detector_invocations
    return VisionTimingMetrics(
        video_duration_seconds=video_duration,
        decoded_frame_count=stats.decoded_frame_count,
        cadence_frame_count=stats.cadence_frame_count,
        decode_seconds=stats.decode_seconds,
        timer_detector_seconds=detector_times.get("timer", 0.0),
        death_detector_seconds=detector_times.get("death", 0.0),
        splat_detector_seconds=detector_times.get("splat", 0.0),
        respawn_detector_seconds=detector_times.get("respawn", 0.0),
        active_gameplay_detector_seconds=detector_times.get("active_gameplay", 0.0),
        map_overlay_detector_seconds=detector_times.get("map_overlay", 0.0),
        player_count_detector_seconds=detector_times.get("player_count", 0.0),
        special_gauge_detector_seconds=detector_times.get("special_gauge", 0.0),
        ready_detector_seconds=detector_times.get("ready", 0.0),
        match_intro_detector_seconds=detector_times.get("match_intro", 0.0),
        timer_detector_invocations=invocations.get("timer", 0),
        death_detector_invocations=invocations.get("death", 0),
        splat_detector_invocations=invocations.get("splat", 0),
        respawn_detector_invocations=invocations.get("respawn", 0),
        active_gameplay_detector_invocations=invocations.get("active_gameplay", 0),
        map_overlay_detector_invocations=invocations.get("map_overlay", 0),
        player_count_detector_invocations=invocations.get("player_count", 0),
        special_gauge_detector_invocations=invocations.get("special_gauge", 0),
        ready_detector_invocations=invocations.get("ready", 0),
        match_intro_detector_invocations=invocations.get("match_intro", 0),
        temporal_seconds=temporal_seconds,
        total_seconds=total_seconds,
    )


def build_manifest(
    analysis_id: str,
    vid_identity: str,
    vision_sha: str,
    detector_versions: dict[str, str],
    frame_results: list[VisionFrameResult],
    state_snapshots: list[GameStateSnapshot],
    game_events: list[GameEvent],
    timing: VisionTimingMetrics,
    *,
    language: str,
    video: VideoRunMetadata | None = None,
) -> VisionManifest:
    """Assemble the persisted vision manifest."""
    analysis = AnalysisIdentity(
        analysis_id=analysis_id,
        package_version=package_version(),
        detector_versions=detector_versions,
        extraction_manifest_sha256=CADENCE_EXTRACTION_SHA256,
        vision_config_sha256=vision_sha,
        video_identity=vid_identity,
        language=language,
    )
    return VisionManifest(
        analysis=analysis,
        video_identity=vid_identity,
        extraction_manifest_path=None,
        frame_results=frame_results,
        state_snapshots=state_snapshots,
        game_events=game_events,
        timing=timing,
        video=video,
    )


def log_completion(manifest: VisionManifest, timing: VisionTimingMetrics) -> None:
    """Log frame counts and cadence timing."""
    logger.info(
        "Vision complete: {} frames, {} state snapshots, {} events",
        len(manifest.frame_results),
        len(manifest.state_snapshots),
        len(manifest.game_events),
    )
    logger.info(
        "Vision timing: decoded={} cadence={} decode={:.3f}s "
        "timer={:.3f}s/{} death={:.3f}s/{} splat={:.3f}s/{} respawn={:.3f}s/{} "
        "active={:.3f}s/{} map={:.3f}s/{} players={:.3f}s/{} special={:.3f}s/{} "
        "temporal={:.3f}s total={:.3f}s realtime_factor={:.3f}",
        timing.decoded_frame_count,
        timing.cadence_frame_count,
        timing.decode_seconds,
        timing.timer_detector_seconds,
        timing.timer_detector_invocations,
        timing.death_detector_seconds,
        timing.death_detector_invocations,
        timing.splat_detector_seconds,
        timing.splat_detector_invocations,
        timing.respawn_detector_seconds,
        timing.respawn_detector_invocations,
        timing.active_gameplay_detector_seconds,
        timing.active_gameplay_detector_invocations,
        timing.map_overlay_detector_seconds,
        timing.map_overlay_detector_invocations,
        timing.player_count_detector_seconds,
        timing.player_count_detector_invocations,
        timing.special_gauge_detector_seconds,
        timing.special_gauge_detector_invocations,
        timing.temporal_seconds,
        timing.total_seconds,
        timing.realtime_factor,
    )


_build_timing = build_timing
_build_manifest = build_manifest
_log_completion = log_completion
