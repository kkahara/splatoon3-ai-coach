"""Canonical serialization and hashing for reproducible manifests."""

import hashlib
import json
from typing import Any


def canonical_timestamp(value: float) -> str:
    """Format a timestamp for stable identity strings."""
    return f"{value:.6f}"


def canonical_json(data: Any) -> str:
    """Serialize data to canonical JSON for hashing."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes | str) -> str:
    """Return the SHA-256 hex digest of bytes or UTF-8 text."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def sha256_prefix(data: bytes | str, length: int = 12) -> str:
    """Return a stable prefix of a SHA-256 hex digest."""
    return sha256_hex(data)[:length]
