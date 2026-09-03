"""Media input and output."""

from splatoon3_ai_coach.media.manifest import load_manifest, save_manifest
from splatoon3_ai_coach.media.video import VideoFrame, VideoLoader, VideoMetadata

__all__ = [
    "VideoFrame",
    "VideoLoader",
    "VideoMetadata",
    "load_manifest",
    "save_manifest",
]
