"""Accounts, sessions, and coaching history in PostgreSQL."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import psycopg
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from loguru import logger
from psycopg.rows import dict_row

from public_site.errors import (
    BAD_ACCOUNT_EMAIL,
    BAD_ACCOUNT_NAME,
    BAD_FEEDBACK,
    BAD_FEEDBACK_LONG,
    BAD_LINK,
    BAD_LOGIN,
    BAD_PASSWORD,
    BAD_PASSWORD_LONG,
    EMAIL_TAKEN,
    UNVERIFIED,
    RequestRejected,
)
from public_site.store import parse_utc
from public_site.tokens import hash_token, new_token

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HASHER = PasswordHasher()
_VERIFY_HOURS = 24
_RESET_HOURS = 1
_SESSION_DAYS = 30
_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id uuid PRIMARY KEY,
        email text NOT NULL UNIQUE,
        name text NOT NULL,
        password_hash text NOT NULL,
        verified_at timestamptz,
        created_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id uuid PRIMARY KEY,
        user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        token_hash text NOT NULL UNIQUE,
        expires_at timestamptz NOT NULL,
        created_at timestamptz NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS email_tokens (
        id uuid PRIMARY KEY,
        user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        purpose text NOT NULL CHECK (purpose IN ('verify', 'reset')),
        token_hash text NOT NULL UNIQUE,
        expires_at timestamptz NOT NULL,
        used_at timestamptz
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS coaching_history (
        submission_id text PRIMARY KEY,
        user_id uuid NOT NULL REFERENCES users (id) ON DELETE CASCADE,
        created_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS coaching_history_user
        ON coaching_history (user_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS feedback (
        id uuid PRIMARY KEY,
        submission_id text NOT NULL,
        user_id uuid REFERENCES users (id) ON DELETE SET NULL,
        body text NOT NULL,
        created_at timestamptz NOT NULL
    )
    """,
)


@dataclass(frozen=True)
class Account:
    """A registered player. The password hash is not included."""

    id: str
    email: str
    name: str
    verified: bool


@dataclass(frozen=True)
class HistoryEntry:
    """Pointer from an account to one submission. Coaching prose stays on disk."""

    submission_id: str
    created_at: str
    expires_at: str


