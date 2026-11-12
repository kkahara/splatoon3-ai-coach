"""Create and validate submissions. Does not run the analysis pipeline."""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

from loguru import logger

from public_site.accounts import AccountStore
from public_site.clips import clip_path, extract_clips, missing_clips
from public_site.errors import (
    ACCOUNT_RATE_LIMIT,
    BAD_EMAIL,
    BAD_FILE,
    BAD_NAME,
    BAD_SIZE,
    CAPACITY,
    EXPIRED,
    GAME_LANGUAGE_UNDETERMINED,
    PAYMENT_NOT_REQUIRED,
    PAYMENT_UNAVAILABLE,
    PAYMENT_WINDOW,
    PROBE_SIZE,
    RATE_LIMIT,
    UPLOAD_MISMATCH,
    UPLOAD_MISSING,
    UPLOAD_UNFINISHED,
    VERIFY,
    VOUCHER_PENDING,
    VOUCHER_UNAVAILABLE,
    RequestRejected,
)
from public_site.frames import extract_frames, frame_path, missing_frames, ranked_entries
from public_site.guest_notices import GuestNotices
from public_site.keyed_locks import KeyedLocks
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
from public_site.payments import (
    EffectiveUploadLimits,
    FailureCategory,
    PaymentStore,
    PriceQuote,
    SubmissionGate,
)
from public_site.recording_models import ReviewCodeCreated
from public_site.recording_store import report_expires_at
from public_site.results import load_result
from public_site.settings import PublicSettings
from public_site.storage import LocalStorage, R2Storage
from public_site.store import PublicStore, add_days, copy_lifecycle, parse_utc, utc_now
from public_site.stripe_checkout import (
    CheckoutEvent,
    CheckoutGateway,
    CheckoutRequestError,
    CheckoutSession,
)
from public_site.telemetry import TelemetryStore, best_effort, classify_error
from public_site.tokens import hash_ip, hash_token, new_token
from public_site.vouchers import (
    VOUCHER_ATTEMPTS_PER_HOUR,
    SubmissionScope,
    VoucherPreview,
    VoucherStore,
    raise_voucher_conflict,
)

