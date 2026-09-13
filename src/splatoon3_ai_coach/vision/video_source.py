"""Re-export acquisition metadata types for vision consumers."""

from splatoon3_ai_coach.media.video_source import (
    AnalysisQuality,
    Observability,
    SourceDetermination,
    UnsupportedVideoSourceError,
    VideoRunMetadata,
    VideoSource,
    analysis_frame_size,
    build_video_run_metadata,
    resolve_video_source,
)

__all__ = [
    "AnalysisQuality",
    "Observability",
    "SourceDetermination",
    "UnsupportedVideoSourceError",
    "VideoRunMetadata",
    "VideoSource",
    "analysis_frame_size",
    "build_video_run_metadata",
    "resolve_video_source",
]