class AccountStore:
    """PostgreSQL store for accounts and the coaching history index."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def ensure_schema(self) -> None:
        """Create tables when they are missing."""
        with self._conn() as conn:
            for statement in _SCHEMA:
                conn.execute(statement)

    def clear(self) -> None:
        """Delete account rows. Used by tests, not by the public site."""
        with self._conn() as conn:
            conn.execute("TRUNCATE feedback, users CASCADE")

    def register(self, *, name: str, email: str, password: str, now: datetime) -> str:
        """Create or refresh an unverified account and return a verify token."""
        checked_name = account_name(name)
        checked_email = account_email(email)
        checked_password = account_password(password)
        with self._conn() as conn:
            existing = _user_by_email(conn, checked_email)
            if existing is not None and existing["verified_at"] is not None:
                raise RequestRejected(400, EMAIL_TAKEN)
            user_id = _save_unverified(
                conn, existing, checked_email, checked_name, checked_password, now
            )
            return _issue(conn, user_id, "verify", now + timedelta(hours=_VERIFY_HOURS))

    def verify(self, token: str, now: datetime) -> tuple[Account, str]:
        """Consume a verify link, mark the account verified, and open a session."""
        with self._conn() as conn:
            row = _consume_token(conn, token, "verify", now)
            user_id = str(row["user_id"])
            conn.execute(
                """
                UPDATE users SET verified_at = %s
                WHERE id = %s AND verified_at IS NULL
                """,
                (now, user_id),
            )
            user = _require_user(conn, user_id)
            return _account(user), _open_session(conn, user_id, now)

    def login(self, *, email: str, password: str, now: datetime) -> tuple[Account, str]:
        """Return the account and a new session token."""
        with self._conn() as conn:
            row = _user_by_email(conn, account_email(email))
            if row is None or not password_matches(password, row["password_hash"]):
                raise RequestRejected(401, BAD_LOGIN)
            if row["verified_at"] is None:
                raise RequestRejected(403, UNVERIFIED)
            user_id = str(row["id"])
            return _account(row), _open_session(conn, user_id, now)

    def request_reset(self, email: str, now: datetime) -> str | None:
        """Return a reset token for a verified account, or None when none exists."""
        with self._conn() as conn:
            row = _user_by_email(conn, account_email(email))
            if row is None or row["verified_at"] is None:
                return None
            user_id = str(row["id"])
            conn.execute(
                "DELETE FROM email_tokens WHERE user_id = %s AND purpose = 'reset'",
                (user_id,),
            )
            return _issue(conn, user_id, "reset", now + timedelta(hours=_RESET_HOURS))

    def reset_password(self, token: str, password: str, now: datetime) -> tuple[Account, str]:
        """Set a new password, drop old sessions, and open a new one."""
        checked = account_password(password)
        with self._conn() as conn:
            row = _consume_token(conn, token, "reset", now)
            user_id = str(row["user_id"])
            conn.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (hash_password(checked), user_id),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
            conn.execute(
                """
                DELETE FROM email_tokens
                WHERE user_id = %s AND purpose = 'reset' AND used_at IS NULL
                """,
                (user_id,),
            )
            user = _require_user(conn, user_id)
            if user["verified_at"] is None:
                raise RequestRejected(400, BAD_LINK)
            return _account(user), _open_session(conn, user_id, now)

    def user_from_session(self, token: str, now: datetime) -> Account | None:
        """Return the account for a live session, or None."""
        if not token:
            return None
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT users.id, users.email, users.name, users.verified_at
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = %s AND sessions.expires_at > %s
                """,
                (hash_token(token), now),
            ).fetchone()
        if row is None:
            return None
        return _account(row)

    def logout(self, token: str) -> None:
        """Drop one session. An unknown token is ignored."""
        if not token:
            return
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE token_hash = %s",
                (hash_token(token),),
            )

    def record_history(
        self,
        user_id: str,
        submission_id: str,
        created_at: str,
        expires_at: str,
    ) -> None:
        """Index one owned submission. A repeated id is ignored."""
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO coaching_history (submission_id, user_id, created_at, expires_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (submission_id) DO NOTHING
                """,
                (submission_id, user_id, parse_utc(created_at), parse_utc(expires_at)),
            )

    def list_history(self, user_id: str) -> list[HistoryEntry]:
        """Newest owned submissions first."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT submission_id, created_at, expires_at
                FROM coaching_history
                WHERE user_id = %s
                ORDER BY created_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [_history(row) for row in rows]

    def drop_history(self, submission_id: str) -> None:
        """Remove the history row when a submission expires."""
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM coaching_history WHERE submission_id = %s",
                (submission_id,),
            )

    def owns(self, user_id: str, submission_id: str) -> bool:
        """True when this account's history includes the submission."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM coaching_history
                WHERE user_id = %s AND submission_id = %s
                """,
                (user_id, submission_id),
            ).fetchone()
        return row is not None

    def add_feedback(
        self,
        submission_id: str,
        user_id: str | None,
        body: str,
        now: datetime,
    ) -> None:
        """Store one note. A guest leaves ``user_id`` empty."""
        text = feedback_body(body)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO feedback (id, submission_id, user_id, body, created_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (str(uuid.uuid4()), submission_id, user_id, text, now),
            )
        logger.info("feedback stored for submission {}", submission_id)

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)


def feedback_body(value: str) -> str:
    """Accept a non-empty note of at most 2000 characters."""
    text = value.strip()
    if not text:
        raise RequestRejected(400, BAD_FEEDBACK)
    if len(text) > 2000:
        raise RequestRejected(400, BAD_FEEDBACK_LONG)
    return text


