"""One snapshotted price, and the gate that queues a submission once.

Upload and payment are independent. A conditional update is the only way to
leave ``received``. An older Checkout attempt cannot overwrite the current one.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from public_site.errors import PAYMENT_UNAVAILABLE, RequestRejected
from public_site.public_reference import PATTERN as REFERENCE_PATTERN
from public_site.public_reference import new_reference

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
_REFERENCE_ATTEMPTS = 10
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
    """
    CREATE TABLE IF NOT EXISTS account_upload_limit (
        id boolean PRIMARY KEY DEFAULT true CHECK (id),
        account_uploads_per_hour integer NOT NULL
            CHECK (account_uploads_per_hour BETWEEN 1 AND 100)
    )
    """,
    """
    INSERT INTO account_upload_limit (id, account_uploads_per_hour)
    VALUES (true, 5)
    ON CONFLICT (id) DO NOTHING
    """,
    """
    ALTER TABLE account_upload_limit ADD COLUMN IF NOT EXISTS guest_uploads_per_hour integer
        CHECK (guest_uploads_per_hour BETWEEN 1 AND 100)
    """,
    """
    ALTER TABLE account_upload_limit ADD COLUMN IF NOT EXISTS network_uploads_per_hour integer
        CHECK (network_uploads_per_hour BETWEEN 1 AND 100)
    """,
    """
    ALTER TABLE submission_gates ADD COLUMN IF NOT EXISTS voucher_id uuid
    """,
    """
    ALTER TABLE submission_gates ADD COLUMN IF NOT EXISTS voucher_state text
    """,
    """
    ALTER TABLE submission_gates ADD COLUMN IF NOT EXISTS voucher_failure_reason text
    """,
    """
    DO $$
    BEGIN
        ALTER TABLE submission_gates
            ADD CONSTRAINT submission_gates_voucher_state
            CHECK (
                voucher_state IS NULL
                OR voucher_state IN ('pending', 'redeemed', 'failed')
            );
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END $$
    """,
    """
    DO $$
    BEGIN
        ALTER TABLE submission_gates
            ADD CONSTRAINT submission_gates_voucher_reason
            CHECK (
                voucher_failure_reason IS NULL
                OR voucher_failure_reason IN (
                    'expired', 'revoked', 'exhausted', 'inactive', 'wrong_service'
                )
            );
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END $$
    """,
    """
    DO $$
    BEGIN
        ALTER TABLE submission_gates
            ADD CONSTRAINT submission_gates_voucher_shape
            CHECK (
                (
                    voucher_id IS NULL
                    AND voucher_state IS NULL
                    AND voucher_failure_reason IS NULL
                )
                OR (
                    voucher_id IS NOT NULL
                    AND voucher_state = 'pending'
                    AND voucher_failure_reason IS NULL
                )
                OR (
                    voucher_id IS NOT NULL
                    AND voucher_state = 'redeemed'
                    AND voucher_failure_reason IS NULL
                )
                OR (
                    voucher_id IS NOT NULL
                    AND voucher_state = 'failed'
                    AND voucher_failure_reason IS NOT NULL
                )
            );
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END $$
    """,
    """
    ALTER TABLE submission_gates ADD COLUMN IF NOT EXISTS public_reference text
    """,
    f"""
    DO $$
    BEGIN
        ALTER TABLE submission_gates
            ADD CONSTRAINT submission_gates_public_reference_format
            CHECK (public_reference ~ '{REFERENCE_PATTERN}');
    EXCEPTION
        WHEN duplicate_object THEN NULL;
    END $$
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS submission_gates_public_reference
        ON submission_gates (public_reference)
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
_QUEUE_REVIEW_SAFE = _QUEUE.replace(
    "AND payment_state", "AND source = 'upload'\n      AND payment_state"
)


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
    voucher_id: str | None = None
    voucher_state: Literal["pending", "redeemed", "failed"] | None = None
    voucher_failure_reason: str | None = None
    public_reference: str | None = None
    source: str = "upload"


