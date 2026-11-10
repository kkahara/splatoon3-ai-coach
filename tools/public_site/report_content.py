"""Content policy for published report bytes. Fails closed.

The scanner runs on the bytes that would be uploaded (after transforms). A
finding is reported by its rule name only; matched values never reach logs,
records, or exceptions.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONTENT_POLICY_VERSION = 1
FORBIDDEN_KEYS = frozenset(
    {
        "victim_name",
        "ocr_text",
        "email",
        "display_name",
        "user_id",
        "access_token",
        "access_token_hash",
        "review_code",
        "original_filename",
        "analysis_dir",
        "ip",
    }
)
ALLOWED_FORMAT_TAGS = frozenset(
    {"major_brand", "minor_version", "compatible_brands", "encoder"}
)
ALLOWED_STREAM_TAGS = frozenset({"handler_name", "vendor_id", "language", "encoder"})
ALLOWED_HANDLERS = frozenset({"VideoHandler", "SoundHandler"})
ALLOWED_STREAMS = frozenset({"video", "audio"})
_BLOCKED_JPEG_MARKERS = frozenset({0xE1, 0xED})
_JPEG_COMMENT = 0xFE
# ffmpeg's MJPEG encoder writes its own version as the only comment.
_ENCODER_COMMENT = re.compile(rb"^Lavc[0-9]{1,3}\.[0-9]{1,4}\.[0-9]{1,4}\x00?$")
_MIN_SECRET = 8
_MIN_PERSONAL = 4
# A filename stem shorter than this is usually a plain word ("sample", "replay").
MIN_FILENAME_STEM = 8
_ABS_PATH = re.compile(
    r"(?:^|[\s\"'=(:,])(?:/(?:Users|home|root|data|srv|tmp|var|private|opt|mnt|workspace)/"
    r"|[A-Za-z]:\\\\?[A-Za-z])"
)
_EMAIL = re.compile(
    r"(?<![\w.+%-])[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@"
    r"[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}(?![\w-])"
)


class ContentFinding(ValueError):
    """A content policy violation. ``rule`` is the only detail ever exposed."""

    def __init__(self, rule: str) -> None:
        super().__init__(f"content_policy:{rule}")
        self.rule = rule


@dataclass
class KnownValues:
    """Exact values that must never appear in any published byte."""

    tokens: list[tuple[str, bytes]] = field(default_factory=list)

    def add(self, rule: str, value: str | None, *, minimum: int) -> None:
        """Register one value under ``rule`` when it is long enough to be meaningful."""
        if value and len(value.strip()) >= minimum:
            self.tokens.append((rule, value.strip().encode()))

    def add_personal(self, rule: str, value: str | None) -> None:
        """A personal value (email, display name, filename, ...)."""
        self.add(rule, value, minimum=_MIN_PERSONAL)

    def add_secret(self, rule: str, value: str | None) -> None:
        """A configured secret; values shorter than 8 characters are ignored."""
        self.add(rule, value, minimum=_MIN_SECRET)

    def check(self, data: bytes) -> None:
        """Raise the first matching rule."""
        for rule, token in self.tokens:
            if token in data:
                raise ContentFinding(rule)


def scan_json(data: bytes, known: KnownValues) -> None:
    """Forbidden non-null keys, absolute paths, email addresses, known values."""
    known.check(data)
    try:
        payload = json.loads(data)
    except ValueError:
        raise ContentFinding("json_unparseable") from None
    for key, value in _walk(payload):
        if key in FORBIDDEN_KEYS and value not in (None, "", [], {}):
            raise ContentFinding(f"forbidden_key:{key}")
        for text in (key, value):
            if isinstance(text, str):
                _scan_text(text)


def _scan_text(text: str) -> None:
    if _ABS_PATH.search(text):
        raise ContentFinding("absolute_path")
    if _EMAIL.search(text):
        raise ContentFinding("email_address")


def _walk(payload: Any, key: str | None = None) -> Iterable[tuple[str | None, Any]]:
    yield key, payload
    if isinstance(payload, dict):
        for name, value in payload.items():
            yield from _walk(value, name)
    elif isinstance(payload, list):
        for value in payload:
            yield from _walk(value, key)


def scan_jpeg(data: bytes, known: KnownValues) -> None:
    """Reject EXIF/XMP (APP1), IPTC (APP13) and any comment segment other than
    ffmpeg's encoder version (``Lavc<major>.<minor>.<micro>``).
    """
    known.check(data)
    if data[:2] != b"\xff\xd8":
        raise ContentFinding("jpeg_invalid")
    index = 2
    while index + 4 <= len(data):
        if data[index] != 0xFF:
            raise ContentFinding("jpeg_invalid")
        marker = data[index + 1]
        if marker == 0xFF:
            index += 1
            continue
        if marker in _BLOCKED_JPEG_MARKERS:
            raise ContentFinding("jpeg_metadata")
        if marker == 0xDA:
            return
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:
            index += 2
            continue
        length = int.from_bytes(data[index + 2 : index + 4], "big")
        body = data[index + 4 : index + 2 + length]
        if marker == _JPEG_COMMENT and not _ENCODER_COMMENT.fullmatch(body):
            raise ContentFinding("jpeg_metadata")
        index += 2 + length
    raise ContentFinding("jpeg_invalid")


def scan_mp4(path: Path, data: bytes, known: KnownValues) -> None:
    """Allowlisted container/stream tags, no chapters, only video and audio streams.

    ``data`` is the file content already read once (for known values); ffprobe
    reads ``path``. The caller re-hashes ``path`` afterwards.
    """
    known.check(data)
    probe = _ffprobe(path)
    format_tags = set((probe.get("format") or {}).get("tags") or {})
    if format_tags - ALLOWED_FORMAT_TAGS:
        raise ContentFinding("mp4_format_tags")
    if probe.get("chapters"):
        raise ContentFinding("mp4_chapters")
    for stream in probe.get("streams") or []:
        if stream.get("codec_type") not in ALLOWED_STREAMS:
            raise ContentFinding("mp4_stream_type")
        tags = stream.get("tags") or {}
        if set(tags) - ALLOWED_STREAM_TAGS:
            raise ContentFinding("mp4_stream_tags")
        handler = tags.get("handler_name")
        if handler is not None and handler not in ALLOWED_HANDLERS:
            raise ContentFinding("mp4_stream_tags")


def _ffprobe(path: Path) -> dict:
    argv = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        str(path),
    ]
    try:
        completed = subprocess.run(argv, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise ContentFinding("mp4_unverifiable") from None
    if completed.returncode != 0:
        raise ContentFinding("mp4_unverifiable")
    try:
        return json.loads(completed.stdout)
    except ValueError:
        raise ContentFinding("mp4_unverifiable") from None
