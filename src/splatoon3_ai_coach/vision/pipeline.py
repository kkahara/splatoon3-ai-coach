"""End-to-end cadence vision analysis pipeline (thin orchestrator)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from loguru import logger

from splatoon3_ai_coach.config.models import AppConfig, VisionLanguage
from splatoon3_ai_coach.exceptions import GameLanguageUndeterminedError
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
from splatoon3_ai_coach.vision.game_language import (
    GameLanguageProbeOutcome,
    probe_game_language,
)
from splatoon3_ai_coach.vision.game_language_result import (
    build_game_language_result,
    with_detected_language,
    write_game_language_diagnostics,
)
from splatoon3_ai_coach.vision.ids import compute_analysis_id
from splatoon3_ai_coach.vision.match_end import detect_match_end, match_end_event
from splatoon3_ai_coach.vision.match_side_effects import (
    MapInkScanContext,
    # Re-export underscore aliases used by tests.
    _detectors_for_frame,
    _finalize_ready_gated_map_artifacts,
    _maybe_calibrate_team_colors,
    _persist_map_artifacts,
    _should_run_ready_detector,
    _update_ready_gate_from_detections,
    build_map_ink_context,
    finalize_ready_gated_map_artifacts,
    persist_map_artifacts,
)
from splatoon3_ai_coach.vision.models import (
    GameEvent,
    GameLanguageResult,
    GameStateSnapshot,
    VisionFrameResult,
    VisionManifest,
)
from splatoon3_ai_coach.vision.provenance import detector_version, git_short_sha
from splatoon3_ai_coach.vision.rainmaker_checkpoint_observations import (
    collect_rainmaker_checkpoint_observations,
)
from splatoon3_ai_coach.vision.rainmaker_possession_observations import (
    collect_rainmaker_possession_observations,
)
from splatoon3_ai_coach.vision.registry import build_detectors
from splatoon3_ai_coach.vision.review_icon import ReviewIconTracker
from splatoon3_ai_coach.vision.state import fuse_game_state
from splatoon3_ai_coach.vision.timing_manifest import (
    CADENCE_EXTRACTION_SHA256,
    build_manifest,
    build_timing,
    log_completion,
)
from splatoon3_ai_coach.vision.tower_opponent_lost import (
    collect_opponent_lost_observations,
)
from splatoon3_ai_coach.vision.tower_took import collect_took_observations

# Re-exports for public / test API stability.
__all__ = [
    "CADENCE_EXTRACTION_SHA256",
    "CadenceScanStats",
    "MapInkScanContext",
    "observe_cadence_stream",
    "refuse_vision_manifest",
    "run_vision",
    "_detectors_for_frame",
    "_finalize_ready_gated_map_artifacts",
    "_maybe_calibrate_team_colors",
    "_persist_map_artifacts",
    "_should_run_ready_detector",
    "_update_ready_gate_from_detections",
]


@dataclass(frozen=True)
class _ResolvedIdentity:
    config: AppConfig
    video_identity: str
    vision_sha: str
    analysis_id: str
    game_language: GameLanguageResult


def _resolve_language_and_identity(
    video: Path,
    config: AppConfig,
    output_dir: Path,
    *,
    supplied_language: VisionLanguage | None,
    game_language: GameLanguageProbeOutcome | None,
) -> _ResolvedIdentity:
    """Resolve the game language, then derive identity from the detected value.

    The probe runs before any hashing. ``supplied_language`` never reaches the
    config, the hash or ``analysis_id``. Unresolved: write diagnostics, raise.
    """
    outcome = game_language
    if outcome is None:
        outcome = probe_game_language(
            video,
            config.vision,
            max_width=config.video.max_width,
            max_height=config.video.max_height,
        )
    if not outcome.resolved:
        result = build_game_language_result(
            outcome, output_dir, analysis_id=None, supplied_language=supplied_language
        )
        write_game_language_diagnostics(result, outcome, output_dir)
        raise GameLanguageUndeterminedError()
    effective = with_detected_language(config, outcome.language)
    vid_identity = video_identity(video)
    vision_sha = hash_vision_config(effective.vision)
    analysis_id = compute_analysis_id(vid_identity, CADENCE_EXTRACTION_SHA256, vision_sha)
    result = build_game_language_result(
        outcome, output_dir, analysis_id=analysis_id, supplied_language=supplied_language
    )
    write_game_language_diagnostics(result, outcome, output_dir)
    return _ResolvedIdentity(effective, vid_identity, vision_sha, analysis_id, result)


def run_vision(
    video: Path,
    config: AppConfig,
    output_dir: Path,
    *,
    debug_persist_cadence_frames: bool = False,
    video_source: VideoSource | None = None,
    supplied_language: VisionLanguage | None = None,
    game_language: GameLanguageProbeOutcome | None = None,
) -> VisionManifest:
    """Run cadence-only vision analysis and write a vision manifest.

    ``video_source`` overrides ``config.video.source`` when provided (CLI).
    ``supplied_language`` is a deprecated caller statement, recorded for
    comparison only. ``game_language`` injects a probe outcome (tests) instead
    of probing the video.

    Raises:
        GameLanguageUndeterminedError: When the game language stays unknown.
    """
    started = perf_counter()
    resolved = _resolve_language_and_identity(
        video,
        config,
        output_dir,
        supplied_language=supplied_language,
        game_language=game_language,
    )
    config = resolved.config
    vid_identity = resolved.video_identity
    vision_sha = resolved.vision_sha
    analysis_id = resolved.analysis_id
    detectors = build_detectors(config.vision)
    detector_versions = {
        detector.name: detector_version(detector.name) for detector in detectors
    }
    git_sha = git_short_sha()
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
        git_sha=git_sha,
    )
    manifest.game_language = resolved.game_language
    manifest.tower_opponent_lost_observations = collect_opponent_lost_observations(
        frame_results
    )
    manifest.tower_took_observations = collect_took_observations(frame_results)
    manifest.rainmaker_possession_observations = (
        collect_rainmaker_possession_observations(frame_results)
    )
    manifest.rainmaker_checkpoint_observations = (
        collect_rainmaker_checkpoint_observations(frame_results)
    )
    save_vision_manifest(manifest, output_dir)
    log_completion(manifest, timing)
    return manifest


def refuse_vision_manifest(manifest: VisionManifest, config: AppConfig) -> VisionManifest:
    """Re-run state fusion and event inference over stored frame readings.

    Detector readings are reused as recorded; only fusion and event settings
    from ``config`` take effect. No video is decoded.
    """
    snapshots, events = _interpret_observations(manifest.frame_results, config)
    analysis = manifest.analysis.model_copy(
        update={"refused_vision_config_sha256": hash_vision_config(config.vision)}
    )
    return manifest.model_copy(
        update={
            "analysis": analysis,
            "state_snapshots": snapshots,
            "game_events": events,
            "tower_opponent_lost_observations": collect_opponent_lost_observations(
                manifest.frame_results
            ),
            "tower_took_observations": collect_took_observations(manifest.frame_results),
            "rainmaker_possession_observations": (
                collect_rainmaker_possession_observations(manifest.frame_results)
            ),
            "rainmaker_checkpoint_observations": (
                collect_rainmaker_checkpoint_observations(manifest.frame_results)
            ),
        }
    )


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
        config.vision.score,
        config.vision.zone_control,
        config.vision.tower_control,
    )
    decision = detect_match_end(
        frame_results,
        config.vision.timer,
        config.vision.lifecycle,
        config.vision.match_end,
    )
    if decision is None:
        return snapshots, infer_events(snapshots, config.vision.events)
    logger.info("Match end at {:.1f}s ({})", decision.time, decision.reason.value)
    snapshots = [
        item.model_copy(
            update={
                "match_phase": "post_match",
                "basket_state": "unknown",
                "basket_state_evidence_ids": [],
                "clam_score": None,
                "tower_checkpoint_identity": None,
                "tower_checkpoint_state": None,
                "tower_control_state": "unknown",
                "tower_control_evidence": None,
                "rainmaker_score_reading": None,
                "rainmaker_score_association": None,
                "rainmaker_ally_remaining": None,
                "rainmaker_opponent_remaining": None,
                "rainmaker_score_evidence_ids": [],
                "rainmaker_possession_state": "unknown",
                "rainmaker_possession_evidence": None,
                "rainmaker_checkpoint_state": None,
            }
        )
        if item.timestamp >= decision.time
        else item
        for item in snapshots
    ]
    events = infer_events(
        snapshots, config.vision.events, match_end=match_end_event(decision)
    )
    return snapshots, events
