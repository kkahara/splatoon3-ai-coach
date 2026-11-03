"""Shared HTTP media helpers for the React VMV site."""

from __future__ import annotations

import re
from pathlib import Path

_VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def review_video_content_type(path: Path) -> str:
    """Return the content type for a VMV-served video file."""
    return _VIDEO_TYPES.get(path.suffix.lower(), "application/octet-stream")


def parse_byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Parse one HTTP Range header into inclusive ``(start, end)`` bytes.

    ``None`` means the header is absent, malformed, or unsatisfiable. The API
    converts a supplied invalid header into HTTP 416.
    """
    if not header:
        return None
    match = _RANGE_RE.fullmatch(header.strip())
    if match is None:
        return None
    start_s, end_s = match.group(1), match.group(2)
    if start_s == "" and end_s == "":
        return None
    if start_s == "":
        length = int(end_s)
        if length <= 0:
            return None
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    if start >= size or start < 0 or end < start:
        return None
    return start, min(end, size - 1)
