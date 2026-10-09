"""Deterministic ID generation for vision analysis.

Result IDs are ``{analysis_id}:result:{frame_key}:{detector}:{version}:{hash}``.
``frame_key`` may itself contain ``:`` (``ts:12.5``), so parse from the right
with :func:`parse_result_id` instead of substring matching.
"""

from __future__ import annotations

from dataclasses import dataclass

from splatoon3_ai_coach.vision.canonical import (
    canonical_json,
    canonical_timestamp,
    sha256_hex,
)
from splatoon3_ai_coach.vision.models import Reading
from splatoon3_ai_coach.vision.provenance import PIPELINE_VERSION


def compute_analysis_id(
    video_identity: str,
    extraction_manifest_sha256: str,
    vision_config_sha256: str,
    pipeline_version: str = PIPELINE_VERSION,
) -> str:
    """Derive a deterministic analysis ID from canonical inputs."""
    payload = canonical_json(
        {
            "video_identity": video_identity,
            "extraction_manifest_sha256": extraction_manifest_sha256,
            "vision_config_sha256": vision_config_sha256,
            "pipeline_version": pipeline_version,
        }
    )
    return sha256_hex(payload)


def frame_key(source_frame_index: int | None, timestamp: float) -> str:
    """Return the frame identity suffix used in IDs."""
    if source_frame_index is not None:
        return str(source_frame_index)
    return f"ts:{canonical_timestamp(timestamp)}"


def make_frame_id(
    analysis_id: str,
    source_frame_index: int | None,
    timestamp: float,
) -> str:
    """Build a deterministic frame ID."""
    return f"{analysis_id}:frame:{frame_key(source_frame_index, timestamp)}"


def reading_hash(reading: Reading) -> str:
    """Return a stable hash prefix for a reading."""
    from splatoon3_ai_coach.vision.canonical import sha256_prefix

    return sha256_prefix(canonical_json(reading.model_dump(mode="json")))


def make_result_id(
    analysis_id: str,
    source_frame_index: int | None,
    timestamp: float,
    detector_name: str,
    detector_version: str,
    reading: Reading,
) -> str:
    """Build a deterministic detector result ID."""
    return (
        f"{analysis_id}:result:{frame_key(source_frame_index, timestamp)}"
        f":{detector_name}:{detector_version}:{reading_hash(reading)}"
    )


@dataclass(frozen=True)
class ResultIdParts:
    """Structured fields of a detector result ID."""

    analysis_id: str
    frame_key: str
    detector_name: str
    detector_version: str
    reading_hash: str


def parse_result_id(result_id: str) -> ResultIdParts | None:
    """Split a result ID into its fields; ``None`` when it is not a result ID."""
    head, sep, tail = result_id.partition(":result:")
    if not sep or not head:
        return None
    parts = tail.rsplit(":", 3)
    if len(parts) != 4 or not all(parts):
        return None
    key, detector_name, version, digest = parts
    return ResultIdParts(
        analysis_id=head,
        frame_key=key,
        detector_name=detector_name,
        detector_version=version,
        reading_hash=digest,
    )


def result_detector_name(result_id: str) -> str | None:
    """Detector that produced a result ID, or ``None`` for other IDs."""
    parts = parse_result_id(result_id)
    return parts.detector_name if parts is not None else None