def account_email(value: str) -> str:
    """Normalize an account email, or reject it."""
    text = value.strip().lower()
    if not _EMAIL.fullmatch(text) or len(text) > 200:
        raise RequestRejected(400, BAD_ACCOUNT_EMAIL)
    return text


def account_name(value: str) -> str:
    """Normalize a required account name."""
    text = " ".join(value.split())
    if not text or len(text) > 40 or any(ord(char) < 32 for char in text):
        raise RequestRejected(400, BAD_ACCOUNT_NAME)
    return text


def account_password(value: str) -> str:
    """Accept a password of at least 8 characters."""
    if len(value) < 8:
        raise RequestRejected(400, BAD_PASSWORD)
    if len(value) > 200:
        raise RequestRejected(400, BAD_PASSWORD_LONG)
    return value


def hash_password(password: str) -> str:
    """Argon2 hash. The plaintext password is not stored."""
    return _HASHER.hash(password)


def password_matches(password: str, hashed: str) -> bool:
    """True when the password matches the stored hash."""
    try:
        return _HASHER.verify(hashed, password)
    except VerificationError:
        return False


def _save_unverified(conn, existing, email: str, name: str, password: str, now: datetime) -> str:
    hashed = hash_password(password)
    if existing is None:
        user_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO users (id, email, name, password_hash, verified_at, created_at)
            VALUES (%s, %s, %s, %s, NULL, %s)
            """,
            (user_id, email, name, hashed, now),
        )
        return user_id
    user_id = str(existing["id"])
    conn.execute(
        "UPDATE users SET name = %s, password_hash = %s WHERE id = %s",
        (name, hashed, user_id),
    )
    conn.execute(
        "DELETE FROM email_tokens WHERE user_id = %s AND purpose = 'verify'",
        (user_id,),
    )
    return user_id


def _issue(conn, user_id: str, purpose: str, expires: datetime) -> str:
    token = new_token()
    conn.execute(
        """
        INSERT INTO email_tokens (id, user_id, purpose, token_hash, expires_at, used_at)
        VALUES (%s, %s, %s, %s, %s, NULL)
        """,
        (str(uuid.uuid4()), user_id, purpose, hash_token(token), expires),
    )
    return token


def _consume_token(conn, token: str, purpose: str, now: datetime) -> dict:
    row = conn.execute(
        """
        SELECT id, user_id, expires_at, used_at
        FROM email_tokens
        WHERE token_hash = %s AND purpose = %s
        """,
        (hash_token(token), purpose),
    ).fetchone()
    if row is None or row["used_at"] is not None or row["expires_at"] <= now:
        raise RequestRejected(400, BAD_LINK)
    conn.execute(
        "UPDATE email_tokens SET used_at = %s WHERE id = %s",
        (now, row["id"]),
    )
    return row


def _open_session(conn, user_id: str, now: datetime) -> str:
    token = new_token()
    conn.execute(
        """
        INSERT INTO sessions (id, user_id, token_hash, expires_at, created_at)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (str(uuid.uuid4()), user_id, hash_token(token), now + timedelta(days=_SESSION_DAYS), now),
    )
    return token


def _user_by_email(conn, email: str) -> dict | None:
    return conn.execute("SELECT * FROM users WHERE email = %s", (email,)).fetchone()


def _require_user(conn, user_id: str) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
    if row is None:
        raise RequestRejected(400, BAD_LINK)
    return row


def _account(row: dict) -> Account:
    return Account(
        id=str(row["id"]),
        email=row["email"],
        name=row["name"],
        verified=row["verified_at"] is not None,
    )


def _history(row: dict) -> HistoryEntry:
    return HistoryEntry(
        submission_id=row["submission_id"],
        created_at=_stamp(row["created_at"]),
        expires_at=_stamp(row["expires_at"]),
    )


def _stamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
