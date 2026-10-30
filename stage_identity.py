"""Stage identity for the point domain (blind): sealed blind-input manifests.

Amendment 4 (A3-AMB-1): ``stage_id`` / ``battle_mode_id`` come only from the
sealed ``amendment3-blind-input/v1`` manifest. This module validates that
schema and reads the sealed corpus or pilot instance after checking the
step 2a commit order (pilot-source → pilot blind-input → corpus blind-input).
It never derives values; the derivation lives in ``intro_latch``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from map_ink_reference.constants import (
    BLIND_INPUT_MANIFEST,
    BLIND_INPUT_SCHEMA,
    CORRECTNESS_APPLICABLE,
    INTRO_LATCH_RULE,
    PILOT_BLIND_INPUT_MANIFEST,
    PILOT_SOURCE_MANIFEST,
    SCOPE_CORPUS,
    SCOPE_PILOT,
)
from map_ink_reference.errors import MalformedInput, PinMismatch
from map_ink_reference.io_utils import read_json, sha256_file
from map_ink_reference.pins import (
    CONFIG_PIN,
    INTRO_LATCH_CODE_PINS,
    MEDIA_PINS,
    require_pinned_geometry,
)
from map_ink_reference.seal import require_in_order

TOP_KEYS = frozenset(
    {
        "schema",
        "scope",
        "amendment4_commit",
        "tool_commit",
        "rule",
        "code_commit",
        "code_sha256",
        "config_sha256",
        "python_version",
        "pydantic_version",
        "recordings",
    }
)
PILOT_TOP_KEYS = TOP_KEYS | {"pilot_source_manifest_sha256"}
ENTRY_KEYS = frozenset(
    {
        "recording",
        "video_identity",
        "video_sha256",
        "vision_manifest_path",
        "vision_manifest_sha256",
        "stage_id",
        "battle_mode_id",
        "stage_latched_at",
        "battle_mode_latched_at",
        "intro_reading_count",
    }
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class StageIdentity:
    """Stage geometry key used by the point domain."""

    stage_id: str
    battle_mode_id: str | None


@dataclass(frozen=True)
class BlindInput:
    """Stage identities read from one sealed blind-input manifest."""

    identities: dict[str, StageIdentity]
    manifest_sha256: str
    entries: dict[str, dict[str, Any]]


def _require_str(value: Any, where: str, pattern: re.Pattern[str] | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise MalformedInput(f"{where} must be a non-empty string")
    if pattern is not None and not pattern.match(value):
        raise MalformedInput(f"{where} has an invalid format: {value!r}")
    return value


def _optional_time(value: Any, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MalformedInput(f"{where} must be a number or null")
    return float(value)


def validate_entry(entry: Any) -> dict[str, Any]:
    """One per-recording entry (exact keys, types, latch consistency)."""
    if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
        raise MalformedInput("blind-input entry must have exactly the sealed keys")
    rid = _require_str(entry["recording"], "recording")
    for key in ("video_identity", "vision_manifest_path"):
        _require_str(entry[key], f"{rid}.{key}")
    for key in ("video_sha256", "vision_manifest_sha256"):
        _require_str(entry[key], f"{rid}.{key}", _HEX64)
    for id_key, at_key in (
        ("stage_id", "stage_latched_at"),
        ("battle_mode_id", "battle_mode_latched_at"),
    ):
        if entry[id_key] is not None:
            _require_str(entry[id_key], f"{rid}.{id_key}")
        at = _optional_time(entry[at_key], f"{rid}.{at_key}")
        if (entry[id_key] is None) != (at is None):
            raise MalformedInput(f"{rid}: {id_key} and {at_key} must be set together")
    count = entry["intro_reading_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise MalformedInput(f"{rid}.intro_reading_count must be a non-negative int")
    return entry


def validate_blind_input(payload: Any, scope: str) -> list[dict[str, Any]]:
    """Validate an ``amendment3-blind-input/v1`` payload; return its entries."""
    expected = PILOT_TOP_KEYS if scope == SCOPE_PILOT else TOP_KEYS
    if not isinstance(payload, dict) or set(payload) != expected:
        raise MalformedInput(f"blind-input manifest must have exactly {sorted(expected)}")
    if payload["schema"] != BLIND_INPUT_SCHEMA or payload["rule"] != INTRO_LATCH_RULE:
        raise MalformedInput("blind-input manifest has the wrong schema or rule")
    if payload["scope"] != scope:
        raise MalformedInput(f"blind-input manifest scope is not {scope!r}")
    for key in ("amendment4_commit", "tool_commit", "code_commit"):
        _require_str(payload[key], key, _COMMIT)
    for key in ("python_version", "pydantic_version"):
        _require_str(payload[key], key)
    _require_str(payload["config_sha256"], "config_sha256", _HEX64)
    if scope == SCOPE_PILOT:
        _require_str(
            payload["pilot_source_manifest_sha256"],
            "pilot_source_manifest_sha256",
            _HEX64,
        )
    code = payload["code_sha256"]
    if not isinstance(code, dict) or set(code) != set(INTRO_LATCH_CODE_PINS):
        raise MalformedInput("code_sha256 must list exactly the pinned code files")
    entries = payload["recordings"]
    if not isinstance(entries, list) or not entries:
        raise MalformedInput("blind-input manifest has no recordings")
    entries = [validate_entry(e) for e in entries]
    names = [e["recording"] for e in entries]
    if len(set(names)) != len(names):
        raise MalformedInput("duplicate recording in blind-input manifest")
    return entries


def require_pinned_provenance(payload: Mapping[str, Any]) -> None:
    """Recorded code and configuration digests must equal the sealed pins."""
    if dict(payload["code_sha256"]) != INTRO_LATCH_CODE_PINS:
        raise PinMismatch("blind-input manifest code digests differ from the pins")
    if payload["config_sha256"] != CONFIG_PIN[1]:
        raise PinMismatch(
            "blind-input manifest configuration digest differs from the pin"
        )


def step2a_paths(records: Path) -> tuple[Path, Path, Path]:
    """Step 2a records in their required commit order."""
    return (
        records / PILOT_SOURCE_MANIFEST,
        records / PILOT_BLIND_INPUT_MANIFEST,
        records / BLIND_INPUT_MANIFEST,
    )


def require_step2a_sealed(repo: Path, records: Path) -> None:
    """Amendment 3 step 3 onwards: all three 2a records committed, in order."""
    require_in_order(repo, step2a_paths(records))


def _check_corpus_entry(repo: Path, entry: Mapping[str, Any]) -> StageIdentity:
    rid = entry["recording"]
    pins = MEDIA_PINS[rid]
    if (
        entry["vision_manifest_path"] != pins.vision_manifest_path
        or entry["vision_manifest_sha256"] != pins.vision_manifest_sha256
        or entry["video_sha256"] != pins.video_sha256
    ):
        raise PinMismatch(f"{rid}: blind-input entry differs from the Amendment 3 pins")
    if entry["stage_id"] is None:
        raise MalformedInput(f"{rid}: correctness-applicable recording has no stage_id")
    require_pinned_geometry(repo, entry["stage_id"], entry["battle_mode_id"])
    return StageIdentity(entry["stage_id"], entry["battle_mode_id"])


def load_corpus_blind_input(repo: Path, records: Path) -> BlindInput:
    """Sealed corpus blind-input manifest (after the step 2a order is verified)."""
    paths = step2a_paths(records)
    require_step2a_sealed(repo, records)
    payload = read_json(paths[2])
    entries = validate_blind_input(payload, SCOPE_CORPUS)
    require_pinned_provenance(payload)
    names = [e["recording"] for e in entries]
    if sorted(names) != sorted(CORRECTNESS_APPLICABLE):
        raise MalformedInput(
            "corpus blind-input must cover the correctness-applicable set"
        )
    by_rid = {e["recording"]: e for e in entries}
    identities = {
        rid: _check_corpus_entry(repo, by_rid[rid]) for rid in CORRECTNESS_APPLICABLE
    }
    return BlindInput(identities, sha256_file(paths[2]), by_rid)


def load_pilot_blind_input(repo: Path, records: Path) -> dict[str, Any]:
    """Sealed pilot blind-input manifest, bound to the sealed pilot-source manifest."""
    source, pilot, _ = step2a_paths(records)
    require_in_order(repo, (source, pilot))
    payload = read_json(pilot)
    validate_blind_input(payload, SCOPE_PILOT)
    require_pinned_provenance(payload)
    if payload["pilot_source_manifest_sha256"] != sha256_file(source):
        raise PinMismatch(
            "pilot blind-input manifest is bound to another source manifest"
        )
    return payload
