"""End-to-end cadence vision analysis pipeline."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

import cv2
from loguru import logger

from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.extraction.sampler import FrameSampler
from splatoon3_ai_coach.media.video import VideoFrame, VideoLoader
from splatoon3_ai_coach.media.video_identity import video_identity
from splatoon3_ai_coach.media.vision_manifest import (
    hash_vision_config,
    save_vision_manifest,
)
from splatoon3_ai_coach.paths import portable_path
from splatoon3_ai_coach.vision.base import BaseDetector
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.ids import (
    compute_analysis_id,
    make_frame_id,
    make_result_id,
)
from splatoon3_ai_coach.vision.map_ink import (
    MAP_OBSERVATIONS_FILENAME,
    MapInkClassifier,
    MapObservation,
    analyze_map_ink,
    write_map_ink_diagnostic,
    write_map_observations,
)
from splatoon3_ai_coach.vision.match_intro import MatchIdentityTracker
from splatoon3_ai_coach.vision.models import (
    AnalysisIdentity,
    DetectorResult,
    GameEvent,
    GameStateSnapshot,
    MapOverlayReading,
    MatchIntroReading,
    VisionFrameResult,
    VisionManifest,
    VisionTimingMetrics,
)
from splatoon3_ai_coach.vision.provenance import detector_version, package_version
from splatoon3_ai_coach.vision.registry import build_detectors
from splatoon3_ai_coach.vision.stage_maps import (
    MATCH_IDENTITY_FILENAME,
    resolve_stage_map_geometry,
)
from splatoon3_ai_coach.vision.state import fuse_game_state

# Cadence-only runs keep the existing three-input analysis ID scheme
# (video, extraction hash, vision config) with an empty extraction payload.
CADENCE_EXTRACTION_SHA256 = ""


@dataclass
class CadenceScanStats:
    """In-process counters for one cadence scan.

    ``decode_seconds`` is ingest cost copied from ``VideoLoader``: PyAV
    retrieval, BGR conversion, and optional downscale. Fake-frame tests
    leave it at 0.
    """

    decoded_frame_count: int = 0
    cadence_frame_count: int = 0
    decode_seconds: float = 0.0
    detector_seconds: dict[str, float] = field(default_factory=dict)
    detector_invocations: dict[str, int] = field(default_factory=dict)


@dataclass
class MapInkScanContext:
    """Mutable intro identity + sparse map-ink observations for one run."""

    identity: MatchIdentityTracker
    classifier: MapInkClassifier | None
    observations: list[MapObservation] = field(default_factory=list)
    last_sample_at: float | None = None
    diagnostics_dir: Path | None = None
    config: AppConfig | None = None


def run_vision(
    video: Path,
    config: AppConfig,
    output_dir: Path,
    *,
    debug_persist_cadence_frames: bool = False,
) -> VisionManifest:
    """Run cadence-only vision analysis and write a vision manifest."""
    started = perf_counter()
    vid_identity = video_identity(video)
    vision_sha = hash_vision_config(config.vision)
    analysis_id = compute_analysis_id(vid_identity, CADENCE_EXTRACTION_SHA256, vision_sha)
    detectors = build_detectors(config.vision)
    detector_versions = {
        detector.name: detector_version(detector.name) for detector in detectors
    }
    debug_dir = output_dir / "debug_snapshots" if debug_persist_cadence_frames else None
    map_ctx = _build_map_ink_context(config, output_dir, debug_persist_cadence_frames)

    frame_results, scan_stats, video_duration = _scan_video(
        video,
        config,
        detectors,
        analysis_id,
        detector_versions,
        debug_dir,
        output_dir,
        map_ctx,
    )
    map_ctx.identity.close_intro(video_duration)
    _persist_map_artifacts(map_ctx, output_dir)
    temporal_started = perf_counter()
    state_snapshots, game_events = _interpret_observations(frame_results, config)
    timing = _build_timing(
        video_duration,
        scan_stats,
        temporal_seconds=perf_counter() - temporal_started,
        total_seconds=perf_counter() - started,
    )
    manifest = _build_manifest(
        analysis_id,
        vid_identity,
        vision_sha,
        detector_versions,
        frame_results,
        state_snapshots,
        game_events,
        timing,
        language=config.vision.language.value,
    )
    save_vision_manifest(manifest, output_dir)
    _log_completion(manifest, timing)
    return manifest


def observe_cadence_stream(
    frames: Iterator[VideoFrame],
    detectors: Sequence[BaseDetector],
    *,
    cadence_fps: float,
    analysis_id: str,
    detector_versions: dict[str, str],
    debug_dir: Path | None = None,
    output_dir: Path | None = None,
    map_ctx: MapInkScanContext | None = None,
) -> tuple[list[VisionFrameResult], CadenceScanStats]:
    """Sample a decoded stream at ``cadence_fps`` and run all detectors per frame.

    Each cadence frame is decoded once. All detectors receive that same in-memory
    image. ``frame_path`` stays ``None`` unless ``debug_dir`` is set.
    """
    stats = CadenceScanStats()
    sampler = FrameSampler(1.0 / cadence_fps)
    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)

    results: list[VisionFrameResult] = []
    for video_frame in sampler.sample(_pulled_frames(frames, stats)):
        results.append(
            _observe_cadence_frame(
                video_frame,
                detectors,
                analysis_id,
                detector_versions,
                stats,
                debug_dir,
                output_dir,
                map_ctx,
            )
        )
        stats.cadence_frame_count += 1
    return results, stats


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
    )
    return snapshots, infer_events(snapshots, config.vision.events)


def _scan_video(
    video: Path,
    config: AppConfig,
    detectors: Sequence[BaseDetector],
    analysis_id: str,
    detector_versions: dict[str, str],
    debug_dir: Path | None,
    output_dir: Path,
    map_ctx: MapInkScanContext | None,
) -> tuple[list[VisionFrameResult], CadenceScanStats, float]:
    """Decode the video once and observe cadence frames in memory."""
    with VideoLoader(
        video,
        max_width=config.video.max_width,
        max_height=config.video.max_height,
    ) as loader:
        duration = loader.open().duration_seconds
        results, stats = observe_cadence_stream(
            loader.frames(),
            detectors,
            cadence_fps=config.vision.hud_cadence_fps,
            analysis_id=analysis_id,
            detector_versions=detector_versions,
            debug_dir=debug_dir,
            output_dir=output_dir,
            map_ctx=map_ctx,
        )
        stats.decode_seconds = loader.decode_seconds
        stats.decoded_frame_count = loader.decoded_frame_count
    return results, stats, duration


def _pulled_frames(
    frames: Iterator[VideoFrame],
    stats: CadenceScanStats,
) -> Iterator[VideoFrame]:
    """Count frames taken from an already-decoded iterator. Does not time them."""
    for frame in frames:
        stats.decoded_frame_count += 1
        yield frame


def _observe_cadence_frame(
    video_frame: VideoFrame,
    detectors: Sequence[BaseDetector],
    analysis_id: str,
    detector_versions: dict[str, str],
    stats: CadenceScanStats,
    debug_dir: Path | None,
    output_dir: Path | None,
    map_ctx: MapInkScanContext | None,
) -> VisionFrameResult:
    """Run detectors on one cadence frame; optionally sample map ink."""
    active = _detectors_for_frame(detectors, map_ctx, video_frame.timestamp)
    detections = _run_detectors(
        video_frame,
        active,
        analysis_id,
        detector_versions,
        stats,
    )
    if map_ctx is not None:
        _update_identity_from_detections(map_ctx, detections, video_frame.timestamp)
        _maybe_sample_map_ink(map_ctx, video_frame, detections)
    frame_path = _optional_debug_snapshot(video_frame, debug_dir, output_dir)
    return VisionFrameResult(
        frame_id=make_frame_id(
            analysis_id,
            video_frame.frame_index,
            video_frame.timestamp,
        ),
        timestamp=video_frame.timestamp,
        source="cadence",
        source_frame_index=video_frame.frame_index,
        source_pts=video_frame.source_pts,
        source_time_base_num=video_frame.source_time_base_num,
        source_time_base_den=video_frame.source_time_base_den,
        frame_path=frame_path,
        detections=detections,
    )


def _detectors_for_frame(
    detectors: Sequence[BaseDetector],
    map_ctx: MapInkScanContext | None,
    video_time: float,
) -> list[BaseDetector]:
    """Skip match_intro once identity is resolved or intro window closed."""
    if map_ctx is None or map_ctx.identity.should_run_intro_detector(video_time):
        return list(detectors)
    return [d for d in detectors if d.name != "match_intro"]


def _update_identity_from_detections(
    map_ctx: MapInkScanContext,
    detections: list[DetectorResult],
    video_time: float,
) -> None:
    """Latch stage/mode from match_intro readings."""
    for det in detections:
        if det.detector_name != "match_intro":
            continue
        reading = det.reading
        if not isinstance(reading, MatchIntroReading):
            continue
        map_ctx.identity.update(reading, video_time=video_time)


def _maybe_sample_map_ink(
    map_ctx: MapInkScanContext,
    video_frame: VideoFrame,
    detections: list[DetectorResult],
) -> None:
    """Emit a MapObservation only while MAP_OVERLAY is present and identity known.

    Does not interpolate. Does not create GameEvents.
    """
    cfg = map_ctx.config
    if cfg is None or not cfg.vision.map_ink.enabled:
        return
    if not map_ctx.identity.identity.map_ink_enabled:
        return
    if map_ctx.classifier is None:
        return
    overlay = _map_overlay_present(detections)
    if not overlay:
        return
    interval = cfg.vision.map_ink.sample_interval_seconds
    t = float(video_frame.timestamp)
    if map_ctx.last_sample_at is not None and (t - map_ctx.last_sample_at) < interval:
        return
    stage_id = map_ctx.identity.identity.stage_id
    mode_id = map_ctx.identity.identity.battle_mode_id
    if stage_id is None:
        return
    geometry = resolve_stage_map_geometry(
        cfg.vision.map_ink.geometry_dir,
        stage_id=stage_id,
        battle_mode_id=mode_id,
    )
    if geometry is None:
        return
    evidence_ids = [
        d.id for d in detections if d.detector_name == "map_overlay"
    ]
    observation = analyze_map_ink(
        video_frame.image,
        geometry,
        map_ctx.classifier,
        video_time=t,
        battle_mode_id=mode_id,
        evidence_ids=evidence_ids,
    )
    if observation.confidence < cfg.vision.map_ink.min_usable_confidence:
        return
    map_ctx.observations.append(observation)
    map_ctx.last_sample_at = t
    if map_ctx.diagnostics_dir is not None:
        write_map_ink_diagnostic(
            map_ctx.diagnostics_dir,
            image=video_frame.image,
            observation=observation,
            geometry=geometry,
            classifier=map_ctx.classifier,
        )


def _map_overlay_present(detections: list[DetectorResult]) -> bool:
    """True when this frame's map_overlay reading asserts present."""
    for det in detections:
        if det.detector_name != "map_overlay":
            continue
        reading = det.reading
        if isinstance(reading, MapOverlayReading) and reading.present:
            return True
    return False


