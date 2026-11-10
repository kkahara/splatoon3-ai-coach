"""Durable review-code acquisition queue, separate from operational telemetry."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from public_site.errors import RequestRejected
from public_site.models import Submission
from public_site.payments import PaymentStore, PriceQuote
from public_site.recording_models import RecordingAction, RecordingJob, normalize_code

MIGRATION = Path(__file__).parent / "migrations" / "001_review_code_acquisitions.sql"
_ANALYSIS_READY = """g.source = 'nintendo_review_code' AND g.payment_state = 'paid'
    AND a.state = 'uploading' AND a.target_confirmed_at IS NOT NULL
    AND a.queued_at IS NOT NULL"""


class RecordingStore:
    """PostgreSQL is authoritative for paid recording work and its audit trail."""

    def __init__(self, payments: PaymentStore) -> None:
        self.payments = payments

    def ensure_schema(
        self, guest_retention_days: int, account_retention_days: int
    ) -> None:
        """Install the additive acquisition schema under a transaction lock."""
        with self._conn() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(8273341907)")
            conn.execute(MIGRATION.read_text())
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
        target: str,
        email: str,
        quote: PriceQuote,
        payment_expires: datetime,
        verified: bool,
        retention_days: int,
    ) -> None:
        """Commit the pending submission and payment gate in one transaction."""
        with self._conn() as conn:
            self.payments.open_gate(
                submission.id,
                quote,
                payment_expires,
                conn=conn,
                source="nintendo_review_code",
            )
            conn.execute(
                """INSERT INTO review_code_acquisitions (
                    submission_id, submission_snapshot, review_code, target_player_name,
                    requested_email, contact_verified_at, retention_days, created_at)
                    VALUES (%s, %s, %s, %s, %s, CASE WHEN %s THEN now() END, %s, %s)""",
                (
                    submission.id,
                    Jsonb(submission.model_dump()),
                    code,
                    target,
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

    def reconcile(self) -> None:
        """Recover paid activation after any crash, without the checkout deadline."""
        with self._conn() as conn:
            rows = conn.execute(
                """UPDATE review_code_acquisitions a
                   SET state = 'awaiting_recording', queued_at = now(), version = version + 1
                   FROM submission_gates g
                   WHERE a.submission_id = g.submission_id
                     AND g.source = 'nintendo_review_code' AND g.payment_state = 'paid'
                     AND a.contact_verified_at IS NOT NULL AND a.state IN ('pending_payment', 'expired')
                   RETURNING a.submission_id""",
            ).fetchall()
            for row in rows:
                self._event(conn, row["submission_id"], "paid_recording_queued", None, {})
            conn.execute(
                """UPDATE review_code_acquisitions a SET state = 'expired'
                   FROM submission_gates g WHERE a.submission_id = g.submission_id
                   AND a.state = 'pending_payment' AND g.status = 'expired'
                   AND g.payment_state <> 'paid'""",
            )

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
                """SELECT state, target_confirmed_at, recording_handoff_at,
                          handoff_reference
                   FROM review_code_acquisitions
                   WHERE submission_id = %s FOR UPDATE""",
                (submission_id,),
            ).fetchone()
            if row is None:
                return False
            if row["recording_handoff_at"] is not None:
                return row["handoff_reference"] == reference
            if row["state"] != "uploading" or row["target_confirmed_at"] is None:
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
        self, submission_id: str, actor_id: str, expected_version: int
    ) -> int:
        """Allow one operator upload for a paid, target-confirmed job; return its version.

        A repeated grant while still ``recording`` replaces the earlier one (the
        object key is fixed, so a later upload overwrites the earlier object).
        """
        with self._conn() as conn:
            row = self._paid_for_update(conn, submission_id, expected_version)
            if row["state"] not in {"awaiting_recording", "recording"}:
                raise RequestRejected(409, "This recording can no longer be uploaded.")
            if row["target_confirmed_at"] is None:
                raise RequestRejected(409, "Confirm the target player before uploading.")
            version = conn.execute(
                """UPDATE review_code_acquisitions
                   SET state = 'recording', version = version + 1
                   WHERE submission_id = %s RETURNING version""",
                (submission_id,),
            ).fetchone()["version"]
            self._event(conn, submission_id, "recording_upload_granted", actor_id, {})
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
                     AND version = %s AND target_confirmed_at IS NOT NULL
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
        row = conn.execute(
            """SELECT a.*, g.payment_state FROM review_code_acquisitions a
               JOIN submission_gates g USING (submission_id)
               WHERE a.submission_id = %s FOR UPDATE OF a""",
            (submission_id,),
        ).fetchone()
        if row is None or row["queued_at"] is None or row["payment_state"] != "paid":
            raise RequestRejected(404, "Recording job not found.")
        if row["version"] != expected_version:
            raise RequestRejected(409, "Job changed. Reload before applying this action.")
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
        """List paid jobs only, with the oldest recording requests first."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT a.* FROM review_code_acquisitions a
                   JOIN submission_gates g USING (submission_id)
                   WHERE g.payment_state = 'paid' AND a.contact_verified_at IS NOT NULL
                   ORDER BY (a.state = 'closed'), a.created_at LIMIT 200""",
            ).fetchall()
        return [RecordingJob.model_validate(row) for row in rows]

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
            "job": RecordingJob.model_validate(row),
            "events": events,
            "verified_contact_email": row["requested_email"],
            "payment_reference": gate.payment_intent_id or gate.checkout_session_id,
        }

    def act(self, submission_id: str, actor_id: str, action: RecordingAction) -> None:
        """Apply an audited operator action with a stale-write guard."""
        with self._conn() as conn:
            row = conn.execute(
                """SELECT a.*, g.payment_state FROM review_code_acquisitions a
                   JOIN submission_gates g USING (submission_id)
                   WHERE a.submission_id = %s FOR UPDATE OF a""",
                (submission_id,),
            ).fetchone()
            if row is None or row["queued_at"] is None or row["payment_state"] != "paid":
                raise RequestRejected(404, "Recording job not found.")
            if row["version"] != action.expected_version:
                raise RequestRejected(
                    409, "Job changed. Reload before applying this action."
                )
            if row["state"] in {"closed", "ready", "expired"}:
                raise RequestRejected(409, "This acquisition is closed.")
            self._apply_action(conn, row, actor_id, action)
            self._event(
                conn,
                submission_id,
                action.action,
                actor_id,
                self._audit_details(action, row),
            )

    def _apply_action(self, conn, row: dict, actor: str, action: RecordingAction) -> None:
        fields = self._action_fields(row, actor, action)
        # Column names are fixed by _action_fields; values always use parameters.
        assignments = ", ".join(f"{key} = %s" for key in fields)
        conn.execute(
            f"UPDATE review_code_acquisitions SET {assignments}, version = version + 1 "
            "WHERE submission_id = %s",
            (*fields.values(), row["submission_id"]),
        )

    def _action_fields(self, row: dict, actor: str, action: RecordingAction) -> dict:
        if not action.note.strip():
            raise RequestRejected(
                400, "A recording verification or resolution note is required."
            )
        if action.action == "confirm_target":
            name = (action.confirmed_player_name or "").strip()
            if not name or row["state"] != "awaiting_recording":
                raise RequestRejected(
                    400, "Confirm the observed player while awaiting recording."
                )
            from datetime import UTC, datetime

            return {
                "target_confirmed_at": datetime.now(UTC),
                "target_confirmed_by": actor,
                "confirmed_player_name": name,
            }
        if action.action == "recording_failed":
            return {
                "state": "recording_failed",
                "customer_message": action.customer_message,
            }
        if row["state"] != "recording_failed":
            raise RequestRejected(409, "Resolve a recording exception first.")
        if action.action == "correct_code":
            try:
                code = normalize_code(action.review_code or "")
            except ValueError as exc:
                raise RequestRejected(400, str(exc)) from exc
            return {
                "review_code": code,
                "state": "awaiting_recording",
                "target_confirmed_at": None,
                "target_confirmed_by": None,
                "confirmed_player_name": None,
                "customer_message": None,
            }
        if not (action.refund_reference or "").strip():
            raise RequestRejected(
                400, "Record the externally completed refund reference."
            )
        snapshot = dict(row["submission_snapshot"])
        snapshot.update(email=None, display_name=None, user_id=None, notify=False)
        from datetime import UTC, datetime

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

    def _audit_details(self, action: RecordingAction, row: dict) -> dict:
        """Keep action audit fields while removing raw current and corrected codes."""
        details = action.model_dump(exclude_none=True)
        details.pop("review_code", None)
        candidates = {row.get("review_code"), action.review_code}
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
