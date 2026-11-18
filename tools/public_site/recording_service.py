"""Manual acquisition orchestration; P1-A never hands a replay to analysis."""

from __future__ import annotations

import hashlib
import json
import re
import smtplib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import psycopg
from loguru import logger

from public_site.accounts import Account, account_email
from public_site.admin_alerts import AdminAlertSender
from public_site.errors import EXPIRED, RequestRejected
from public_site.guest_notices import GuestNoticeArrange
from public_site.models import Submission
from public_site.payments import PriceQuote
from public_site.recording_intake import RecordingIntake
from public_site.recording_models import (
    AcquisitionView,
    RecordingAction,
    RecordingJob,
    ReviewCodeCreated,
    ReviewCodeDraft,
    normalize_code,
)
from public_site.recording_store import RecordingStore, report_expires_at
from public_site.report_canary import CanaryStore, CanaryUnavailable
from public_site.store import add_days, utc_now
from public_site.telemetry import classify_error
from public_site.tokens import hash_token, new_token

_ANALYSIS_STARTED = frozenset({"validating", "processing", "complete"})
_WORKER_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
RETRY_RUNNING = "Analysis is still stopping for this recording. Try again in a moment."
RETRY_NOT_FAILED = "Only a failed analysis of a verified recording can be retried."
_OPERATOR_STATES = frozenset({"awaiting_recording", "recording", "recording_failed"})
_ADMIN_ALERT_RETRY = timedelta(minutes=5)
STRIPE_UNCHECKED = (
    "Stripe could not confirm that this checkout can no longer be paid. "
    "Nothing was changed. Try again."
)

if TYPE_CHECKING:
    from public_site.service import SubmissionService, Verifier


