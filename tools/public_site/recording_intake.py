"""Operator upload of one manual review-code recording into the existing pipeline.

The site owns the key, type and verification; the operator's browser only
transfers bytes to a presigned PUT. Nothing here trusts browser metadata: the
stored object is checked by HEAD and probed before the job can be queued.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from public_site.errors import RequestRejected
from public_site.media_probe import ProbeFacts, ProbeRejected
from public_site.models import PresignedUpload, Submission, VideoInput
from public_site.service import _declared_file

if TYPE_CHECKING:
    from public_site.recording_service import RecordingService

INTAKE_SUFFIX = ".mp4"
INTAKE_TYPE = "video/mp4"
INTAKE_FILENAME = "source.mp4"
BAD_INTAKE_FILE = "Upload one MP4 recording (video/mp4)."
STALE_GRANT = "The upload grant changed. Reload and upload again."


def intake_key(submission_id: str) -> str:
    """The one source key for a review-code submission; never derived from input."""
    return f"submissions/{submission_id}/{INTAKE_FILENAME}"


class RecordingUploadRequest(BaseModel):
    """Operator's declared file; checked here and again against the stored object."""

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(ge=1)


class RecordingUploadConfirm(BaseModel):
    """Version returned by the upload grant."""

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=0)


class RecordingUploadGrant(BaseModel):
    """Presigned PUT plus the job version the confirm call must quote."""

    upload: PresignedUpload
    version: int


class RecordingIntake:
    """Grant, verify and queue one operator-uploaded recording."""

    def __init__(self, recordings: RecordingService) -> None:
        self.recordings = recordings
        self.service = recordings.service
        self.database = recordings.database

    def request_upload(
        self, submission_id: str, actor_id: str, body: RecordingUploadRequest
    ) -> RecordingUploadGrant:
        """Validate the declared file, grant the upload, and presign the fixed key."""
        suffix, mime = _declared_file(
            body.filename,
            body.content_type,
            body.size_bytes,
            self.service.settings.max_video_bytes,
        )
        if suffix != INTAKE_SUFFIX or mime != INTAKE_TYPE:
            raise RequestRejected(400, BAD_INTAKE_FILE)
        version = self.database.grant_upload(
            submission_id, actor_id, body.expected_version
        )
        key = intake_key(submission_id)
        self.service.store.save_video(
            VideoInput(
                submission_id=submission_id,
                object_key=key,
                original_filename=INTAKE_FILENAME,
                size_bytes=body.size_bytes,
                content_type=INTAKE_TYPE,
            )
        )
        upload = self.service.storage.presign_put(key, INTAKE_TYPE, body.size_bytes)
        return RecordingUploadGrant(upload=upload, version=version)

    def confirm_upload(
        self, submission_id: str, actor_id: str, body: RecordingUploadConfirm
    ) -> None:
        """Verify the stored object, then queue it. Safe to repeat after success."""
        with self.service._probe_locks.hold(submission_id):
            row = self.database.get(submission_id)
            if row is None:
                raise RequestRejected(404, "Recording job not found.")
            if row["state"] == "recording":
                self._verify(submission_id, actor_id, body.expected_version)
            elif row["state"] != "uploading":
                raise RequestRejected(409, "No recording upload is pending.")
            self._queue(submission_id)

    def admits(self, submission: Submission) -> bool:
        """Worker eligibility: verified stored source and a committed queue transition."""
        if submission.input_type != "review_code" or submission.status != "queued":
            return False
        video = self.service.store.get_video(submission.id)
        if not _stored(video, submission.id):
            return False
        try:
            return self.database.analysis_admitted(submission.id)
        except Exception as exc:
            logger.warning(
                "review-code admission check unavailable for {} ({})",
                submission.id,
                type(exc).__name__,
            )
            return False

    def _verify(self, submission_id: str, actor_id: str, expected_version: int) -> None:
        video = self.service.store.get_video(submission_id)
        if video is None or video.object_key != intake_key(submission_id):
            raise RequestRejected(400, "No recording upload was granted.")
        try:
            self.service._require_owned(video)
            facts = self._probe(video)
        except RequestRejected as exc:
            self.database.record_upload_rejected(submission_id, actor_id, exc.detail)
            raise
        video.duration_seconds = facts.duration_seconds
        video.width = facts.width
        video.height = facts.height
        self.service.store.save_video(video)
        audit = {
            "size_bytes": video.size_bytes,
            "duration_seconds": facts.duration_seconds,
            "width": facts.width,
            "height": facts.height,
        }
        if not self.database.verify_upload(
            submission_id, actor_id, expected_version, audit
        ):
            raise RequestRejected(409, STALE_GRANT)

    def _probe(self, video: VideoInput) -> ProbeFacts:
        """Probe the stored object; a rejected file is deleted, as on the upload path."""
        svc = self.service
        dest = svc._probe_dest(video.submission_id, video.object_key)
        path, temporary = svc.storage.local_file(video.object_key, dest)
        try:
            return svc.probe(path)
        except ProbeRejected as exc:
            svc.storage.delete(video.object_key)
            raise RequestRejected(400, exc.detail) from None
        finally:
            if temporary and path.is_file():
                path.unlink()

    def _queue(self, submission_id: str) -> None:
        """Commit the queue transition, then publish it to the worker's projection.

        A crash between the two leaves the gate queued and the projection
        received; repeating the confirm call finishes the projection.
        """
        won = self.database.queue_for_analysis(submission_id)
        if not won and not self.database.analysis_admitted(submission_id):
            raise RequestRejected(409, "The recording is not eligible for analysis.")
        svc = self.service
        submission = svc.store.get(submission_id)
        video = svc.store.get_video(submission_id)
        if submission is None or video is None or submission.status != "received":
            return
        facts = _facts(video)
        if facts is None:
            raise RequestRejected(409, "The verified recording facts are missing.")
        svc._queue(submission, video, facts, svc.notices.read_sealed(submission_id) or "")


def _stored(video: VideoInput | None, submission_id: str) -> bool:
    return (
        video is not None
        and video.object_key == intake_key(submission_id)
        and video.upload_state == "stored"
        and _facts(video) is not None
    )


def _facts(video: VideoInput) -> ProbeFacts | None:
    if not video.duration_seconds or not video.width or not video.height:
        return None
    return ProbeFacts(video.duration_seconds, video.width, video.height, "")
