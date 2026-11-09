"""Manual acquisition orchestration; P1-A never hands a replay to analysis."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import psycopg
from loguru import logger

from public_site.accounts import Account, account_email
from public_site.errors import EXPIRED, RequestRejected
from public_site.models import Submission
from public_site.recording_models import (
    AcquisitionView,
    ReviewCodeCreated,
    ReviewCodeDraft,
)
from public_site.recording_store import RecordingStore
from public_site.store import add_days, utc_now
from public_site.tokens import hash_token, new_token

if TYPE_CHECKING:
    from public_site.service import SubmissionService, Verifier


class RecordingService:
    """Reuse contact verification and checkout around a durable acquisition row."""

    def __init__(self, service: SubmissionService, database: RecordingStore) -> None:
        self.service = service
        self.database = database
        self._published: dict[str, str] = {}

    def create(
        self,
        draft: ReviewCodeDraft,
        account: Account | None,
        ip: str,
        verifier: Verifier,
        browser_cookie: str,
    ) -> tuple[ReviewCodeCreated, str | None]:
        """Persist before checkout; verification gates both checkout and recording."""
        if not verifier(draft.turnstile_token):
            raise RequestRejected(400, "Complete the verification challenge.")
        svc = self.service
        if svc.accounts is None or svc.guest is None or svc.checkout is None:
            raise RequestRejected(503, "Review-code submission is unavailable.")
        now = datetime.now(UTC)
        svc._check_limits(ip, account.id if account else None, now)
        if self.database.open_count() >= svc.settings.max_queued:
            raise RequestRejected(429, "The queue is full. Try again later.")
        quote = svc.payments.quote(admin=False)
        if quote.payment_state != "unpaid" or quote.price_cents <= 0:
            raise RequestRejected(503, "Review-code recording requires a paid checkout.")
        email = account_email(account.email if account else draft.email or "")
        if account is not None and not account.verified:
            raise RequestRejected(
                403, "Verify your email before submitting a review code."
            )
        token, submission = self._new_submission(account, email)
        retention_days = (
            svc.settings.account_retention_days
            if account
            else svc.settings.retention_days
        )
        self.database.create(
            submission,
            draft.review_code,
            draft.target_player_name,
            email,
            quote,
            now + timedelta(seconds=svc.settings.payment_hold_seconds),
            verified=account is not None,
            retention_days=retention_days,
        )
        self.publish(self.database.get(submission.id))
        svc.notices.seal(submission.id, token)
        created = ReviewCodeCreated(
            token=token,
            submission_id=submission.id,
            review_path=f"/review/{token}",
            price_cents=quote.price_cents,
        )
        fresh = None
        if account is None:
            created.notice_pending, fresh = svc.arrange_guest_notice(
                created, email, browser_cookie
            )
        self.reconcile()
        return created, fresh

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
        self.database.reconcile()
        for row in self.database.snapshots():
            submission_id = row["submission_id"]
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
        return AcquisitionView(
            job_reference=submission_id,
            price_cents=self.service.payments.get(submission_id).price_cents,
            state=row["state"],
            target_player_name=row["target_player_name"],
            contact_verified=row["contact_verified_at"] is not None,
            target_confirmed=row["target_confirmed_at"] is not None,
            customer_message=row["customer_message"],
        )

    def require_verified_contact(self, submission_id: str) -> None:
        """Refuse checkout until the requested mailbox is verified."""
        row = self.database.get(submission_id)
        if row is None or row["contact_verified_at"] is None:
            raise RequestRejected(409, "Confirm your email before opening checkout.")


def _row_fingerprint(row: dict) -> str:
    """Digest of one committed acquisition row, used to skip unchanged rows."""
    encoded = json.dumps(row, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
