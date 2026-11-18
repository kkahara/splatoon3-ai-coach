"""Checks that a stored review-code recording is the one that was verified.

Intake admission and the worker use these before analysis. They read storage
and local files only; they do not capture, upload or analyze video.
"""

from __future__ import annotations

from pathlib import Path

from public_site.models import VideoInput
from public_site.storage import file_sha256

MISMATCH = "The stored recording does not match the verified file."


def downloaded_matches(path: Path, fingerprint: str | None) -> bool:
    """True when the worker's copy is the exact verified recording."""
    return bool(fingerprint) and file_sha256(path) == fingerprint


def remote_agrees(storage, video: VideoInput) -> bool:
    """True when the stored object is the recording named on ``video``."""
    if not video.sha256 or video.size_bytes < 1:
        return False
    try:
        stat = storage.head(video.object_key)
    except OSError:
        return False
    if stat is None or stat.size_bytes != video.size_bytes:
        return False
    remote = stat.sha256
    if not remote:
        try:
            _size, remote = storage.read_sha256(video.object_key)
        except (OSError, FileNotFoundError):
            return False
    return remote == video.sha256
