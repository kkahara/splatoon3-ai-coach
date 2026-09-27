"""Domain records for videos, jobs, and runs.

Filesystem paths stay inside the repository layer. API models do not
carry client-supplied server paths.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]
JobKind = Literal["analyze", "coach", "coach-llm", "coach-experiment"]
VideoStatus = Literal["stored", "invalid"]


class VideoRecord(BaseModel):
    """One uploaded source video owned by the backend."""

    id: str
    original_filename: str
    size_bytes: int = Field(ge=0)
    suffix: str
    uploaded_at: str
    status: VideoStatus = "stored"


class JobError(BaseModel):
    """Failure details safe to show in the admin UI."""

    message: str
    phase: str = ""
    exit_code: int | None = None
    log_tail: str = ""


class JobRecord(BaseModel):
    """One analyze, official coaching, or single-scenario experiment attempt.

    ``kind`` defaults to ``analyze`` so job files written before coaching
    jobs existed still load. Coach and experiment jobs set ``run_id`` and
    leave ``video_id`` empty.
    """

    id: str
    kind: JobKind = "analyze"
    video_id: str = ""
    language: Literal["en", "ja"] = "en"
    scenario_id: str | None = None
    status: JobStatus = "queued"
    phase: str = "queued"
    submitted_at: str
    started_at: str | None = None
    completed_at: str | None = None
    run_id: str | None = None
    error: JobError | None = None


class RunSummary(BaseModel):
    """A completed analysis directory, legacy or created by a job."""

    id: str
    video_id: str | None = None
    job_id: str | None = None
    source_filename: str | None = None
    created_at: str | None = None
    duration_seconds: float | None = None
    frame_count: int | None = None
    language: str | None = None
    stage_id: str | None = None
    battle_mode_id: str | None = None
    video_available: bool = False
    status: str = "completed"
