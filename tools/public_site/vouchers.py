"""Voucher lookup and the one transaction that can authorize a submission.

Applying or attaching a code does not redeem it. Redemption, the gate's move
to ``not_required``, and the existing queue update commit together. A later
disk write can still fail; this module does not claim that write is atomic.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, NoReturn

import psycopg
from loguru import logger
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from public_site.errors import (
    VOUCHER_CONFLICT,
    VOUCHER_EXHAUSTED,
    VOUCHER_EXPIRED,
    VOUCHER_INACTIVE,
    VOUCHER_INVALID,
    VOUCHER_NOT_READY,
    VOUCHER_REVOKED,
    VOUCHER_UNAVAILABLE,
    VOUCHER_WINDOW,
    VOUCHER_WRONG_SCOPE,
    VOUCHER_WRONG_SERVICE,
    RequestRejected,
)

MATCH_ANALYSIS_SERVICE_ID = "match_analysis"
VOUCHER_ATTEMPTS_PER_HOUR = 20
REDEMPTION_SCOPES = ("video_upload", "review_code", "both")
DEFAULT_REDEMPTION_SCOPE = "video_upload"
SubmissionScope = Literal["video_upload", "review_code"]
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_SCHEMA_LOCK = 8_273_341_906
_SOURCES = frozenset({"skilltown", "youtube"})
_BENEFITS = {
    "skilltown": "externally_prepaid",
    "youtube": "complimentary",
}
_GATE_SCOPES: dict[str, SubmissionScope] = {
    "upload": "video_upload",
    "nintendo_review_code": "review_code",
}
_DETAILS = {
    "invalid": VOUCHER_INVALID,
    "expired": VOUCHER_EXPIRED,
    "exhausted": VOUCHER_EXHAUSTED,
    "revoked": VOUCHER_REVOKED,
    "inactive": VOUCHER_INACTIVE,
    "wrong_service": VOUCHER_WRONG_SERVICE,
    "wrong_scope": VOUCHER_WRONG_SCOPE,
    "not_ready": VOUCHER_NOT_READY,
    "window": VOUCHER_WINDOW,
    "conflict": VOUCHER_CONFLICT,
}
_FAILURES = frozenset({"expired", "revoked", "exhausted", "inactive", "wrong_service"})
_STATE_LABELS = {"exhausted": "fully_used"}
_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS vouchers (
        id uuid PRIMARY KEY,
        code_hash text NOT NULL UNIQUE,
        source text NOT NULL CHECK (source IN ('skilltown', 'youtube')),
        campaign text NOT NULL,
        eligible_service_id text NOT NULL,
        benefit_type text NOT NULL CHECK (
            benefit_type IN ('externally_prepaid', 'complimentary')
        ),
        max_redemptions integer NOT NULL CHECK (max_redemptions >= 1),
        starts_at timestamptz,
        expires_at timestamptz NOT NULL,
        status text NOT NULL CHECK (status IN ('active', 'revoked')),
        note text,
        created_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS voucher_redemptions (
        id uuid PRIMARY KEY,
        voucher_id uuid NOT NULL REFERENCES vouchers (id),
        submission_id text NOT NULL UNIQUE,
        redeemed_at timestamptz NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS voucher_redemptions_voucher
        ON voucher_redemptions (voucher_id)
    """,
    """
    ALTER TABLE vouchers ADD COLUMN IF NOT EXISTS redemption_scope text NOT NULL
        DEFAULT 'video_upload'
        CHECK (redemption_scope IN ('video_upload', 'review_code', 'both'))
    """,
)

VoucherOutcome = Literal["redeemed", "failed", "absent", "conflict"]


@dataclass(frozen=True)
class VoucherPreview:
    """Advisory validation. The code is not consumed."""

    benefit_type: Literal["externally_prepaid", "complimentary"]
    amount_due_cents: int = 0
    currency: str = "usd"


@dataclass(frozen=True)
class VoucherDecision:
    """Result of the final authorization transaction."""

    outcome: VoucherOutcome
    reason: str | None = None


@dataclass(frozen=True)
class VoucherIssue:
    """Operator input. ``code`` is set only for a public YouTube code."""

    source: str
    campaign: str
    eligible_service_id: str
    benefit_type: str
    max_redemptions: int
    expires_at: datetime
    code: str | None = None
    starts_at: datetime | None = None
    note: str | None = None
    redemption_scope: str = DEFAULT_REDEMPTION_SCOPE


