"""Deterministic, named transforms that make published report copies.

Transforms replace exact byte tokens only; JSON is never re-serialized, so
every other byte is unchanged. The local analysis folder is never modified.

- ``redact_video_identity_v1``: the 64-hex ``video_identity`` (a hash of the
  uploaded file) becomes the sentinel ``redacted:v1``.
- ``pseudonymize_analysis_id_v1``: the 64-hex ``analysis_id`` (derived from the
  video hash) becomes one random 64-hex pseudonym per publication run, the same
  in every file of the run. No mapping to the original is stored anywhere.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from splatoon3_ai_coach.vision.ids import parse_result_id
from splatoon3_ai_coach.vision.models import VisionManifest

REDACT_VIDEO_IDENTITY = "redact_video_identity_v1"
PSEUDONYMIZE_ANALYSIS_ID = "pseudonymize_analysis_id_v1"
REDACTED_SENTINEL = "redacted:v1"
MANIFEST_PATH = "vision_manifest.json"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PREFIXED_ID = re.compile(r"^([0-9a-f]{64}):[a-z_]+:")


class TransformError(ValueError):
    """A transform or consistency check failed. Carries a fixed reason, no values."""


@dataclass(frozen=True)
class ReportIdentity:
    """The two fingerprints read from the local manifest. Kept in memory only."""

    video_identity: str
    analysis_id: str

    def __repr__(self) -> str:
        return "ReportIdentity(<redacted>)"


def read_identity(manifest_bytes: bytes) -> ReportIdentity:
    """Read both fingerprints from the local manifest bytes.

    Raises:
        TransformError: The manifest is unreadable or a fingerprint is not 64 hex.
    """
    try:
        payload = json.loads(manifest_bytes)
        video = payload["video_identity"]
        nested = payload["analysis"]["video_identity"]
        analysis_id = payload["analysis"]["analysis_id"]
    except (ValueError, KeyError, TypeError):
        raise TransformError("manifest_unreadable") from None
    if not all(isinstance(v, str) and _HEX64.fullmatch(v) for v in (video, analysis_id)):
        raise TransformError("fingerprint_format")
    if nested != video:
        raise TransformError("video_identity_mismatch")
    return ReportIdentity(video_identity=video, analysis_id=analysis_id)


def new_pseudonym(identity: ReportIdentity) -> str:
    """A fresh random 64-hex ID, never equal to either original fingerprint."""
    while True:
        value = secrets.token_hex(32)
        if value not in (identity.analysis_id, identity.video_identity):
            return value


def transform_json(
    path: str, data: bytes, identity: ReportIdentity, pseudonym: str
) -> tuple[bytes, list[str]]:
    """Published bytes for one JSON file and the transforms that changed it.

    Raises:
        TransformError: The manifest lacks the expected tokens, or a file no
            longer parses after replacement.
    """
    applied: list[str] = []
    video = identity.video_identity.encode()
    if video in data:
        data = data.replace(video, REDACTED_SENTINEL.encode())
        applied.append(REDACT_VIDEO_IDENTITY)
    elif path == MANIFEST_PATH:
        raise TransformError("video_identity_not_found")
    original = identity.analysis_id.encode()
    if original in data:
        data = data.replace(original, pseudonym.encode())
        applied.append(PSEUDONYMIZE_ANALYSIS_ID)
    elif path == MANIFEST_PATH:
        raise TransformError("analysis_id_not_found")
    if applied:
        try:
            json.loads(data)
        except ValueError:
            raise TransformError("parse_after_transform") from None
    return data, applied


def verify_published(
    files: Mapping[str, bytes], identity: ReportIdentity, pseudonym: str
) -> None:
    """Check the whole published JSON set before any upload.

    - neither original fingerprint appears in any byte;
    - every ``analysis_id`` key holds the pseudonym (or null);
    - every ``<64 hex>:<kind>:...`` identifier starts with the pseudonym;
    - the manifest's identity fields, frame IDs and result IDs agree, and the
      manifest still validates as ``VisionManifest``.

    Raises:
        TransformError: Any check failed (fixed reason, no values).
    """
    originals = (identity.video_identity.encode(), identity.analysis_id.encode())
    for data in files.values():
        if any(token in data for token in originals):
            raise TransformError("original_fingerprint_remains")
    for data in files.values():
        _check_references(json.loads(data), pseudonym)
    manifest = files.get(MANIFEST_PATH)
    if manifest is None:
        raise TransformError("manifest_missing")
    _check_manifest(manifest, pseudonym)


def _check_references(payload: Any, pseudonym: str) -> None:
    for key, value in _walk(payload):
        if key == "analysis_id" and value is not None and value != pseudonym:
            raise TransformError("analysis_id_inconsistent")
        if isinstance(value, str):
            match = _PREFIXED_ID.match(value)
            if match and match.group(1) != pseudonym:
                raise TransformError("reference_inconsistent")


def _walk(payload: Any, key: str | None = None) -> Iterator[tuple[str | None, Any]]:
    yield key, payload
    if isinstance(payload, dict):
        for name, value in payload.items():
            yield from _walk(value, name)
    elif isinstance(payload, list):
        for value in payload:
            yield from _walk(value, key)


def _check_manifest(data: bytes, pseudonym: str) -> None:
    try:
        manifest = VisionManifest.model_validate_json(data)
    except ValidationError:
        raise TransformError("manifest_schema") from None
    if manifest.analysis.analysis_id != pseudonym:
        raise TransformError("analysis_id_inconsistent")
    if REDACTED_SENTINEL not in (
        manifest.video_identity,
        manifest.analysis.video_identity,
    ):
        raise TransformError("video_identity_not_redacted")
    if manifest.video_identity != manifest.analysis.video_identity:
        raise TransformError("video_identity_not_redacted")
    for frame in manifest.frame_results:
        if not frame.frame_id.startswith(f"{pseudonym}:frame:"):
            raise TransformError("frame_id_inconsistent")
        for detection in frame.detections:
            parts = parse_result_id(detection.id)
            if parts is None or parts.analysis_id != pseudonym:
                raise TransformError("result_id_inconsistent")
    for snapshot in manifest.state_snapshots:
        for evidence in snapshot.evidence_ids:
            parts = parse_result_id(evidence)
            if parts is not None and parts.analysis_id != pseudonym:
                raise TransformError("evidence_id_inconsistent")