def _build_map_ink_context(
    config: AppConfig,
    output_dir: Path,
    debug_persist: bool,
) -> MapInkScanContext:
    """Create intro latch + optional classifier for this analyze run."""
    intro_cfg = config.vision.match_intro
    identity = MatchIdentityTracker(
        intro_deadline_seconds=intro_cfg.intro_deadline_seconds
    )
    classifier = None
    diagnostics = None
    if config.vision.map_ink.enabled:
        classifier = MapInkClassifier(config.vision.map_ink)
        if config.vision.map_ink.write_diagnostics or debug_persist:
            diagnostics = output_dir / "debug_map_ink"
    return MapInkScanContext(
        identity=identity,
        classifier=classifier,
        diagnostics_dir=diagnostics,
        config=config,
    )


def _persist_map_artifacts(map_ctx: MapInkScanContext, output_dir: Path) -> None:
    """Write match_identity.json and map_observations.json sidecars."""
    output_dir.mkdir(parents=True, exist_ok=True)
    identity = map_ctx.identity.identity
    payload = {
        "stage_id": identity.stage_id,
        "battle_mode_id": identity.battle_mode_id,
        "stage_score": identity.stage_score,
        "battle_mode_score": identity.battle_mode_score,
        "resolved": identity.resolved,
        "resolved_at": identity.resolved_at,
        "intro_closed": identity.intro_closed,
        "map_ink_enabled": identity.map_ink_enabled,
    }
    path = output_dir / MATCH_IDENTITY_FILENAME
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if map_ctx.config is not None and map_ctx.config.vision.map_ink.enabled:
        write_map_observations(output_dir / MAP_OBSERVATIONS_FILENAME, map_ctx.observations)



