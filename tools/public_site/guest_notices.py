"""Guest notice browsers, requested addresses, and one-time confirmation links.

The requested address stays on ``guest_notice_requests`` until the mailbox is
verified. It is not written to ``submission.email`` here.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from public_site.tokens import hash_token, new_token

_IDLE = timedelta(days=180)
_ABSOLUTE = timedelta(days=365)
_CHALLENGE_TTL = timedelta(minutes=60)
_RATE_WINDOW = timedelta(minutes=15)

GUEST_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS guest_notice_browsers (
        id uuid PRIMARY KEY,
        browser_token_hash text NOT NULL UNIQUE,
        created_at timestamptz NOT NULL,
        last_used_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS guest_verified_emails (
        browser_id uuid NOT NULL REFERENCES guest_notice_browsers (id) ON DELETE CASCADE,
        normalized_email text NOT NULL,
        verified_at timestamptz NOT NULL,
        last_used_at timestamptz NOT NULL,
        PRIMARY KEY (browser_id, normalized_email)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS guest_notice_requests (
        submission_id text PRIMARY KEY,
        browser_id uuid NOT NULL REFERENCES guest_notice_browsers (id) ON DELETE CASCADE,
        normalized_email text NOT NULL,
        created_at timestamptz NOT NULL,
        confirmed_at timestamptz
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS guest_notice_requests_burst
        ON guest_notice_requests (browser_id, normalized_email, created_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS guest_email_challenges (
        id uuid PRIMARY KEY,
        browser_id uuid NOT NULL REFERENCES guest_notice_browsers (id) ON DELETE CASCADE,
        normalized_email text NOT NULL,
        submission_id text NOT NULL,
        token_hash text NOT NULL UNIQUE,
        created_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL,
        used_at timestamptz
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS guest_email_challenges_browser
        ON guest_email_challenges (browser_id, created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS guest_email_challenges_email
        ON guest_email_challenges (normalized_email, created_at)
    """,
)