if TYPE_CHECKING:
    from public_site.recording_service import RecordingService
    from public_site.report_publisher import ReportPublisher

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CLAIM_STALE = timedelta(minutes=2)
_TERMINAL_NOTICES = frozenset({"ready", "no_moments", "failed", "cancelled"})
# Sent to the verified request address whatever the notify preference.
_SERVICE_NOTICES = frozenset({"cancelled"})
_REPORT_NOTICES = frozenset({"ready", "no_moments"})
_BURST_WINDOW = timedelta(hours=24)
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
        vouchers: VoucherStore | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.storage = storage
        self.notices = notices
        self.accounts = accounts
        database_url = getattr(accounts, "database_url", None)
        self.guest = GuestNotices(database_url) if database_url else None
        self.translator = translator
        self.telemetry = telemetry
        self.payments = payments
        self.checkout = checkout
        self.recordings: RecordingService | None = None
        self.reports: ReportPublisher | None = None
        self.vouchers = vouchers
        self.probe = probe or (lambda path: probe_file(path, settings))
        self._probe_locks = KeyedLocks()
        self.limiter = RateLimiter(
            store,
            limit=settings.submissions_per_hour,
            window_seconds=settings.submission_window_seconds,
        )
        self.voucher_limiter = RateLimiter(
            store,
            limit=VOUCHER_ATTEMPTS_PER_HOUR,
            window_seconds=settings.submission_window_seconds,
        )

    def create(
        self,
        *,
        turnstile_token: str,
        filename: str,
        size_bytes: int,
        content_type: str,
        email: str | None,
        notify: bool,
        display_name: str | None,
        ip: str,
        verifier: Verifier,
        now: datetime | None = None,
        user_id: str | None = None,
        retention_days: int | None = None,
        voucher_code: str | None = None,
    ) -> CreateSubmissionResponse:
        """Verify, limit, then create. A failed Turnstile check writes nothing."""
        if not verifier(turnstile_token):
            raise RequestRejected(400, VERIFY)
        moment = now or datetime.now(UTC)
        code = _blank_to_none(voucher_code)
        voucher_id = self.attach_voucher(code, ip, moment, "video_upload")
        quote = self._quote(user_id)
        if voucher_id is not None and quote is not None:
            quote = replace(quote, payment_state="unpaid")
        privileged = self._privileged(user_id)
        if not privileged:
            self._check_limits(ip, user_id, moment)
        if self.store.unfinished_count() >= self.settings.max_queued:
            raise RequestRejected(429, CAPACITY)
        cap = None if privileged else self.settings.max_video_bytes
        suffix, mime = _declared_file(filename, content_type, size_bytes, cap)
        notice_email = email if user_id else _notice_email(email, notify)
        name = _display_name(display_name)
        created, submission = self._persist(
            suffix=suffix,
            mime=mime,
            size_bytes=size_bytes,
            original_filename=Path(filename).name,
            email=notice_email,
            notify=notify,
            display_name=name,
            now=moment,
            user_id=user_id,
            retention_days=retention_days,
        )
        created = self._open_payment(created, submission, quote, moment, voucher_id)
        if self.telemetry is not None:
            best_effort(
                "open_initial_run",
                self.telemetry.open_initial_run,
                submission.id,
                user_id=user_id,
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
        limits = self.effective_upload_limits()
        if user_id is None:
            own_key = "guest:" + ip_key
            own_limit = limits.guest
        else:
            own_key = "user:" + hash_token(user_id)
            own_limit = limits.account
        checks = [(ip_key, limits.network), (own_key, own_limit)]
        full = self.limiter.allow_all(checks, now)
        if full is None:
            return
        if full == own_key and user_id is not None:
            raise RequestRejected(429, ACCOUNT_RATE_LIMIT)
        raise RequestRejected(429, RATE_LIMIT)

    def effective_upload_limits(self) -> EffectiveUploadLimits:
        """Saved limits, with the environment setting wherever none is saved."""
        if self.payments is None:
            return EffectiveUploadLimits(
                account=self.settings.account_submissions_per_hour,
                guest=self.settings.guest_submissions_per_hour,
                network=self.settings.submissions_per_hour,
                account_saved=False,
                guest_saved=False,
                network_saved=False,
            )
        saved = self.payments.upload_limits()
        return EffectiveUploadLimits(
            account=saved.account,
            guest=_or_default(saved.guest, self.settings.guest_submissions_per_hour),
            network=_or_default(saved.network, self.settings.submissions_per_hour),
            account_saved=True,
            guest_saved=saved.guest is not None,
            network_saved=saved.network is not None,
        )

    def confirm(self, token: str) -> PublicSubmissionResponse:
        """Record the upload, then queue when payment is already satisfied."""
        submission = self._require(token)
        if submission.input_type == "review_code":
            raise RequestRejected(409, "Operator recording verification is required.")
        if submission.status != "received":
            return self.project(submission)
        if self.payments is not None:
            return self._confirm_gated(submission, token)
        return self._confirm_free(submission, token)

    def _confirm_free(
        self, submission: Submission, token: str
    ) -> PublicSubmissionResponse:
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

    def view(
        self, token: str, locale: DisplayLocale = "en", browser_cookie: str = ""
    ) -> PublicSubmissionResponse:
        """Project one submission. Unknown tokens are a 404.

        ``browser_cookie`` only adds this browser's confirmation-email cooldown
        to an unverified review-code request.
        """
        submission = self._require(token)
        response = self.project(submission, locale)
        acquisition = response.acquisition
        if acquisition is not None and not acquisition.contact_verified:
            acquisition.resend_retry_after_seconds = self.recordings.contact_cooldown(
                submission.id, browser_cookie
            )
        return response

    def guest_cooldown(
        self, browser_cookie: str, email: str, now: datetime | None = None
    ) -> int | None:
        """Seconds before this browser may request another confirmation email.

        Only the browser's own limit is reported; the per-address limit stays
        silent. None when this browser already verified the address, since no
        email is needed then.
        """
        if self.guest is None or not browser_cookie:
            return None
        moment = now or datetime.now(UTC)
        browser_id = self.guest.live_browser_id(browser_cookie, moment)
        if browser_id is None or self.guest.is_verified(browser_id, email):
            return None
        remaining = self.guest.browser_cooldown(browser_id, moment)
        return None if remaining is None else math.ceil(remaining.total_seconds())

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
            result = localize_result(
                load_result(analysis), locale, analysis, self.translator
            )
        error = submission.error if submission.status in {"failed", "expired"} else None
        error_key = (
            "game_language_undetermined" if error == GAME_LANGUAGE_UNDETERMINED else None
        )
        gate = None if self.payments is None else self.payments.get(submission.id)
        return PublicSubmissionResponse(
            acquisition=(
                self.recordings.view(submission.id)
                if self.recordings is not None and submission.input_type == "review_code"
                else None
            ),
            status=submission.status,
            step=submission.step,
            created_at=submission.created_at,
            expires_at=submission.expires_at,
            display_name=submission.display_name,
            error=error,
            error_key=error_key,
            outcome=submission.outcome,
            match_seconds=_match_seconds(self.store.get_video(submission.id)),
            locale=locale,
            result=result,
            payment_state=None if gate is None else gate.payment_state,
            voucher_state=None if gate is None else gate.voucher_state,
            voucher_failure_reason=None if gate is None else gate.voucher_failure_reason,
            notice_pending=self._notice_pending(submission),
            public_reference=None if gate is None else gate.public_reference,
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

    def clip_file(self, submission: Submission, index: int) -> Path:
        """Return a completed clip only for an accessible completed result."""
        if submission.status != "complete" or not submission.analysis_dir:
            raise RequestRejected(404, "not found")
        analysis = Path(submission.analysis_dir)
        entries = ranked_entries(analysis)
        if index < 0 or index >= len(entries):
            raise RequestRejected(404, "not found")
        path = clip_path(analysis, entries[index].safe_id)
        if path is None or not path.is_file():
            raise RequestRejected(404, "not found")
        return path

    def moment_clip(self, token: str, index: int) -> Path:
        """Resolve a clip through the guest token access boundary."""
        return self.clip_file(self._require(token), index)

    def maintain(self, now: datetime) -> None:
        """Release abandoned uploads and expire old submissions."""
        if self.recordings is not None:
            self.recordings.reconcile()
        self._abandon(now)
        self._release_unpaid(now)
        if self.recordings is not None:
            self.recordings.database.reconcile()
            self.recordings.database.purge_expired_unpaid(now)
            self.recordings.reconcile()
            self.recordings.send_pending_cancellations()
        self._recover_ready()
        self._expire(now)
        self.retry_notices(now)

    def _persist(
        self,
        *,
        suffix: str,
        mime: str,
        size_bytes: int,
        original_filename: str,
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
        """Probe and queue a free upload once; a concurrent caller sees the first result."""
        with self._probe_locks.hold(submission.id):
            current = self.store.get(submission.id) or submission
            if current.status != "received":
                return self.project(current)
            return self._probe_and_queue_locked(current, video, token)

    def _probe_and_queue_locked(
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
        dest = self._probe_dest(submission.id, video.object_key)
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
        # The run must be queued before the worker can claim the submission;
        # otherwise ``started`` sees ``validating`` and the run never leaves ``queued``.
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
        self.store.save_submission(submission)
        self.deliver_notice(submission, "received")

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
        self._save_lifecycle(submission)
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
        """Send a ready or failed notice for a status the caller already saved.

        This does not write ``submission``: another thread may have changed
        notice fields since the caller loaded it. A failed send keeps the
        sealed token.
        """
        self.deliver_notice(submission, kind)

    def _save_lifecycle(self, submission: Submission) -> None:
        """Save status fields without overwriting notice fields written elsewhere."""
        self.store.update(
            submission.id, lambda current: copy_lifecycle(submission, current)
        )

    def arrange_guest_notice(
        self,
        created: CreateSubmissionResponse | ReviewCodeCreated,
        email: str,
        browser_cookie: str,
        now: datetime | None = None,
    ) -> tuple[bool, str | None]:
        """Bind a guest notice request. Returns pending, and a cookie when new."""
        if self.guest is None:
            return False, None
        moment = now or datetime.now(UTC)
        submission = self.store.get_by_hash(hash_token(created.token))
        if submission is None:
            return False, None
        self.notices.seal(submission.id, created.token)
        browser_id, fresh = self.guest.open_browser(browser_cookie or None, moment)
        if self.guest.is_verified(browser_id, email):
            if not self._enable_notice(submission, email):
                return True, fresh
            self.guest.remember_verified(browser_id, email, moment)
            return False, fresh
        browser_id, replacement_cookie = self.guest.add_request(
            browser_id, submission.id, email, moment
        )
        fresh = replacement_cookie or fresh
        self._maybe_confirm(browser_id, email, submission.id, moment)
        return True, fresh

    def confirm_guest_notice(self, token: str, now: datetime | None = None) -> bool:
        """Verify one address for the browser that requested it, then enable notices."""
        if self.guest is None:
            return False
        moment = now or datetime.now(UTC)
        found = self.guest.consume(token, moment, self._guest_request_exists)
        if found is None:
            return False
        browser_id, email, challenge_submission_id = found
        since = moment - _BURST_WINDOW
        submission_ids = dict.fromkeys(
            [
                *self.guest.pending_ids(browser_id, email, since),
                challenge_submission_id,
            ]
        )
        target_verified = False
        for submission_id in submission_ids:
            verified = self._confirm_request(submission_id, email, moment)
            if submission_id == challenge_submission_id:
                target_verified = verified
        return target_verified

    def _guest_request_exists(self, submission_id: str, email: str) -> bool:
        """Check the challenge's exact server-side submission/address binding."""
        submission = self.store.get(submission_id)
        if submission is None and self.recordings is not None:
            acquisition = self.recordings.database.get(submission_id)
            if acquisition is not None:
                if acquisition["requested_email"] != email:
                    return False
                submission = self.recordings.publish(acquisition)
        if submission is None:
            return False
        if submission.input_type != "review_code":
            return submission.email in {None, email} and not submission.notify
        if self.recordings is None:
            return False
        acquisition = self.recordings.database.get(submission_id)
        return bool(acquisition is not None and acquisition["requested_email"] == email)

    def deliver_notice(self, submission: Submission, kind: str) -> None:
        """Move one notice unsent → claimed → sent. A stale claim can be retried."""
        current = self.store.get(submission.id)
        if current is None or kind in current.notices_sent:
            return
        recipient = self._notice_recipient(current, kind)
        if not recipient:
            return
        if kind in _REPORT_NOTICES and not self._report_released(current):
            return
        token = self.notices.read_sealed(current.id)
        if not token or not self._claim_notice(current, kind):
            return
        try:
            self.notices.deliver(
                current.model_copy(update={"email": recipient}),
                kind,
                token,
                self._public_reference(current.id),
            )
        except Exception as exc:
            logger.warning(
                "notice {} failed for submission {} ({})",
                kind,
                current.id,
                type(exc).__name__,
            )
            return
        self._mark_notice_sent(current.id, kind)

    def _notice_recipient(self, submission: Submission, kind: str) -> str | None:
        """The opted-in address, or for a service notice the verified request address."""
        if kind in _SERVICE_NOTICES:
            if self.recordings is None:
                return None
            return self.recordings.verified_contact(submission.id)
        return submission.email if submission.notify else None

    def _public_reference(self, submission_id: str) -> str | None:
        """The support reference on this submission's gate, if it has one."""
        gate = None if self.payments is None else self.payments.get(submission_id)
        return None if gate is None else gate.public_reference

    def _report_released(self, submission: Submission) -> bool:
        """A review-code report is announced only after its durable handoff."""
        if submission.input_type != "review_code":
            return True
        return self.recordings is not None and self.recordings.report_available(
            submission.id
        )

    def retry_notices(self, now: datetime) -> None:
        """Retry claims that were left behind by a failed send."""
        for submission in self.store.list_submissions():
            for kind in list(submission.notice_claims):
                if kind in submission.notices_sent:
                    continue
                stamp = submission.notice_claims.get(kind)
                if stamp and parse_utc(stamp) > now - _CLAIM_STALE:
                    continue
                self.deliver_notice(submission, kind)

    def _maybe_confirm(
        self, browser_id: str, email: str, submission_id: str, now: datetime
    ) -> None:
        if self.guest is None:
            return
        started = self.guest.begin_challenge(browser_id, email, submission_id, now)
        if started is None:
            return
        challenge_id, token = started
        url = f"{self.settings.public_base_url}/confirm-notices/{token}"
        try:
            self.notices.send_confirm(email, url)
        except Exception as exc:
            self.guest.drop_challenge(challenge_id)
            logger.warning(
                "notice confirmation failed for submission {} ({})",
                submission_id,
                type(exc).__name__,
            )

    def _confirm_request(self, submission_id: str, email: str, now: datetime) -> bool:
        if self.guest is None:
            return False
        submission = self.store.get(submission_id)
        if submission is None:
            return False
        if submission.input_type == "review_code":
            if self.recordings is None:
                return False
            acquisition = self.recordings.database.get(submission_id)
            if acquisition is None or acquisition["requested_email"] != email:
                return False
            if acquisition["contact_verified_at"] is not None:
                self.guest.mark_confirmed(submission_id, now)
                self.recordings.redeem_pending_vouchers()
                return True
        elif submission.email is not None or submission.notify:
            return submission.email == email and submission.notify
        if not self._enable_notice(submission, email):
            return False
        self.guest.mark_confirmed(submission_id, now)
        self._release_due_notices(submission)
        return True

    def _enable_notice(self, submission: Submission, email: str) -> bool:
        if submission.input_type == "review_code":
            if self.recordings is None or not self.recordings.database.verify_contact(
                submission.id, email
            ):
                return False
            self.recordings.redeem_pending_vouchers()
        submission.email = email
        submission.notify = True
        submission.updated_at = utc_now()

        def enable(current: Submission) -> None:
            current.email = email
            current.notify = True
            current.updated_at = submission.updated_at

        self.store.update(submission.id, enable)
        return True

    def _release_due_notices(self, submission: Submission) -> None:
        if submission.status == "complete":
            kind = "no_moments" if submission.outcome == "no_deaths" else "ready"
            self.deliver_notice(submission, kind)
            return
        if submission.status == "failed":
            self.deliver_notice(submission, "failed")
            return
        if submission.status in {"queued", "processing"}:
            self.deliver_notice(submission, "received")

    def _claim_notice(self, submission: Submission, kind: str) -> bool:
        won: list[bool] = []

        def claim(current: Submission) -> None:
            if kind in current.notices_sent:
                return
            claimed = current.notice_claims.get(kind)
            if claimed and parse_utc(claimed) > datetime.now(UTC) - _CLAIM_STALE:
                return
            current.notice_claims[kind] = utc_now()
            current.updated_at = utc_now()
            won.append(True)

        self.store.update(submission.id, claim)
        return bool(won)

    def _mark_notice_sent(self, submission_id: str, kind: str) -> None:
        def mark(current: Submission) -> None:
            if kind not in current.notices_sent:
                current.notices_sent.append(kind)
            current.notice_claims.pop(kind, None)
            current.updated_at = utc_now()

        if self.store.update(submission_id, mark) is None:
            return
        if kind in _TERMINAL_NOTICES:
            self.notices.clear_sealed(submission_id)

    def _notice_pending(self, submission: Submission) -> bool:
        if self.guest is None or submission.notify or submission.email:
            return False
        return self.guest.has_open_request(submission.id)

    def release_source_video(self, submission: Submission) -> None:
        """Delete the source video after a completed review is stored.

        Dev mode or missing clips keep the file until retry or expiry. A delete error leaves ``object_key`` set so
        the expiry sweep can try again. The coaching result stays available.
        """
        if self.settings.dev_mode:
            return
        if submission.analysis_dir and missing_clips(Path(submission.analysis_dir)):
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
        """Fill missing presentation media while the source is still stored."""
        if not submission.analysis_dir:
            return
        analysis = Path(submission.analysis_dir)
        if not missing_frames(analysis) and not missing_clips(analysis):
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
            logger.warning(
                "moment frame source unavailable for submission {}", submission.id
            )
            return
        before = _media_count(analysis)
        try:
            extract_clips(analysis, path, submission_id=submission.id)
            extract_frames(analysis, path, submission_id=submission.id)
            self.release_source_video(submission)
        finally:
            if temporary and path.is_file():
                path.unlink()
        if _media_count(analysis) > before:
            self._report_hook("media_added", submission.id)

    def _require(self, token: str) -> Submission:
        found = self.store.get_by_hash(hash_token(token))
        if found is None and self.recordings is not None:
            found = self.recordings.publish(
                self.recordings.database.by_token(hash_token(token))
            )
        if found is None:
            raise RequestRejected(404, "not found")
        return found

    def _abandon(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.settings.upload_url_seconds)
        for submission in self.store.list_submissions():
            if submission.input_type == "review_code":
                if submission.status == "expired" and not submission.analysis_dir:
                    continue
                if self.recordings is not None:
                    acquisition = self.recordings.database.get(submission.id)
                    if (
                        acquisition is not None
                        and acquisition["recording_handoff_at"] is not None
                        and submission.status != "processing"
                    ):
                        if acquisition["report_expired_at"] is not None:
                            if submission.status != "expired" or submission.analysis_dir:
                                self._expire_one(submission)
                        elif (
                            submission.status != "expired"
                            and parse_utc(report_expires_at(acquisition)) <= now
                            and self.recordings.database.mark_report_expired(
                                submission.id, now
                            )
                        ):
                            self._expire_one(submission)
                continue
            if (
                submission.status != "received"
                or parse_utc(submission.created_at) > cutoff
            ):
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
        self._save_lifecycle(submission)
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
            if submission.input_type == "review_code":
                continue
            if submission.status == "processing":
                continue
            if submission.status == "expired" and not submission.analysis_dir:
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
        if submission.status != "expired":
            submission.status = "expired"
            submission.error = EXPIRED
            submission.updated_at = utc_now()
            self.store.save_submission(submission)
        if submission.analysis_dir:
            try:
                _remove_tree(Path(submission.analysis_dir))
                submission.analysis_dir = None
            except OSError as exc:
                # analysis_dir stays set so the next sweep retries; identity is
                # still cleared below.
                logger.warning(
                    "analysis cleanup failed for submission {} ({})",
                    submission.id,
                    type(exc).__name__,
                )
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
        self._report_hook("submission_expired", submission.id)

    def note_report_completed(self, submission_id: str) -> None:
        """Queue a completed report for publishing. Local record only; never fails."""
        self._report_hook("submission_completed", submission_id)

    def _report_hook(self, action: str, submission_id: str) -> None:
        if self.reports is None:
            return
        try:
            getattr(self.reports, action)(submission_id)
        except Exception as exc:  # noqa: BLE001 — publishing never affects serving
            logger.warning("report hook {} failed ({})", action, type(exc).__name__)

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
        voucher_id: str | None = None,
    ) -> CreateSubmissionResponse:
        if self.payments is None or quote is None:
            return created
        expires = now + timedelta(seconds=self.settings.payment_hold_seconds)
        self.payments.open_gate(submission.id, quote, expires, voucher_id)
        self._price_event(submission.id, "upload_started")
        return created.model_copy(
            update={
                "price_cents": quote.price_cents,
                "payment_required": quote.payment_state == "unpaid",
                "voucher_state": None if voucher_id is None else "pending",
            }
        )

    def _confirm_gated(
        self, submission: Submission, token: str
    ) -> PublicSubmissionResponse:
        """Mark the upload, then queue only when this submission is already paid."""
        self._owned_video(submission)
        if self.payments is not None and self.payments.mark_uploaded(submission.id):
            self._price_event(submission.id, "upload_completed")
        if self.telemetry is not None:
            best_effort(
                "upload_confirmed", self.telemetry.upload_confirmed, submission.id
            )
        return self.attempt_queue(submission, token)

    def attempt_queue(
        self, submission: Submission, token: str
    ) -> PublicSubmissionResponse:
        """Probe and queue when upload and payment are both already true.

        The upload request and the maintenance pass may both call this for one
        submission. They run one at a time; the later caller sees the first
        caller's result instead of probing again.
        """
        with self._probe_locks.hold(submission.id):
            return self._attempt_queue_locked(submission, token)

    def _attempt_queue_locked(
        self, submission: Submission, token: str
    ) -> PublicSubmissionResponse:
        current = self.store.get(submission.id) or submission
        if current.input_type == "review_code":
            if self.recordings is not None:
                self.recordings.database.reconcile()
                current = (
                    self.recordings.publish(self.recordings.database.get(current.id))
                    or current
                )
            return self.project(current)
        if self.payments is None or current.status != "received":
            return self.project(current)
        gate = self.payments.get(current.id)
        if gate is not None and gate.voucher_state == "pending":
            return self._finish_voucher(current, token)
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

    def preview_voucher(
        self,
        code: str,
        ip: str,
        scope: SubmissionScope,
        now: datetime | None = None,
    ) -> VoucherPreview:
        """Validate a code for one form. Does not attach it or redeem it.

        ``scope`` is chosen by the route, not by the request body.
        """
        moment = now or datetime.now(UTC)
        self._watch_voucher(ip, moment)
        if self.vouchers is None:
            raise RequestRejected(503, VOUCHER_UNAVAILABLE, reason="unavailable")
        return self.vouchers.preview(code, moment, scope)

    def _finish_voucher(
        self, submission: Submission, token: str
    ) -> PublicSubmissionResponse:
        """Probe, then redeem and queue in one database transaction."""
        if self.vouchers is None or self.payments is None:
            raise RequestRejected(503, VOUCHER_UNAVAILABLE, reason="unavailable")
        video = self.store.get_video(submission.id)
        if video is None:
            raise RequestRejected(400, UPLOAD_MISSING)
        facts = self._probe_facts(submission, video)
        current = self.store.get(submission.id) or submission
        if facts is None:
            return self.project(current)
        decision = self.vouchers.finalize(submission.id, datetime.now(UTC))
        if decision.outcome == "failed":
            return self.project(current)
        if decision.outcome != "redeemed":
            raise_voucher_conflict(decision)
        sealed = token or self.notices.read_sealed(submission.id) or ""
        self._queue(submission, video, facts, sealed)
        return self.project(self.store.get(submission.id) or submission)

    def attach_voucher(
        self, code: str | None, ip: str, now: datetime, scope: SubmissionScope
    ) -> str | None:
        """Revalidate before any submission is written. Still not a redemption.

        Each submission route passes its own fixed ``scope``.
        """
        if code is None:
            return None
        self._watch_voucher(ip, now)
        if self.vouchers is None or self.payments is None:
            raise RequestRejected(503, VOUCHER_UNAVAILABLE, reason="unavailable")
        return self.vouchers.attached_id(code, now, scope)

    def _watch_voucher(self, ip: str, now: datetime) -> None:
        if self.voucher_limiter.allow("voucher:" + hash_ip(ip), now):
            return
        raise RequestRejected(429, RATE_LIMIT)

    def _raise_unclaimed(self, submission_id: str) -> NoReturn:
        gate = None if self.payments is None else self.payments.get(submission_id)
        if gate is not None and gate.voucher_state == "pending":
            raise RequestRejected(409, VOUCHER_PENDING, reason="pending")
        raise RequestRejected(409, PAYMENT_WINDOW)

    def start_checkout(self, token: str) -> dict[str, str | bool | None]:
        """Return the current card session, or open the next attempt."""
        if self.payments is None or self.checkout is None:
            raise RequestRejected(503, PAYMENT_UNAVAILABLE)
        submission = self._require(token)
        return self._begin_checkout(submission, f"/review/{token}")

    def start_owned_checkout(self, submission: Submission) -> dict[str, str | bool | None]:
        """Open checkout for a signed-in owner. Stripe returns to the account page."""
        if self.payments is None or self.checkout is None:
            raise RequestRejected(503, PAYMENT_UNAVAILABLE)
        return self._begin_checkout(submission, f"/coaching/{submission.id}")

    def _begin_checkout(
        self, submission: Submission, return_path: str
    ) -> dict[str, str | bool | None]:
        if submission.input_type == "review_code" and self.recordings is not None:
            self.recordings.require_verified_contact(submission.id)
        gate = self.payments.get(submission.id)
        if gate is None:
            raise RequestRejected(404, "not found")
        if gate.payment_state == "paid":
            return {"url": None, "paid": True, "pending": False}
        if gate.payment_state == "not_required":
            raise RequestRejected(400, PAYMENT_NOT_REQUIRED)
        if gate.voucher_state == "pending":
            raise RequestRejected(409, VOUCHER_PENDING, reason="pending")
        self._require_open_window(gate)
        reusable = self._reusable_session(gate)
        if reusable == "paid":
            return {"url": None, "paid": True, "pending": False}
        if reusable == "pending":
            return {"url": None, "paid": False, "pending": True}
        if isinstance(reusable, CheckoutSession):
            return {
                "url": reusable.url or gate.checkout_url,
                "paid": False,
                "pending": False,
            }
        return self._open_next_session(submission, return_path, gate)

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
        return self.record_paid_session(gate.submission_id, session)

    def record_paid_session(self, submission_id: str, session: CheckoutSession) -> str:
        """Apply a paid session through the amount-checked transition, then try to queue.

        Shared by the webhook and by operator reconciliation. Idempotent: a
        session already recorded as paid returns ``paid`` and changes nothing.
        """
        if self.payments is None or session.payment_status != "paid":
            return "ignored"
        if session.client_reference_id != submission_id:
            return "association"
        result = self.payments.apply_paid(
            submission_id=submission_id,
            session_id=session.id,
            amount_subtotal=session.amount_subtotal,
            amount_total=session.amount_total,
            currency=(session.currency or "").lower(),
            payment_intent_id=session.payment_intent_id,
        )
        if result != "paid":
            return result
        submission = self.store.get(submission_id)
        if submission is not None:
            self.attempt_queue(submission, "")
        self._note_paid_past_window(submission_id)
        return "paid"

    def _owned_video(self, submission: Submission) -> VideoInput:
        video = self.store.get_video(submission.id)
        if video is None:
            raise RequestRejected(400, UPLOAD_MISSING)
        self._require_owned(video)
        return video

    def _probe_dest(self, submission_id: str, object_key: str) -> Path:
        """A probe copy path unique to this call, so no other probe can replace or remove it."""
        suffix = Path(object_key).suffix
        name = f"{submission_id}.{uuid.uuid4().hex}{suffix}"
        return self.settings.root / "probe" / name

    def _probe_facts(
        self, submission: Submission, video: VideoInput
    ) -> ProbeFacts | None:
        if self.telemetry is not None:
            best_effort(
                "validation_started",
                self.telemetry.validation_started,
                submission.id,
            )
        dest = self._probe_dest(submission.id, video.object_key)
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
        """The open session, ``paid`` once a paid session is recorded, or ``pending``.

        A session Stripe shows as paid is recorded here as the webhook would; one
        the amount check refuses stays ``pending`` for the operator.
        """
        if self.checkout is None or gate.payment_state != "checkout_created":
            return None
        if not gate.checkout_session_id:
            return None
        session = self.checkout.retrieve_session(gate.checkout_session_id)
        if session.status == "open":
            return session
        if session.payment_status == "paid":
            recorded = self.record_paid_session(gate.submission_id, session)
            return "paid" if recorded == "paid" else "pending"
        return None

    def _open_next_session(
        self, submission: Submission, return_path: str, gate: SubmissionGate
    ) -> dict[str, str | bool | None]:
        if self.payments is None or self.checkout is None:
            raise RequestRejected(503, PAYMENT_UNAVAILABLE)
        replace = (
            gate.checkout_session_id if gate.payment_state == "checkout_created" else None
        )
        attempt = self.payments.claim_checkout(submission.id, replace)
        if attempt is None:
            self._raise_unclaimed(submission.id)
        self._require_stripe_lifetime(gate)
        try:
            created = self._create_checkout_session(submission, return_path, gate, attempt)
        except CheckoutRequestError as exc:
            self.payments.abandon_checkout_attempt(submission.id, attempt)
            raise RequestRejected(502, exc.message) from exc
        if not self.payments.attach_session(
            submission.id, attempt, created.id, created.url or ""
        ):
            raise RequestRejected(409, PAYMENT_WINDOW)
        return {"url": created.url, "paid": False, "pending": False}

    def _create_checkout_session(
        self, submission: Submission, return_path: str, gate: SubmissionGate, attempt: int
    ) -> CheckoutSession:
        """Ask Stripe for one session. The idempotency key is the attempt number."""
        return_url = f"{self.settings.public_base_url}{return_path}"
        return self.checkout.create_session(
            submission_id=submission.id,
            price_cents=gate.price_cents,
            currency=gate.currency,
            expires_at=gate.expires_at,
            success_url=return_url,
            cancel_url=return_url,
            idempotency_key=f"{submission.id}:{attempt}",
        )

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


def _or_default(saved: int | None, default: int) -> int:
    return default if saved is None else saved


def _blank_to_none(value: str | None) -> str | None:
    """Treat a missing or whitespace-only code as no voucher."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


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
    """Remove a cleanup path, raising unless the path is actually absent."""
    import shutil

    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    if path.exists() or path.is_symlink():
        raise OSError("cleanup path remains after deletion")


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


def _media_count(analysis: Path) -> int:
    """Stored clips plus stills; used to notice media filled in after completion."""
    total = 0
    for folder, pattern in (("public_clips", "*.mp4"), ("public_frames", "*.jpg")):
        directory = analysis / folder
        if directory.is_dir():
            total += sum(1 for path in directory.glob(pattern) if path.is_file())
    return total
