"""Index analysis directories as runs without reinterpreting manifests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from vmv_site.models import RunSummary
from vmv_site.settings import PlatformSettings
from vmv_site.store import PlatformStore

_VIDEO_SUFFIXES = (".mov", ".mp4", ".m4v")


def list_runs(store: PlatformStore) -> list[RunSummary]:
    """Every analysis folder that contains a vision manifest."""
    root = store.settings.analysis_root
    if not root.is_dir():
        return []
    runs = []
    for folder in sorted(root.iterdir(), key=lambda path: path.name):
        if not folder.is_dir() or folder.name.startswith("_"):
            continue
        if not (folder / "vision_manifest.json").is_file():
            continue
        runs.append(summarize_run(store, folder.name))
    runs.sort(key=lambda item: item.created_at or "", reverse=True)
    return runs


def summarize_run(store: PlatformStore, run_id: str) -> RunSummary:
    """Metadata for one run. Missing video stays listed."""
    folder = run_dir(store.settings, run_id)
    manifest = _read_json(folder / "vision_manifest.json") or {}
    identity = _read_json(folder / "match_identity.json") or {}
    analysis = manifest.get("analysis") if isinstance(manifest.get("analysis"), dict) else {}
    timing = manifest.get("timing") if isinstance(manifest.get("timing"), dict) else {}
    link = store.read_link(run_id) or {}
    video_id = link.get("video_id")
    video_path = resolve_run_video(store, run_id, video_id)
    language = analysis.get("language") if analysis.get("language") in {"en", "ja"} else None
    duration = timing.get("video_duration_seconds")
    frames = timing.get("cadence_frame_count")
    created = None
    manifest_path = folder / "vision_manifest.json"
    if manifest_path.is_file():
        created = _mtime_iso(manifest_path)
    return RunSummary(
        id=run_id,
        video_id=video_id,
        job_id=link.get("job_id"),
        source_filename=link.get("source_filename") or run_id,
        created_at=created,
        duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
        frame_count=int(frames) if isinstance(frames, int) else None,
        language=language,
        stage_id=identity.get("stage_id") if isinstance(identity.get("stage_id"), str) else None,
        battle_mode_id=(
            identity.get("battle_mode_id")
            if isinstance(identity.get("battle_mode_id"), str)
            else None
        ),
        video_available=video_path is not None,
    )


def run_dir(settings: PlatformSettings, run_id: str) -> Path:
    """Analysis directory for a run id. Rejects traversal."""
    if not run_id or run_id.startswith("_") or "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        raise KeyError(run_id)
    folder = (settings.analysis_root / run_id).resolve()
    root = settings.analysis_root.resolve()
    if folder.parent != root:
        raise KeyError(run_id)
    return folder


class RunBusy(Exception):
    """A queued or running job still owns this analysis."""


def remove_run(store: PlatformStore, run_id: str) -> None:
    """Delete one analysis folder and an upload no other run still links."""
    folder = run_dir(store.settings, run_id)
    if not (folder / "vision_manifest.json").is_file():
        raise KeyError(run_id)
    if _run_is_busy(store, run_id):
        raise RunBusy(run_id)
    video_id = (store.read_link(run_id) or {}).get("video_id")
    shutil.rmtree(folder)
    if video_id and not _upload_still_referenced(store, video_id):
        store.delete_upload(video_id)


def _run_is_busy(store: PlatformStore, run_id: str) -> bool:
    return any(
        job.run_id == run_id and job.status in {"queued", "running"}
        for job in store.list_jobs()
    )


def _upload_still_referenced(store: PlatformStore, video_id: str) -> bool:
    root = store.settings.analysis_root
    if not root.is_dir():
        return False
    for folder in root.iterdir():
        if not folder.is_dir() or folder.name.startswith("_"):
            continue
        link = store.read_link(folder.name)
        if link and link.get("video_id") == video_id:
            return True
    return False


def resolve_run_video(
    store: PlatformStore, run_id: str, video_id: str | None
) -> Path | None:
    """Stored upload for platform runs, else a movie matched by folder name."""
    if video_id:
        stored = store.video_path(video_id)
        if stored is not None:
            return stored
    movies = store.settings.movies_dir
    for suffix in _VIDEO_SUFFIXES:
        candidate = movies / f"{run_id}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def resolve_media(settings: PlatformSettings, run_id: str, relpath: str) -> Path:
    """File under the run directory. Rejects absolute paths and ``..``."""
    if not relpath or relpath.startswith("/") or relpath.startswith("\\"):
        raise ValueError("absolute path rejected")
    if ".." in Path(relpath).parts:
        raise ValueError("path traversal rejected")
    folder = run_dir(settings, run_id)
    target = (folder / relpath).resolve()
    if folder not in target.parents and target != folder:
        raise ValueError("path escapes run directory")
    if not target.is_file():
        raise FileNotFoundError(relpath)
    if target.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise ValueError("media type not allowed")
    return target


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else None


def _mtime_iso(path: Path) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
