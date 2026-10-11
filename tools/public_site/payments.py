"""One snapshotted price, and the gate that queues a submission once.

Upload and payment are independent. A conditional update is the only way to
leave ``received``. An older Checkout attempt cannot overwrite the current one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import psycopg
from psycopg.rows import dict_row

from public_site.errors import PAYMENT_UNAVAILABLE, RequestRejected

PaymentState = Literal["not_required", "unpaid", "checkout_created", "paid"]
GateStatus = Literal["received", "queued", "expired"]
PriceEventKind = Literal[
    "page_viewed",
    "upload_started",
    "upload_completed",
    "checkout_opened",
    "payment_completed",
    "analysis_completed",
    "analysis_failed",
]
FailureCategory = Literal["processing", "invalid_upload", "expired"]
PaidResult = Literal["paid", "queued", "stale", "amount", "expired", "missing"]

_SCHEMA_LOCK = 8_273_341_904
_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS analysis_price (
        id boolean PRIMARY KEY DEFAULT true CHECK (id),
        price_cents integer NOT NULL CHECK (price_cents >= 0),
        currency text NOT NULL CHECK (currency = 'usd'),
        pricing_version integer NOT NULL CHECK (pricing_version >= 1),
        updated_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS submission_gates (
        submission_id text PRIMARY KEY,
        status text NOT NULL CHECK (status IN ('received', 'queued', 'expired')),
        uploaded boolean NOT NULL,
        payment_state text NOT NULL CHECK (
            payment_state IN ('not_required', 'unpaid', 'checkout_created', 'paid')
        ),
        checkout_session_id text,
        checkout_url text,
        payment_intent_id text,
        checkout_attempt integer NOT NULL CHECK (checkout_attempt >= 0),
        price_cents integer NOT NULL CHECK (price_cents >= 0),
        currency text NOT NULL CHECK (currency = 'usd'),
        pricing_version integer NOT NULL CHECK (pricing_version >= 0),
        expires_at timestamptz NOT NULL
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS submission_gates_session
        ON submission_gates (checkout_session_id)
        WHERE checkout_session_id IS NOT NULL
    """,
    """
    CREATE TABLE IF NOT EXISTS price_events (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        kind text NOT NULL CHECK (
            kind IN (
                'page_viewed', 'upload_started', 'upload_completed',
                'checkout_opened', 'payment_completed',
                'analysis_completed', 'analysis_failed'
            )
        ),
        price_cents integer,
        currency text,
        pricing_version integer,
        submission_id text,
        failure_category text CHECK (
            failure_category IS NULL
            OR failure_category IN ('processing', 'invalid_upload', 'expired')
        ),
        at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS price_events_version
        ON price_events (pricing_version, kind)
    """,
)
_QUEUE = """
    UPDATE submission_gates
    SET status = 'queued'
    WHERE submission_id = %s
      AND status = 'received'
      AND uploaded
      AND payment_state IN ('paid', 'not_required')
      AND expires_at > now()
"""


@dataclass(frozen=True)
class AnalysisPrice:
    """The single live price. Submissions copy it; they do not follow later edits."""

    price_cents: int
    currency: str
    pricing_version: int


@dataclass(frozen=True)
class PriceQuote:
    """What one new submission will snapshot."""

    price_cents: int
    currency: str
    pricing_version: int
    payment_state: PaymentState


@dataclass(frozen=True)
class SubmissionGate:
    """Payment and upload prerequisites for one submission."""

    submission_id: str
    status: GateStatus
    uploaded: bool
    payment_state: PaymentState
    checkout_session_id: str | None
    checkout_url: str | None
    payment_intent_id: str | None
    checkout_attempt: int
    price_cents: int
    currency: str
    pricing_version: int
    expires_at: datetime


@dataclass(frozen=True)
class PriceEventCount:
    """One allowlisted funnel count. Not a conversion rate."""

    pricing_version: int | None
    price_cents: int | None
    kind: str
    count: int


