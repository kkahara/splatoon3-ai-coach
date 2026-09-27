"""Filesystem repository for videos, jobs, and run links.

JSON files are the first storage backend. Callers never pass a client path.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

from vmv_site.models import JobRecord, VideoRecord
from vmv_site.settings import PlatformSettings

_ALLOWED_SUFFIXES = {".mov", ".mp4", ".m4v"}


def utc_now() -> str:
    """ISO-8601 timestamp in UTC."""
    return datetime.now(UTC).isoformat()


def new_id() -> str:
    """Stable id that is safe as a single path segment."""
    return uuid.uuid4().hex


class PlatformStore:
    """Read and write platform metadata under ``settings.platform_root``."""

    def __init__(self, settings: PlatformSettings) -> None:
        self.settings = settings
        self.videos_dir = settings.platform_root / "videos"
        self.jobs_dir = settings.platform_root / "jobs"
        self.videos_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)

    def save_upload(self, filename: str, data: bytes) -> VideoRecord:
        """Store an uploaded video and return its record."""
        suffix = Path(filename).suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            raise ValueError(f"unsupported video format: {suffix or '(none)'}")
        if not data:
            raise ValueError("empty upload")
        video_id = new_id()
        folder = self.videos_dir / video_id
        folder.mkdir(parents=True, exist_ok=False)
        stored = folder / f"source{suffix}"
        stored.write_bytes(data)
        record = VideoRecord(
            id=video_id,
            original_filename=Path(filename).name,
            size_bytes=len(data),
            suffix=suffix,
            uploaded_at=utc_now(),
        )
        self._write_json(folder / "video.json", record.model_dump())
        return record

    def get_video(self, video_id: str) -> VideoRecord | None:
        """Return one video record, or None when the id is unknown."""
        path = self._video_json(video_id)
        if path is None or not path.is_file():
            return None
        return VideoRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list_videos(self) -> list[VideoRecord]:
        """Return every stored video, newest first."""
        records = []
        for folder in self.videos_dir.iterdir():
            record = self.get_video(folder.name)
            if record is not None:
                records.append(record)
        records.sort(key=lambda item: item.uploaded_at, reverse=True)
        return records

    def video_path(self, video_id: str) -> Path | None:
        """Absolute path of a stored video file."""
        record = self.get_video(video_id)
        if record is None:
            return None
        path = self.videos_dir / video_id / f"source{record.suffix}"
        return path if path.is_file() else None

    def create_job(self, video_id: str, language: str) -> JobRecord:
        """Queue an analysis job for a stored video."""
        if language not in {"en", "ja"}:
            raise ValueError("language must be en or ja")
        if self.get_video(video_id) is None:
            raise KeyError(video_id)
        job = JobRecord(
            id=new_id(),
            video_id=video_id,
            language=language,  # type: ignore[arg-type]
            submitted_at=utc_now(),
        )
        self.save_job(job)
        return job

    def create_run_job(
        self,
        run_id: str,
        kind: str,
        scenario_id: str | None = None,
    ) -> JobRecord:
        """Queue a coaching or experiment job for an existing run."""
        if kind not in {"coach", "coach-llm", "coach-experiment"}:
            raise ValueError("unsupported run job")
        if kind == "coach-experiment" and not scenario_id:
            raise ValueError("scenario_id is required")
        job = JobRecord(
            id=new_id(),
            kind=kind,  # type: ignore[arg-type]
            run_id=_segment(run_id),
            scenario_id=scenario_id,
            submitted_at=utc_now(),
        )
        self.save_job(job)
        return job

    def save_job(self, job: JobRecord) -> None:
        """Replace a job record atomically."""
        path = self.jobs_dir / f"{job.id}.json"
        _atomic_write(path, job.model_dump_json(indent=2) + "\n")

    def get_job(self, job_id: str) -> JobRecord | None:
        """Return one job, or None."""
        path = self.jobs_dir / f"{_segment(job_id)}.json"
        if not path.is_file():
            return None
        return JobRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def list_jobs(self) -> list[JobRecord]:
        """Return every job, newest submission first."""
        jobs = []
        for path in self.jobs_dir.glob("*.json"):
            jobs.append(JobRecord.model_validate_json(path.read_text(encoding="utf-8")))
        jobs.sort(key=lambda item: item.submitted_at, reverse=True)
        return jobs

    def next_queued(self) -> JobRecord | None:
        """Oldest queued job, if any."""
        queued = [job for job in self.list_jobs() if job.status == "queued"]
        if not queued:
            return None
        queued.sort(key=lambda item: item.submitted_at)
        return queued[0]

    def log_path(self, job_id: str) -> Path:
        """Log file for one job. The API returns a tail, not this path."""
        return self.jobs_dir / f"{_segment(job_id)}.log"

    def link_path(self, run_id: str) -> Path:
        """Sidecar that ties a finished run to its video and job."""
        return self.settings.analysis_root / _segment(run_id) / "platform_link.json"

    def write_link(self, run_id: str, video_id: str, job_id: str, filename: str) -> None:
        """Record which video and job produced a run."""
        payload = {
            "video_id": video_id,
            "job_id": job_id,
            "source_filename": filename,
        }
        _atomic_write(self.link_path(run_id), json.dumps(payload, indent=2) + "\n")

    def read_link(self, run_id: str) -> dict[str, str] | None:
        """Return the platform link for a run, if this platform created it."""
        path = self.link_path(run_id)
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        return {str(key): str(value) for key, value in raw.items()}

    def delete_upload(self, video_id: str) -> None:
        """Remove one platform upload directory. Does not touch other movies."""
        folder = (self.videos_dir / _segment(video_id)).resolve()
        root = self.videos_dir.resolve()
        if folder.parent != root:
            raise ValueError("invalid id")
        if folder.is_dir():
            shutil.rmtree(folder)

    def _video_json(self, video_id: str) -> Path | None:
        try:
            segment = _segment(video_id)
        except ValueError:
            return None
        return self.videos_dir / segment / "video.json"

    def _write_json(self, path: Path, payload: dict) -> None:
        _atomic_write(path, json.dumps(payload, indent=2) + "\n")


def _segment(value: str) -> str:
    """Reject ids that are not a single path segment."""
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("invalid id")
    return value


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` via a temp file and rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
