"""Hashing and canonical JSON persistence helpers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from map_ink_reference.errors import MalformedInput

_CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> bytes:
    """Return the raw SHA-256 digest of UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).digest()


def seed_from_text(text: str) -> int:
    """First 8 bytes, big-endian, of SHA-256(text) as an unsigned integer."""
    return int.from_bytes(sha256_text(text)[:8], "big")


def sha256_array(image: np.ndarray) -> str:
    """Hash a decoded array including its shape and dtype."""
    digest = hashlib.sha256()
    digest.update(f"{image.dtype.str}|{image.shape}".encode())
    digest.update(np.ascontiguousarray(image).tobytes())
    return digest.hexdigest()


def canonical_json(payload: Any) -> str:
    """Deterministic JSON text (sorted keys, two-space indent, final newline)."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_json(path: Path, payload: Any) -> str:
    """Atomically write canonical JSON and return its SHA-256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = canonical_json(payload)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    """Read JSON; a missing or unparsable file fails closed."""
    if not path.is_file():
        raise MalformedInput(f"required file is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MalformedInput(f"malformed JSON in {path}: {exc}") from exc


def require_keys(payload: Any, keys: tuple[str, ...], where: str) -> dict[str, Any]:
    """Require a JSON object with the given keys."""
    if not isinstance(payload, dict):
        raise MalformedInput(f"{where}: expected an object")
    missing = [key for key in keys if key not in payload]
    if missing:
        raise MalformedInput(f"{where}: missing keys {missing}")
    return payload
