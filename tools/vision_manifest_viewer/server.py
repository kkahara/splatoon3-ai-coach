"""Local HTTP server for Scenario Review video playback."""

from __future__ import annotations

import re
import socket
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

REVIEW_VIDEO_PATH = "/review-video"

_VIDEO_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def review_video_content_type(path: Path) -> str:
    """Return a Content-Type for a review video file."""
    return _VIDEO_TYPES.get(path.suffix.lower(), "application/octet-stream")


def resolve_review_video(url_path: str, video_path: Path) -> Path | None:
    """Map ``/review-video`` to ``video_path``; other URLs stay on disk."""
    parsed = urlparse(url_path)
    if parsed.path.rstrip("/") == REVIEW_VIDEO_PATH:
        return video_path
    return None


def pick_free_port(preferred: int | None = None, host: str = "127.0.0.1") -> int:
    """Return ``preferred`` if bindable, otherwise an ephemeral free port."""
    if preferred is not None and preferred > 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, preferred))
            except OSError:
                preferred = None
            else:
                return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def parse_byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Parse a single HTTP Range header into inclusive ``(start, end)``.

    Returns ``None`` when the header is absent, malformed, or unsatisfiable.
    Callers that received a Range header must treat ``None`` as HTTP 416.
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
        # suffix bytes: last N bytes
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
    end = min(end, size - 1)
    return start, end


def make_handler(root: Path, video_path: Path) -> type[SimpleHTTPRequestHandler]:
    """Build a handler that serves ``root`` and ranged GET /review-video."""
    directory = str(root.resolve())
    video = video_path.resolve()

    class ReviewHandler(SimpleHTTPRequestHandler):
        """Serve the analysis folder plus ranged GET /review-video."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, directory=directory, **kwargs)

        def do_GET(self) -> None:  # noqa: N802 — stdlib HTTP API
            """Serve review video with Range support; otherwise static files."""
            if resolve_review_video(self.path, video) is not None:
                self._serve_video()
                return
            super().do_GET()

        def do_HEAD(self) -> None:  # noqa: N802 — stdlib HTTP API
            """HEAD for review video without a body."""
            if resolve_review_video(self.path, video) is not None:
                self._serve_video(body=False)
                return
            super().do_HEAD()

        def _serve_video(self, *, body: bool = True) -> None:
            """Stream the review video file; honor ``Range`` when present."""
            size = video.stat().st_size
            content_type = review_video_content_type(video)
            range_header = self.headers.get("Range")
            if not range_header:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(size))
                self.end_headers()
                if body and size > 0:
                    self._copy_file_range(0, size - 1)
                return

            requested = parse_byte_range(range_header, size)
            if requested is None:
                self.send_response(416)
                self.send_header("Content-Type", content_type)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            start, end = requested
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            if body:
                self._copy_file_range(start, end)

        def _copy_file_range(self, start: int, end: int) -> None:
            """Copy ``[start, end]`` inclusive from disk without loading all bytes.

            Client disconnects (seek / tab close) raise ``BrokenPipeError`` /
            ``ConnectionResetError``; those are normal for HTML5 video Range GETs.
            """
            remaining = end - start + 1
            chunk = 64 * 1024
            try:
                with video.open("rb") as handle:
                    handle.seek(start)
                    while remaining > 0:
                        data = handle.read(min(chunk, remaining))
                        if not data:
                            break
                        self.wfile.write(data)
                        remaining -= len(data)
            except (BrokenPipeError, ConnectionResetError):
                return

        def handle(self) -> None:
            """Ignore client disconnects while serving Range video chunks."""
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, format: str, *args: Any) -> None:
            """Keep the review server quiet."""
            _ = format, args

    return ReviewHandler


def serve_review(
    root: Path,
    video_path: Path,
    *,
    port: int | None = None,
    host: str = "127.0.0.1",
) -> ThreadingHTTPServer:
    """Bind a review server on localhost. Caller must serve_forever / shutdown."""
    chosen = pick_free_port(port, host=host)
    handler = make_handler(root, video_path)
    return ThreadingHTTPServer((host, chosen), handler)


def review_page_url(
    html_name: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> str:
    """Browser URL for the generated viewer HTML."""
    return f"http://{host}:{port}/{html_name}"
