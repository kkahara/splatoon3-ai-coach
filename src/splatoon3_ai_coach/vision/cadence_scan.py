"""Cadence frame sampling and per-frame detector execution."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter

import cv2

from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.extraction.sampler import FrameSampler
from splatoon3_ai_coach.media.video import VideoFrame, VideoLoader, VideoMetadata
from splatoon3_ai_coach.paths import portable_path
from splatoon3_ai_coach.vision.base import BaseDetector
from splatoon3_ai_coach.vision.ids import make_frame_id, make_result_id
from splatoon3_ai_coach.vision.match_side_effects import (
    MapInkScanContext,
    detectors_for_frame,
    maybe_calibrate_team_colors,
    maybe_sample_map_ink,
    update_identity_from_detections,
    update_ready_gate_from_detections,
)
from splatoon3_ai_coach.vision.models import DetectorResult, VisionFrameResult
from splatoon3_ai_coach.vision.review_icon import ReviewIconTracker


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
    review_tracker: ReviewIconTracker | None = None,
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
        if review_tracker is not None:
            review_tracker.observe(
                video_frame.image, video_time=video_frame.timestamp
            )
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


def scan_video(
    video: Path,
    config: AppConfig,
    detectors: Sequence[BaseDetector],
    analysis_id: str,
    detector_versions: dict[str, str],
    debug_dir: Path | None,
    output_dir: Path,
    map_ctx: MapInkScanContext | None,
    review_tracker: ReviewIconTracker,
) -> tuple[list[VisionFrameResult], CadenceScanStats, float, VideoMetadata]:
    """Decode the video once and observe cadence frames in memory."""
    with VideoLoader(
        video,
        max_width=config.video.max_width,
        max_height=config.video.max_height,
    ) as loader:
        meta = loader.open()
        duration = meta.duration_seconds
        results, stats = observe_cadence_stream(
            loader.frames(),
            detectors,
            cadence_fps=config.vision.hud_cadence_fps,
            analysis_id=analysis_id,
            detector_versions=detector_versions,
            debug_dir=debug_dir,
            output_dir=output_dir,
            map_ctx=map_ctx,
            review_tracker=review_tracker,
        )
        stats.decode_seconds = loader.decode_seconds
        stats.decoded_frame_count = loader.decoded_frame_count
    return results, stats, duration, meta


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
    active = detectors_for_frame(detectors, map_ctx, video_frame.timestamp)
    detections = run_detectors(
        video_frame,
        active,
        analysis_id,
        detector_versions,
        stats,
    )
    if map_ctx is not None:
        update_identity_from_detections(map_ctx, detections, video_frame.timestamp)
        update_ready_gate_from_detections(map_ctx, detections)
        maybe_calibrate_team_colors(map_ctx, video_frame, detections)
        maybe_sample_map_ink(map_ctx, video_frame, detections)
    frame_path = optional_debug_snapshot(video_frame, debug_dir, output_dir)
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


def run_detectors(
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


def optional_debug_snapshot(
    video_frame: VideoFrame,
    debug_dir: Path | None,
    output_dir: Path | None,
) -> str | None:
    """Write an already-observed cadence frame when debug persistence is on."""
    if debug_dir is None:
        return None
    snapshot = write_debug_snapshot(debug_dir, video_frame)
    if output_dir is None:
        return snapshot.name
    return portable_path(snapshot, output_dir)


def write_debug_snapshot(output_dir: Path, frame: VideoFrame) -> Path:
    """Persist a cadence frame that was already decoded and observed."""
    path = output_dir / f"{frame.frame_index:08d}_{frame.timestamp:010.3f}.jpg"
    cv2.imwrite(str(path), frame.image)
    return path


# Backward-compatible aliases.
_scan_video = scan_video
_run_detectors = run_detectors
