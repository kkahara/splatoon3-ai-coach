"""Orchestration for the full Phase 3 analyze command."""

from pathlib import Path

from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.extraction.pipeline import run_extraction
from splatoon3_ai_coach.media.manifest import MANIFEST_FILENAME
from splatoon3_ai_coach.vision.models import VisionManifest
from splatoon3_ai_coach.vision.pipeline import run_vision


def run_analysis(
    video: Path,
    config: AppConfig,
    output_dir: Path,
    *,
    extraction_dir: Path | None = None,
    reuse_extraction: bool = False,
    debug_persist_cadence_frames: bool = False,
) -> VisionManifest:
    """Run extraction (if needed) and vision analysis."""
    frames_dir = extraction_dir or (output_dir / "frames")
    manifest_path = frames_dir / MANIFEST_FILENAME

    if reuse_extraction and manifest_path.exists():
        extraction_manifest_path = frames_dir
    else:
        run_extraction(video, config, output_dir=frames_dir)
        extraction_manifest_path = frames_dir

    return run_vision(
        video,
        config,
        extraction_manifest_path,
        output_dir,
        debug_persist_cadence_frames=debug_persist_cadence_frames,
    )
