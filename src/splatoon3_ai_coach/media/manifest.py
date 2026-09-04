"""Reading and writing extraction manifests and evidence frames."""

from pathlib import Path

import cv2
from loguru import logger

from splatoon3_ai_coach.exceptions import ManifestError
from splatoon3_ai_coach.extraction.models import (
    ExtractionManifest,
    ExtractionResult,
    ManifestFrame,
    SelectedFrame,
)
from splatoon3_ai_coach.paths import portable_path

MANIFEST_FILENAME = "manifest.json"


def frame_filename(frame: SelectedFrame, index: int) -> str:
    """Build a sortable, self-describing filename for a saved frame."""
    return f"{frame.timestamp:010.3f}_{frame.trigger_type.value}_{index:04d}.jpg"


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
                trigger_type=frame.trigger_type,
                confidence=frame.confidence,
                source_frame_index=frame.source_frame_index,
                source_pts=frame.source_pts,
                source_time_base_num=frame.source_time_base_num,
                source_time_base_den=frame.source_time_base_den,
                path=Path(portable_path(path, output_dir)),
            )
        )

    manifest = ExtractionManifest(
        video=video,
        frames=manifest_frames,
        trigger_events=result.trigger_events,
    )
    manifest_path = output_dir / MANIFEST_FILENAME
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    logger.info("Wrote extraction manifest to {}", manifest_path)
    return manifest


def load_manifest(path: Path) -> ExtractionManifest:
    """Load and validate an extraction manifest from disk."""
    if not path.exists():
        raise ManifestError(f"Manifest not found: {path}")

    manifest_dir = path if path.is_dir() else path.parent
    manifest_file = path / MANIFEST_FILENAME if path.is_dir() else path

    try:
        manifest = ExtractionManifest.model_validate_json(
            manifest_file.read_text(encoding="utf-8")
        )
    except Exception as exc:
        raise ManifestError(f"Invalid manifest at {manifest_file}: {exc}") from exc

    resolved_frames = []
    for frame in manifest.frames:
        frame_path = frame.path
        if not frame_path.is_absolute():
            frame_path = (manifest_dir / frame_path).resolve()
        resolved_frames.append(frame.model_copy(update={"path": frame_path}))
    return manifest.model_copy(update={"frames": resolved_frames})