class GuestNotices:
    """Browser-bound notice requests. Throttles use the browser row id."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def open_browser(self, raw_token: str | None, now: datetime) -> tuple[str, str | None]:
        """Return the browser id, and a new cookie token when one must be set."""
        if raw_token:
            row = self._live_browser(hash_token(raw_token), now)
            if row is not None:
                self._touch(row, now)
                return str(row["id"]), None
        return self._create_browser(now)

    def is_verified(self, browser_id: str, email: str) -> bool:
        """True when this browser has already confirmed this exact address."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM guest_verified_emails
                WHERE browser_id = %s AND normalized_email = %s
                """,
                (browser_id, email),
            ).fetchone()
        return row is not None

    def remember_verified(self, browser_id: str, email: str, now: datetime) -> None:
        """Record or refresh a verified address for one browser."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO guest_verified_emails
                    (browser_id, normalized_email, verified_at, last_used_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (browser_id, normalized_email)
                DO UPDATE SET last_used_at = EXCLUDED.last_used_at
                """,
                (browser_id, email, now, now),
            )

    def add_request(self, browser_id: str, submission_id: str, email: str, now: datetime) -> None:
        """Store the requested address outside the submission record."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO guest_notice_requests
                    (submission_id, browser_id, normalized_email, created_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (submission_id) DO NOTHING
                """,
                (submission_id, browser_id, email, now),
            )

    def begin_challenge(
        self,
        browser_id: str,
        email: str,
        submission_id: str,
        now: datetime,
    ) -> tuple[str, str] | None:
        """Insert one challenge, or return None when this browser or address is limited.

        The limit counts challenge rows, keyed by the browser row id.
        """
        since = now - _RATE_WINDOW
        token = new_token()
        challenge_id = str(uuid.uuid4())
        with self._conn() as conn:
            _lock_pair(conn, browser_id, email)
            if _recent(conn, browser_id=browser_id, email=None, since=since):
                return None
            if _recent(conn, browser_id=None, email=email, since=since):
                return None
            conn.execute(
                """
                INSERT INTO guest_email_challenges (
                    id, browser_id, normalized_email, submission_id,
                    token_hash, created_at, expires_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    challenge_id,
                    browser_id,
                    email,
                    submission_id,
                    hash_token(token),
                    now,
                    now + _CHALLENGE_TTL,
                ),
            )
        return challenge_id, token

    def drop_challenge(self, challenge_id: str) -> None:
        """Remove a challenge whose message was not sent, so it does not count."""
        with self._conn() as conn:
            conn.execute("DELETE FROM guest_email_challenges WHERE id = %s", (challenge_id,))

    def consume(self, raw_token: str, now: datetime) -> tuple[str, str] | None:
        """Use one challenge. Returns the browser id and normalized address."""
        digest = hash_token(raw_token)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM guest_email_challenges WHERE token_hash = %s FOR UPDATE",
                (digest,),
            ).fetchone()
            if not _usable(row, digest, now):
                return None
            updated = conn.execute(
                """
                UPDATE guest_email_challenges
                SET used_at = %s
                WHERE id = %s AND used_at IS NULL
                RETURNING browser_id, normalized_email
                """,
                (now, row["id"]),
            ).fetchone()
            if updated is None:
                return None
            browser_id = str(updated["browser_id"])
            email = updated["normalized_email"]
            conn.execute(
                """
                INSERT INTO guest_verified_emails
                    (browser_id, normalized_email, verified_at, last_used_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (browser_id, normalized_email)
                DO UPDATE SET last_used_at = EXCLUDED.last_used_at
                """,
                (browser_id, email, now, now),
            )
        return browser_id, email

    def pending_ids(self, browser_id: str, email: str, since: datetime) -> list[str]:
        """Unconfirmed requests for this browser and requested address."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT submission_id FROM guest_notice_requests
                WHERE browser_id = %s
                  AND normalized_email = %s
                  AND confirmed_at IS NULL
                  AND created_at >= %s
                """,
                (browser_id, email, since),
            ).fetchall()
        return [row["submission_id"] for row in rows]

    def mark_confirmed(self, submission_id: str, now: datetime) -> None:
        """Close one request after its submission was enabled."""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE guest_notice_requests
                SET confirmed_at = %s
                WHERE submission_id = %s AND confirmed_at IS NULL
                """,
                (now, submission_id),
            )

    def has_open_request(self, submission_id: str) -> bool:
        """True when this submission still has an unconfirmed notice request."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM guest_notice_requests
                WHERE submission_id = %s AND confirmed_at IS NULL
                """,
                (submission_id,),
            ).fetchone()
        return row is not None

    def _live_browser(self, digest: str, now: datetime) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM guest_notice_browsers WHERE browser_token_hash = %s",
                (digest,),
            ).fetchone()
        if row is None or not hmac.compare_digest(row["browser_token_hash"], digest):
            return None
        if row["expires_at"] <= now or row["created_at"] + _ABSOLUTE <= now:
            return None
        return row

    def _touch(self, row: dict, now: datetime) -> None:
        expires = min(now + _IDLE, row["created_at"] + _ABSOLUTE)
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE guest_notice_browsers
                SET last_used_at = %s, expires_at = %s
                WHERE id = %s
                """,
                (now, expires, row["id"]),
            )

    def _create_browser(self, now: datetime) -> tuple[str, str]:
        token = new_token()
        browser_id = str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO guest_notice_browsers
                    (id, browser_token_hash, created_at, last_used_at, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (browser_id, hash_token(token), now, now, now + _IDLE),
            )
        return browser_id, token

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)


def _usable(row: dict | None, digest: str, now: datetime) -> bool:
    if row is None or row["used_at"] is not None or row["expires_at"] <= now:
        return False
    return hmac.compare_digest(row["token_hash"], digest)


def _recent(conn, *, browser_id: str | None, email: str | None, since: datetime) -> bool:
    if browser_id is not None:
        row = conn.execute(
            """
            SELECT 1 FROM guest_email_challenges
            WHERE browser_id = %s AND created_at > %s
            LIMIT 1
            """,
            (browser_id, since),
        ).fetchone()
        return row is not None
    row = conn.execute(
        """
        SELECT 1 FROM guest_email_challenges
        WHERE normalized_email = %s AND created_at > %s
        LIMIT 1
        """,
        (email, since),
    ).fetchone()
    return row is not None


def _lock_pair(conn, browser_id: str, email: str) -> None:
    keys = sorted((_lock_key(f"notice-browser:{browser_id}"), _lock_key(f"notice-email:{email}")))
    for key in keys:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))


def _lock_key(text: str) -> int:
    raw = hashlib.sha256(text.encode("utf-8")).digest()[:8]
    value = int.from_bytes(raw, "big", signed=True)
    return value
