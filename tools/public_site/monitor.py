"""Read-only status for public submissions. Tokens and email stay out."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from public_site.app import open_storage
from public_site.models import Submission, VideoInput
from public_site.settings import PublicSettings
from public_site.store import PublicStore, parse_utc
from splatoon3_ai_coach.media.vision_manifest import VISION_MANIFEST_FILENAME

_GIB = 1024**3
_STATUSES = (
    "received",
    "validating",
    "queued",
    "processing",
    "complete",
    "failed",
    "expired",
)
_HEADERS = (
    "id",
    "status",
    "step",
    "game_language",
    "filename",
    "size",
    "duration",
    "created",
    "started",
    "elapsed",
    "error",
    "video",
)


def render_monitor(settings: PublicSettings, *, now: datetime | None = None) -> str:
    """Text report of submission state and disk use."""
    moment = now or datetime.now(UTC)
    store = PublicStore(settings.root)
    storage = open_storage(settings)
    submissions = store.list_submissions()
    lines = [
        _counts(submissions),
        f"unfinished {store.unfinished_count()} / {settings.max_queued}",
        "",
        _disk(settings),
        "",
        _table(submissions, store, storage, moment),
    ]
    return "\n".join(lines) + "\n"


def _counts(submissions: list[Submission]) -> str:
    found = {item.status: 0 for item in submissions}
    for item in submissions:
        found[item.status] = found.get(item.status, 0) + 1
    parts = [f"{name} {found[name]}" for name in _STATUSES if found.get(name)]
    return "counts " + (", ".join(parts) if parts else "none")


def _disk(settings: PublicSettings) -> str:
    usage = shutil.disk_usage(settings.root)
    percent = round(100 * usage.free / usage.total) if usage.total else 0
    videos = _tree_bytes(settings.root / "objects")
    analysis = _tree_bytes(settings.root / "analysis")
    return "\n".join(
        [
            f"Videos:   {_size(videos)}",
            f"Analysis: {_size(analysis)}",
            f"Free:     {_size(usage.free)} ({percent}%)",
        ]
    )


def _table(submissions, store, storage, now: datetime) -> str:
    rows = [_HEADERS]
    for submission in submissions:
        video = store.get_video(submission.id)
        rows.append(_row(submission, video, storage, now, _game_language(submission)))
    widths = [max(len(row[index]) for row in rows) for index in range(len(_HEADERS))]
    return "\n".join(
        "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row)) for row in rows
    )


def _game_language(submission: Submission) -> str:
    """Detected game language from a finished analysis manifest, else a dash."""
    if not submission.analysis_dir:
        return "—"
    path = Path(submission.analysis_dir) / VISION_MANIFEST_FILENAME
    try:
        language = json.loads(path.read_text(encoding="utf-8"))["analysis"]["language"]
    except (OSError, ValueError, KeyError, TypeError):
        return "—"
    return language if language in {"en", "ja"} else "—"


def _row(
    submission: Submission,
    video: VideoInput | None,
    storage,
    now: datetime,
    game_language: str,
) -> tuple[str, ...]:
    return (
        submission.id,
        submission.status,
        submission.step,
        game_language,
        _filename(video),
        _size(video.size_bytes) if video else "—",
        _clock(video.duration_seconds if video else None),
        submission.created_at,
        submission.processing_started_at or "—",
        _elapsed(submission, now),
        _plain(submission.error),
        _stored(storage, video),
    )


def _elapsed(submission: Submission, now: datetime) -> str:
    if submission.status != "processing" or not submission.processing_started_at:
        return "—"
    seconds = int((now - parse_utc(submission.processing_started_at)).total_seconds())
    seconds = max(seconds, 0)
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _stored(storage, video: VideoInput | None) -> str:
    if video is None or not video.object_key:
        return "no"
    try:
        present = storage.head(video.object_key) is not None
    except OSError:
        return "unknown"
    return "yes" if present else "no"


def _filename(video: VideoInput | None) -> str:
    if video is None or not video.original_filename:
        return "—"
    return _plain(video.original_filename)[:40]


def _plain(value: str | None) -> str:
    if not value:
        return "—"
    return " ".join(value.split())


def _clock(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = max(int(seconds), 0)
    return f"{total // 60}:{total % 60:02d}"


def _size(num: int) -> str:
    if num >= _GIB / 10:
        return f"{num / _GIB:.1f} GiB"
    if num >= 1024**2 / 10:
        return f"{num / 1024**2:.1f} MiB"
    return f"{num / 1024:.1f} KiB"


def _tree_bytes(path) -> int:
    if not path.is_dir():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
