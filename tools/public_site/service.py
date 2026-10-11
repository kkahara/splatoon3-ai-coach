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
    ACCOUNT_RATE_LIMIT,
    BAD_EMAIL,
    BAD_FILE,
    BAD_NAME,
    BAD_SIZE,
    CAPACITY,
    EXPIRED,
    PAYMENT_NOT_REQUIRED,
    PAYMENT_UNAVAILABLE,
    PAYMENT_WINDOW,
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
from public_site.localize import Translator, localize_result
from public_site.media_probe import ProbeFacts, ProbeRejected, probe_file
from public_site.models import (
    CreateSubmissionResponse,
    DisplayLocale,
    PublicSubmissionResponse,
    Submission,
    VideoInput,
)
from public_site.notices import NoticeSender
from public_site.payments import FailureCategory, PaymentStore, PriceQuote, SubmissionGate
from public_site.results import load_result
from public_site.settings import PublicSettings
from public_site.storage import LocalStorage, R2Storage
from public_site.store import PublicStore, add_days, parse_utc, utc_now
from public_site.stripe_checkout import CheckoutEvent, CheckoutGateway, CheckoutSession
from public_site.telemetry import TelemetryStore, best_effort, classify_error
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
        translator: Translator | None = None,
        telemetry: TelemetryStore | None = None,
        payments: PaymentStore | None = None,
        checkout: CheckoutGateway | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.storage = storage
        self.notices = notices
        self.accounts = accounts
        self.translator = translator
        self.telemetry = telemetry
        self.payments = payments
        self.checkout = checkout
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
        quote = self._quote(user_id)
        privileged = self._privileged(user_id)
        if not privileged:
            self._check_limits(ip, user_id, moment)
        if self.store.unfinished_count() >= self.settings.max_queued:
            raise RequestRejected(429, CAPACITY)
        cap = None if privileged else self.settings.max_video_bytes
        suffix, mime = _declared_file(filename, content_type, size_bytes, cap)
        notice_email = _notice_email(email, notify)
        name = _display_name(display_name)
        created, submission = self._persist(
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
        created = self._open_payment(created, submission, quote, moment)
        if self.telemetry is not None:
            best_effort(
                "open_initial_run",
                self.telemetry.open_initial_run,
                submission.id,
                user_id=user_id,
                language=language,
            )
        return created

    def _privileged(self, user_id: str | None) -> bool:
        """True for an administrator, who skips the hourly and file-size caps."""
        if not user_id or self.accounts is None:
            return False
        return self.accounts.role_for(user_id) == "admin"

    def _within_size_cap(self, size_bytes: int, user_id: str | None) -> bool:
        """True when the object is allowed for this account."""
        if self._privileged(user_id):
            return True
        return size_bytes <= self.settings.max_video_bytes

    def _check_limits(self, ip: str, user_id: str | None, now: datetime) -> None:
        """Network cap for everyone, plus a per-account or per-network guest cap."""
        ip_key = hash_ip(ip)
        if user_id is None:
            own_key = "guest:" + ip_key
            own_limit = self.settings.guest_submissions_per_hour
        else:
            own_key = "user:" + hash_token(user_id)
            own_limit = self.settings.account_submissions_per_hour
        checks = [(ip_key, self.settings.submissions_per_hour), (own_key, own_limit)]
        full = self.limiter.allow_all(checks, now)
        if full is None:
            return
        if full == own_key and user_id is not None:
            raise RequestRejected(429, ACCOUNT_RATE_LIMIT)
        raise RequestRejected(429, RATE_LIMIT)

    def confirm(self, token: str) -> PublicSubmissionResponse:
        """Record the upload, then queue when payment is already satisfied."""
        submission = self._require(token)
        if submission.status != "received":
            return self.project(submission)
        if self.payments is not None:
            return self._confirm_gated(submission, token)
        return self._confirm_free(submission, token)

    def _confirm_free(self, submission: Submission, token: str) -> PublicSubmissionResponse:
        """Probe and queue immediately when this site is not charging."""
        if self.telemetry is not None:
            best_effort(
                "upload_confirmed",
                self.telemetry.upload_confirmed,
                submission.id,
            )
        video = self.store.get_video(submission.id)
        if video is None:
            raise RequestRejected(400, UPLOAD_MISSING)
        self._require_owned(video, submission.user_id)
        return self._probe_and_queue(submission, video, token)

    def view(self, token: str, locale: DisplayLocale = "en") -> PublicSubmissionResponse:
        """Project one submission. Unknown tokens are a 404."""
        return self.project(self._require(token), locale)

    def project(
        self, submission: Submission, locale: DisplayLocale = "en"
    ) -> PublicSubmissionResponse:
        """Narrow public view in ``locale``. Internal paths and worker fields are omitted.

        The locale only changes presentation: English artifacts are read as-is
        and nothing is re-analyzed or re-coached.
        """
        result = None
        coached = submission.outcome != "no_deaths"
        if submission.status == "complete" and coached and submission.analysis_dir:
            self._fill_frames(submission)
            analysis = Path(submission.analysis_dir)
            result = localize_result(load_result(analysis), locale, analysis, self.translator)
        error = submission.error if submission.status in {"failed", "expired"} else None
        return PublicSubmissionResponse(
            status=submission.status,
            step=submission.step,
            created_at=submission.created_at,
            expires_at=submission.expires_at,
            display_name=submission.display_name,
            error=error,
            outcome=submission.outcome,
            match_seconds=_match_seconds(self.store.get_video(submission.id)),
            locale=locale,
            result=result,
            payment_state=self._payment_state(submission.id),
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
        self._release_unpaid(now)
        self._recover_ready()
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
    ) -> tuple[CreateSubmissionResponse, Submission]:
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
        return (
            CreateSubmissionResponse(
                token=token,
                review_path=f"/review/{token}",
                upload=upload,
            ),
            submission,
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
        if self.telemetry is not None:
            best_effort(
                "validation_started",
                self.telemetry.validation_started,
                submission.id,
            )
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
        if self.telemetry is not None:
            best_effort(
                "queued",
                self.telemetry.queued,
                submission.id,
                video_seconds=facts.duration_seconds,
                size_bytes=video.size_bytes,
                width=facts.width,
                height=facts.height,
            )

    def _fail(
        self,
        submission: Submission,
        video: VideoInput,
        detail: str,
        *,
        category: FailureCategory | None = None,
    ) -> None:
        self.storage.delete(video.object_key)
        video.upload_state = "rejected"
        submission.status = "failed"
        submission.error = detail
        submission.updated_at = utc_now()
        self.store.save_video(video)
        self.note_terminal(submission, "failed")
        if self.telemetry is not None:
            code, diagnostic = classify_error(detail)
            best_effort(
                "validation_failed",
                self.telemetry.failed,
                submission.id,
                stage=None,
                error_code=code,
                diagnostic=diagnostic,
            )
        if category is not None:
            self._record_paid_failure(submission.id, category)

    def note_analysis_completed(self, submission_id: str) -> None:
        """Count a delivered analysis against the snapshotted price."""
        self._price_event(submission_id, "analysis_completed")

    def note_analysis_failed(self, submission_id: str, category: FailureCategory) -> None:
        """Count a processing failure. The category is allowlisted, never an exception."""
        self._price_event(submission_id, "analysis_failed", category)

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

    def _require_owned(self, video: VideoInput, user_id: str | None = None) -> None:
        stat = self.storage.head(video.object_key)
        prefix = f"submissions/{video.submission_id}/"
        if stat is None:
            raise RequestRejected(400, UPLOAD_MISSING)
        if stat.key != video.object_key or not video.object_key.startswith(prefix):
            raise RequestRejected(400, UPLOAD_MISMATCH)
        if stat.size_bytes != video.size_bytes:
            raise RequestRejected(400, UPLOAD_MISMATCH)
        if not self._within_size_cap(stat.size_bytes, user_id):
            self.storage.delete(video.object_key)
            raise RequestRejected(400, PROBE_SIZE)

    def _stored_frame(self, submission: Submission, index: int) -> Path | None:
        if submission.status != "complete" or not submission.analysis_dir:
            return None
        analysis = Path(submission.analysis_dir)
        entries = ranked_entries(analysis)
        if index < 0 or index >= len(entries):
            return None
        dest = frame_path(analysis, entries[index].safe_id)
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
        if self._hold_unpaid(submission):
            return
        video = self.store.get_video(submission.id)
        if video is not None and self.storage.head(video.object_key) is not None:
            if self.payments is not None:
                self.payments.mark_uploaded(submission.id)
                sealed = self.notices.read_sealed(submission.id) or ""
                self.attempt_queue(submission, sealed)
                return
            token = self.notices.read_sealed(submission.id) or ""
            try:
                self._require_owned(video, submission.user_id)
            except RequestRejected as exc:
                self._fail(submission, video, exc.detail)
                return
            self._probe_and_queue(submission, video, token)
            return
        submission.status = "failed"
        submission.error = UPLOAD_UNFINISHED
        submission.updated_at = utc_now()
        self.note_terminal(submission, "failed")
        if self.telemetry is not None:
            code, diagnostic = classify_error(UPLOAD_UNFINISHED)
            best_effort(
                "upload_unfinished",
                self.telemetry.failed,
                submission.id,
                stage=None,
                error_code=code,
                diagnostic=diagnostic,
            )

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
        if self.telemetry is not None:
            best_effort(
                "unlink_expired",
                self.telemetry.unlink_user,
                submission.id,
            )
        submission.email = None
        submission.display_name = None
        submission.user_id = None
        submission.status = "expired"
        submission.error = EXPIRED
        submission.updated_at = utc_now()
        self.store.save_submission(submission)

    def _quote(self, user_id: str | None) -> PriceQuote | None:
        if self.payments is None:
            return None
        return self.payments.quote(admin=self._is_admin(user_id))

    def _is_admin(self, user_id: str | None) -> bool:
        if not user_id or self.accounts is None:
            return False
        return self.accounts.role_for(user_id) == "admin"

    def _open_payment(
        self,
        created: CreateSubmissionResponse,
        submission: Submission,
        quote: PriceQuote | None,
        now: datetime,
    ) -> CreateSubmissionResponse:
        if self.payments is None or quote is None:
            return created
        expires = now + timedelta(seconds=self.settings.payment_hold_seconds)
        self.payments.open_gate(submission.id, quote, expires)
        self._price_event(submission.id, "upload_started")
        return created.model_copy(
            update={
                "price_cents": quote.price_cents,
                "payment_required": quote.payment_state == "unpaid",
            }
        )

    def _confirm_gated(self, submission: Submission, token: str) -> PublicSubmissionResponse:
        """Mark the upload, then queue only when this submission is already paid."""
        self._owned_video(submission)
        if self.payments is not None and self.payments.mark_uploaded(submission.id):
            self._price_event(submission.id, "upload_completed")
        if self.telemetry is not None:
            best_effort("upload_confirmed", self.telemetry.upload_confirmed, submission.id)
        return self.attempt_queue(submission, token)

    def attempt_queue(self, submission: Submission, token: str) -> PublicSubmissionResponse:
        """Probe and queue when upload and payment are both already true."""
        current = self.store.get(submission.id) or submission
        if self.payments is None or current.status != "received":
            return self.project(current)
        gate = self.payments.get(current.id)
        if gate is None or not _ready(gate):
            return self.project(current)
        video = self.store.get_video(current.id)
        if video is None:
            raise RequestRejected(400, UPLOAD_MISSING)
        facts = self._probe_facts(current, video)
        if facts is None:
            return self.project(self.store.get(current.id) or current)
        if self.payments.try_queue(current.id):
            sealed = token or self.notices.read_sealed(current.id) or ""
            self._queue(current, video, facts, sealed)
        return self.project(self.store.get(current.id) or current)

    def start_checkout(self, token: str) -> dict[str, str | bool | None]:
        """Return the current card session, or open the next attempt."""
        if self.payments is None or self.checkout is None:
            raise RequestRejected(503, PAYMENT_UNAVAILABLE)
        submission = self._require(token)
        gate = self.payments.get(submission.id)
        if gate is None:
            raise RequestRejected(404, "not found")
        if gate.payment_state == "paid":
            return {"url": None, "paid": True, "pending": False}
        if gate.payment_state == "not_required":
            raise RequestRejected(400, PAYMENT_NOT_REQUIRED)
        self._require_open_window(gate)
        reusable = self._reusable_session(gate)
        if reusable == "pending":
            return {"url": None, "paid": False, "pending": True}
        if isinstance(reusable, CheckoutSession):
            return {"url": reusable.url or gate.checkout_url, "paid": False, "pending": False}
        return self._open_next_session(submission, token, gate)

    def apply_webhook(self, event: CheckoutEvent) -> str:
        """Record a synchronously paid card session, then try to queue."""
        if self.payments is None or event.type != "checkout.session.completed":
            return "ignored"
        session = event.session
        if session.payment_status != "paid":
            return "ignored"
        gate = self.payments.get_by_session(session.id)
        if gate is None or gate.submission_id != (session.client_reference_id or ""):
            return "association"
        result = self.payments.apply_paid(
            submission_id=gate.submission_id,
            session_id=session.id,
            amount_total=session.amount_total,
            currency=(session.currency or "").lower(),
            payment_intent_id=session.payment_intent_id,
        )
        if result != "paid":
            return result
        submission = self.store.get(gate.submission_id)
        if submission is not None:
            self.attempt_queue(submission, "")
        self._note_paid_past_window(gate.submission_id)
        return "paid"

    def _owned_video(self, submission: Submission) -> VideoInput:
        video = self.store.get_video(submission.id)
        if video is None:
            raise RequestRejected(400, UPLOAD_MISSING)
        self._require_owned(video)
        return video

    def _probe_facts(self, submission: Submission, video: VideoInput) -> ProbeFacts | None:
        if self.telemetry is not None:
            best_effort(
                "validation_started",
                self.telemetry.validation_started,
                submission.id,
            )
        dest = self.settings.root / "probe" / f"{submission.id}{Path(video.object_key).suffix}"
        path, temporary = self.storage.local_file(video.object_key, dest)
        try:
            return self.probe(path)
        except ProbeRejected as exc:
            self._fail(submission, video, exc.detail, category="invalid_upload")
            return None
        finally:
            if temporary and path.is_file():
                path.unlink()

    def _reusable_session(self, gate: SubmissionGate) -> CheckoutSession | str | None:
        if self.checkout is None or gate.payment_state != "checkout_created":
            return None
        if not gate.checkout_session_id:
            return None
        session = self.checkout.retrieve_session(gate.checkout_session_id)
        if session.status == "open":
            return session
        if session.payment_status == "paid":
            return "pending"
        return None

    def _open_next_session(
        self, submission: Submission, token: str, gate: SubmissionGate
    ) -> dict[str, str | bool | None]:
        if self.payments is None or self.checkout is None:
            raise RequestRejected(503, PAYMENT_UNAVAILABLE)
        replace = gate.checkout_session_id if gate.payment_state == "checkout_created" else None
        attempt = self.payments.claim_checkout(submission.id, replace)
        if attempt is None:
            raise RequestRejected(409, PAYMENT_WINDOW)
        self._require_stripe_lifetime(gate)
        created = self.checkout.create_session(
            submission_id=submission.id,
            price_cents=gate.price_cents,
            currency=gate.currency,
            expires_at=gate.expires_at,
            success_url=f"{self.settings.public_base_url}/review/{token}",
            cancel_url=f"{self.settings.public_base_url}/review/{token}",
            idempotency_key=f"{submission.id}:{attempt}",
        )
        if not self.payments.attach_session(submission.id, attempt, created.id, created.url or ""):
            raise RequestRejected(409, PAYMENT_WINDOW)
        return {"url": created.url, "paid": False, "pending": False}

    def _require_open_window(self, gate: SubmissionGate) -> None:
        if gate.status == "expired" or gate.expires_at <= datetime.now(UTC):
            raise RequestRejected(400, PAYMENT_WINDOW)

    def _require_stripe_lifetime(self, gate: SubmissionGate) -> None:
        remaining = gate.expires_at - datetime.now(UTC)
        if remaining < timedelta(minutes=30):
            raise RequestRejected(400, PAYMENT_WINDOW)

    def _note_paid_past_window(self, submission_id: str) -> None:
        if self.payments is None:
            return
        gate = self.payments.get(submission_id)
        if gate is None or gate.payment_state != "paid" or gate.status != "received":
            return
        if gate.expires_at <= datetime.now(UTC):
            self._price_event(submission_id, "analysis_failed", "expired")

    def _release_unpaid(self, now: datetime) -> None:
        if self.payments is None:
            return
        for submission_id in self.payments.unpaid_due_ids(now):
            if not self.payments.claim_unpaid_expiry(submission_id, now):
                continue
            if not self.payments.remains_unpaid_expired(submission_id):
                continue
            self._delete_unpaid(submission_id)

    def _delete_unpaid(self, submission_id: str) -> None:
        submission = self.store.get(submission_id)
        if submission is None or submission.status != "received":
            return
        video = self.store.get_video(submission_id)
        if video is not None and video.object_key:
            self.storage.delete(video.object_key)
            video.object_key = ""
            video.upload_state = "rejected"
            self.store.save_video(video)
        submission.status = "expired"
        submission.error = EXPIRED
        submission.updated_at = utc_now()
        self.store.save_submission(submission)
        self._price_event(submission_id, "analysis_failed", "expired")

    def _recover_ready(self) -> None:
        if self.payments is None:
            return
        for submission_id in self.payments.ready_ids():
            submission = self.store.get(submission_id)
            if submission is not None and submission.status == "received":
                self.attempt_queue(submission, "")

    def _hold_unpaid(self, submission: Submission) -> bool:
        if self.payments is None:
            return False
        gate = self.payments.get(submission.id)
        if gate is None or gate.payment_state in {"paid", "not_required"}:
            return False
        video = self.store.get_video(submission.id)
        return video is not None and self.storage.head(video.object_key) is not None

    def _payment_state(self, submission_id: str) -> str | None:
        if self.payments is None:
            return None
        gate = self.payments.get(submission_id)
        return None if gate is None else gate.payment_state

    def _record_paid_failure(self, submission_id: str, category: FailureCategory) -> None:
        if self.payments is None:
            return
        gate = self.payments.get(submission_id)
        if gate is not None and gate.payment_state in {"paid", "not_required"}:
            self._price_event(submission_id, "analysis_failed", category)

    def _price_event(
        self,
        submission_id: str,
        kind: str,
        category: FailureCategory | None = None,
    ) -> None:
        if self.payments is None:
            return
        best_effort(
            "price_event",
            self.payments.record_for_submission,
            submission_id,
            kind,
            category,
        )

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
    max_bytes: int | None,
) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    mime = content_type.split(";", 1)[0].strip().lower()
    if suffix not in _SUFFIXES or mime not in _TYPES:
        raise RequestRejected(400, BAD_FILE)
    if size_bytes < 1 or (max_bytes is not None and size_bytes > max_bytes):
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


def _ready(gate: SubmissionGate) -> bool:
    """True when both prerequisites are present and the site lifetime is open."""
    return (
        gate.status == "received"
        and gate.uploaded
        and gate.payment_state in {"paid", "not_required"}
        and gate.expires_at > datetime.now(UTC)
    )


def _match_seconds(video: object) -> int | None:
    """Whole seconds from the probed video, when that length was stored."""
    raw = getattr(video, "duration_seconds", None)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if raw <= 0:
        return None
    return int(raw)