class RecordingService:
    """Reuse contact verification and checkout around a durable acquisition row."""

    def __init__(
        self,
        service: SubmissionService,
        database: RecordingStore,
        canary: CanaryStore | None = None,
    ) -> None:
        self.service = service
        self.database = database
        self.canary = canary or CanaryStore(service.settings.root)
        self.intake = RecordingIntake(self)
        self.admin_alerts = AdminAlertSender(service.settings)
        self._alert_retry_at: dict[str, datetime] = {}
        self._published: dict[str, str] = {}
        self._is_running: Callable[[str], bool] | None = None
        self._wake: Callable[[], None] | None = None

    def bind_worker(
        self, *, is_running: Callable[[str], bool], wake: Callable[[], None]
    ) -> None:
        """Give analysis retry the worker's job ownership check and wake-up."""
        self._is_running = is_running
        self._wake = wake

    def create(
        self,
        draft: ReviewCodeDraft,
        account: Account | None,
        ip: str,
        verifier: Verifier,
        browser_cookie: str,
    ) -> tuple[ReviewCodeCreated, str | None]:
        """Persist before checkout; verification gates checkout, voucher use and recording.

        A voucher code is checked for the review-code path here and attached as
        pending. It is used only once the contact is verified (see
        ``redeem_pending_vouchers``), which for a signed-in user is right away.
        """
        if not verifier(draft.turnstile_token):
            raise RequestRejected(400, "Complete the verification challenge.")
        svc = self.service
        if svc.accounts is None or svc.guest is None or svc.checkout is None:
            raise RequestRejected(503, "Review-code submission is unavailable.")
        now = datetime.now(UTC)
        code = (draft.voucher_code or "").strip() or None
        voucher_id = svc.attach_voucher(code, ip, now, "review_code")
        svc._check_limits(ip, account.id if account else None, now)
        if self.database.open_count() >= svc.settings.max_queued:
            raise RequestRejected(429, "The queue is full. Try again later.")
        quote = self._quote(account, voucher_id)
        email = account_email(account.email if account else draft.email or "")
        if account is not None and not account.verified:
            raise RequestRejected(
                403, "Verify your email before submitting a review code."
            )
        token, submission = self._new_submission(account, email)
        self._capture_canary(submission.id, draft.review_code)
        self._persist(submission, draft, email, quote, now, account, voucher_id)
        svc.notices.seal(submission.id, token)
        created = ReviewCodeCreated(
            token=token,
            submission_id=submission.id,
            review_path=f"/review/{token}",
            payment_required=quote.payment_state != "not_required",
            price_cents=quote.price_cents,
        )
        fresh = None
        if account is None:
            arranged = svc.arrange_guest_notice(created, email, browser_cookie)
            created.notice_pending = arranged.pending
            created.confirm_mail_outcome = arranged.mail_outcome
            created.confirm_mail_retry_after_seconds = arranged.retry_after_seconds
            fresh = arranged.cookie
        self.reconcile()
        if voucher_id is not None:
            created.voucher_state = svc.payments.get(submission.id).voucher_state
        return created, fresh

    def _quote(self, account: Account | None, voucher_id: str | None) -> PriceQuote:
        """An admin without a voucher pays nothing; everyone else needs a paid checkout.

        A voucher keeps the voucher flow, as on the upload path.
        """
        svc = self.service
        if account is not None and voucher_id is None and svc._is_admin(account.id):
            quote = svc.payments.quote(admin=True)
            if quote.payment_state != "not_required":
                raise RequestRejected(503, "Review-code submission is unavailable.")
            return quote
        quote = svc.payments.quote(admin=False)
        if quote.payment_state != "unpaid" or quote.price_cents <= 0:
            raise RequestRejected(503, "Review-code recording requires a paid checkout.")
        return quote

    def _persist(
        self,
        submission: Submission,
        draft: ReviewCodeDraft,
        email: str,
        quote: PriceQuote,
        now: datetime,
        account: Account | None,
        voucher_id: str | None,
    ) -> None:
        settings = self.service.settings
        self.database.create(
            submission,
            draft.review_code,
            email,
            quote,
            now + timedelta(seconds=settings.payment_hold_seconds),
            verified=account is not None,
            retention_days=(
                settings.account_retention_days if account else settings.retention_days
            ),
            voucher_id=voucher_id,
        )
        self.publish(self.database.get(submission.id))

    def redeem_pending_vouchers(self) -> int:
        """Use each verified request's pending voucher, then activate what was redeemed.

        Called after create, after guest verification, and on every reconcile
        pass, so a redemption interrupted before it ran is retried. Redemption
        itself is ``VoucherStore.finalize``; activation is ``database.reconcile``.
        Returns how many vouchers were redeemed by this call.
        """
        vouchers = self.service.vouchers
        if vouchers is None:
            return 0
        try:
            pending = self.database.pending_voucher_ids()
        except psycopg.Error as exc:
            logger.warning("Review-code voucher scan deferred ({})", type(exc).__name__)
            return 0
        redeemed = 0
        for submission_id in pending:
            try:
                decision = vouchers.finalize(submission_id, datetime.now(UTC))
            except psycopg.Error as exc:
                logger.warning(
                    "Review-code voucher redemption deferred for {} ({})",
                    submission_id,
                    type(exc).__name__,
                )
                continue
            if decision.outcome == "redeemed":
                redeemed += 1
            elif decision.outcome != "failed":
                logger.warning(
                    "Review-code voucher not redeemed for {} ({})",
                    submission_id,
                    decision.reason or decision.outcome,
                )
        if redeemed:
            self.database.reconcile()
        return redeemed

    def _capture_canary(self, submission_id: str, review_code: str) -> None:
        """Store the code's HMAC durably before the row exists; refuse without it."""
        try:
            self.canary.capture(submission_id, review_code)
        except (CanaryUnavailable, ValueError) as exc:
            logger.error("Review-code canary capture failed ({})", type(exc).__name__)
            raise RequestRejected(
                503, "Review-code submission is temporarily unavailable."
            ) from None

    def act(self, submission_id: str, actor_id: str, action: RecordingAction) -> None:
        """Apply an operator action; ``correct_code`` keeps its canary in step.

        The corrected code's HMAC is written as a pending file before the
        database update and promoted over the record after it. A pending file
        leaves the submission unchecked, so a failed promotion keeps publication
        blocked until startup repair (or the operator) resolves it.
        """
        if action.action == "recording_failed":
            self._fail_recording(submission_id, actor_id, action)
            return
        if action.action == "retry_analysis":
            self._retry_analysis(submission_id, actor_id, action)
            return
        if action.action != "correct_code":
            self.database.act(submission_id, actor_id, action)
            return
        try:
            code = normalize_code(action.review_code or "")
        except ValueError as exc:
            raise RequestRejected(400, str(exc)) from exc
        try:
            self.canary.stage_correction(submission_id, code)
        except (CanaryUnavailable, ValueError) as exc:
            logger.error("Review-code canary staging failed ({})", type(exc).__name__)
            raise RequestRejected(
                503, "The review-code privacy check is unavailable."
            ) from None
        try:
            self.database.act(submission_id, actor_id, action)
        except BaseException:
            self._discard_correction(submission_id)
            raise
        self._commit_correction(submission_id)

    def _fail_recording(
        self, submission_id: str, actor_id: str, action: RecordingAction
    ) -> None:
        """Flag a recording exception without leaving a verified job queued locally.

        The worker claim lock is held throughout: a queued recording is either
        claimed first (then refused here) or returned to ``received`` before any
        claim can see it.
        """
        store = self.service.store
        with store.claim_guard():
            current = store.get(submission_id)
            if current is not None and current.status in _ANALYSIS_STARTED:
                raise RequestRejected(
                    409, "Analysis has started for this recording. Wait for it to finish."
                )
            self.database.act(submission_id, actor_id, action)
            self.refresh_projection(submission_id)

    def _retry_analysis(
        self, submission_id: str, actor_id: str, action: RecordingAction
    ) -> None:
        """Queue the verified recording again after its analysis failed.

        The claim lock is held and no job thread may still own the submission,
        so the failed run's cleanup can never touch the new run's files. A failed
        run is identified by its ``processing_started_at``: when the latest retry
        already names it, only the local requeue is completed (no new event).
        """
        svc, store = self.service, self.service.store
        if not action.note.strip():
            raise RequestRejected(400, "A note is required to retry analysis.")
        with svc._probe_locks.hold(submission_id), store.claim_guard():
            if self._is_running is None or self._is_running(submission_id):
                raise RequestRejected(409, RETRY_RUNNING)
            current = store.get(submission_id)
            if not _failed_analysis(current):
                raise RequestRejected(409, RETRY_NOT_FAILED)
            self.intake.verified_for_retry(submission_id)
            if self.database.retried_attempt(submission_id) != current.processing_started_at:
                self.database.act(submission_id, actor_id, action, _failed_attempt(current))
            elif not self._retry_committed(submission_id):
                raise RequestRejected(409, RETRY_NOT_FAILED)
            store.requeue_failed(submission_id, utc_now())
        if self._wake is not None:
            self._wake()

    def detail(self, submission_id: str) -> dict:
        """Operator detail plus the local analysis state; never paths or contact data.

        ``analysis.error`` is the fixed public message the customer already sees.
        """
        detail = self.database.detail(submission_id)
        current = self.service.store.get(submission_id)
        detail["analysis"] = (
            None
            if current is None
            else {
                "status": current.status,
                "step": current.step,
                "error": current.error,
                "input_type": current.input_type,
            }
        )
        return detail

    def _retry_committed(self, submission_id: str) -> bool:
        """True while the database still allows the retry it recorded to run."""
        row = self.database.get(submission_id)
        return (
            row is not None
            and row["recording_handoff_at"] is None
            and row["report_expired_at"] is None
            and self.database.analysis_admitted(submission_id)
        )

    def hand_off(self, submission: Submission) -> bool:
        """Record that analysis accepted the recording; True once durably handed off.

        Idempotent: a repeat with the same reference records nothing new. A
        failure leaves the code stored and the reconcile pass retries.
        """
        if (
            submission.input_type != "review_code"
            or submission.status != "complete"
            or not submission.processing_started_at
        ):
            return False
        try:
            accepted = self.database.confirm_recording_handoff(
                submission.id, handoff_reference(submission)
            )
        except (psycopg.Error, OSError) as exc:
            logger.warning(
                "Review-code handoff deferred for {} ({})", submission.id, type(exc).__name__
            )
            return False
        if accepted:
            try:
                self.refresh_projection(submission.id)
            except psycopg.Error:
                logger.warning("Review-code projection unavailable: {}", submission.id)
        return accepted

    def report_available(self, submission_id: str) -> bool:
        """True only after a durable handoff; unknown or unreadable means not yet."""
        try:
            row = self.database.get(submission_id)
        except (psycopg.Error, OSError):
            return False
        return row is not None and row["recording_handoff_at"] is not None

    def _finish_handoff(self, row: dict) -> None:
        """Retry a missing handoff for a completed analysis, then its held notice.

        Uses the same reference as the worker. Notice delivery is idempotent and
        reads nothing from the database once the notice was sent. A submission
        incorrectly marked failed after a verified publication is restored to
        ``complete`` first so handoff can proceed without re-analysis.
        """
        svc = self.service
        current = svc.store.get(row["submission_id"])
        if current is None:
            return
        if current.status != "complete":
            if not self._restore_complete_from_verified_report(current):
                return
            current = svc.store.get(row["submission_id"])
            if current is None or current.status != "complete":
                return
        if row["recording_handoff_at"] is None and not self.hand_off(current):
            return
        kind = "no_moments" if current.outcome == "no_deaths" else "ready"
        svc.note_terminal(current, kind)

    def _restore_complete_from_verified_report(self, submission: Submission) -> bool:
        """Undo a false interrupted failure when the report is already verified."""
        from public_site.report_publications import load_verified_publication

        if submission.status != "failed":
            return False
        if submission.outcome not in {"coached", "no_deaths"}:
            return False
        if load_verified_publication(self.service.settings.root, submission.id) is None:
            return False

        def restore(item: Submission) -> None:
            if item.status != "failed" or item.outcome not in {"coached", "no_deaths"}:
                return
            item.status = "complete"
            item.error = None
            item.step = "ready"
            item.updated_at = utc_now()

        updated = self.service.store.update(submission.id, restore)
        return updated is not None and updated.status == "complete"

    def _discard_correction(self, submission_id: str) -> None:
        try:
            self.canary.discard_correction(submission_id)
        except OSError as exc:
            logger.error("Review-code canary discard failed ({})", type(exc).__name__)

    def _commit_correction(self, submission_id: str) -> None:
        try:
            self.canary.commit_correction(submission_id)
        except OSError as exc:
            logger.error("Review-code canary promotion failed ({})", type(exc).__name__)
            raise RequestRejected(
                500,
                "The review code was corrected, but its privacy check could not be "
                "finalized. Report publication stays blocked until it is repaired.",
            ) from None

    def repair_canaries(self) -> None:
        """Startup repair of pending ``correct_code`` canaries (see ``CanaryStore.repair``)."""
        try:
            self.canary.repair(self.database.get)
        except OSError as exc:
            logger.error("Review-code canary repair unavailable ({})", type(exc).__name__)

    def _new_submission(
        self, account: Account | None, email: str
    ) -> tuple[str, Submission]:
        svc = self.service
        stamp, token = utc_now(), new_token()
        days = (
            svc.settings.account_retention_days
            if account
            else svc.settings.retention_days
        )
        return token, Submission(
            id=uuid.uuid4().hex,
            access_token_hash=hash_token(token),
            input_type="review_code",
            source="nintendo_review_code",
            email=email if account else None,
            notify=account.notify_by_email if account else False,
            user_id=account.id if account else None,
            created_at=stamp,
            updated_at=stamp,
            expires_at=add_days(stamp, days),
        )

    def publish(self, row: dict | None) -> Submission | None:
        """Restore a missing filesystem projection from its committed snapshot."""
        if row is None:
            return None
        svc = self.service
        snapshot = Submission.model_validate(row["submission_snapshot"])
        current = svc.store.get(snapshot.id)
        if current is None:
            svc.store.save_submission(snapshot)
            current = snapshot
        token_path = svc.store.root / "by_token" / snapshot.access_token_hash
        if not token_path.is_file():
            svc.store.remember_token(snapshot.id, snapshot.access_token_hash)
        if row["state"] == "expired" and current.status != "expired":
            current.status, current.error = "expired", EXPIRED
            svc.store.save_submission(current)
        elif row["report_expired_at"] is not None and current.status != "expired":
            current.status, current.error = "expired", EXPIRED
            svc.store.save_submission(current)
        elif (
            row["queued_at"] is not None
            and row["report_expired_at"] is None
            and current.status == "expired"
        ):
            current.status, current.error = "received", None
            svc.store.save_submission(current)
        self._sync_lifecycle(row, current)
        self._finish_retry(row, current)
        if row["sensitive_data_purged_at"] or row["report_expired_at"]:
            self._scrub_projection(current)
            current = svc.store.get(snapshot.id) or current
        else:
            svc._remember_account(snapshot.user_id, snapshot)
        if (
            row["contact_verified_at"] is not None
            and current.user_id is None
            and not current.notify
        ):

            def enable(item: Submission) -> None:
                item.email, item.notify = row["requested_email"], True

            svc.store.update(snapshot.id, enable)
        return svc.store.get(snapshot.id)

    def _sync_lifecycle(self, row: dict, current: Submission) -> None:
        """Mirror durable recording state the worker and customer view depend on.

        Only a verified (``uploading``) recording may stay locally ``queued``;
        a handed-off report expires ``retention_days`` after its handoff.
        """
        expires = report_expires_at(row)
        stale_expiry = (
            expires is not None
            and row["report_expired_at"] is None
            and current.expires_at != expires
        )
        unqueue = row["state"] != "uploading" and current.status == "queued"
        if not stale_expiry and not unqueue:
            return

        def sync(item: Submission) -> None:
            if stale_expiry:
                item.expires_at = expires
            if unqueue and item.status == "queued":
                item.status, item.step = "received", "received"
            item.updated_at = utc_now()

        self.service.store.update(current.id, sync)

    def _finish_retry(self, row: dict, current: Submission) -> None:
        """Requeue locally when a committed analysis retry names this failed run.

        Covers a crash or write failure between the retry event and the requeue.
        The retry was refused while any job thread still owned the submission.
        """
        if row["state"] != "uploading" or not _failed_analysis(current):
            return
        if self.database.retried_attempt(current.id) != current.processing_started_at:
            return
        if self._retry_committed(current.id):
            self.service.store.requeue_failed(current.id, utc_now())

    def _scrub_projection(self, submission: Submission) -> None:
        """Mirror durable privacy cleanup into the local status projection."""
        svc = self.service
        if (
            submission.email
            or submission.display_name
            or submission.user_id
            or submission.notify
        ):

            def scrub(item: Submission) -> None:
                item.email = None
                item.display_name = None
                item.user_id = None
                item.notify = False

            svc.store.update(submission.id, scrub)
        svc.notices.clear_sealed(submission.id)
        if svc.accounts is not None:
            svc.accounts.drop_history(submission.id)
        if svc.guest is not None:
            svc.guest.purge_submission(submission.id)

    def reconcile(self) -> None:
        """Rebuild projections and recover committed payment-to-queue transitions.

        A row is republished only when its committed state changed since its last
        successful publish in this process; failures are retried on the next pass.
        """
        self.redeem_pending_vouchers()
        self.database.reconcile()
        for row in self.database.snapshots():
            submission_id = row["submission_id"]
            if row["state"] in {"uploading", "ready"} and row["report_expired_at"] is None:
                try:
                    self._finish_handoff(row)
                except (OSError, psycopg.Error):
                    logger.warning("Review-code handoff retry unavailable: {}", submission_id)
            fingerprint = _row_fingerprint(row)
            if self._published.get(submission_id) == fingerprint:
                continue
            try:
                self.publish(row)
            except (OSError, psycopg.Error):
                logger.warning("Review-code projection unavailable: {}", submission_id)
                continue
            self._published[submission_id] = fingerprint

    def refresh_projection(self, submission_id: str) -> None:
        """Apply one committed acquisition change to its filesystem projection."""
        try:
            self.publish(self.database.get(submission_id))
        except OSError:
            logger.warning("Review-code projection unavailable: {}", submission_id)

    def view(self, submission_id: str) -> AcquisitionView | None:
        """Project factual acquisition status without exposing code or contact details."""
        row = self.database.get(submission_id)
        if row is None:
            return None
        verified = row["contact_verified_at"] is not None
        email = row["requested_email"]
        dispatched = False
        if not verified and email and self.service.guest is not None:
            dispatched = self.service.guest.has_open_challenge(submission_id, email)
        return AcquisitionView(
            price_cents=self.service.payments.get(submission_id).price_cents,
            state=row["state"],
            contact_verified=verified,
            customer_message=row["customer_message"],
            contact_email_masked=None if verified or not email else mask_email(email),
            confirm_mail_dispatched=dispatched,
            cancelled=row["state"] == "expired" and self.database.was_cancelled(submission_id),
        )

    def contact_cooldown(self, submission_id: str, browser_cookie: str) -> int | None:
        """This browser's confirmation cooldown for an unverified request, if any."""
        row = self.database.get(submission_id)
        if row is None or row["contact_verified_at"] is not None or not row["requested_email"]:
            return None
        return self.service.guest_cooldown(browser_cookie, row["requested_email"])

    def resend_contact(
        self, token: str, browser_cookie: str
    ) -> GuestNoticeArrange:
        """Send another confirmation link unless this browser is cooling down.

        Cooldown is reported without claiming a send. SMTP failures raise.
        """
        submission, row, price_cents = self._contact_request(token)
        email = row["requested_email"]
        retry = self.service.guest_cooldown(browser_cookie, email)
        if retry is not None:
            return GuestNoticeArrange(True, None, "cooldown", retry)
        return self.service.arrange_guest_notice(
            _created(token, submission.id, price_cents),
            email,
            browser_cookie,
            propagate_mail_failure=True,
        )

    def change_contact(
        self, token: str, raw_email: str, browser_cookie: str
    ) -> GuestNoticeArrange:
        """Correct the address of an unconfirmed request, then try to send a new link.

        The request, its reference, voucher and payment state stay as they are.
        The new link follows the same limits as any other confirmation email.
        """
        submission, _row, price_cents = self._contact_request(token)
        email = account_email(raw_email)
        if not self.database.change_contact(submission.id, email):
            raise RequestRejected(409, "This request's email address can no longer be changed.")
        svc = self.service
        now = datetime.now(UTC)
        if svc.guest is not None:
            svc.guest.rebind_request(submission.id, email, now)
        return svc.arrange_guest_notice(
            _created(token, submission.id, price_cents),
            email,
            browser_cookie,
            propagate_mail_failure=True,
        )

    def _contact_request(self, token: str) -> tuple[Submission, dict, int]:
        """The review-code request behind a review link, while checkout is still open."""
        svc = self.service
        submission = svc._require(token)
        row = self.database.get(submission.id)
        if row is None:
            raise RequestRejected(404, "Review-code submission not found.")
        gate = svc.payments.get(submission.id)
        if gate is None or gate.status == "expired":
            raise RequestRejected(409, "Checkout has expired. Submit a new request.")
        return submission, row, gate.price_cents

    def queue_jobs(self) -> list[RecordingJob]:
        """Jobs that still need the operator.

        Waiting, recording and flagged jobs stay. A verified recording stays only
        when its analysis failed or expired, or its status cannot be read.
        Ready, closed and expired jobs leave the queue.
        """
        return [job for job in self.database.list_jobs() if self._needs_operator(job)]

    def _needs_operator(self, job: RecordingJob) -> bool:
        if job.state in _OPERATOR_STATES:
            return True
        if job.state != "uploading":
            return False
        try:
            current = self.service.store.get(job.submission_id)
        except OSError:
            return True
        return current is None or current.status in {"failed", "expired"}

    def close_unpaid(
        self, submission_id: str, actor_id: str, expected_version: int, reason: str
    ) -> dict:
        """Cancel an unpaid request after Stripe confirms no payment can still complete.

        Stripe is settled first; a paid session is recorded through the shared
        paid transition and the cancel is refused. Unknown or unreachable Stripe
        state changes nothing. The cancellation email failing never undoes it.
        """
        row = self.database.get(submission_id)
        if row is None:
            raise RequestRejected(404, "Recording job not found.")
        if row["version"] != expected_version:
            raise RequestRejected(409, "Job changed. Reload before applying this action.")
        stripe = self._settle_checkout(submission_id)
        details = self.database.close_unpaid(
            submission_id, actor_id, expected_version, reason, stripe
        )
        self.refresh_projection(submission_id)
        if details["cancellation_email"] == "pending":
            try:
                self.send_cancellation(submission_id)
            except (OSError, psycopg.Error) as exc:
                logger.warning(
                    "Cancellation email deferred for {} ({})", submission_id, type(exc).__name__
                )
        current = self.service.store.get(submission_id)
        sent = current is not None and "cancelled" in current.notices_sent
        return {**details, "email_sent": sent}

    def _settle_checkout(self, submission_id: str) -> dict:
        """Make sure no Checkout session for this request can still be paid."""
        svc = self.service
        gate = svc.payments.get(submission_id)
        if gate is None or gate.payment_state == "paid":
            raise RequestRejected(409, "A payment has already been recorded for this request.")
        if gate.payment_state != "checkout_created" or not gate.checkout_session_id:
            return {"observed": "no_session"}
        if svc.checkout is None:
            raise RequestRejected(503, STRIPE_UNCHECKED)
        session_id = gate.checkout_session_id
        session = self._retrieve(session_id)
        if session.status == "open":
            try:
                svc.checkout.expire_session(session_id)
            except Exception as exc:
                logger.info("Checkout expire refused for {} ({})", submission_id, type(exc).__name__)
            session = self._retrieve(session_id)
            observed = "expired_by_operator"
        else:
            observed = f"already_{session.status}"
        if session.payment_status == "paid":
            result = svc.record_paid_session(submission_id, session)
            self.refresh_projection(submission_id)
            raise RequestRejected(
                409,
                f"Stripe shows this request as paid (recorded: {result}). It was not cancelled.",
                "paid_at_stripe",
            )
        if session.status != "expired":
            raise RequestRejected(503, STRIPE_UNCHECKED)
        return {"observed": observed, "session_id": session_id, "session_status": session.status}

    def _retrieve(self, session_id: str):
        try:
            return self.service.checkout.retrieve_session(session_id)
        except Exception as exc:
            logger.warning("Checkout lookup failed ({})", type(exc).__name__)
            raise RequestRejected(503, STRIPE_UNCHECKED) from None

    def verified_contact(self, submission_id: str) -> str | None:
        """The request's address, only once the customer has confirmed it."""
        row = self.database.get(submission_id)
        if row is None or row["contact_verified_at"] is None:
            return None
        return row["requested_email"]

    def send_cancellation(self, submission_id: str) -> None:
        """Send the cancellation email once and audit it; a failure is retried later."""
        svc = self.service
        submission = svc.store.get(submission_id)
        if submission is None:
            return
        svc.deliver_notice(submission, "cancelled")
        current = svc.store.get(submission_id)
        if current is not None and "cancelled" in current.notices_sent:
            self.database.note_cancellation_sent(submission_id)

    def send_pending_cancellations(self) -> None:
        """Maintenance: finish cancellation emails left behind by a failure or crash."""
        try:
            pending = self.database.pending_cancellation_notices()
        except psycopg.Error as exc:
            logger.warning("Cancellation email scan deferred ({})", type(exc).__name__)
            return
        for submission_id in pending:
            try:
                self.send_cancellation(submission_id)
            except (OSError, psycopg.Error) as exc:
                logger.warning(
                    "Cancellation email deferred for {} ({})", submission_id, type(exc).__name__
                )

    def send_admin_alerts(self, now: datetime | None = None) -> None:
        """Maintenance: email the admins once per job newly awaiting its recording.

        A failed send never touches the job; it is retried after a cool-down.
        """
        if not self.admin_alerts.enabled:
            return
        now = now or utc_now()
        try:
            pending = self.database.pending_admin_alerts()
        except psycopg.Error as exc:
            logger.warning("Admin alert scan deferred ({})", type(exc).__name__)
            return
        for submission_id in pending:
            if self._alert_retry_at.get(submission_id, now) > now:
                continue
            try:
                self.database.send_admin_alert(submission_id, self.admin_alerts.send)
                self._alert_retry_at.pop(submission_id, None)
            except (OSError, smtplib.SMTPException, psycopg.Error) as exc:
                self._alert_retry_at[submission_id] = now + _ADMIN_ALERT_RETRY
                logger.warning(
                    "Admin alert deferred for {} ({})", submission_id, type(exc).__name__
                )

    def require_verified_contact(self, submission_id: str) -> None:
        """Refuse checkout until the requested mailbox is verified."""
        row = self.database.get(submission_id)
        if row is None or row["contact_verified_at"] is None:
            raise RequestRejected(409, "Confirm your email before opening checkout.")