def _run_detectors(
    video_frame: VideoFrame,
    detectors: Sequence[BaseDetector],
    analysis_id: str,
    detector_versions: dict[str, str],
    stats: CadenceScanStats,
) -> list[DetectorResult]:
    """Run all detectors against the same in-memory frame image."""
    detections: list[DetectorResult] = []
    for detector in detectors:
        started = perf_counter()
        reading, score = detector.detect(video_frame.image, video_frame.timestamp)
        elapsed = perf_counter() - started
        stats.detector_seconds[detector.name] = (
            stats.detector_seconds.get(detector.name, 0.0) + elapsed
        )
        stats.detector_invocations[detector.name] = (
            stats.detector_invocations.get(detector.name, 0) + 1
        )
        if reading is None:
            continue
        version = detector_versions[detector.name]
        detections.append(
            DetectorResult(
                id=make_result_id(
                    analysis_id,
                    video_frame.frame_index,
                    video_frame.timestamp,
                    detector.name,
                    version,
                    reading,
                ),
                detector_name=detector.name,
                detector_version=version,
                confidence=score,
                reading=reading,
            )
        )
    return detections


def _optional_debug_snapshot(
    video_frame: VideoFrame,
    debug_dir: Path | None,
    output_dir: Path | None,
) -> str | None:
    """Write an already-observed cadence frame when debug persistence is on."""
    if debug_dir is None:
        return None
    snapshot = _write_debug_snapshot(debug_dir, video_frame)
    if output_dir is None:
        return snapshot.name
    return portable_path(snapshot, output_dir)


