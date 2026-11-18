"""Customer-facing support reference for one submission.

The reference only identifies a submission when a customer contacts support.
It never authorizes access; the review link token does that.
"""

from __future__ import annotations

import re
import secrets

ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
PREFIX = "S3-"
LENGTH = 8
PATTERN = f"^S3-[{ALPHABET}]{{{LENGTH}}}$"
_PATTERN = re.compile(PATTERN)


def new_reference() -> str:
    """A random reference such as ``S3-K7M4P9Q2`` from a CSPRNG."""
    return PREFIX + "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))


def normalize_reference(text: str) -> str | None:
    """The canonical reference for operator input, or None when it is not one.

    Accepts lowercase, surrounding spaces and a missing ``S3-`` prefix.
    """
    candidate = re.sub(r"\s+", "", text).upper()
    if not candidate.startswith(PREFIX):
        candidate = PREFIX + candidate
    return candidate if _PATTERN.fullmatch(candidate) else None
