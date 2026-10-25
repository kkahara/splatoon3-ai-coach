"""Stage 2 trigger record (Amendment 4 ruling 1; step 6a).

``stage2_trigger.json`` contains only the SHA-256 of the Stage 1 comparison
record and the sorted ids of the Stage 1 INCONCLUSIVE recordings. It is the
only comparison-derived input any blind tool may accept: blind tools verify
that it is sealed and require the Stage 2 recording set to equal it exactly.
This module reads nothing else.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from map_ink_reference.constants import CORRECTNESS_APPLICABLE
from map_ink_reference.errors import MalformedInput
from map_ink_reference.io_utils import read_json
from map_ink_reference.seal import require_sealed

STAGE2_TRIGGER = "stage2_trigger.json"
TRIGGER_KEYS = frozenset({"stage1_comparison_sha256", "recordings"})
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def trigger_payload(digest: str, recordings: Sequence[str]) -> dict:
    """The exact sealed content: the Stage 1 record digest and sorted ids."""
    payload = {
        "stage1_comparison_sha256": digest,
        "recordings": sorted(recordings),
    }
    validate_trigger(payload)
    return payload


def validate_trigger(payload: Any) -> list[str]:
    """Exact keys, a 64-hex digest and a sorted, non-empty, applicable id list."""
    if not isinstance(payload, dict) or set(payload) != TRIGGER_KEYS:
        raise MalformedInput(f"Stage 2 trigger must have exactly {sorted(TRIGGER_KEYS)}")
    digest = payload["stage1_comparison_sha256"]
    if not isinstance(digest, str) or not _HEX64.match(digest):
        raise MalformedInput("Stage 2 trigger digest must be 64 lowercase hex digits")
    ids = payload["recordings"]
    if not isinstance(ids, list) or not ids or ids != sorted(set(ids)):
        raise MalformedInput(
            "Stage 2 trigger ids must be a sorted, unique, non-empty list"
        )
    if not set(ids) <= set(CORRECTNESS_APPLICABLE):
        raise MalformedInput("Stage 2 trigger lists a recording outside the corpus")
    return list(ids)


def trigger_path(records: Path) -> Path:
    """Committed trigger location."""
    return records / STAGE2_TRIGGER


def read_sealed_trigger(repo: Path, records: Path) -> dict[str, Any]:
    """The sealed trigger payload (validated)."""
    path = trigger_path(records)
    require_sealed(repo, path)
    payload = read_json(path)
    validate_trigger(payload)
    return payload


def require_stage2_recordings(
    repo: Path, records: Path, recordings: Sequence[str]
) -> list[str]:
    """Stage 2 recordings must equal the sealed trigger exactly."""
    expected = read_sealed_trigger(repo, records)["recordings"]
    if len(set(recordings)) != len(recordings) or sorted(recordings) != expected:
        raise MalformedInput("Stage 2 recordings must equal the sealed Stage 2 trigger")
    return expected