class PaymentStore:
    """Postgres gate. Disk submissions stay the worker's queue."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def ensure_schema(self) -> None:
        """Create the price, gate, and funnel tables when they are missing."""
        with self._conn() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK,))
            for statement in _SCHEMA:
                conn.execute(statement)

    def clear(self) -> None:
        """Delete payment rows. Used by tests, not by the public site."""
        with self._conn() as conn:
            conn.execute("TRUNCATE price_events, submission_gates, analysis_price")

    def current_price(self) -> AnalysisPrice | None:
        """The live price, or None until an administrator saves one."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT price_cents, currency, pricing_version
                FROM analysis_price WHERE id = true
                """
            ).fetchone()
        if row is None:
            return None
        return AnalysisPrice(
            price_cents=int(row["price_cents"]),
            currency=str(row["currency"]),
            pricing_version=int(row["pricing_version"]),
        )

    def set_price(self, price_cents: int) -> AnalysisPrice:
        """Save a USD price. A changed amount increments ``pricing_version``."""
        if price_cents < 0:
            raise ValueError("price_cents must be >= 0")
        with self._conn() as conn:
            row = conn.execute(
                """
                INSERT INTO analysis_price
                    (id, price_cents, currency, pricing_version, updated_at)
                VALUES (true, %s, 'usd', 1, now())
                ON CONFLICT (id) DO UPDATE
                SET price_cents = EXCLUDED.price_cents,
                    pricing_version = analysis_price.pricing_version + 1,
                    updated_at = now()
                WHERE analysis_price.price_cents IS DISTINCT FROM EXCLUDED.price_cents
                RETURNING price_cents, currency, pricing_version
                """,
                (price_cents,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """
                    SELECT price_cents, currency, pricing_version
                    FROM analysis_price WHERE id = true
                    """
                ).fetchone()
        return AnalysisPrice(
            price_cents=int(row["price_cents"]),
            currency=str(row["currency"]),
            pricing_version=int(row["pricing_version"]),
        )

    def quote(self, *, admin: bool) -> PriceQuote:
        """Snapshot for a new submission. Non-admins are refused until a price exists."""
        price = self.current_price()
        if admin:
            cents = 0 if price is None else price.price_cents
            version = 0 if price is None else price.pricing_version
            return PriceQuote(cents, "usd", version, "not_required")
        if price is None:
            raise RequestRejected(503, PAYMENT_UNAVAILABLE)
        state: PaymentState = "not_required" if price.price_cents == 0 else "unpaid"
        return PriceQuote(price.price_cents, price.currency, price.pricing_version, state)

    def open_gate(self, submission_id: str, quote: PriceQuote, expires_at: datetime) -> None:
        """Insert the gate for a submission that was just created."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO submission_gates (
                    submission_id, status, uploaded, payment_state, checkout_attempt,
                    price_cents, currency, pricing_version, expires_at
                )
                VALUES (%s, 'received', false, %s, 0, %s, %s, %s, %s)
                """,
                (
                    submission_id,
                    quote.payment_state,
                    quote.price_cents,
                    quote.currency,
                    quote.pricing_version,
                    expires_at,
                ),
            )

    def get(self, submission_id: str) -> SubmissionGate | None:
        """Load one gate, or None when this submission is not billed here."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM submission_gates WHERE submission_id = %s",
                (submission_id,),
            ).fetchone()
        return None if row is None else _gate(row)

    def get_by_session(self, session_id: str) -> SubmissionGate | None:
        """Load the gate that currently owns this Checkout Session id."""
        if not session_id:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM submission_gates WHERE checkout_session_id = %s",
                (session_id,),
            ).fetchone()
        return None if row is None else _gate(row)

    def mark_uploaded(self, submission_id: str) -> bool:
        """Set the upload prerequisite. True only when this call changes the row."""
        with self._conn() as conn:
            updated = conn.execute(
                """
                UPDATE submission_gates
                SET uploaded = true
                WHERE submission_id = %s AND status = 'received' AND NOT uploaded
                """,
                (submission_id,),
            )
        return updated.rowcount == 1

    def try_queue(self, submission_id: str) -> bool:
        """Move an eligible gate from received to queued. Only one caller wins."""
        with self._conn() as conn:
            updated = conn.execute(_QUEUE, (submission_id,))
        return updated.rowcount == 1

    def apply_paid(
        self,
        *,
        submission_id: str,
        session_id: str,
        amount_total: int | None,
        currency: str | None,
        payment_intent_id: str | None,
    ) -> PaidResult:
        """Mark the current attempt paid when the amount matches the snapshot.

        Payment only moves forward. A session id from an earlier attempt updates
        nothing. Queueing stays a separate conditional update so a probe can run
        between the two without holding this row lock.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM submission_gates WHERE submission_id = %s FOR UPDATE",
                (submission_id,),
            ).fetchone()
            if row is None:
                return "missing"
            if row["status"] == "expired":
                return "expired"
            if row["checkout_session_id"] != session_id:
                return "stale"
            if not _amount_matches(row, amount_total, currency):
                return "amount"
            if row["payment_state"] == "paid":
                return "paid"
            if row["payment_state"] != "checkout_created" or row["status"] != "received":
                return "stale"
            updated = conn.execute(
                """
                UPDATE submission_gates
                SET payment_state = 'paid', payment_intent_id = %s
                WHERE submission_id = %s
                  AND checkout_session_id = %s
                  AND checkout_attempt = %s
                  AND payment_state = 'checkout_created'
                  AND status = 'received'
                """,
                (payment_intent_id, submission_id, session_id, row["checkout_attempt"]),
            )
            if updated.rowcount != 1:
                return "stale"
            _insert_event(
                conn,
                "payment_completed",
                price_cents=int(row["price_cents"]),
                currency=str(row["currency"]),
                pricing_version=int(row["pricing_version"]),
                submission_id=submission_id,
                failure_category=None,
            )
        return "paid"

    def claim_checkout(self, submission_id: str, replace_session_id: str | None) -> int | None:
        """Reserve an attempt number, or None when another request owns it.

        Replacing an expired session is conditional on the stored attempt and
        session id. A concurrent replace updates zero rows.
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM submission_gates WHERE submission_id = %s FOR UPDATE",
                (submission_id,),
            ).fetchone()
            if row is None or row["status"] != "received":
                return None
            if row["payment_state"] in {"paid", "not_required"}:
                return None
            if row["payment_state"] == "unpaid" and row["checkout_session_id"] is None:
                return _claim_open_attempt(conn, row)
            if (
                replace_session_id
                and row["payment_state"] == "checkout_created"
                and row["checkout_session_id"] == replace_session_id
            ):
                return _replace_attempt(conn, row, replace_session_id)
        return None

    def attach_session(self, submission_id: str, attempt: int, session_id: str, url: str) -> bool:
        """Store the session for this attempt. A repeat of the same id is a no-op."""
        with self._conn() as conn:
            updated = conn.execute(
                """
                UPDATE submission_gates
                SET checkout_session_id = %s,
                    checkout_url = %s,
                    payment_state = 'checkout_created'
                WHERE submission_id = %s
                  AND checkout_attempt = %s
                  AND status = 'received'
                  AND payment_state = 'unpaid'
                  AND checkout_session_id IS NULL
                RETURNING price_cents, currency, pricing_version
                """,
                (session_id, url, submission_id, attempt),
            ).fetchone()
            if updated is not None:
                _insert_event(
                    conn,
                    "checkout_opened",
                    price_cents=int(updated["price_cents"]),
                    currency=str(updated["currency"]),
                    pricing_version=int(updated["pricing_version"]),
                    submission_id=submission_id,
                    failure_category=None,
                )
                return True
            current = conn.execute(
                """
                SELECT checkout_session_id FROM submission_gates
                WHERE submission_id = %s AND checkout_attempt = %s
                """,
                (submission_id, attempt),
            ).fetchone()
        return current is not None and current["checkout_session_id"] == session_id

    def claim_unpaid_expiry(self, submission_id: str, now: datetime) -> bool:
        """Expire one unpaid gate. A paid row updates nothing, so payment wins."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM submission_gates WHERE submission_id = %s FOR UPDATE",
                (submission_id,),
            ).fetchone()
            if row is None or row["status"] != "received":
                return False
            if row["payment_state"] in {"paid", "not_required"}:
                return False
            if row["expires_at"] > now:
                return False
            updated = conn.execute(
                """
                UPDATE submission_gates
                SET status = 'expired'
                WHERE submission_id = %s
                  AND status = 'received'
                  AND payment_state = %s
                  AND payment_state NOT IN ('paid', 'not_required')
                  AND expires_at <= %s
                """,
                (submission_id, row["payment_state"], now),
            )
        return updated.rowcount == 1

    def remains_unpaid_expired(self, submission_id: str) -> bool:
        """Recheck under a lock before the uploaded video is deleted."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT status, payment_state FROM submission_gates WHERE submission_id = %s FOR UPDATE",
                (submission_id,),
            ).fetchone()
        if row is None:
            return False
        return row["status"] == "expired" and row["payment_state"] not in {
            "paid",
            "not_required",
        }

    def unpaid_due_ids(self, now: datetime) -> list[str]:
        """Unpaid gates whose site lifetime has ended."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT submission_id FROM submission_gates
                WHERE status = 'received'
                  AND payment_state IN ('unpaid', 'checkout_created')
                  AND expires_at <= %s
                """,
                (now,),
            ).fetchall()
        return [str(row["submission_id"]) for row in rows]

    def ready_ids(self) -> list[str]:
        """Gates that can still be queued. Used to recover a crash after payment."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT submission_id FROM submission_gates
                WHERE status = 'received'
                  AND uploaded
                  AND payment_state IN ('paid', 'not_required')
                  AND expires_at > now()
                """
            ).fetchall()
        return [str(row["submission_id"]) for row in rows]

    def record_page_view(self) -> None:
        """Count one submit-page view at the price currently on screen."""
        price = self.current_price()
        cents = None if price is None else price.price_cents
        version = None if price is None else price.pricing_version
        currency = None if price is None else price.currency
        self._record(
            "page_viewed",
            price_cents=cents,
            currency=currency,
            pricing_version=version,
            submission_id=None,
            failure_category=None,
        )

    def record_for_submission(
        self,
        submission_id: str,
        kind: PriceEventKind,
        failure_category: FailureCategory | None = None,
    ) -> None:
        """Count one later funnel event using that submission's snapshot."""
        gate = self.get(submission_id)
        if gate is None:
            return
        self._record(
            kind,
            price_cents=gate.price_cents,
            currency=gate.currency,
            pricing_version=gate.pricing_version,
            submission_id=submission_id,
            failure_category=failure_category,
        )

    def event_counts(self) -> list[PriceEventCount]:
        """Funnel counts grouped by the snapshotted price version."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT pricing_version, price_cents, kind, count(*) AS count
                FROM price_events
                GROUP BY pricing_version, price_cents, kind
                ORDER BY pricing_version NULLS FIRST, kind
                """
            ).fetchall()
        return [
            PriceEventCount(
                pricing_version=None if row["pricing_version"] is None else int(row["pricing_version"]),
                price_cents=None if row["price_cents"] is None else int(row["price_cents"]),
                kind=str(row["kind"]),
                count=int(row["count"]),
            )
            for row in rows
        ]

    def _record(
        self,
        kind: PriceEventKind,
        *,
        price_cents: int | None,
        currency: str | None,
        pricing_version: int | None,
        submission_id: str | None,
        failure_category: str | None,
    ) -> None:
        with self._conn() as conn:
            _insert_event(
                conn,
                kind,
                price_cents=price_cents,
                currency=currency,
                pricing_version=pricing_version,
                submission_id=submission_id,
                failure_category=failure_category,
            )

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)


