"""Content policy for published report bytes. Fails closed.

The scanner runs on the bytes that would be uploaded (after transforms). A
finding is reported by its rule name only; matched values never reach logs,
records, or exceptions.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import subprocess
import unicodedata
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
_JPEG_APP0 = 0xE0
_JPEG_COMMENT = 0xFE
_JPEG_SOS = 0xDA
# SOFn (not DHT C4, JPG C8 or DAC CC), DHT, DQT, DRI, SOS and DNL.
_JPEG_STRUCTURAL = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    | {0xC4, 0xDB, 0xDD, 0xDA, 0xDC}
)
# JFIF APP0: "JFIF\0", version (2), units (1), densities (2+2), thumbnail size (1+1).
_JFIF_BODY_LENGTH = 14
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


CODE_LENGTH = 16
_CODE_RUN = re.compile(rb"[A-Za-z0-9](?:[\s\-]*[A-Za-z0-9])*")
_CODE_SEPARATORS = re.compile(rb"[\s\-]+")
_CODE_SEEN_LIMIT = 200_000


class CodeMatcher:
    """Matches a review code by its HMAC-SHA-256, never by plaintext.

    Runs of ASCII letters/digits, with whitespace and hyphens allowed between
    characters, are compacted and uppercased; every 16-character window is
    HMACed with the precomputed key state and compared in constant time.
    Windows already found not to match are remembered (bounded), so repeated
    identifiers are hashed once. ``repr`` reveals neither key nor digest.
    """

    __slots__ = ("_mac", "_digest", "_seen")

    def __init__(self, key: bytes, digest: bytes) -> None:
        self._mac = hmac.new(key, digestmod=hashlib.sha256)
        self._digest = digest
        self._seen: set[bytes] = set()

    def __repr__(self) -> str:
        return "CodeMatcher(<redacted>)"

    def matches(self, data: bytes) -> bool:
        """Whether any window of ``data`` is the code."""
        for run in _CODE_RUN.finditer(data):
            if run.end() - run.start() < CODE_LENGTH:
                continue
            compact = _CODE_SEPARATORS.sub(b"", run.group()).upper()
            for start in range(len(compact) - CODE_LENGTH + 1):
                if self._window(compact[start : start + CODE_LENGTH]):
                    return True
        return False

    def matches_text(self, text: str) -> bool:
        """Whether a decoded string (NFKC-normalized here) contains the code."""
        return self.matches(unicodedata.normalize("NFKC", text).encode("utf-8"))

    def _window(self, window: bytes) -> bool:
        if window in self._seen:
            return False
        mac = self._mac.copy()
        mac.update(window)
        if hmac.compare_digest(mac.digest(), self._digest):
            return True
        if len(self._seen) >= _CODE_SEEN_LIMIT:
            self._seen.clear()
        self._seen.add(window)
        return False


class ContentFinding(ValueError):
    """A content policy violation. ``rule`` is the only detail ever exposed."""

    def __init__(self, rule: str) -> None:
        super().__init__(f"content_policy:{rule}")
        self.rule = rule


@dataclass
class KnownValues:
    """Values that must never appear in any published byte.

    ``tokens`` are matched against raw bytes. ``texts`` are matched against every
    decoded JSON key and string, case-folded and also with whitespace and hyphens
    removed, so JSON ``\\u`` escapes, case changes or separators cannot hide a value.
    """

    tokens: list[tuple[str, bytes]] = field(default_factory=list)
    texts: list[tuple[str, str]] = field(default_factory=list)
    codes: list[tuple[str, CodeMatcher]] = field(default_factory=list)
    exact: list[tuple[str, str]] = field(default_factory=list)

    def add_code(self, rule: str, matcher: CodeMatcher) -> None:
        """Register a review-code canary, matched on raw bytes and decoded strings."""
        self.codes.append((rule, matcher))

    def add(self, rule: str, value: str | None, *, minimum: int) -> None:
        """Register one value under ``rule`` when it is long enough to be meaningful.

        Raw-byte tokens cover both the value as given and its NFKC form; decoded
        strings are compared in NFKC form, so full-width and half-width
        spellings of a name match each other.
        """
        if not value:
            return
        original = value.strip()
        value = unicodedata.normalize("NFKC", value).strip()
        if len(value) < minimum:
            return
        for form in dict.fromkeys((value, original)):
            self.tokens.append((rule, form.encode()))
            escaped = json.dumps(form)[1:-1].encode()
            if escaped != form.encode():
                self.tokens.append((rule, escaped))
        folded = value.casefold()
        self.texts.append((rule, folded))
        compact = _compact(folded)
        if compact != folded and len(compact) >= minimum:
            self.texts.append((rule, compact))

    def copy(self) -> KnownValues:
        """An independent copy (per-run additions never leak into the original)."""
        return KnownValues(
            list(self.tokens), list(self.texts), list(self.codes), list(self.exact)
        )

    def add_personal(self, rule: str, value: str | None) -> None:
        """A personal value (email, display name, filename, ...)."""
        self.add(rule, value, minimum=_MIN_PERSONAL)

    def add_name(self, rule: str, value: str | None) -> None:
        """A player or display name. Under 4 characters it is matched only as an
        entire decoded JSON string value (after NFKC, case-folding and
        compaction), never as a substring.
        """
        if not value:
            return
        normalized = unicodedata.normalize("NFKC", value).strip()
        if len(normalized) >= _MIN_PERSONAL:
            self.add_personal(rule, normalized)
        elif normalized:
            self.exact.append((rule, _compact(normalized.casefold())))

    def add_secret(self, rule: str, value: str | None) -> None:
        """A configured secret; values shorter than 8 characters are ignored."""
        self.add(rule, value, minimum=_MIN_SECRET)

    def check(self, data: bytes) -> None:
        """Raise the first rule whose raw bytes occur in ``data``."""
        for rule, token in self.tokens:
            if token in data:
                raise ContentFinding(rule)
        for rule, matcher in self.codes:
            if matcher.matches(data):
                raise ContentFinding(rule)

    def check_text(self, text: str, *, is_value: bool = True) -> None:
        """Raise the first rule whose value occurs in one decoded string.

        Short names (``exact``) are compared with the whole string, and only
        for string values (``is_value``), never for keys.
        """
        folded = unicodedata.normalize("NFKC", text).casefold()
        compact = _compact(folded)
        for rule, value in self.texts:
            if value in folded or value in compact:
                raise ContentFinding(rule)
        if is_value:
            for rule, value in self.exact:
                if compact == value:
                    raise ContentFinding(rule)
        for rule, matcher in self.codes:
            if matcher.matches_text(text):
                raise ContentFinding(rule)


def _compact(text: str) -> str:
    return re.sub(r"[\s\-]+", "", text)


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
        for text, is_value in ((key, False), (value, True)):
            if isinstance(text, str):
                known.check_text(text, is_value=is_value)
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
    """Walk every marker from SOI to EOI, across all scans (baseline and
    progressive), against a segment allowlist.

    Allowed: structural markers (SOFn, DHT, DQT, DRI, SOS, RSTn, DNL), at most
    one APP0 that is a plain JFIF header without thumbnail, and at most one
    comment that is exactly ffmpeg's encoder version
    (``Lavc<major>.<minor>.<micro>``, optional trailing NUL). Every other
    marker (APP1-APP15, JFXX, a second APP0 or comment, any other comment,
    unknown markers) is ``jpeg_metadata``. A truncated or malformed structure,
    a second SOI or any byte after EOI is ``jpeg_invalid``.
    """
    known.check(data)
    if data[:2] != b"\xff\xd8":
        raise ContentFinding("jpeg_invalid")
    index, seen = 2, set()
    while True:
        marker, index = _jpeg_marker(data, index)
        if marker == 0xD9:
            if index != len(data):
                raise ContentFinding("jpeg_invalid")
            return
        if 0xD0 <= marker <= 0xD7:
            continue
        if marker in (0x00, 0xD8):
            raise ContentFinding("jpeg_invalid")
        if marker not in _JPEG_STRUCTURAL and marker not in (_JPEG_APP0, _JPEG_COMMENT):
            raise ContentFinding("jpeg_metadata")
        body, index = _jpeg_segment(data, index)
        _check_jpeg_segment(marker, body, seen)
        if marker == _JPEG_SOS:
            index = _skip_entropy_coded(data, index)


def _check_jpeg_segment(marker: int, body: bytes, seen: set[int]) -> None:
    """APP0 must be a thumbnail-free JFIF header and COM the encoder version;
    each may occur once.
    """
    if marker not in (_JPEG_APP0, _JPEG_COMMENT):
        return
    if marker in seen:
        raise ContentFinding("jpeg_metadata")
    seen.add(marker)
    if marker == _JPEG_COMMENT:
        if not _ENCODER_COMMENT.fullmatch(body):
            raise ContentFinding("jpeg_metadata")
        return
    if (
        len(body) != _JFIF_BODY_LENGTH
        or body[:5] != b"JFIF\x00"
        or body[12:14] != b"\x00\x00"
    ):
        raise ContentFinding("jpeg_metadata")


def _jpeg_marker(data: bytes, index: int) -> tuple[int, int]:
    """The marker at ``index`` (``FF`` plus code, fill ``FF`` bytes skipped)."""
    if index >= len(data) or data[index] != 0xFF:
        raise ContentFinding("jpeg_invalid")
    while index < len(data) and data[index] == 0xFF:
        index += 1
    if index >= len(data):
        raise ContentFinding("jpeg_invalid")
    return data[index], index + 1


def _jpeg_segment(data: bytes, index: int) -> tuple[bytes, int]:
    """The body of the segment whose length field starts at ``index``."""
    if index + 2 > len(data):
        raise ContentFinding("jpeg_invalid")
    length = int.from_bytes(data[index : index + 2], "big")
    if length < 2 or index + length > len(data):
        raise ContentFinding("jpeg_invalid")
    return data[index + 2 : index + length], index + length


def _skip_entropy_coded(data: bytes, index: int) -> int:
    """Position of the first marker after a scan's entropy-coded data.

    Inside the data ``FF 00`` is a stuffed byte and ``FF D0``–``FF D7`` are
    restart markers; any other ``FF xx`` ends the scan.
    """
    while True:
        index = data.find(b"\xff", index)
        if index < 0:
            raise ContentFinding("jpeg_invalid")
        code = index + 1
        while code < len(data) and data[code] == 0xFF:
            code += 1
        if code >= len(data):
            raise ContentFinding("jpeg_invalid")
        if data[code] == 0x00 or 0xD0 <= data[code] <= 0xD7:
            index = code + 1
            continue
        return code - 1


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
