"""Create and validate submissions. Does not run the analysis pipeline."""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from loguru import logger

from public_site.accounts import AccountStore
from public_site.errors import (
    BAD_EMAIL,
    BAD_FILE,
    BAD_NAME,
    BAD_SIZE,
    CAPACITY,
    EXPIRED,
    PROBE_SIZE,
    RATE_LIMIT,
    UPLOAD_MISMATCH,
    UPLOAD_MISSING,
    UPLOAD_UNFINISHED,
    VERIFY,
    RequestRejected,
)
from public_site.frames import extract_frames, frame_path, missing_frames, ranked_entries
from public_site.limits import RateLimiter
from public_site.media_probe import ProbeFacts, ProbeRejected, probe_file
from public_site.models import (
    CreateSubmissionResponse,
    PublicSubmissionResponse,
    Submission,
    VideoInput,
)
from public_site.notices import NoticeSender
from public_site.results import load_result
from public_site.settings import PublicSettings
from public_site.storage import LocalStorage, R2Storage
from public_site.store import PublicStore, add_days, parse_utc, utc_now
from public_site.tokens import hash_ip, hash_token, new_token

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TYPES = frozenset({"video/mp4", "video/quicktime"})
_SUFFIXES = frozenset({".mp4", ".mov"})
Probe = Callable[[Path], ProbeFacts]
Verifier = Callable[[str], bool]
Storage = LocalStorage | R2Storage


