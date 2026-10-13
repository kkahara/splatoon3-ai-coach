"""One raw gameplay still per public coaching moment.

Frames are copied from the source video after ``coaching_index.json`` exists.
They are not detector input, scenario evidence, or LLM context. A death still
may show an empty tank or a full special gauge from the seconds before the
death. That choice illustrates the moment; it is not a new coaching claim.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from splatoon3_ai_coach.coach.artifacts import (
    CoachingIndexEntry,
    coach_inputs_dir,
    read_coaching_index_or_none,
)
from splatoon3_ai_coach.coach.claim_catalog import CANDIDATE_TYPE_DEATH_EPISODE

_SAFE_ID = re.compile(r"^[\w.\-]+$")
_LEAD_SECONDS = 0.5
_LOOKBACK_SECONDS = 8.0
_MANIFEST = "vision_manifest.json"
Runner = Callable[[list[str]], int]


def frame_seek(video_time: float) -> float:
    """Seconds to grab: just before the moment, and never before the file start."""
    return max(0.0, float(video_time) - _LEAD_SECONDS)


def moment_seek(
    analysis: Path,
    entry: CoachingIndexEntry,
    events: list | None = None,
) -> float:
    """Still time for one moment. A missing cue keeps the half-second lead.

    Death episodes may use a ``low_ink`` start or a coaching-valid ready gauge
    reading when ``death - 8 <= time < death``. Anything unreadable is ignored.
    """
    fallback = frame_seek(entry.video_time)
    if entry.candidate_type != CANDIDATE_TYPE_DEATH_EPISODE:
        return fallback
    try:
        cues = _death_cues(analysis, entry, events)
    except Exception as exc:  # noqa: BLE001 — a cue must not abort the still
        logger.warning("moment frame cue skipped ({})", type(exc).__name__)
        return fallback
    chosen = _latest_cue(float(entry.video_time), cues)
    return fallback if chosen is None else chosen


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
        path = frame_path(analysis, entry.safe_id)
        if path is not None and not path.is_file():
            return True
    return False


def ranked_entries(analysis: Path) -> list[CoachingIndexEntry]:
    """Coaching moments in video-time order, matching the public review."""
    index = read_coaching_index_or_none(coach_inputs_dir(analysis))
    if index is None:
        return []
    return sorted(index.entries, key=_chrono)


def _chrono(entry: CoachingIndexEntry) -> tuple[float, int]:
    return (entry.video_time, 10**9 if entry.rank is None else entry.rank)


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
    events = _load_game_events(analysis)
    for entry in ranked_entries(analysis):
        _extract_one(
            analysis,
            video,
            entry,
            events,
            submission_id=submission_id,
            run=run,
        )


def _extract_one(
    analysis: Path,
    video: Path,
    entry: CoachingIndexEntry,
    events: list,
    *,
    submission_id: str,
    run: Runner | None,
) -> None:
    safe_id = entry.safe_id
    dest = frame_path(analysis, safe_id)
    if dest is None or dest.is_file() or not video.is_file():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(f"{dest.stem}.part.jpg")
    try:
        at = moment_seek(analysis, entry, events)
        code = _invoke(ffmpeg_argv(video, partial, at), run)
    except (OSError, subprocess.TimeoutExpired):
        _drop(partial)
        _warn(submission_id, safe_id)
        return
    if code != 0 or not partial.is_file():
        _drop(partial)
        _warn(submission_id, safe_id)
        return
    partial.replace(dest)


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


def _death_cues(
    analysis: Path,
    entry: CoachingIndexEntry,
    events: list | None,
) -> list[float]:
    """Eligible pre-death times. An unreadable source contributes nothing."""
    death = float(entry.video_time)
    loaded = events if events is not None else _load_game_events(analysis)
    return _low_ink_times(loaded, death) + _special_times(analysis, entry, death)


def _latest_cue(death: float, cues: list[float]) -> float | None:
    """Latest timestamp in ``[death - 8, death)``, or None."""
    eligible = [time for time in cues if _in_window(time, death)]
    if not eligible:
        return None
    return max(eligible)


def _in_window(timestamp: float, death: float) -> bool:
    """True for a cue at the lookback edge and false at the death itself."""
    return death - _LOOKBACK_SECONDS <= timestamp < death


def _load_game_events(analysis: Path) -> list:
    """``game_events`` from the vision manifest, or an empty list."""
    raw = _read_json(analysis / _MANIFEST)
    if not isinstance(raw, dict):
        return []
    events = raw.get("game_events")
    return events if isinstance(events, list) else []


def _low_ink_times(events: list, death: float) -> list[float]:
    times: list[float] = []
    for event in events:
        if not isinstance(event, dict) or event.get("event_type") != "low_ink":
            continue
        start = _timestamp(event.get("start_time"))
        if start is not None and _in_window(start, death):
            times.append(start)
    return times


def _special_times(analysis: Path, entry: CoachingIndexEntry, death: float) -> list[float]:
    document = _coach_document(analysis, entry)
    observations = _observations(document)
    times: list[float] = []
    for reading in observations:
        ready = _ready_time(reading, death)
        if ready is not None:
            times.append(ready)
    return times


def _coach_document(analysis: Path, entry: CoachingIndexEntry) -> dict | None:
    root = coach_inputs_dir(analysis).resolve()
    path = (root / entry.coach_input_json).resolve()
    if not path.is_relative_to(root):
        return None
    raw = _read_json(path)
    return raw if isinstance(raw, dict) else None


def _observations(document: dict | None) -> list:
    if document is None:
        return []
    context = document.get("primary_context")
    if not isinstance(context, dict):
        return []
    special = context.get("special")
    if not isinstance(special, dict):
        return []
    observations = special.get("observations")
    return observations if isinstance(observations, list) else []


def _ready_time(reading: object, death: float) -> float | None:
    """A visible full gauge inside the lookback. Invalid readings are skipped."""
    if not isinstance(reading, dict) or reading.get("coaching_valid") is False:
        return None
    if reading.get("visible") is not True or reading.get("ready") is not True:
        return None
    timestamp = _timestamp(reading.get("video_time"))
    if timestamp is None or not _in_window(timestamp, death):
        return None
    return timestamp


def _timestamp(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    timestamp = float(value)
    if timestamp != timestamp:
        return None
    return timestamp


def _read_json(path: Path) -> object | None:
    """Parsed JSON, or None when the file is missing or unreadable."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        logger.warning(
            "moment frame cue unreadable {} ({})",
            path.name,
            type(exc).__name__,
        )
        return None
