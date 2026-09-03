"""Orchestration for the extraction stage."""

from pathlib import Path

from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.extraction.extractor import MeaningfulFrameExtractor
from splatoon3_ai_coach.extraction.models import ExtractionManifest
from splatoon3_ai_coach.media.manifest import save_manifest
from splatoon3_ai_coach.media.video import VideoLoader


def run_extraction(
    video: Path,
    config: AppConfig,
    output_dir: Path | None = None,
) -> ExtractionManifest:
    """Decode a video, select meaningful frames, and write a manifest."""
    destination = output_dir or config.paths.frame_output
    loader = VideoLoader(
        video,
        max_width=config.video.max_width,
        max_height=config.video.max_height,
    )
    with loader:
        result = MeaningfulFrameExtractor(config.extraction).extract(loader)

    return save_manifest(
        video,
        result,
        destination,
        config.extraction.save_jpeg_quality,
    )
