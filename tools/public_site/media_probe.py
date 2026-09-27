"""ffprobe checks that run before a submission may take an analysis slot."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from public_site.errors import (
    PROBE_CONTAINER,
    PROBE_DIMENSIONS,
    PROBE_DURATION,
    PROBE_READ,
    PROBE_STREAM,
)
from public_site.settings import PublicSettings

_CONTAINERS = frozenset({"mov", "mp4", "qt"})


@dataclass(frozen=True)
class ProbeFacts:
    """Facts established by a clean ffprobe."""

    duration_seconds: float
    width: int
    height: int
    container: str


class ProbeRejected(Exception):
    """The file is not something the pipeline should consume."""

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def probe_file(path: Path, settings: PublicSettings) -> ProbeFacts:
    """Run ffprobe and interpret the result. Failures stay public-safe."""
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_format",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeRejected(PROBE_READ) from exc
    if completed.returncode != 0 or not completed.stdout.strip():
        raise ProbeRejected(PROBE_READ)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeRejected(PROBE_READ) from exc
    return interpret_probe(payload, settings)


def interpret_probe(payload: dict, settings: PublicSettings) -> ProbeFacts:
    """Accept a supported container with one video stream inside the limits."""
    container = _container(payload)
    stream = _video_stream(payload)
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width < settings.min_dimension or height < settings.min_dimension:
        raise ProbeRejected(PROBE_DIMENSIONS)
    if width > settings.max_dimension or height > settings.max_dimension:
        raise ProbeRejected(PROBE_DIMENSIONS)
    duration = _duration(payload, stream)
    if duration <= 0:
        raise ProbeRejected(PROBE_READ)
    if duration > settings.max_duration_seconds:
        raise ProbeRejected(PROBE_DURATION)
    return ProbeFacts(duration, width, height, container)


def _container(payload: dict) -> str:
    names = str(payload.get("format", {}).get("format_name", ""))
    found = {item.strip() for item in names.split(",") if item.strip()}
    if not found & _CONTAINERS:
        raise ProbeRejected(PROBE_CONTAINER)
    return names


def _video_stream(payload: dict) -> dict:
    for stream in payload.get("streams") or []:
        if stream.get("codec_type") == "video":
            return stream
    raise ProbeRejected(PROBE_STREAM)


def _duration(payload: dict, stream: dict) -> float:
    raw = payload.get("format", {}).get("duration")
    if raw is None:
        raw = stream.get("duration")
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ProbeRejected(PROBE_READ) from exc
