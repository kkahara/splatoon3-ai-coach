"""One raw gameplay still per public coaching moment.

Frames are copied from the source video after ``coaching_index.json`` exists.
They are not detector input, scenario evidence, or LLM context.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from splatoon3_ai_coach.cli.coach_common import (
    COACH_INPUTS_DIRNAME,
    COACHING_INDEX_FILENAME,
)

_SAFE_ID = re.compile(r"^[\w.\-]+$")
_LEAD_SECONDS = 0.5
Runner = Callable[[list[str]], int]


def frame_seek(video_time: float) -> float:
    """Seconds to grab: just before the moment, and never before the file start."""
    return max(0.0, float(video_time) - _LEAD_SECONDS)


def frame_path(analysis: Path, safe_id: str) -> Path | None:
    """JPEG location for one moment. Unsafe ids are refused."""
    if not _SAFE_ID.fullmatch(safe_id):
        return None
    return analysis / "public_frames" / f"{safe_id}.jpg"


def has_frame(analysis: Path, safe_id: str) -> bool:
    """True when that moment's JPEG is already stored."""
    path = frame_path(analysis, safe_id)
    return path is not None and path.is_file()


def missing_frames(analysis: Path) -> bool:
    """True when a public moment has no JPEG yet."""
    for entry in ranked_entries(analysis):
        path = frame_path(analysis, str(entry.get("safe_id") or ""))
        if path is not None and not path.is_file():
            return True
    return False


def ranked_entries(analysis: Path) -> list[dict]:
    """Coaching moments in video-time order, matching the public review."""
    path = analysis / COACH_INPUTS_DIRNAME / COACHING_INDEX_FILENAME
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    rows = [item for item in payload if isinstance(item, dict)]
    return sorted(rows, key=_chrono)


def _chrono(item: dict) -> tuple[float, int]:
    try:
        when = float(item.get("video_time") or 0)
    except (TypeError, ValueError):
        when = 0.0
    try:
        rank = int(item.get("rank"))
    except (TypeError, ValueError):
        rank = 10**9
    return (when, rank)


def ffmpeg_argv(video: Path, dest: Path, at: float) -> list[str]:
    """Accurate one-frame grab. ``-ss`` follows ``-i`` so the seek is decoded."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video),
        "-ss",
        f"{at:.3f}",
        "-frames:v",
        "1",
        "-vf",
        "scale=1280:-2",
        str(dest),
    ]


def extract_frames(
    analysis: Path,
    video: Path,
    *,
    submission_id: str = "",
    run: Runner | None = None,
) -> None:
    """Write one unannotated JPEG per ranked moment. A failed grab is skipped."""
    for entry in ranked_entries(analysis):
        _extract_one(analysis, video, entry, submission_id=submission_id, run=run)


def _extract_one(
    analysis: Path,
    video: Path,
    entry: dict,
    *,
    submission_id: str,
    run: Runner | None,
) -> None:
    safe_id = str(entry.get("safe_id") or "")
    dest = frame_path(analysis, safe_id)
    if dest is None or dest.is_file() or not video.is_file():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(f"{dest.stem}.part.jpg")
    try:
        code = _invoke(ffmpeg_argv(video, partial, frame_seek(_video_time(entry))), run)
    except (OSError, subprocess.TimeoutExpired):
        _drop(partial)
        _warn(submission_id, safe_id)
        return
    if code != 0 or not partial.is_file():
        _drop(partial)
        _warn(submission_id, safe_id)
        return
    partial.replace(dest)


def _video_time(entry: dict) -> float:
    try:
        return float(entry.get("video_time") or 0)
    except (TypeError, ValueError):
        return 0.0


def _invoke(argv: list[str], run: Runner | None) -> int:
    if run is not None:
        return run(argv)
    completed = subprocess.run(argv, capture_output=True, timeout=120, check=False)
    return int(completed.returncode)


def _drop(path: Path) -> None:
    if path.is_file():
        path.unlink()


def _warn(submission_id: str, safe_id: str) -> None:
    logger.warning(
        "moment frame extract failed for submission {} moment {}",
        submission_id,
        safe_id,
    )
