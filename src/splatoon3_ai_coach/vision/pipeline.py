"""End-to-end cadence vision analysis pipeline (thin orchestrator)."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

from loguru import logger

from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.media.video_identity import video_identity
from splatoon3_ai_coach.media.video_source import (
    VideoSource,
    analysis_frame_size,
    build_video_run_metadata,
)
from splatoon3_ai_coach.media.vision_manifest import (
    hash_vision_config,
    save_vision_manifest,
)
from splatoon3_ai_coach.vision.cadence_scan import (
    CadenceScanStats,
    observe_cadence_stream,
    scan_video,
)
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.ids import compute_analysis_id
from splatoon3_ai_coach.vision.match_side_effects import (
    MapInkScanContext,
    build_map_ink_context,
    finalize_ready_gated_map_artifacts,
    persist_map_artifacts,
    # Re-export underscore aliases used by tests.
    _detectors_for_frame,
    _finalize_ready_gated_map_artifacts,
    _maybe_calibrate_team_colors,
    _persist_map_artifacts,
    _should_run_ready_detector,
    _update_ready_gate_from_detections,
)
from splatoon3_ai_coach.vision.models import (
    GameEvent,
    GameStateSnapshot,
    VisionFrameResult,
    VisionManifest,
)
from splatoon3_ai_coach.vision.provenance import detector_version
from splatoon3_ai_coach.vision.registry import build_detectors
from splatoon3_ai_coach.vision.review_icon import ReviewIconTracker
from splatoon3_ai_coach.vision.state import fuse_game_state
from splatoon3_ai_coach.vision.timing_manifest import (
    CADENCE_EXTRACTION_SHA256,
    build_manifest,
    build_timing,
    log_completion,
)

# Re-exports for public / test API stability.
__all__ = [
    "CADENCE_EXTRACTION_SHA256",
    "CadenceScanStats",
    "MapInkScanContext",
    "observe_cadence_stream",
    "run_vision",
    "_detectors_for_frame",
    "_finalize_ready_gated_map_artifacts",
    "_maybe_calibrate_team_colors",
    "_persist_map_artifacts",
    "_should_run_ready_detector",
    "_update_ready_gate_from_detections",
]


def run_vision(
    video: Path,
    config: AppConfig,
    output_dir: Path,
    *,
    debug_persist_cadence_frames: bool = False,
    video_source: VideoSource | None = None,
) -> VisionManifest:
    """Run cadence-only vision analysis and write a vision manifest.

    ``video_source`` overrides ``config.video.source`` when provided (CLI).
    """
    started = perf_counter()
    vid_identity = video_identity(video)
    vision_sha = hash_vision_config(config.vision)
    analysis_id = compute_analysis_id(vid_identity, CADENCE_EXTRACTION_SHA256, vision_sha)
    detectors = build_detectors(config.vision)
    detector_versions = {
        detector.name: detector_version(detector.name) for detector in detectors
    }
    debug_dir = output_dir / "debug_snapshots" if debug_persist_cadence_frames else None
    map_ctx = build_map_ink_context(config, output_dir, debug_persist_cadence_frames)
    declared = video_source if video_source is not None else config.video.source
    review_tracker = ReviewIconTracker(config.vision.review_icon)

    frame_results, scan_stats, video_duration, video_meta = scan_video(
        video,
        config,
        detectors,
        analysis_id,
        detector_versions,
        debug_dir,
        output_dir,
        map_ctx,
        review_tracker,
    )
    map_ctx.identity.close_intro(video_duration)
    finalize_ready_gated_map_artifacts(map_ctx)
    persist_map_artifacts(map_ctx, output_dir)
    temporal_started = perf_counter()
    state_snapshots, game_events = _interpret_observations(frame_results, config)
    timing = build_timing(
        video_duration,
        scan_stats,
        temporal_seconds=perf_counter() - temporal_started,
        total_seconds=perf_counter() - started,
    )
    if declared is VideoSource.REVIEW and review_tracker.hit is None:
        logger.warning(
            "Declared video source=review but Review icon was not detected "
            "within {:.1f}s (icon may be cropped or late)",
            config.vision.review_icon.deadline_seconds,
        )
    hit = review_tracker.hit
    aw, ah = analysis_frame_size(
        video_meta.width,
        video_meta.height,
        max_width=config.video.max_width,
        max_height=config.video.max_height,
    )
    run_video = build_video_run_metadata(
        declared=declared,
        review_icon_detected=hit is not None,
        review_icon_video_time=None if hit is None else hit.video_time,
        review_icon_score=None if hit is None else hit.score,
        original_width=video_meta.width,
        original_height=video_meta.height,
        analysis_width=aw,
        analysis_height=ah,
    )
    manifest = build_manifest(
        analysis_id,
        vid_identity,
        vision_sha,
        detector_versions,
        frame_results,
        state_snapshots,
        game_events,
        timing,
        language=config.vision.language.value,
        video=run_video,
    )
    save_vision_manifest(manifest, output_dir)
    log_completion(manifest, timing)
    return manifest


def _interpret_observations(
    frame_results: list[VisionFrameResult],
    config: AppConfig,
) -> tuple[list[GameStateSnapshot], list[GameEvent]]:
    """Fuse cadence readings into snapshots, then infer gameplay events."""
    snapshots = fuse_game_state(
        frame_results,
        config.vision.timer,
        config.vision.state_fusion,
        config.vision.death,
        config.vision.splat,
        config.vision.respawn,
        config.vision.active_gameplay,
        config.vision.lifecycle,
        config.vision.map_overlay,
        config.vision.player_count,
        config.vision.low_ink,
    )
    return snapshots, infer_events(snapshots, config.vision.events)
