"""Content-based video identity for reproducible analysis."""

import hashlib
from pathlib import Path

CHUNK_SIZE = 1024 * 1024


def video_identity(path: Path) -> str:
    """Return a stable identity string for a video file based on content."""
    digest = hashlib.sha256()
    size = path.stat().st_size
    digest.update(str(size).encode("utf-8"))
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
