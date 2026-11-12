"""Publish one finalized recording into the existing analysis queue.

Acquisition (the capture station, and later SCLC) stops at a finished file.
This module checks that file, stores it, and hands the same submission to the
worker. It does not capture video and it does not analyze it.
"""

from __future__ import annotations

from pathlib import Path

from public_site.errors import RequestRejected
from public_site.media_probe import ProbeRejected
from public_site.models import VideoInput
from public_site.recording_intake import INTAKE_FILENAME, INTAKE_TYPE, intake_key
from public_site.storage import file_sha256

CONFLICT = "A different recording is already stored for this submission."
CONFLICT_REASON = "recording_conflict"
MISMATCH = "The stored recording does not match the verified file."
FINGERPRINT = "The local recording does not match its fingerprint."


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


def publish_finalized(
    recordings,
    submission_id: str,
    actor_id: str,
    source: Path,
    fingerprint: str,
    expected_version: int,
) -> None:
    """Store ``source`` only when its bytes are ``fingerprint``, then queue it.

    A different object already at this submission's key is left in place.
    """
    _require_local(recordings, source, fingerprint)
    key = intake_key(submission_id)
    _resolve_remote(recordings.service.storage, key, source, fingerprint)
    _remember(recordings, submission_id, source, fingerprint)
    row = recordings.database.get(submission_id)
    if row is None:
        raise RequestRejected(404, "Recording job not found.")
    if row["state"] == "recording":
        verified = recordings.database.verify_upload(
            submission_id, actor_id, expected_version, _audit(source, fingerprint)
        )
        if not verified:
            raise RequestRejected(409, "The upload grant changed. Reload and upload again.")
    elif row["state"] != "uploading":
        raise RequestRejected(409, "This recording cannot be published.")
    recordings.intake._queue(submission_id)


def _require_local(recordings, source: Path, fingerprint: str) -> None:
    if not source.is_file():
        raise RequestRejected(400, "The finalized recording is missing.")
    size = source.stat().st_size
    limit = recordings.service.settings.max_video_bytes
    if size < 1 or (limit is not None and size > limit):
        raise RequestRejected(400, "That video is too large.")
    if file_sha256(source) != fingerprint:
        raise RequestRejected(409, FINGERPRINT)


def _resolve_remote(storage, key: str, source: Path, fingerprint: str) -> None:
    """PUT only when the key is empty. A different object is a conflict."""
    size = source.stat().st_size
    try:
        stat = storage.head(key)
    except OSError as exc:
        raise RequestRejected(503, "The stored recording could not be checked.") from exc
    if stat is None:
        _put_and_read(storage, key, source, fingerprint, size)
        return
    if stat.size_bytes != size or not _same_remote(storage, key, stat.sha256, fingerprint):
        raise RequestRejected(409, CONFLICT, CONFLICT_REASON)
    found = _read(storage, key)
    if found != (size, fingerprint):
        raise RequestRejected(409, CONFLICT, CONFLICT_REASON)


def _put_and_read(storage, key: str, source: Path, fingerprint: str, size: int) -> None:
    try:
        storage.put_file(key, source, INTAKE_TYPE, fingerprint, size)
    except OSError as exc:
        raise RequestRejected(503, "The recording could not be stored.") from exc
    if _read(storage, key) != (size, fingerprint):
        raise RequestRejected(409, MISMATCH)


def _same_remote(storage, key: str, recorded: str | None, fingerprint: str) -> bool:
    if recorded:
        return recorded == fingerprint
    try:
        return _read(storage, key)[1] == fingerprint
    except RequestRejected:
        return False


def _read(storage, key: str) -> tuple[int, str]:
    try:
        return storage.read_sha256(key)
    except FileNotFoundError as exc:
        raise RequestRejected(409, MISMATCH) from exc
    except OSError as exc:
        raise RequestRejected(503, "The stored recording could not be checked.") from exc


def _remember(recordings, submission_id: str, source: Path, fingerprint: str) -> None:
    try:
        facts = recordings.service.probe(source)
    except ProbeRejected as exc:
        raise RequestRejected(400, exc.detail) from None
    video = VideoInput(
        submission_id=submission_id,
        object_key=intake_key(submission_id),
        original_filename=INTAKE_FILENAME,
        size_bytes=source.stat().st_size,
        content_type=INTAKE_TYPE,
        duration_seconds=facts.duration_seconds,
        width=facts.width,
        height=facts.height,
        sha256=fingerprint,
    )
    recordings.service.store.save_video(video)


def _audit(source: Path, fingerprint: str) -> dict:
    return {"size_bytes": source.stat().st_size, "sha256": fingerprint}
