"""End-to-end vision analysis pipeline."""

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.extraction.models import ExtractionManifest
from splatoon3_ai_coach.media.manifest import load_manifest
from splatoon3_ai_coach.media.video import VideoFrame, VideoLoader
from splatoon3_ai_coach.media.video_identity import video_identity
from splatoon3_ai_coach.media.vision_manifest import (
    hash_extraction_manifest,
    hash_vision_config,
    save_vision_manifest,
)
from splatoon3_ai_coach.paths import portable_path
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.ids import (
    compute_analysis_id,
    make_frame_id,
    make_result_id,
)
from splatoon3_ai_coach.vision.models import (
    AnalysisIdentity,
    DetectorResult,
    VisionFrameResult,
    VisionManifest,
)
from splatoon3_ai_coach.vision.provenance import detector_version, package_version
from splatoon3_ai_coach.vision.registry import build_detectors
from splatoon3_ai_coach.vision.state import fuse_game_state


@dataclass(frozen=True)
class ScheduledFrame:
    """A frame scheduled for vision analysis."""

    timestamp: float
    source: str
    source_frame_index: int | None
    source_pts: int | None
    source_time_base_num: int | None
    source_time_base_den: int | None
    frame_path: str | None
    image: np.ndarray


def run_vision(
    video: Path,
    config: AppConfig,
    extraction_manifest_path: Path,
    output_dir: Path,
    *,
    debug_persist_cadence_frames: bool = False,
) -> VisionManifest:
    """Run Phase 3 vision analysis and write a vision manifest."""
    extraction_manifest = load_manifest(extraction_manifest_path)
    vid_identity = video_identity(video)
    extraction_sha = hash_extraction_manifest(extraction_manifest_path)
    vision_sha = hash_vision_config(config.vision)
    analysis_id = compute_analysis_id(vid_identity, extraction_sha, vision_sha)

    scheduled = _schedule_frames(
        video,
        config,
        extraction_manifest,
        extraction_manifest_path.parent,
        debug_persist_cadence_frames,
        output_dir,
    )
    detectors = build_detectors(config.vision)
    detector_versions = {
        detector.name: detector_version(detector.name) for detector in detectors
    }

    frame_results: list[VisionFrameResult] = []
    for scheduled_frame in scheduled:
        frame_id = make_frame_id(
            analysis_id,
            scheduled_frame.source_frame_index,
            scheduled_frame.timestamp,
        )
        detections: list[DetectorResult] = []
        for detector in detectors:
            if scheduled_frame.source == "cadence" and detector.cadence_fps is None:
                continue
            if scheduled_frame.source == "evidence" and not detector.run_on_evidence:
                continue

            reading, score = detector.detect(
                scheduled_frame.image,
                scheduled_frame.timestamp,
            )
            if reading is None:
                continue
            version = detector_versions[detector.name]
            detections.append(
                DetectorResult(
                    id=make_result_id(
                        analysis_id,
                        scheduled_frame.source_frame_index,
                        scheduled_frame.timestamp,
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

        frame_results.append(
            VisionFrameResult(
                frame_id=frame_id,
                timestamp=scheduled_frame.timestamp,
                source=scheduled_frame.source,  # type: ignore[arg-type]
                source_frame_index=scheduled_frame.source_frame_index,
                source_pts=scheduled_frame.source_pts,
                source_time_base_num=scheduled_frame.source_time_base_num,
                source_time_base_den=scheduled_frame.source_time_base_den,
                frame_path=scheduled_frame.frame_path,
                detections=detections,
            )
        )

    state_snapshots = fuse_game_state(
        frame_results,
        config.vision.timer,
        config.vision.state_fusion,
        config.vision.death,
        config.vision.splat,
        config.vision.respawn,
        config.vision.active_gameplay,
        config.vision.lifecycle,
    )
    game_events = infer_events(state_snapshots, config.vision.events)

    analysis = AnalysisIdentity(
        analysis_id=analysis_id,
        package_version=package_version(),
        detector_versions=detector_versions,
        extraction_manifest_sha256=extraction_sha,
        vision_config_sha256=vision_sha,
        video_identity=vid_identity,
    )
    manifest = VisionManifest(
        analysis=analysis,
        video_identity=vid_identity,
        extraction_manifest_path=portable_path(
            extraction_manifest_path,
            output_dir,
        ),
        frame_results=frame_results,
        state_snapshots=state_snapshots,
        game_events=game_events,
    )
    save_vision_manifest(manifest, output_dir)
    logger.info(
        "Vision complete: {} frames, {} state snapshots, {} events",
        len(frame_results),
        len(state_snapshots),
        len(game_events),
    )
    return manifest


def _schedule_frames(
    video: Path,
    config: AppConfig,
    extraction_manifest: ExtractionManifest,
    manifest_dir: Path,
    debug_persist_cadence: bool,
    output_dir: Path,
) -> list[ScheduledFrame]:
    """Build deduplicated evidence and cadence frame schedule."""
    evidence_by_index: dict[int, ScheduledFrame] = {}
    evidence_by_time: list[ScheduledFrame] = []

    for manifest_frame in extraction_manifest.frames:
        image = cv2.imread(str(manifest_frame.path))
        if image is None:
            continue
        scheduled = ScheduledFrame(
            timestamp=manifest_frame.timestamp,
            source="evidence",
            source_frame_index=manifest_frame.source_frame_index,
            source_pts=manifest_frame.source_pts,
            source_time_base_num=manifest_frame.source_time_base_num,
            source_time_base_den=manifest_frame.source_time_base_den,
            frame_path=portable_path(manifest_frame.path, output_dir),
            image=image,
        )
        evidence_by_index[manifest_frame.source_frame_index] = scheduled
        evidence_by_time.append(scheduled)

    tolerance = config.vision.state_fusion.dedupe_tolerance_seconds
    cadence_step = 1.0 / config.vision.hud_cadence_fps
    scheduled: list[ScheduledFrame] = list(evidence_by_index.values())
    seen_indices = set(evidence_by_index.keys())
    last_cadence = -float("inf")

    cadence_dir = output_dir / "cadence_frames"
    if debug_persist_cadence:
        cadence_dir.mkdir(parents=True, exist_ok=True)

    with VideoLoader(
        video,
        max_width=config.video.max_width,
        max_height=config.video.max_height,
    ) as loader:
        for video_frame in loader.frames():
            if video_frame.timestamp - last_cadence < cadence_step:
                continue
            last_cadence = video_frame.timestamp

            if video_frame.frame_index in seen_indices:
                continue

            if _matches_evidence_by_time(video_frame, evidence_by_time, tolerance):
                continue

            frame_path = None
            if debug_persist_cadence:
                frame_path = portable_path(
                    _write_cadence_frame(cadence_dir, video_frame),
                    output_dir,
                )

            scheduled.append(
                ScheduledFrame(
                    timestamp=video_frame.timestamp,
                    source="cadence",
                    source_frame_index=video_frame.frame_index,
                    source_pts=video_frame.source_pts,
                    source_time_base_num=video_frame.source_time_base_num,
                    source_time_base_den=video_frame.source_time_base_den,
                    frame_path=frame_path,
                    image=video_frame.image.copy(),
                )
            )

    scheduled.sort(key=lambda item: (item.timestamp, item.source_frame_index or -1))
    return scheduled


def _matches_evidence_by_time(
    video_frame: VideoFrame,
    evidence_frames: list[ScheduledFrame],
    tolerance: float,
) -> bool:
    """Return whether a cadence frame overlaps an evidence frame by timestamp."""
    for evidence in evidence_frames:
        if abs(evidence.timestamp - video_frame.timestamp) <= tolerance:
            return True
    return False


def _write_cadence_frame(output_dir: Path, frame: VideoFrame) -> Path:
    """Persist one cadence debug frame."""
    path = output_dir / f"{frame.frame_index:08d}_{frame.timestamp:010.3f}.jpg"
    cv2.imwrite(str(path), frame.image)
    return path
