"""Unguessable review tokens. Only the SHA-256 hash is persisted."""

from __future__ import annotations

import hashlib
import secrets


def new_token() -> str:
    """Return a URL-safe secret. Callers must not write it to a JSON record."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex digest used as the lookup key."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_ip(ip: str) -> str:
    """One-way hash for rate-limit keys. The address itself is not stored."""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()
