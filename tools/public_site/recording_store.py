"""Durable review-code acquisition queue, separate from operational telemetry."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from public_site.errors import RequestRejected
from public_site.models import Submission
from public_site.payments import PaymentStore, PriceQuote
from public_site.recording_models import (
    RecordingAction,
    RecordingJob,
    UploadAttempt,
    normalize_code,
)
from public_site.store import add_days

# An abandoned upload link stays usable until it expires; a replacement waits
# out the link lifetime plus this allowance for clock skew between hosts.
UPLOAD_CLOCK_TOLERANCE = timedelta(minutes=15)
UPLOAD_ENDINGS = (
    "recording_upload_verified",
    "recording_upload_rejected",
    "recording_upload_abandoned",
)
UPLOAD_OPEN = (
    "An upload is already open for this job with a different file. "
    "Cancel it before choosing another file."
)

MIGRATION = Path(__file__).parent / "migrations" / "001_review_code_acquisitions.sql"
MIGRATION_STATION = (
    Path(__file__).parent / "migrations" / "002_drop_target_confirmation_check.sql"
)
# A review-code gate is authorized by a Stripe payment, by a redeemed voucher, or
# by an administrator's request: ``not_required`` with no voucher. Only an admin
# create opens a review-code gate that is ``not_required`` without a voucher.
# ``is_authorized`` is the Python form of the same rule; keep them identical.
AUTHORIZED = """(g.payment_state = 'paid'
    OR (g.payment_state = 'not_required'
        AND (g.voucher_state = 'redeemed' OR g.voucher_state IS NULL)))"""
_ANALYSIS_READY = f"""g.source = 'nintendo_review_code' AND {AUTHORIZED}
    AND a.state = 'uploading' AND a.queued_at IS NOT NULL"""


def is_authorized(row: dict) -> bool:
    """True when a gate row was paid by card, by a redeemed voucher, or for an admin.

    ``row`` must carry ``voucher_state``; a row without it is never authorized
    as an admin request.
    """
    if row.get("payment_state") == "paid":
        return True
    if row.get("payment_state") != "not_required" or "voucher_state" not in row:
        return False
    return row["voucher_state"] in {"redeemed", None}


def _authorization(payment_state: str, voucher_state: str | None) -> str:
    """How an authorized review-code job was authorized, for the operator."""
    if payment_state != "not_required":
        return "payment"
    return "voucher" if voucher_state == "redeemed" else "admin"


def analysis_retryable(row: dict) -> bool:
    """A verified, still-queued recording that was never handed off or expired.

    ``row`` must carry the gate status as ``gate_status``.
    """
    return (
        row["state"] == "uploading"
        and row["recording_handoff_at"] is None
        and row["report_expired_at"] is None
        and row.get("gate_status") == "queued"
        and is_authorized(row)
    )


def report_expires_at(row: dict) -> str | None:
    """Report retention runs from the handoff, when the report becomes available."""
    handoff = row.get("recording_handoff_at")
    if handoff is None:
        return None
    stamp = handoff.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return add_days(stamp, row["retention_days"])


def _closed_fields(row: dict, action: RecordingAction) -> dict:
    """Close an acquisition and purge its contact and review-code data."""
    snapshot = dict(row["submission_snapshot"])
    snapshot.update(email=None, display_name=None, user_id=None, notify=False)
    return {
        "state": "closed",
        "customer_message": action.customer_message,
        "review_code": None,
        "target_player_name": None,
        "requested_email": None,
        "contact_verified_at": None,
        "submission_snapshot": Jsonb(snapshot),
        "sensitive_data_purged_at": datetime.now(UTC),
    }


def _without_codes(details: dict, codes: set[str | None]) -> dict:
    """Replace any form of these review codes in top-level text fields."""
    candidates = set(codes)
    for code in tuple(candidates):
        if not code:
            continue
        try:
            canonical = normalize_code(code)
        except ValueError:
            canonical = code
        candidates.update({code.lower(), canonical, canonical.replace("-", "")})
    for key, value in details.items():
        if not isinstance(value, str):
            continue
        for code in candidates:
            if code:
                value = value.replace(code, "[redacted]")
                value = value.replace(code.lower(), "[redacted]")
        details[key] = value
    return details


def _attempt_status(attempt: str | None, kind: str, link_valid: bool) -> str:
    """Where an attempt stands. Grants from before attempt ids are ``legacy``."""
    if attempt is None:
        return "legacy"
    if kind == "recording_upload_granted":
        return "live" if link_valid else "expired"
    return kind.removeprefix("recording_upload_")


def _replace_wait(current: UploadAttempt) -> str:
    stamp = current.replace_after.astimezone(UTC).strftime("%H:%M UTC")
    return (
        "The previous upload link can still be used until it expires. "
        f"A new upload can start after {stamp}."
    )


class RecordingStore:
    """PostgreSQL is authoritative for paid recording work and its audit trail."""

    def __init__(self, payments: PaymentStore, upload_link_seconds: int = 900) -> None:
        self.payments = payments
        self.upload_link_seconds = upload_link_seconds

    def ensure_schema(
        self, guest_retention_days: int, account_retention_days: int
    ) -> None:
        """Install the additive acquisition schema under a transaction lock."""
        with self._conn() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(8273341907)")
            conn.execute(MIGRATION.read_text())
            conn.execute(MIGRATION_STATION.read_text())
            conn.execute(
                """UPDATE review_code_acquisitions
                   SET retention_days = CASE
                       WHEN submission_snapshot->>'user_id' IS NULL THEN %s ELSE %s END
                   WHERE retention_days IS NULL""",
                (guest_retention_days, account_retention_days),
            )
            conn.execute(
                """ALTER TABLE review_code_acquisitions
                   ALTER COLUMN retention_days SET NOT NULL"""
            )
        self.payments.refresh_source_schema(required=True)

    def create(
        self,
        submission: Submission,
        code: str,
        email: str,
        quote: PriceQuote,
        payment_expires: datetime,
        verified: bool,
        retention_days: int,
        voucher_id: str | None = None,
    ) -> None:
        """Commit the pending submission and payment gate in one transaction.

        An attached voucher is only recorded as pending here; it is redeemed
        later by ``VoucherStore.finalize`` once the contact is verified.
        """
        with self._conn() as conn:
            self.payments.open_gate(
                submission.id,
                quote,
                payment_expires,
                voucher_id,
                conn=conn,
                source="nintendo_review_code",
            )
            conn.execute(
                """INSERT INTO review_code_acquisitions (
                    submission_id, submission_snapshot, review_code,
                    requested_email, contact_verified_at, retention_days, created_at)
                    VALUES (%s, %s, %s, %s, CASE WHEN %s THEN now() END, %s, %s)""",
                (
                    submission.id,
                    Jsonb(submission.model_dump()),
                    code,
                    email,
                    verified,
                    retention_days,
                    submission.created_at,
                ),
            )

    def verify_contact(self, submission_id: str, email: str) -> bool:
        """Record matching mailbox confirmation, returning success idempotently."""
        with self._conn() as conn:
            row = conn.execute(
                """UPDATE review_code_acquisitions
                   SET contact_verified_at = COALESCE(contact_verified_at, now())
                   WHERE submission_id = %s AND requested_email = %s
                   RETURNING submission_id""",
                (submission_id, email),
            ).fetchone()
        if row is None:
            return False
        self.reconcile()
        return True

    def change_contact(self, submission_id: str, email: str) -> bool:
        """Correct the address of a request awaiting confirmation. Audited, no address kept."""
        with self._conn() as conn:
            row = conn.execute(
                """UPDATE review_code_acquisitions
                   SET requested_email = %s, version = version + 1
                   WHERE submission_id = %s AND contact_verified_at IS NULL
                     AND state = 'pending_payment'
                   RETURNING submission_id""",
                (email, submission_id),
            ).fetchone()
            if row is None:
                return False
            self._event(conn, submission_id, "contact_email_changed", None, {})
        return True

    def reconcile(self) -> None:
        """Recover authorized activation after any crash, without the checkout deadline.

        The only activation path for both card payments and redeemed vouchers.
        It reads committed gate state alone, so it is safe to repeat.
        """
        with self._conn() as conn:
            rows = conn.execute(
                f"""UPDATE review_code_acquisitions a
                   SET state = 'awaiting_recording', queued_at = now(), version = version + 1
                   FROM submission_gates g
                   WHERE a.submission_id = g.submission_id
                     AND g.source = 'nintendo_review_code' AND {AUTHORIZED}
                     AND a.contact_verified_at IS NOT NULL AND a.state IN ('pending_payment', 'expired')
                   RETURNING a.submission_id""",
            ).fetchall()
            for row in rows:
                self._event(conn, row["submission_id"], "paid_recording_queued", None, {})
            conn.execute(
                f"""UPDATE review_code_acquisitions a SET state = 'expired'
                   FROM submission_gates g WHERE a.submission_id = g.submission_id
                   AND a.state = 'pending_payment' AND g.status = 'expired'
                   AND NOT {AUTHORIZED}""",
            )

    def pending_voucher_ids(self) -> list[str]:
        """Verified, unpaid requests whose attached voucher is not yet redeemed.

        An unverified guest is never listed, so their voucher is never used.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT g.submission_id FROM submission_gates g
                   JOIN review_code_acquisitions a USING (submission_id)
                   WHERE g.source = 'nintendo_review_code'
                     AND g.voucher_state = 'pending' AND g.payment_state = 'unpaid'
                     AND g.status = 'received' AND g.expires_at > now()
                     AND a.state = 'pending_payment'
                     AND a.contact_verified_at IS NOT NULL
                     AND a.sensitive_data_purged_at IS NULL
                   ORDER BY g.submission_id""",
            ).fetchall()
        return [row["submission_id"] for row in rows]

    def purge_expired_unpaid(self, now: datetime) -> list[str]:
        """Scrub unpaid acquisition data after its captured retention interval."""
        purged: list[str] = []
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT a.submission_id, a.retention_days, a.submission_snapshot
                   FROM review_code_acquisitions a
                   JOIN submission_gates g USING (submission_id)
                   WHERE a.state = 'expired' AND a.sensitive_data_purged_at IS NULL
                     AND g.status = 'expired'
                     AND g.payment_state IN ('unpaid', 'checkout_created')
                     AND g.expires_at + a.retention_days * interval '1 day' <= %s
                   FOR UPDATE OF a, g SKIP LOCKED""",
                (now,),
            ).fetchall()
            for row in rows:
                snapshot = dict(row["submission_snapshot"])
                snapshot.update(
                    email=None,
                    display_name=None,
                    user_id=None,
                    notify=False,
                )
                submission_id = row["submission_id"]
                conn.execute(
                    """UPDATE review_code_acquisitions
                       SET review_code = NULL, target_player_name = NULL,
                           requested_email = NULL, contact_verified_at = NULL,
                           submission_snapshot = %s, sensitive_data_purged_at = %s
                       WHERE submission_id = %s AND sensitive_data_purged_at IS NULL""",
                    (Jsonb(snapshot), now, submission_id),
                )
                self._event(conn, submission_id, "sensitive_data_purged", None, {})
                purged.append(submission_id)
        return purged

    def confirm_recording_handoff(
        self, submission_id: str, handoff_reference: str
    ) -> bool:
        """Record a verified analysis handoff and remove the no-longer-needed code.

        The caller must verify that the recording artifact was accepted by the
        existing analysis pipeline before invoking this transition.
        """
        reference = handoff_reference.strip()
        if not reference:
            raise ValueError("handoff reference is required")
        with self._conn() as conn:
            row = conn.execute(
                """SELECT state, recording_handoff_at, handoff_reference
                   FROM review_code_acquisitions
                   WHERE submission_id = %s FOR UPDATE""",
                (submission_id,),
            ).fetchone()
            if row is None:
                return False
            if row["recording_handoff_at"] is not None:
                return row["handoff_reference"] == reference
            if row["state"] != "uploading":
                return False
            conn.execute(
                """UPDATE review_code_acquisitions
                   SET state = 'ready', review_code = NULL,
                       recording_handoff_at = %s, handoff_reference = %s,
                       version = version + 1
                   WHERE submission_id = %s""",
                (datetime.now(UTC), reference, submission_id),
            )
            self._event(
                conn,
                submission_id,
                "recording_handoff_verified",
                None,
                {"handoff_reference": reference},
            )
        return True

    def grant_upload(
        self, submission_id: str, actor_id: str, expected_version: int, declared: dict
    ) -> tuple[UploadAttempt, int]:
        """Return the job's one open upload attempt, opening it when there is none.

        Repeating the same declaration returns the same attempt with no new
        event. A new attempt waits until every earlier upload link has expired.
        """
        with self._conn() as conn:
            row = self._authorized_for_update(conn, submission_id)
            if row["state"] not in {"awaiting_recording", "recording"}:
                raise RequestRejected(409, "This recording can no longer be uploaded.")
            current = self._attempt(conn, submission_id)
            now = datetime.now(UTC).replace(microsecond=0)
            if current is not None and current.status == "live" and row["state"] == "recording":
                if current.declared() != declared:
                    raise RequestRejected(409, UPLOAD_OPEN)
                return current, row["version"]
            if current is not None and now < current.replace_after:
                raise RequestRejected(409, _replace_wait(current))
            if row["version"] != expected_version:
                raise RequestRejected(409, "Job changed. Reload before applying this action.")
            version = conn.execute(
                """UPDATE review_code_acquisitions
                   SET state = 'recording', version = version + 1
                   WHERE submission_id = %s RETURNING version""",
                (submission_id,),
            ).fetchone()["version"]
            details = {"attempt": secrets.token_hex(16), "issued_at": now.isoformat(), **declared}
            self._event(conn, submission_id, "recording_upload_granted", actor_id, details)
        return self._attempt_from(details, "recording_upload_granted", now), version

    def abandon_upload(
        self, submission_id: str, actor_id: str, expected_version: int
    ) -> int:
        """Close the open upload attempt and return the job to awaiting a recording.

        The attempt's link cannot be revoked; it stays create-only, and no new
        attempt is granted until it has expired.
        """
        with self._conn() as conn:
            row = self._paid_for_update(conn, submission_id, expected_version)
            current = self._attempt(conn, submission_id)
            if (
                row["state"] != "recording"
                or current is None
                or current.status not in {"live", "expired", "rejected"}
            ):
                raise RequestRejected(409, "No upload is open for this job.")
            version = conn.execute(
                """UPDATE review_code_acquisitions
                   SET state = 'awaiting_recording', version = version + 1
                   WHERE submission_id = %s RETURNING version""",
                (submission_id,),
            ).fetchone()["version"]
            self._event(
                conn,
                submission_id,
                "recording_upload_abandoned",
                actor_id,
                {"attempt": current.attempt},
            )
        return version

    def upload_attempt(self, submission_id: str) -> UploadAttempt | None:
        """The latest operator upload attempt for a job, or None."""
        with self._conn() as conn:
            return self._attempt(conn, submission_id)

    def _attempt(self, conn, submission_id: str) -> UploadAttempt | None:
        grant = conn.execute(
            """SELECT id, details, created_at FROM review_code_acquisition_events
               WHERE submission_id = %s AND kind = 'recording_upload_granted'
               ORDER BY id DESC LIMIT 1""",
            (submission_id,),
        ).fetchone()
        if grant is None:
            return None
        ending = conn.execute(
            """SELECT kind FROM review_code_acquisition_events
               WHERE submission_id = %s AND id > %s AND kind = ANY(%s)
               ORDER BY id LIMIT 1""",
            (submission_id, grant["id"], list(UPLOAD_ENDINGS)),
        ).fetchone()
        kind = ending["kind"] if ending else "recording_upload_granted"
        details = dict(grant["details"] or {})
        details.setdefault("issued_at", grant["created_at"].isoformat())
        return self._attempt_from(details, kind, datetime.now(UTC))

    def _attempt_from(self, details: dict, kind: str, now: datetime) -> UploadAttempt:
        issued = datetime.fromisoformat(str(details["issued_at"]))
        expires = issued + timedelta(seconds=self.upload_link_seconds)
        return UploadAttempt(
            attempt=details.get("attempt"),
            issued_at=issued,
            link_expires_at=expires,
            replace_after=expires + UPLOAD_CLOCK_TOLERANCE,
            suffix=details.get("suffix"),
            content_type=details.get("content_type"),
            size_bytes=details.get("size_bytes"),
            status=_attempt_status(details.get("attempt"), kind, now < expires),
        )

    def begin_capture(
        self, submission_id: str, actor_id: str, expected_version: int
    ) -> int:
        """Move a paid job from awaiting a recording to capturing. No player confirmation."""
        with self._conn() as conn:
            row = self._paid_for_update(conn, submission_id, expected_version)
            if row["state"] != "awaiting_recording":
                raise RequestRejected(409, "This job is not waiting for a recording.")
            version = conn.execute(
                """UPDATE review_code_acquisitions
                   SET state = 'recording', version = version + 1
                   WHERE submission_id = %s RETURNING version""",
                (submission_id,),
            ).fetchone()["version"]
            self._event(conn, submission_id, "capture_started", actor_id, {})
        return version

    def release_capture(
        self, submission_id: str, actor_id: str, expected_version: int
    ) -> int:
        """Return a capture that was discarded, before anything was queued."""
        with self._conn() as conn:
            row = self._paid_for_update(conn, submission_id, expected_version)
            if row["state"] != "recording":
                raise RequestRejected(409, "This recording can no longer be discarded.")
            version = conn.execute(
                """UPDATE review_code_acquisitions
                   SET state = 'awaiting_recording', version = version + 1
                   WHERE submission_id = %s RETURNING version""",
                (submission_id,),
            ).fetchone()["version"]
            self._event(conn, submission_id, "capture_discarded", actor_id, {})
        return version

    def verify_upload(
        self, submission_id: str, actor_id: str, expected_version: int, facts: dict
    ) -> bool:
        """Record server-side verification; False when this grant is no longer current."""
        with self._conn() as conn:
            row = conn.execute(
                """UPDATE review_code_acquisitions
                   SET state = 'uploading', version = version + 1
                   WHERE submission_id = %s AND state = 'recording'
                     AND version = %s
                   RETURNING submission_id""",
                (submission_id, expected_version),
            ).fetchone()
            if row is None:
                return False
            self._event(conn, submission_id, "recording_upload_verified", actor_id, facts)
        return True

    def record_upload_rejected(
        self, submission_id: str, actor_id: str, reason: str
    ) -> None:
        """Audit a failed verification; the job stays in ``recording``."""
        with self._conn() as conn:
            self._event(
                conn, submission_id, "recording_upload_rejected", actor_id, {"reason": reason}
            )

    def queue_for_analysis(self, submission_id: str) -> bool:
        """Atomically queue a verified paid recording once. Only one caller wins."""
        with self._conn() as conn:
            row = conn.execute(
                f"""UPDATE submission_gates g
                    SET status = 'queued', uploaded = true
                    FROM review_code_acquisitions a
                    WHERE g.submission_id = %s AND a.submission_id = g.submission_id
                      AND g.status = 'received' AND {_ANALYSIS_READY}
                    RETURNING g.submission_id""",
                (submission_id,),
            ).fetchone()
            if row is None:
                return False
            self._event(conn, submission_id, "recording_analysis_queued", None, {})
        return True

    def analysis_admitted(self, submission_id: str) -> bool:
        """True only for a verified recording whose queue transition committed."""
        with self._conn() as conn:
            row = conn.execute(
                f"""SELECT 1 FROM submission_gates g
                    JOIN review_code_acquisitions a USING (submission_id)
                    WHERE g.submission_id = %s AND g.status = 'queued'
                      AND {_ANALYSIS_READY}""",
                (submission_id,),
            ).fetchone()
        return row is not None

    def _paid_for_update(self, conn, submission_id: str, expected_version: int) -> dict:
        row = self._authorized_for_update(conn, submission_id)
        if row["version"] != expected_version:
            raise RequestRejected(409, "Job changed. Reload before applying this action.")
        return row

    def _authorized_for_update(self, conn, submission_id: str) -> dict:
        row = conn.execute(
            """SELECT a.*, g.payment_state, g.voucher_state FROM review_code_acquisitions a
               JOIN submission_gates g USING (submission_id)
               WHERE a.submission_id = %s FOR UPDATE OF a""",
            (submission_id,),
        ).fetchone()
        if row is None or row["queued_at"] is None or not is_authorized(row):
            raise RequestRejected(404, "Recording job not found.")
        return row

    def mark_report_expired(self, submission_id: str, now: datetime) -> bool:
        """Durably mark a retained report expired before deleting its files."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT recording_handoff_at, report_expired_at, submission_snapshot
                   FROM review_code_acquisitions WHERE submission_id = %s
                   FOR UPDATE""",
                (submission_id,),
            ).fetchone()
            if row is None or row["recording_handoff_at"] is None:
                return False
            if row["report_expired_at"] is not None:
                return True
            snapshot = dict(row["submission_snapshot"])
            snapshot.update(email=None, display_name=None, user_id=None, notify=False)
            conn.execute(
                """UPDATE review_code_acquisitions
                   SET requested_email = NULL, target_player_name = NULL,
                       contact_verified_at = NULL, submission_snapshot = %s,
                       report_expired_at = %s
                   WHERE submission_id = %s""",
                (Jsonb(snapshot), now, submission_id),
            )
            self._event(conn, submission_id, "report_expired", None, {})
        return True

    def open_count(self) -> int:
        """Count acquisition capacity independently of the video worker queue."""
        with self._conn() as conn:
            return conn.execute(
                """SELECT count(*) AS n FROM review_code_acquisitions
                   WHERE state NOT IN ('expired', 'closed', 'ready')"""
            ).fetchone()["n"]

    def get(self, submission_id: str) -> dict | None:
        """Read one private durable record."""
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM review_code_acquisitions WHERE submission_id = %s",
                (submission_id,),
            ).fetchone()

    def snapshots(self) -> list[dict]:
        """Read durable snapshots for restoring missing report projections."""
        with self._conn() as conn:
            return conn.execute("SELECT * FROM review_code_acquisitions").fetchall()

    def by_token(self, digest: str) -> dict | None:
        """Resolve a hashed access token without exposing private fields."""
        with self._conn() as conn:
            return conn.execute(
                """SELECT * FROM review_code_acquisitions
                   WHERE submission_snapshot->>'access_token_hash' = %s""",
                (digest,),
            ).fetchone()

    def list_jobs(self) -> list[RecordingJob]:
        """List authorized jobs only, with the oldest recording requests first."""
        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT a.*, g.public_reference FROM review_code_acquisitions a
                   JOIN submission_gates g USING (submission_id)
                   WHERE {AUTHORIZED} AND a.contact_verified_at IS NOT NULL
                   ORDER BY (a.state = 'closed'), a.created_at LIMIT 200""",
            ).fetchall()
        return [RecordingJob.model_validate(row) for row in rows]

    def list_unpaid(self) -> list[RecordingJob]:
        """Review-code requests that have not been paid and are not being recorded."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT a.*, g.public_reference FROM review_code_acquisitions a
                   JOIN submission_gates g USING (submission_id)
                   WHERE g.source = 'nintendo_review_code'
                     AND a.state = 'pending_payment'
                     AND g.status = 'received'
                     AND g.payment_state IN ('unpaid', 'checkout_created')
                   ORDER BY a.created_at LIMIT 200""",
            ).fetchall()
        return [RecordingJob.model_validate(row) for row in rows]

    def close_unpaid(
        self,
        submission_id: str,
        actor_id: str,
        expected_version: int,
        reason: str,
        stripe: dict,
    ) -> dict:
        """Expire one unpaid request and audit it. A recorded payment is never undone.

        Returns the audit details. ``cancellation_email`` is ``pending`` for a
        verified address and ``skipped_unverified`` otherwise.
        """
        with self._conn() as conn:
            row = self._unpaid_for_update(conn, submission_id, expected_version)
            closed = conn.execute(
                """UPDATE submission_gates SET status = 'expired'
                   WHERE submission_id = %s AND status = 'received'
                     AND payment_state IN ('unpaid', 'checkout_created')""",
                (submission_id,),
            )
            if closed.rowcount != 1:
                raise RequestRejected(
                    409, "A payment has already been recorded for this request."
                )
            moved = conn.execute(
                """UPDATE review_code_acquisitions
                   SET state = 'expired', version = version + 1
                   WHERE submission_id = %s AND state = 'pending_payment'
                   RETURNING submission_id""",
                (submission_id,),
            ).fetchone()
            if moved is None:
                raise RequestRejected(409, "This request can no longer be closed.")
            details = {
                **_without_codes({"reason": reason}, {row["review_code"]}),
                "previous": {
                    "acquisition_state": row["state"],
                    "gate_status": row["gate_status"],
                    "payment_state": row["payment_state"],
                },
                "final_state": "expired",
                "stripe": stripe,
                "cancellation_email": (
                    "pending" if row["contact_verified_at"] else "skipped_unverified"
                ),
            }
            self._event(conn, submission_id, "unpaid_closed", actor_id, details)
        return details

    def pending_cancellation_notices(self) -> list[str]:
        """Recent operator cancellations whose email has not been recorded as sent."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT c.submission_id FROM review_code_acquisition_events c
                   WHERE c.kind = 'unpaid_closed'
                     AND c.details->>'cancellation_email' = 'pending'
                     AND c.created_at > now() - interval '7 days'
                     AND NOT EXISTS (
                       SELECT 1 FROM review_code_acquisition_events s
                       WHERE s.submission_id = c.submission_id
                         AND s.kind = 'cancellation_email_sent')
                   ORDER BY c.id LIMIT 100""",
            ).fetchall()
        return [row["submission_id"] for row in rows]

    def note_cancellation_sent(self, submission_id: str) -> None:
        """Audit the cancellation email once; a repeat records nothing."""
        with self._conn() as conn:
            conn.execute(
                "SELECT 1 FROM review_code_acquisitions WHERE submission_id = %s FOR UPDATE",
                (submission_id,),
            )
            sent = conn.execute(
                """SELECT 1 FROM review_code_acquisition_events
                   WHERE submission_id = %s AND kind = 'cancellation_email_sent'""",
                (submission_id,),
            ).fetchone()
            if sent is None:
                self._event(conn, submission_id, "cancellation_email_sent", None, {})

    def was_cancelled(self, submission_id: str) -> bool:
        """True when an operator closed this request as unpaid."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT 1 FROM review_code_acquisition_events
                   WHERE submission_id = %s AND kind = 'unpaid_closed' LIMIT 1""",
                (submission_id,),
            ).fetchone()
        return row is not None

    def _unpaid_for_update(self, conn, submission_id: str, expected_version: int) -> dict:
        row = conn.execute(
            """SELECT a.version, a.state, a.contact_verified_at, a.review_code,
                      g.payment_state, g.status AS gate_status
               FROM review_code_acquisitions a
               JOIN submission_gates g USING (submission_id)
               WHERE a.submission_id = %s
               FOR UPDATE OF a, g""",
            (submission_id,),
        ).fetchone()
        if row is None:
            raise RequestRejected(404, "Recording job not found.")
        if row["version"] != expected_version:
            raise RequestRejected(409, "Job changed. Reload before applying this action.")
        payable = row["payment_state"] in {"unpaid", "checkout_created"}
        if row["state"] != "pending_payment" or row["gate_status"] != "received" or not payable:
            raise RequestRejected(
                409, "Only an unpaid request that has not started recording can be closed."
            )
        return row

    def detail(self, submission_id: str) -> dict:
        """Return the operator projection and its acquisition history."""
        row = self.get(submission_id)
        if row is None or row["queued_at"] is None:
            raise RequestRejected(404, "Recording job not found.")
        with self._conn() as conn:
            events = conn.execute(
                """SELECT kind, actor_id, details, created_at
                   FROM review_code_acquisition_events WHERE submission_id = %s
                   ORDER BY id""",
                (submission_id,),
            ).fetchall()
        gate = self.payments.get(submission_id)
        return {
            "job": RecordingJob.model_validate(
                {**row, "public_reference": gate.public_reference}
            ),
            "events": events,
            "verified_contact_email": row["requested_email"],
            "authorization": _authorization(gate.payment_state, gate.voucher_state),
            "payment_reference": gate.payment_intent_id or gate.checkout_session_id,
            "upload_attempt": self.upload_attempt(submission_id),
        }

    def act(
        self,
        submission_id: str,
        actor_id: str,
        action: RecordingAction,
        extra_details: dict | None = None,
    ) -> None:
        """Apply an audited operator action with a stale-write guard.

        ``extra_details`` are server-built audit facts added to the event as given.
        """
        with self._conn() as conn:
            row = conn.execute(
                """SELECT a.*, g.payment_state, g.voucher_state, g.status AS gate_status
                   FROM review_code_acquisitions a
                   JOIN submission_gates g USING (submission_id)
                   WHERE a.submission_id = %s FOR UPDATE OF a""",
                (submission_id,),
            ).fetchone()
            if row is None or row["queued_at"] is None or not is_authorized(row):
                raise RequestRejected(404, "Recording job not found.")
            if row["version"] != action.expected_version:
                raise RequestRejected(
                    409, "Job changed. Reload before applying this action."
                )
            if row["state"] in {"closed", "ready", "expired"}:
                raise RequestRejected(409, "This acquisition is closed.")
            self._apply_action(conn, row, action)
            if action.action == "recording_failed" and row["state"] == "uploading":
                conn.execute(
                    """UPDATE submission_gates SET status = 'received'
                       WHERE submission_id = %s AND status = 'queued'
                         AND source = 'nintendo_review_code'""",
                    (submission_id,),
                )
            self._event(
                conn,
                submission_id,
                action.action,
                actor_id,
                {**self._audit_details(action, row), **(extra_details or {})},
            )

    def retried_attempt(self, submission_id: str) -> str | None:
        """``processing_started_at`` of the failed run the latest analysis retry named."""
        with self._conn() as conn:
            event = conn.execute(
                """SELECT details FROM review_code_acquisition_events
                   WHERE submission_id = %s AND kind = 'retry_analysis'
                   ORDER BY id DESC LIMIT 1""",
                (submission_id,),
            ).fetchone()
        if event is None:
            return None
        return (event["details"] or {}).get("processing_started_at")

    def _apply_action(self, conn, row: dict, action: RecordingAction) -> None:
        fields = self._action_fields(row, action)
        # Column names are fixed by _action_fields; values always use parameters.
        assignments = ", ".join(f"{key} = %s" for key in fields)
        conn.execute(
            f"UPDATE review_code_acquisitions SET {assignments}, version = version + 1 "
            "WHERE submission_id = %s",
            (*fields.values(), row["submission_id"]),
        )

    def _action_fields(self, row: dict, action: RecordingAction) -> dict:
        if not action.note.strip():
            raise RequestRejected(
                400, "A recording verification or resolution note is required."
            )
        if action.action == "recording_failed":
            return {
                "state": "recording_failed",
                "customer_message": action.customer_message,
            }
        if action.action == "retry_analysis":
            if not analysis_retryable(row):
                raise RequestRejected(
                    409, "Only a verified recording whose analysis failed can be retried."
                )
            return {"customer_message": action.customer_message}
        if row["state"] != "recording_failed":
            raise RequestRejected(409, "Resolve a recording exception first.")
        if action.action == "retry_recording":
            return {"state": "awaiting_recording", "customer_message": None}
        if action.action == "correct_code":
            try:
                code = normalize_code(action.review_code or "")
            except ValueError as exc:
                raise RequestRejected(400, str(exc)) from exc
            return {
                "review_code": code,
                "state": "awaiting_recording",
                "customer_message": None,
            }
        if action.action == "close_without_refund":
            if row["payment_state"] != "not_required":
                raise RequestRejected(
                    400,
                    "This job was paid by card. Refund it in Stripe and record the refund reference.",
                )
            return _closed_fields(row, action)
        if row["payment_state"] != "paid":
            raise RequestRejected(
                400, "No payment was taken for this job, so there is nothing to refund."
            )
        if not (action.refund_reference or "").strip():
            raise RequestRejected(
                400, "Record the externally completed refund reference."
            )
        return _closed_fields(row, action)

    def _audit_details(self, action: RecordingAction, row: dict) -> dict:
        """Keep action audit fields while removing raw current and corrected codes."""
        details = action.model_dump(exclude_none=True)
        details.pop("review_code", None)
        return _without_codes(details, {row.get("review_code"), action.review_code})

    def _event(
        self, conn, submission_id: str, kind: str, actor: str | None, details: dict
    ) -> None:
        conn.execute(
            """INSERT INTO review_code_acquisition_events
               (submission_id, kind, actor_id, details) VALUES (%s, %s, %s, %s)""",
            (submission_id, kind, actor, Jsonb(details)),
        )

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self.payments.database_url, row_factory=dict_row)