def _claim_open_attempt(conn: psycopg.Connection, row: dict) -> int | None:
    attempt = int(row["checkout_attempt"])
    if attempt > 0:
        return attempt
    updated = conn.execute(
        """
        UPDATE submission_gates
        SET checkout_attempt = 1
        WHERE submission_id = %s
          AND checkout_attempt = 0
          AND payment_state = 'unpaid'
          AND checkout_session_id IS NULL
          AND status = 'received'
        """,
        (row["submission_id"],),
    )
    if updated.rowcount != 1:
        return None
    return 1


def _replace_attempt(conn: psycopg.Connection, row: dict, session_id: str) -> int | None:
    attempt = int(row["checkout_attempt"]) + 1
    updated = conn.execute(
        """
        UPDATE submission_gates
        SET checkout_attempt = %s,
            checkout_session_id = NULL,
            checkout_url = NULL,
            payment_state = 'unpaid'
        WHERE submission_id = %s
          AND checkout_attempt = %s
          AND checkout_session_id = %s
          AND payment_state = 'checkout_created'
          AND status = 'received'
        """,
        (attempt, row["submission_id"], row["checkout_attempt"], session_id),
    )
    if updated.rowcount != 1:
        return None
    return attempt


def _amount_matches(row: dict, amount_total: int | None, currency: str | None) -> bool:
    return (
        row["currency"] == "usd"
        and currency == "usd"
        and amount_total is not None
        and int(amount_total) == int(row["price_cents"])
    )


def _insert_event(
    conn: psycopg.Connection,
    kind: str,
    *,
    price_cents: int | None,
    currency: str | None,
    pricing_version: int | None,
    submission_id: str | None,
    failure_category: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO price_events (
            kind, price_cents, currency, pricing_version, submission_id, failure_category
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (kind, price_cents, currency, pricing_version, submission_id, failure_category),
    )


def _gate(row: dict) -> SubmissionGate:
    return SubmissionGate(
        submission_id=str(row["submission_id"]),
        status=row["status"],
        uploaded=bool(row["uploaded"]),
        payment_state=row["payment_state"],
        checkout_session_id=row["checkout_session_id"],
        checkout_url=row["checkout_url"],
        payment_intent_id=row["payment_intent_id"],
        checkout_attempt=int(row["checkout_attempt"]),
        price_cents=int(row["price_cents"]),
        currency=str(row["currency"]),
        pricing_version=int(row["pricing_version"]),
        expires_at=row["expires_at"],
    )