def mask_email(email: str) -> str:
    """Enough of an address to spot a typo: ``ke•••a@gmail.com``.

    The domain stays whole because domain typos are the common mistake.
    """
    local, _, domain = email.partition("@")
    if len(local) <= 3:
        return f"{local[:1]}•••@{domain}"
    return f"{local[:2]}•••{local[-1]}@{domain}"


def _created(token: str, submission_id: str, price_cents: int) -> ReviewCodeCreated:
    return ReviewCodeCreated(
        token=token,
        submission_id=submission_id,
        review_path=f"/review/{token}",
        price_cents=price_cents,
    )


def _failed_analysis(current: Submission | None) -> bool:
    """A review-code submission whose analysis ran and failed."""
    return (
        current is not None
        and current.input_type == "review_code"
        and current.status == "failed"
        and bool(current.processing_started_at)
    )


def _failed_attempt(current: Submission) -> dict:
    """Audit facts of the failed run: closed codes only, never its raw error text."""
    code, _diagnostic = classify_error(current.error or "")
    details = {
        "failed_error_code": code.value,
        "failed_step": current.step,
        "processing_started_at": current.processing_started_at,
    }
    if current.worker_id and _WORKER_ID.fullmatch(current.worker_id):
        details["worker_id"] = current.worker_id
    return details


def handoff_reference(submission: Submission) -> str:
    """Deterministic, code-free reference; a retry always repeats it exactly."""
    return f"analysis:{submission.id}:{submission.processing_started_at}"


def _row_fingerprint(row: dict) -> str:
    """Digest of one committed acquisition row, used to skip unchanged rows."""
    encoded = json.dumps(row, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