class SubmissionService:
    """Turnstile, limits, object checks, and ffprobe before a video is queued."""

    def __init__(
        self,
        settings: PublicSettings,
        store: PublicStore,
        storage: Storage,
        notices: NoticeSender,
        *,
        probe: Probe | None = None,
        accounts: AccountStore | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.storage = storage
        self.notices = notices
        self.accounts = accounts
        self.probe = probe or (lambda path: probe_file(path, settings))
        self.limiter = RateLimiter(
            store,
            limit=settings.submissions_per_hour,
            window_seconds=settings.submission_window_seconds,
        )

    def create(
        self,
        *,
        turnstile_token: str,
        filename: str,
        size_bytes: int,
        content_type: str,
        language: str,
        email: str | None,
        notify: bool,
        display_name: str | None,
        ip: str,
        verifier: Verifier,
        now: datetime | None = None,
        user_id: str | None = None,
        retention_days: int | None = None,
    ) -> CreateSubmissionResponse:
        """Verify, limit, then create. A failed Turnstile check writes nothing."""
        if not verifier(turnstile_token):
            raise RequestRejected(400, VERIFY)
        moment = now or datetime.now(UTC)
        if not self.limiter.allow(hash_ip(ip), moment):
            raise RequestRejected(429, RATE_LIMIT)
        if self.store.unfinished_count() >= self.settings.max_queued:
            raise RequestRejected(429, CAPACITY)
        suffix, mime = _declared_file(filename, content_type, size_bytes, self.settings)
        notice_email = _notice_email(email, notify)
        name = _display_name(display_name)
        return self._persist(
            suffix=suffix,
            mime=mime,
            size_bytes=size_bytes,
            original_filename=Path(filename).name,
            language=language,
            email=notice_email,
            notify=notify,
            display_name=name,
            now=moment,
            user_id=user_id,
            retention_days=retention_days,
        )

    def confirm(self, token: str) -> PublicSubmissionResponse:
        """Verify the stored object, probe it, and queue only on success."""
        submission = self._require(token)
        if submission.status != "received":
            return self.project(submission)
        video = self.store.get_video(submission.id)
        if video is None:
            raise RequestRejected(400, UPLOAD_MISSING)
        self._require_owned(video)
        return self._probe_and_queue(submission, video, token)

    def view(self, token: str) -> PublicSubmissionResponse:
        """Project one submission. Unknown tokens are a 404."""
        return self.project(self._require(token))

    def project(self, submission: Submission) -> PublicSubmissionResponse:
        """Narrow public view. Internal paths and worker fields are omitted."""
        result = None
        if submission.status == "complete" and submission.analysis_dir:
            self._fill_frames(submission)
            result = load_result(Path(submission.analysis_dir))
        error = submission.error if submission.status in {"failed", "expired"} else None
        return PublicSubmissionResponse(
            status=submission.status,
            step=submission.step,
            created_at=submission.created_at,
            expires_at=submission.expires_at,
            display_name=submission.display_name,
            error=error,
            match_seconds=_match_seconds(self.store.get_video(submission.id)),
            result=result,
        )

    def moment_frame(self, token: str, index: int) -> Path:
        """JPEG for one ranked moment. A missing still is a 404."""
        return self.frame_file(self._require(token), index)

    def frame_file(self, submission: Submission, index: int) -> Path:
        """JPEG for one ranked moment on a known submission."""
        path = self._stored_frame(submission, index)
        if path is None:
            raise RequestRejected(404, "not found")
        return path

    def maintain(self, now: datetime) -> None:
        """Release abandoned uploads and expire old submissions."""
        self._abandon(now)
        self._expire(now)

    def _persist(
        self,
        *,
        suffix: str,
        mime: str,
        size_bytes: int,
        original_filename: str,
        language: str,
        email: str | None,
        notify: bool,
        display_name: str | None,
        now: datetime,
        user_id: str | None = None,
        retention_days: int | None = None,
    ) -> CreateSubmissionResponse:
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        submission_id = uuid.uuid4().hex
        token = new_token()
        days = self.settings.retention_days if retention_days is None else retention_days
        submission = Submission(
            id=submission_id,
            access_token_hash=hash_token(token),
            email=email,
            display_name=display_name,
            notify=notify,
            user_id=user_id,
            language=language,  # type: ignore[arg-type]
            created_at=stamp,
            updated_at=stamp,
            expires_at=add_days(stamp, days),
        )
        video = VideoInput(
            submission_id=submission_id,
            object_key=f"submissions/{submission_id}/source{suffix}",
            original_filename=original_filename,
            size_bytes=size_bytes,
            content_type=mime,
        )
        self.store.save_submission(submission)
        self.store.save_video(video)
        self.store.remember_token(submission_id, submission.access_token_hash)
        self._remember_account(user_id, submission)
        if notify:
            self.notices.seal(submission_id, token)
        upload = self.storage.presign_put(video.object_key, mime, size_bytes)
        return CreateSubmissionResponse(
            token=token,
            review_path=f"/review/{token}",
            upload=upload,
        )

    def _probe_and_queue(
        self,
        submission: Submission,
        video: VideoInput,
        token: str,
    ) -> PublicSubmissionResponse:
        submission.status = "validating"
        submission.updated_at = utc_now()
        self.store.save_submission(submission)
        dest = self.settings.root / "probe" / f"{submission.id}{Path(video.object_key).suffix}"
        path, temporary = self.storage.local_file(video.object_key, dest)
        try:
            facts = self.probe(path)
        except ProbeRejected as exc:
            self._fail(submission, video, exc.detail)
            return self.project(submission)
        finally:
            if temporary and path.is_file():
                path.unlink()
        self._queue(submission, video, facts, token)
        return self.project(submission)

    def _queue(
        self,
        submission: Submission,
        video: VideoInput,
        facts: ProbeFacts,
        token: str,
    ) -> None:
        video.duration_seconds = facts.duration_seconds
        video.width = facts.width
        video.height = facts.height
        video.upload_state = "stored"
        submission.status = "queued"
        submission.step = "queued"
        submission.updated_at = utc_now()
        self.store.save_video(video)
        self.notices.send(submission, "received", token)
        self.store.save_submission(submission)

    def _fail(self, submission: Submission, video: VideoInput, detail: str) -> None:
        self.storage.delete(video.object_key)
        video.upload_state = "rejected"
        submission.status = "failed"
        submission.error = detail
        submission.updated_at = utc_now()
        self.store.save_video(video)
        self.note_terminal(submission, "failed")

    def note_terminal(self, submission: Submission, kind: str) -> None:
        """Send a ready or failed notice when a sealed token still exists."""
        token = self.notices.read_sealed(submission.id)
        if token:
            self.notices.send(submission, kind, token)
            self.notices.clear_sealed(submission.id)
        self.store.save_submission(submission)

    def release_source_video(self, submission: Submission) -> None:
        """Delete the source video after a completed review is stored.

        Dev mode keeps the file. A delete error leaves ``object_key`` set so
        the expiry sweep can try again. The coaching result stays available.
        """
        if self.settings.dev_mode:
            return
        video = self.store.get_video(submission.id)
        if video is None or not video.object_key:
            return
        if not self._delete_source(video):
            return
        video.object_key = ""
        self.store.save_video(video)

    def _delete_source(self, video: VideoInput) -> bool:
        try:
            self.storage.delete(video.object_key)
            gone = self.storage.head(video.object_key) is None
        except OSError:
            gone = False
        if gone:
            return True
        logger.error("source video delete failed for submission {}", video.submission_id)
        return False

    def _require_owned(self, video: VideoInput) -> None:
        stat = self.storage.head(video.object_key)
        prefix = f"submissions/{video.submission_id}/"
        if stat is None:
            raise RequestRejected(400, UPLOAD_MISSING)
        if stat.key != video.object_key or not video.object_key.startswith(prefix):
            raise RequestRejected(400, UPLOAD_MISMATCH)
        if stat.size_bytes != video.size_bytes:
            raise RequestRejected(400, UPLOAD_MISMATCH)
        if stat.size_bytes > self.settings.max_video_bytes:
            self.storage.delete(video.object_key)
            raise RequestRejected(400, PROBE_SIZE)

    def _stored_frame(self, submission: Submission, index: int) -> Path | None:
        if submission.status != "complete" or not submission.analysis_dir:
            return None
        analysis = Path(submission.analysis_dir)
        entries = ranked_entries(analysis)
        if index < 0 or index >= len(entries):
            return None
        dest = frame_path(analysis, str(entries[index].get("safe_id") or ""))
        if dest is None:
            return None
        if not dest.is_file():
            self._fill_frames(submission)
        return dest if dest.is_file() else None

    def _fill_frames(self, submission: Submission) -> None:
        """Grab missing stills when the source video is still stored."""
        if not submission.analysis_dir:
            return
        analysis = Path(submission.analysis_dir)
        if not missing_frames(analysis):
            return
        video = self.store.get_video(submission.id)
        if video is None or not video.object_key:
            return
        if self.storage.head(video.object_key) is None:
            return
        dest = analysis / Path(video.object_key).name
        try:
            path, temporary = self.storage.local_file(video.object_key, dest)
        except (FileNotFoundError, OSError):
            logger.warning("moment frame source unavailable for submission {}", submission.id)
            return
        try:
            extract_frames(analysis, path, submission_id=submission.id)
        finally:
            if temporary and path.is_file():
                path.unlink()

    def _require(self, token: str) -> Submission:
        found = self.store.get_by_hash(hash_token(token))
        if found is None:
            raise RequestRejected(404, "not found")
        return found

    def _abandon(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.settings.upload_url_seconds)
        for submission in self.store.list_submissions():
            if submission.status != "received" or parse_utc(submission.created_at) > cutoff:
                continue
            self._abandon_one(submission)

    def _abandon_one(self, submission: Submission) -> None:
        video = self.store.get_video(submission.id)
        if video is not None and self.storage.head(video.object_key) is not None:
            token = self.notices.read_sealed(submission.id) or ""
            try:
                self._require_owned(video)
            except RequestRejected as exc:
                self._fail(submission, video, exc.detail)
                return
            self._probe_and_queue(submission, video, token)
            return
        submission.status = "failed"
        submission.error = UPLOAD_UNFINISHED
        submission.updated_at = utc_now()
        self.note_terminal(submission, "failed")

    def _expire(self, now: datetime) -> None:
        for submission in self.store.list_submissions():
            if submission.status in {"expired", "processing"}:
                continue
            if parse_utc(submission.expires_at) > now:
                continue
            self._expire_one(submission)

    def _expire_one(self, submission: Submission) -> None:
        video = self.store.get_video(submission.id)
        if video is not None and video.object_key:
            self.storage.delete(video.object_key)
            video.object_key = ""
            video.upload_state = "rejected"
            self.store.save_video(video)
        if submission.analysis_dir:
            _remove_tree(Path(submission.analysis_dir))
            submission.analysis_dir = None
        self.notices.clear_sealed(submission.id)
        if self.accounts is not None:
            self.accounts.drop_history(submission.id)
        submission.email = None
        submission.display_name = None
        submission.user_id = None
        submission.status = "expired"
        submission.error = EXPIRED
        submission.updated_at = utc_now()
        self.store.save_submission(submission)


    def _remember_account(self, user_id: str | None, submission: Submission) -> None:
        if user_id and self.accounts is not None:
            self.accounts.record_history(
                user_id,
                submission.id,
                submission.created_at,
                submission.expires_at,
            )


def _declared_file(
    filename: str,
    content_type: str,
    size_bytes: int,
    settings: PublicSettings,
) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    mime = content_type.split(";", 1)[0].strip().lower()
    if suffix not in _SUFFIXES or mime not in _TYPES:
        raise RequestRejected(400, BAD_FILE)
    if size_bytes < 1 or size_bytes > settings.max_video_bytes:
        raise RequestRejected(400, BAD_SIZE)
    return suffix, mime


def _notice_email(email: str | None, notify: bool) -> str | None:
    if not notify:
        return None
    text = (email or "").strip()
    if not _EMAIL.fullmatch(text):
        raise RequestRejected(400, BAD_EMAIL)
    return text


def _display_name(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(value.split())
    if not text:
        return None
    if len(text) > 40 or any(ord(char) < 32 for char in text):
        raise RequestRejected(400, BAD_NAME)
    return text


def _remove_tree(path: Path) -> None:
    import shutil

    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _match_seconds(video: object) -> int | None:
    """Whole seconds from the probed video, when that length was stored."""
    raw = getattr(video, "duration_seconds", None)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if raw <= 0:
        return None
    return int(raw)