class _Unset:
    """Marks an upload limit the caller did not mention."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()
_LIMIT_COLUMNS = (
    ("account", "account_uploads_per_hour"),
    ("guest", "guest_uploads_per_hour"),
    ("network", "network_uploads_per_hour"),
)


@dataclass(frozen=True)
class UploadLimits:
    """Saved per-hour limits. ``None`` means the environment setting applies."""

    account: int
    guest: int | None
    network: int | None


@dataclass(frozen=True)
class EffectiveUploadLimits:
    """The limits enforced now, and whether each one is a saved admin setting."""

    account: int
    guest: int
    network: int
    account_saved: bool
    guest_saved: bool
    network_saved: bool


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
        self.source_column_available = False

    def ensure_schema(self) -> None:
        """Create the price, gate, and funnel tables when they are missing."""
        with self._conn() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK,))
            for statement in _SCHEMA:
                conn.execute(statement)
            _backfill_references(conn)
            conn.execute(
                "ALTER TABLE submission_gates ALTER COLUMN public_reference SET NOT NULL"
            )
            self.source_column_available = self._source_column_exists(conn)

    def refresh_source_schema(self, *, required: bool = False) -> None:
        """Detect the optional review-code schema without modifying the database."""
        with self._conn() as conn:
            self.source_column_available = self._source_column_exists(conn)
        if required and not self.source_column_available:
            raise RuntimeError(
                "Review-code schema is missing the submission_gates.source migration."
            )

    @staticmethod
    def _source_column_exists(conn: psycopg.Connection) -> bool:
        """Check whether the feature-gated source column has been migrated."""
        return bool(
            conn.execute(
                """SELECT EXISTS (
                       SELECT 1 FROM information_schema.columns
                       WHERE table_schema = current_schema()
                         AND table_name = 'submission_gates' AND column_name = 'source'
                   ) AS present"""
            ).fetchone()["present"]
        )

    def clear(self) -> None:
        """Delete payment rows. Used by tests, not by the public site."""
        with self._conn() as conn:
            conn.execute(
                """
                TRUNCATE price_events, submission_gates, analysis_price,
                    account_upload_limit CASCADE
                """
            )

    def account_upload_limit(self) -> int:
        """Logged-in submissions accepted per hour. Five until an admin saves one."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT account_uploads_per_hour
                FROM account_upload_limit WHERE id = true
                """
            ).fetchone()
        if row is None:
            return 5
        return int(row["account_uploads_per_hour"])

    def set_account_upload_limit(self, per_hour: int) -> int:
        """Save how many logged-in submissions one account may start per hour."""
        if per_hour < 1 or per_hour > 100:
            raise ValueError("account upload limit must be from 1 to 100")
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO account_upload_limit (id, account_uploads_per_hour)
                VALUES (true, %s)
                ON CONFLICT (id) DO UPDATE
                    SET account_uploads_per_hour = EXCLUDED.account_uploads_per_hour
                """,
                (per_hour,),
            )
        return per_hour

    def upload_limits(self) -> UploadLimits:
        """All saved limits in one read. Guest and network are None until saved."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT account_uploads_per_hour, guest_uploads_per_hour,
                       network_uploads_per_hour
                FROM account_upload_limit WHERE id = true
                """
            ).fetchone()
        if row is None:
            return UploadLimits(account=5, guest=None, network=None)
        return _upload_limits(row)

    def set_upload_limits(
        self,
        *,
        account: int | _Unset = UNSET,
        guest: int | None | _Unset = UNSET,
        network: int | None | _Unset = UNSET,
    ) -> UploadLimits:
        """Save only the limits given.

        An omitted limit is left as it is. ``None`` clears a guest or network
        limit so the environment setting applies again. The account limit
        cannot be cleared. Every value is checked before anything is written.
        """
        changes = _limit_changes({"account": account, "guest": guest, "network": network})
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = %s").format(sql.Identifier(column)) for column in changes
        )
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO account_upload_limit (id, account_uploads_per_hour)
                VALUES (true, 5)
                ON CONFLICT (id) DO NOTHING
                """
            )
            row = conn.execute(
                sql.SQL(
                    """
                    UPDATE account_upload_limit SET {}
                    WHERE id = true
                    RETURNING account_uploads_per_hour, guest_uploads_per_hour,
                              network_uploads_per_hour
                    """
                ).format(assignments),
                list(changes.values()),
            ).fetchone()
        return _upload_limits(row)

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

    def open_gate(
        self,
        submission_id: str,
        quote: PriceQuote,
        expires_at: datetime,
        voucher_id: str | None = None,
        *,
        conn: psycopg.Connection | None = None,
        source: str = "upload",
    ) -> None:
        """Insert the gate for a submission that was just created.

        A voucher id marks the code as attached. Payment stays on ``quote`` and
        is not authorized by that attachment.
        """
        if source != "upload" and not self.source_column_available:
            raise RuntimeError(
                "Review-code payment source requires the feature-gated schema migration."
            )
        values = {
            "submission_id": submission_id,
            "status": "received",
            "uploaded": False,
            "payment_state": quote.payment_state,
            "checkout_attempt": 0,
            "price_cents": quote.price_cents,
            "currency": quote.currency,
            "pricing_version": quote.pricing_version,
            "expires_at": expires_at,
            "voucher_id": voucher_id,
            "voucher_state": None if voucher_id is None else "pending",
        }
        if self.source_column_available:
            values["source"] = source
        with nullcontext(conn) if conn is not None else self._conn() as conn:
            _insert_gate(conn, values)

    def get_by_reference(self, reference: str) -> SubmissionGate | None:
        """Load the gate with this support reference. Identification only."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM submission_gates WHERE public_reference = %s",
                (reference,),
            ).fetchone()
        return None if row is None else _gate(row)

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
        source_clause = "AND source = 'upload'" if self.source_column_available else ""
        with self._conn() as conn:
            updated = conn.execute(
                f"""
                UPDATE submission_gates
                SET uploaded = true
                WHERE submission_id = %s AND status = 'received' AND NOT uploaded
                    {source_clause}
                """,
                (submission_id,),
            )
        return updated.rowcount == 1

    def try_queue(self, submission_id: str) -> bool:
        """Move an eligible gate from received to queued. Only one caller wins."""
        statement = _QUEUE_REVIEW_SAFE if self.source_column_available else _QUEUE
        with self._conn() as conn:
            updated = conn.execute(statement, (submission_id,))
        return updated.rowcount == 1

    def apply_paid(
        self,
        *,
        submission_id: str,
        session_id: str,
        amount_subtotal: int | None,
        amount_total: int | None,
        currency: str | None,
        payment_intent_id: str | None,
    ) -> PaidResult:
        """Mark the current attempt paid when the amount matches the snapshot.

        The snapshot is pre-tax: Stripe may add tax on top, so the subtotal must
        equal the snapshot and the total may not be lower than it (no discount).
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
            if row["status"] == "expired" and not self.source_column_available:
                return "expired"
            is_review = (
                self.source_column_available and row["source"] == "nintendo_review_code"
            )
            if row["status"] == "expired" and not is_review:
                return "expired"
            if is_review and row["status"] == "expired":
                acquisition = conn.execute(
                    """SELECT sensitive_data_purged_at
                       FROM review_code_acquisitions WHERE submission_id = %s""",
                    (submission_id,),
                ).fetchone()
                if acquisition is None or acquisition["sensitive_data_purged_at"]:
                    return "expired"
            if row["checkout_session_id"] != session_id:
                return "stale"
            if not _amount_matches(row, amount_subtotal, amount_total, currency):
                return "amount"
            if row["payment_state"] == "paid":
                return "paid"
            if row["payment_state"] != "checkout_created" or row["status"] not in {
                "received",
                "expired",
            }:
                return "stale"
            source_condition = (
                "(status = 'received' OR (source = 'nintendo_review_code' AND status = 'expired'))"
                if self.source_column_available
                else "status = 'received'"
            )
            updated = conn.execute(
                f"""
                UPDATE submission_gates
                SET payment_state = 'paid', payment_intent_id = %s, status = 'received'
                WHERE submission_id = %s
                  AND checkout_session_id = %s
                  AND checkout_attempt = %s
                  AND payment_state = 'checkout_created'
                  AND {source_condition}
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

    def claim_checkout(
        self, submission_id: str, replace_session_id: str | None
    ) -> int | None:
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
            if row["voucher_state"] == "pending":
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

    def attach_session(
        self, submission_id: str, attempt: int, session_id: str, url: str
    ) -> bool:
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

    def abandon_checkout_attempt(self, submission_id: str, attempt: int) -> None:
        """Drop a claimed attempt that never received a Checkout session.

        The next click then uses a new idempotency key. Stripe keeps the
        rejected request under the old key.
        """
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE submission_gates
                SET checkout_attempt = checkout_attempt + 1
                WHERE submission_id = %s
                  AND checkout_attempt = %s
                  AND payment_state = 'unpaid'
                  AND checkout_session_id IS NULL
                  AND status = 'received'
                """,
                (submission_id, attempt),
            )

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
        source_clause = "AND source = 'upload'" if self.source_column_available else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT submission_id FROM submission_gates
                WHERE status = 'received'
                  AND uploaded
                  {source_clause}
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
                pricing_version=None
                if row["pricing_version"] is None
                else int(row["pricing_version"]),
                price_cents=None
                if row["price_cents"] is None
                else int(row["price_cents"]),
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


def _limit_changes(given: dict[str, object]) -> dict[str, int | None]:
    """Column to value for every limit the caller mentioned, after validation."""
    changes: dict[str, int | None] = {}
    for name, column in _LIMIT_COLUMNS:
        value = given[name]
        if value is UNSET:
            continue
        if value is None and name == "account":
            raise ValueError("account upload limit cannot be cleared")
        if value is not None and (type(value) is not int or not 1 <= value <= 100):
            raise ValueError(f"{name} upload limit must be a whole number from 1 to 100")
        changes[column] = value
    if not changes:
        raise ValueError("give at least one upload limit")
    return changes


def _upload_limits(row: dict) -> UploadLimits:
    return UploadLimits(
        account=int(row["account_uploads_per_hour"]),
        guest=row["guest_uploads_per_hour"],
        network=row["network_uploads_per_hour"],
    )


def _amount_matches(
    row: dict, amount_subtotal: int | None, amount_total: int | None, currency: str | None
) -> bool:
    return (
        row["currency"] == "usd"
        and currency == "usd"
        and amount_subtotal is not None
        and amount_total is not None
        and int(amount_subtotal) == int(row["price_cents"])
        and int(amount_total) >= int(amount_subtotal)
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
        voucher_id=None if row.get("voucher_id") is None else str(row["voucher_id"]),
        voucher_state=row.get("voucher_state"),
        voucher_failure_reason=row.get("voucher_failure_reason"),
        public_reference=row.get("public_reference"),
        source=row.get("source") or "upload",
    )


def _insert_gate(conn: psycopg.Connection, values: dict[str, object]) -> None:
    """Insert one gate with a fresh support reference, drawing again on a clash.

    Only a clash on the reference index is retried; a duplicate submission id
    still raises.
    """
    columns = sql.SQL(", ").join(
        sql.Identifier(name) for name in [*values, "public_reference"]
    )
    placeholders = sql.SQL(", ").join([sql.Placeholder()] * (len(values) + 1))
    statement = sql.SQL(
        """INSERT INTO submission_gates ({}) VALUES ({})
           ON CONFLICT (public_reference) DO NOTHING
           RETURNING submission_id"""
    ).format(columns, placeholders)
    for _ in range(_REFERENCE_ATTEMPTS):
        row = conn.execute(statement, [*values.values(), new_reference()]).fetchone()
        if row is not None:
            return
    raise RuntimeError("could not allocate a unique support reference")


def _backfill_references(conn: psycopg.Connection) -> None:
    """Give every gate without a support reference a unique one. Never rewrites one."""
    rows = conn.execute(
        "SELECT submission_id FROM submission_gates WHERE public_reference IS NULL"
    ).fetchall()
    for row in rows:
        for _ in range(_REFERENCE_ATTEMPTS):
            updated = conn.execute(
                """
                UPDATE submission_gates SET public_reference = %(ref)s
                WHERE submission_id = %(id)s AND public_reference IS NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM submission_gates WHERE public_reference = %(ref)s
                  )
                """,
                {"ref": new_reference(), "id": row["submission_id"]},
            )
            if updated.rowcount == 1:
                break
        else:
            raise RuntimeError("could not allocate a unique support reference")