@dataclass(frozen=True)
class IssuedVoucher:
    """The one response that still has the plaintext code."""

    voucher_id: str
    code: str


@dataclass(frozen=True)
class VoucherRedemption:
    """One committed use and the submission it authorized."""

    submission_id: str
    redeemed_at: datetime


@dataclass(frozen=True)
class VoucherStatus:
    """Operator view. The plaintext code is not recoverable."""

    voucher_id: str
    source: str
    campaign: str
    eligible_service_id: str
    benefit_type: str
    status: str
    max_redemptions: int
    redemption_count: int
    starts_at: datetime | None
    expires_at: datetime
    note: str | None
    created_at: datetime
    redemptions: tuple[VoucherRedemption, ...]
    redemption_scope: str = DEFAULT_REDEMPTION_SCOPE


class VoucherStore:
    """Postgres vouchers. Hashing uses the process secret, never a table column."""

    def __init__(self, database_url: str, secret: str) -> None:
        self.database_url = database_url
        self.secret = secret

    def ensure_schema(self) -> None:
        """Create voucher tables and the gate foreign key when missing."""
        with self._conn() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK,))
            for statement in _SCHEMA:
                conn.execute(statement)
            _voucher_foreign_key(conn)

    def clear(self) -> None:
        """Delete voucher rows. Used by tests, not by the public site."""
        with self._conn() as conn:
            conn.execute("DELETE FROM voucher_redemptions")
            conn.execute("DELETE FROM vouchers")

    def preview(self, raw: str, now: datetime, scope: SubmissionScope) -> VoucherPreview:
        """Check a code without inserting a redemption or changing a gate.

        ``scope`` is fixed by the route the code arrived on. The result is
        advisory; redemption re-derives the scope from the stored gate.
        """
        row = self._row_for_code(raw)
        reason = _rejection(row, now, self._count(str(row["id"])), scope)
        if reason is not None:
            _reject(400, reason)
        return VoucherPreview(benefit_type=row["benefit_type"])

    def issue(self, spec: VoucherIssue, now: datetime) -> IssuedVoucher:
        """Insert one voucher and return its plaintext code once."""
        validate_issue(spec, now)
        normalized = _issue_code(spec)
        self._require_secret()
        voucher_id = str(uuid.uuid4())
        try:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO vouchers (
                        id, code_hash, source, campaign, eligible_service_id,
                        benefit_type, max_redemptions, starts_at, expires_at,
                        status, note, created_at, redemption_scope
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
                    """,
                    (
                        voucher_id,
                        code_hash(normalized, self.secret),
                        spec.source,
                        spec.campaign,
                        spec.eligible_service_id,
                        spec.benefit_type,
                        spec.max_redemptions,
                        spec.starts_at,
                        spec.expires_at,
                        spec.note,
                        now,
                        spec.redemption_scope,
                    ),
                )
        except UniqueViolation:
            raise ValueError("a voucher with that normalized code already exists") from None
        return IssuedVoucher(voucher_id=voucher_id, code=normalized)

    def revoke(self, voucher_id: str) -> bool:
        """Mark one voucher revoked. True when this call changes it."""
        with self._conn() as conn:
            updated = conn.execute(
                """
                UPDATE vouchers SET status = 'revoked'
                WHERE id = %s AND status = 'active'
                """,
                (voucher_id,),
            )
        return updated.rowcount == 1

    def status(self, voucher_id: str) -> VoucherStatus | None:
        """Load one voucher and the submissions that redeemed it. Read-only."""
        with self._reader() as conn:
            row = conn.execute(
                "SELECT * FROM vouchers WHERE id = %s",
                (voucher_id,),
            ).fetchone()
            if row is None:
                return None
            uses = _uses(conn, str(row["id"]))
        return _status(row, uses)

    def status_for_code(self, raw: str) -> VoucherStatus | None:
        """Operator lookup by plaintext code. Read-only; nothing is redeemed.

        A code that cannot be normalized is reported as not found.
        """
        try:
            normalized = normalize_voucher_code(raw)
        except ValueError:
            return None
        digest = code_hash(normalized, self._require_secret())
        with self._reader() as conn:
            row = conn.execute(
                "SELECT * FROM vouchers WHERE code_hash = %s",
                (digest,),
            ).fetchone()
            if row is None:
                return None
            uses = _uses(conn, str(row["id"]))
        return _status(row, uses)

    def overview(self) -> list[VoucherStatus]:
        """Every voucher, newest first, with its redemptions. Read-only."""
        with self._reader() as conn:
            rows = conn.execute(
                "SELECT * FROM vouchers ORDER BY created_at DESC, id"
            ).fetchall()
            uses = conn.execute(
                """
                SELECT voucher_id, submission_id, redeemed_at
                FROM voucher_redemptions
                ORDER BY redeemed_at, submission_id
                """
            ).fetchall()
        grouped: dict[str, list[dict]] = {}
        for item in uses:
            grouped.setdefault(str(item["voucher_id"]), []).append(item)
        return [_status(row, grouped.get(str(row["id"]), [])) for row in rows]

    def attached_id(self, raw: str, now: datetime, scope: SubmissionScope) -> str:
        """Revalidate a code for one submission path and return its id.

        This does not redeem it. ``scope`` comes from the server route that
        creates the submission, never from the request body.
        """
        row = self._row_for_code(raw)
        reason = _rejection(row, now, self._count(str(row["id"])), scope)
        if reason is not None:
            _reject(400, reason)
        return str(row["id"])

    def finalize(self, submission_id: str, now: datetime) -> VoucherDecision:
        """Redeem and authorize one uploaded submission, or record why not.

        The same submission and voucher, already ``not_required`` and queued,
        is a successful retry and does not insert another redemption. Any other
        committed authorization state fails without being rewritten.
        """
        try:
            with self._conn() as conn:
                return _finalize(conn, submission_id, now)
        except _LeaveUnchanged as exc:
            return exc.decision

    def redemption_count(self, voucher_id: str) -> int:
        """How many submissions have a committed redemption."""
        return self._count(voucher_id)

    def _row_for_code(self, raw: str) -> dict:
        digest = self._digest(raw)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM vouchers WHERE code_hash = %s",
                (digest,),
            ).fetchone()
        if row is None:
            _reject(400, "invalid")
        return row

    def _count(self, voucher_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT count(*) AS n FROM voucher_redemptions WHERE voucher_id = %s",
                (voucher_id,),
            ).fetchone()
        return int(row["n"])

    def _digest(self, raw: str) -> str:
        try:
            normalized = normalize_voucher_code(raw)
        except ValueError:
            _reject(400, "invalid")
        return code_hash(normalized, self._require_secret())

    def _require_secret(self) -> str:
        secret = self.secret.strip()
        if not secret:
            raise RequestRejected(503, VOUCHER_UNAVAILABLE, reason="unavailable")
        return secret

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _reader(self) -> psycopg.Connection:
        """A connection whose transactions Postgres runs as READ ONLY."""
        conn = self._conn()
        conn.read_only = True
        return conn


def normalize_voucher_code(raw: str) -> str:
    """NFKC, strip, and Unicode-uppercase. Internal whitespace is rejected.

    ``squidlab50`` and `` SKT-7k4m-p9qx `` match their issued forms. Hyphens
    are significant. This is the only comparison form.
    """
    text = unicodedata.normalize("NFKC", raw).strip().upper()
    if not text or len(text) > 64 or any(character.isspace() for character in text):
        raise ValueError("invalid voucher code")
    return text


def code_hash(normalized: str, secret: str) -> str:
    """HMAC-SHA256 hex of a code that has already been normalized."""
    return hmac.new(
        secret.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_skilltown_code() -> str:
    """A random ``SKT-XXXX-XXXX`` code. Not derived from a sequence or name."""
    return f"SKT-{_block()}-{_block()}"


def voucher_state(found: VoucherStatus, now: datetime) -> str:
    """Operator label from the same rules redemption applies.

    ``active`` means a redemption at ``now`` would pass on a path its scope
    allows. Otherwise the rejection reason, with ``exhausted`` shown as
    ``fully_used``.
    """
    row = {
        "eligible_service_id": found.eligible_service_id,
        "status": found.status,
        "starts_at": found.starts_at,
        "expires_at": found.expires_at,
        "max_redemptions": found.max_redemptions,
        "redemption_scope": found.redemption_scope,
    }
    reason = _rejection(row, now, found.redemption_count, None)
    if reason is None:
        return "active"
    return _STATE_LABELS.get(reason, reason)


def default_benefit(source: str) -> str:
    """The V1 benefit for one source."""
    if source not in _BENEFITS:
        raise ValueError("source must be skilltown or youtube")
    return _BENEFITS[source]


def validate_issue(spec: VoucherIssue, now: datetime) -> None:
    """Reject operator input that would create an unusable or ambiguous voucher."""
    if spec.source not in _SOURCES:
        raise ValueError("source must be skilltown or youtube")
    if spec.benefit_type != _BENEFITS[spec.source]:
        raise ValueError("source and benefit combination is not valid")
    if spec.eligible_service_id != MATCH_ANALYSIS_SERVICE_ID:
        raise ValueError("service is not a current S3 Coach service")
    if spec.max_redemptions < 1:
        raise ValueError("max redemptions must be at least 1")
    if spec.redemption_scope not in REDEMPTION_SCOPES:
        raise ValueError("scope must be video_upload, review_code, or both")
    if spec.expires_at <= now:
        raise ValueError("expiration must be in the future")
    if spec.starts_at is not None and spec.starts_at >= spec.expires_at:
        raise ValueError("start must be before expiration")
    campaign = spec.campaign.strip()
    if not campaign or len(campaign) > 64 or not campaign.replace("_", "").isalnum():
        raise ValueError("campaign must be 1-64 letters, digits, or underscores")
    if campaign != campaign.lower():
        raise ValueError("campaign must be lowercase")
    if spec.note is not None and len(spec.note) > 500:
        raise ValueError("note must be 500 characters or fewer")
    if spec.source == "youtube" and not spec.code:
        raise ValueError("a YouTube voucher needs its public code")
    if spec.source == "skilltown" and spec.code:
        raise ValueError("a Skilltown voucher code is generated, not chosen")


class _LeaveUnchanged(Exception):
    """Roll back. The gate and redemption rows stay as they were."""

    def __init__(self, decision: VoucherDecision) -> None:
        self.decision = decision
        super().__init__(decision.outcome)


def _finalize(conn: psycopg.Connection, submission_id: str, now: datetime) -> VoucherDecision:
    """Lock order: gate, then voucher, then (review codes) the acquisition."""
    gate = conn.execute(
        "SELECT * FROM submission_gates WHERE submission_id = %s FOR UPDATE",
        (submission_id,),
    ).fetchone()
    if gate is None or gate["voucher_id"] is None:
        return VoucherDecision("absent")
    scope = _gate_scope(gate)
    existing = conn.execute(
        "SELECT * FROM voucher_redemptions WHERE submission_id = %s",
        (submission_id,),
    ).fetchone()
    if existing is not None:
        return _idempotent(gate, existing, scope)
    if gate["voucher_state"] != "pending":
        raise _LeaveUnchanged(VoucherDecision("conflict"))
    voucher = conn.execute(
        "SELECT * FROM vouchers WHERE id = %s FOR UPDATE",
        (gate["voucher_id"],),
    ).fetchone()
    if voucher is None:
        raise _LeaveUnchanged(VoucherDecision("conflict"))
    if scope == "review_code":
        _require_review_ready(conn, gate, now)
    reason = _rejection(voucher, now, _locked_count(conn, str(voucher["id"])), scope)
    if reason == "wrong_scope":
        raise _LeaveUnchanged(VoucherDecision("conflict", "wrong_scope"))
    if reason is not None:
        _mark_failed(conn, gate, reason)
        return VoucherDecision("failed", reason)
    _redeem(conn, gate, voucher, now, scope)
    return VoucherDecision("redeemed")


def _gate_scope(gate: dict) -> SubmissionScope:
    """The submission path, from the stored gate only. Unknown sources fail closed.

    A row without a ``source`` key comes from a database that predates review
    codes, where every gate is a video upload.
    """
    source = gate.get("source", "upload")
    scope = _GATE_SCOPES.get(source)
    if scope is None:
        logger.warning("voucher refused for a gate with an unsupported source")
        raise _LeaveUnchanged(VoucherDecision("conflict"))
    return scope


def _require_review_ready(conn: psycopg.Connection, gate: dict, now: datetime) -> None:
    """A review-code voucher is used only for a verified, unpurged, unpaid request."""
    acquisition = conn.execute(
        """
        SELECT state, contact_verified_at, sensitive_data_purged_at
        FROM review_code_acquisitions WHERE submission_id = %s FOR UPDATE
        """,
        (gate["submission_id"],),
    ).fetchone()
    if (
        acquisition is None
        or acquisition["state"] != "pending_payment"
        or acquisition["sensitive_data_purged_at"] is not None
    ):
        raise _LeaveUnchanged(VoucherDecision("conflict"))
    if acquisition["contact_verified_at"] is None:
        raise _LeaveUnchanged(VoucherDecision("conflict", "not_ready"))
    if gate["expires_at"] <= now:
        raise _LeaveUnchanged(VoucherDecision("conflict", "window"))


def _idempotent(gate: dict, existing: dict, scope: SubmissionScope) -> VoucherDecision:
    """Success only for the state the redemption transaction commits together.

    The stored redemption must name the gate's own voucher. A video gate is
    queued in the same transaction; a review-code gate waits for recording.
    """
    established = (
        str(existing["voucher_id"]) == str(gate["voucher_id"])
        and gate["payment_state"] == "not_required"
        and gate["voucher_state"] == "redeemed"
        and (scope == "review_code" or gate["status"] == "queued")
    )
    if established:
        return VoucherDecision("redeemed")
    raise _LeaveUnchanged(VoucherDecision("conflict"))


def _mark_failed(conn: psycopg.Connection, gate: dict, reason: str) -> None:
    if reason not in _FAILURES:
        raise _LeaveUnchanged(VoucherDecision("conflict"))
    if not _still_pending(gate):
        raise _LeaveUnchanged(VoucherDecision("conflict"))
    updated = conn.execute(
        """
        UPDATE submission_gates
        SET voucher_state = 'failed', voucher_failure_reason = %s
        WHERE submission_id = %s
          AND voucher_state = 'pending'
          AND payment_state = 'unpaid'
          AND status = 'received'
          AND checkout_attempt = 0
          AND checkout_session_id IS NULL
        """,
        (reason, gate["submission_id"]),
    )
    if updated.rowcount != 1:
        raise _LeaveUnchanged(VoucherDecision("conflict"))


def _redeem(
    conn: psycopg.Connection,
    gate: dict,
    voucher: dict,
    now: datetime,
    scope: SubmissionScope,
) -> None:
    """Insert the redemption and authorize the gate together.

    A video gate is also queued here. A review-code gate stays ``received``:
    recording has not happened yet, and activation is ``RecordingStore.reconcile``.
    """
    if not _still_pending(gate):
        raise _LeaveUnchanged(VoucherDecision("conflict"))
    conn.execute(
        """
        INSERT INTO voucher_redemptions (id, voucher_id, submission_id, redeemed_at)
        VALUES (%s, %s, %s, %s)
        """,
        (str(uuid.uuid4()), voucher["id"], gate["submission_id"], now),
    )
    authorized = conn.execute(
        """
        UPDATE submission_gates
        SET payment_state = 'not_required', voucher_state = 'redeemed'
        WHERE submission_id = %s
          AND voucher_id = %s
          AND voucher_state = 'pending'
          AND payment_state = 'unpaid'
          AND status = 'received'
          AND checkout_attempt = 0
          AND checkout_session_id IS NULL
        """,
        (gate["submission_id"], voucher["id"]),
    )
    if authorized.rowcount != 1:
        raise _LeaveUnchanged(VoucherDecision("conflict"))
    if scope == "review_code":
        return
    queued = conn.execute(
        """
        UPDATE submission_gates
        SET status = 'queued'
        WHERE submission_id = %s
          AND status = 'received'
          AND uploaded
          AND payment_state = 'not_required'
          AND expires_at > %s
        """,
        (gate["submission_id"], now),
    )
    if queued.rowcount != 1:
        raise _LeaveUnchanged(VoucherDecision("conflict"))


def _still_pending(gate: dict) -> bool:
    return (
        gate["voucher_state"] == "pending"
        and gate["payment_state"] == "unpaid"
        and gate["status"] == "received"
        and int(gate["checkout_attempt"]) == 0
        and gate["checkout_session_id"] is None
    )


def _locked_count(conn: psycopg.Connection, voucher_id: str) -> int:
    row = conn.execute(
        "SELECT count(*) AS n FROM voucher_redemptions WHERE voucher_id = %s",
        (voucher_id,),
    ).fetchone()
    return int(row["n"])


def scope_allows(redemption_scope: str, scope: SubmissionScope) -> bool:
    """A voucher fits a path when its scope names that path or is ``both``."""
    return redemption_scope in REDEMPTION_SCOPES and redemption_scope in (scope, "both")


def preview_payload(preview: VoucherPreview) -> dict:
    """Public response for an accepted advisory check."""
    return {
        "accepted": True,
        "benefit_type": preview.benefit_type,
        "amount_due_cents": preview.amount_due_cents,
        "currency": preview.currency,
    }


def raise_voucher_conflict(decision: VoucherDecision) -> NoReturn:
    """409 for a redemption that changed nothing, with its specific reason."""
    reason = decision.reason or "conflict"
    raise RequestRejected(409, _DETAILS.get(reason, VOUCHER_CONFLICT), reason=reason)


def _rejection(
    row: dict, now: datetime, count: int, scope: SubmissionScope | None
) -> str | None:
    """The one eligibility rule. ``scope`` None skips only the path check."""
    reason = _state_reason(row, now, scope)
    if reason is not None:
        return reason
    if count >= int(row["max_redemptions"]):
        return "exhausted"
    return None


def _state_reason(row: dict, now: datetime, scope: SubmissionScope | None) -> str | None:
    if row["eligible_service_id"] != MATCH_ANALYSIS_SERVICE_ID:
        return "wrong_service"
    stored_scope = row.get("redemption_scope", DEFAULT_REDEMPTION_SCOPE)
    if scope is not None and not scope_allows(stored_scope, scope):
        return "wrong_scope"
    if row["status"] == "revoked":
        return "revoked"
    if row["status"] != "active":
        return "inactive"
    if row["starts_at"] is not None and now < row["starts_at"]:
        return "inactive"
    if now >= row["expires_at"]:
        return "expired"
    return None


def _reject(status_code: int, reason: str) -> NoReturn:
    detail = _DETAILS.get(reason, VOUCHER_CONFLICT)
    raise RequestRejected(status_code, detail, reason=reason)


def _issue_code(spec: VoucherIssue) -> str:
    if spec.source == "skilltown":
        return generate_skilltown_code()
    if spec.code is None:
        raise ValueError("a YouTube voucher needs its public code")
    try:
        return normalize_voucher_code(spec.code)
    except ValueError:
        raise ValueError("voucher code is not valid") from None


def _block() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))


def _status(row: dict, uses: list[dict]) -> VoucherStatus:
    return VoucherStatus(
        voucher_id=str(row["id"]),
        source=str(row["source"]),
        campaign=str(row["campaign"]),
        eligible_service_id=str(row["eligible_service_id"]),
        benefit_type=str(row["benefit_type"]),
        status=str(row["status"]),
        max_redemptions=int(row["max_redemptions"]),
        redemption_count=len(uses),
        starts_at=row["starts_at"],
        expires_at=row["expires_at"],
        note=row["note"],
        created_at=row["created_at"],
        redemptions=tuple(
            VoucherRedemption(str(item["submission_id"]), item["redeemed_at"]) for item in uses
        ),
        redemption_scope=str(row.get("redemption_scope", DEFAULT_REDEMPTION_SCOPE)),
    )


def _uses(conn: psycopg.Connection, voucher_id: str) -> list[dict]:
    return conn.execute(
        """
        SELECT submission_id, redeemed_at
        FROM voucher_redemptions
        WHERE voucher_id = %s
        ORDER BY redeemed_at, submission_id
        """,
        (voucher_id,),
    ).fetchall()


def _voucher_foreign_key(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE submission_gates
                ADD CONSTRAINT submission_gates_voucher_fk
                FOREIGN KEY (voucher_id) REFERENCES vouchers (id);
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$
        """
    )
