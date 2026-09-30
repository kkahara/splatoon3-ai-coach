"""Internal submission records and the narrow public API projections.

Routes return the public models only. They do not serialize ``Submission``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PublicStatus = Literal[
    "received",
    "validating",
    "queued",
    "processing",
    "complete",
    "failed",
    "expired",
]
PublicStep = Literal[
    "received",
    "checked",
    "queued",
    "analyzing",
    "coaching",
    "ready",
]
InputType = Literal["video", "review_code"]
SourceKind = Literal["upload", "nintendo_review_code"]
UploadState = Literal["pending", "stored", "rejected"]
LanguageCode = Literal["en", "ja"]
DisplayLocale = Literal["en", "ja"]


class Submission(BaseModel):
    """One private coaching request. The raw access token is not a field."""

    id: str
    access_token_hash: str
    input_type: InputType = "video"
    source: SourceKind = "upload"
    email: str | None = None
    display_name: str | None = None
    notify: bool = False
    user_id: str | None = None
    language: LanguageCode = "en"
    status: PublicStatus = "received"
    step: PublicStep = "received"
    created_at: str
    updated_at: str
    expires_at: str
    analysis_dir: str | None = None
    worker_id: str | None = None
    processing_started_at: str | None = None
    error: str | None = None
    notices_sent: list[str] = Field(default_factory=list)


class VideoInput(BaseModel):
    """Video object bound to one submission. The key is chosen by the server."""

    submission_id: str
    object_key: str
    original_filename: str
    size_bytes: int = Field(ge=0)
    content_type: str
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    upload_state: UploadState = "pending"


class ReviewCodeInput(BaseModel):
    """Reserved shape. No route, worker, or downloader writes this record."""

    submission_id: str
    review_code: str
    fetch_status: Literal["not_implemented"] = "not_implemented"
    fetched_video_object_key: str | None = None


class PresignedUpload(BaseModel):
    """Browser PUT target. Credentials for storage are not included."""

    url: str
    method: str = "PUT"
    headers: dict[str, str] = Field(default_factory=dict)
    expires_at: str


class CreateSubmissionResponse(BaseModel):
    """Issued once. This is the only response that contains the raw token."""

    token: str
    review_path: str
    upload: PresignedUpload


class LifecycleMark(BaseModel):
    """One observed event on a death card's context strip."""

    label: str
    title: str
    video_time: float = Field(ge=0)
    seconds_remaining: int | None = None
    clock: str | None = None
    anchor: bool = False


class CoachingMoment(BaseModel):
    """One ranked coaching unit safe to show on the public review page."""

    scenario_type: str
    video_time: float = Field(ge=0)
    statements: list[str] = Field(default_factory=list)
    assessment: str | None = None
    assessment_locale: DisplayLocale = "en"
    frame: bool = False
    heading: str | None = None
    marks: list[LifecycleMark] = Field(default_factory=list)
    gaps: list[str | None] = Field(default_factory=list)
    until_active_again: str | None = None
    recovery_context: str | None = None
    context: list[str] = Field(default_factory=list)
    recording_times: list[str] = Field(default_factory=list)


class PublicSubmissionResult(BaseModel):
    """Coaching material for a finished submission."""

    moments: list[CoachingMoment] = Field(default_factory=list)


class PublicSubmissionResponse(BaseModel):
    """Status projection. Paths, keys, tokens, and worker fields are absent."""

    status: PublicStatus
    step: PublicStep
    created_at: str
    expires_at: str
    display_name: str | None = None
    error: str | None = None
    match_seconds: int | None = None
    locale: DisplayLocale = "en"
    result: PublicSubmissionResult | None = None


class HistoryItem(BaseModel):
    """One owned submission on the past-coaching list."""

    id: str
    created_at: str
    expires_at: str
    status: PublicStatus
    step: PublicStep
    display_name: str | None = None


class PublicConfig(BaseModel):
    """Values the submit page needs. Secrets are not included."""

    turnstile_site_key: str | None = None
    dev_mode: bool = False
    max_video_bytes: int
    max_duration_seconds: int
    feedback: bool = False
