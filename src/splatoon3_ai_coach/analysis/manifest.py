"""Writing extraction results to disk."""

from pathlib import Path

import cv2
from loguru import logger

from splatoon3_ai_coach.analysis.models import (
    ExtractionManifest,
    ExtractionResult,
    ManifestFrame,
    SelectedFrame,
)

MANIFEST_FILENAME = "manifest.json"


def frame_filename(frame: SelectedFrame, index: int) -> str:
    """Build a sortable, self-describing filename for a saved frame."""
    return f"{frame.timestamp:010.3f}_{frame.event_type.value}_{index:04d}.jpg"


def save_manifest(
    video: Path,
    result: ExtractionResult,
    output_dir: Path,
    jpeg_quality: int,
) -> ExtractionManifest:
    """Write evidence JPEGs and a JSON manifest describing them."""
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_frames = []
    for index, frame in enumerate(result.frames):
        path = output_dir / frame_filename(frame, index)
        cv2.imwrite(str(path), frame.image, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
        manifest_frames.append(
            ManifestFrame(
                timestamp=frame.timestamp,
                event_type=frame.event_type,
                confidence=frame.confidence,
                source_frame_index=frame.source_frame_index,
                path=path,
            )
        )

    manifest = ExtractionManifest(
        video=video,
        frames=manifest_frames,
        events=result.events,
    )
    manifest_path = output_dir / MANIFEST_FILENAME
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Wrote extraction manifest to {}", manifest_path)
    return manifest