def _write_debug_snapshot(output_dir: Path, frame: VideoFrame) -> Path:
    """Persist a cadence frame that was already decoded and observed."""
    path = output_dir / f"{frame.frame_index:08d}_{frame.timestamp:010.3f}.jpg"
    cv2.imwrite(str(path), frame.image)
    return path


def _build_timing(
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
        timer_detector_invocations=invocations.get("timer", 0),
        death_detector_invocations=invocations.get("death", 0),
        splat_detector_invocations=invocations.get("splat", 0),
        respawn_detector_invocations=invocations.get("respawn", 0),
        active_gameplay_detector_invocations=invocations.get("active_gameplay", 0),
        map_overlay_detector_invocations=invocations.get("map_overlay", 0),
        player_count_detector_invocations=invocations.get("player_count", 0),
        temporal_seconds=temporal_seconds,
        total_seconds=total_seconds,
    )


def _build_manifest(
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
    )


def _log_completion(manifest: VisionManifest, timing: VisionTimingMetrics) -> None:
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
        "active={:.3f}s/{} map={:.3f}s/{} players={:.3f}s/{} temporal={:.3f}s "
        "total={:.3f}s realtime_factor={:.3f}",
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
        timing.temporal_seconds,
        timing.total_seconds,
        timing.realtime_factor,
    )
