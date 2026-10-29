"""Arm R source-entry checks. Independence is never inferred."""

from __future__ import annotations

from collections.abc import Mapping

from map_ink_reference.errors import MalformedInput

AUTHORIZATION = (
    "development-independent media for Amendment 11 Arm R; "
    "not corpus, pilot or held-out media"
)
INDEPENDENCE = (
    "This recording was not used to develop, debug, tune, select, visually assess, "
    "or make design decisions about team-colour calibration, A1, or map ink."
)
_FIELDS = (
    "analysis_dir",
    "video_path",
    "video_sha256",
    "video_identity",
    "vision_manifest_sha256",
    "language",
    "acquisition_session",
    "authorization",
    "development_independence",
    "declared_by",
)
_PROHIBITED_DIRS = frozenset({"alignment-v3-heldout", "review_validation"})


def require_declaration(entry: Mapping[str, str]) -> None:
    """Accept one operator entry, or stop. Missing independence excludes it."""
    missing = [field for field in _FIELDS if not entry.get(field)]
    if missing:
        raise MalformedInput(f"Arm R entry is missing {', '.join(missing)}")
    if entry["authorization"] != AUTHORIZATION:
        raise MalformedInput("Arm R authorization literal does not match")
    if entry["development_independence"] != INDEPENDENCE:
        raise MalformedInput("development independence was not declared")
    if entry["language"] not in {"en", "ja"}:
        raise MalformedInput("analysis language must be en or ja")
    _require_analysis_dir(entry["analysis_dir"])


def require_hash_match(
    *,
    expected_sha256: str,
    actual_sha256: str,
    expected_identity: str,
    actual_identity: str,
    manifest_identity: str,
    expected_manifest_sha256: str,
    actual_manifest_sha256: str,
) -> None:
    """Stop unless the declared video and manifest hashes match."""
    if actual_sha256 != expected_sha256 or actual_identity != expected_identity:
        raise MalformedInput("declared video hash or identity does not match")
    if manifest_identity != expected_identity:
        raise MalformedInput("manifest video_identity does not match the declaration")
    if actual_manifest_sha256 != expected_manifest_sha256:
        raise MalformedInput("vision manifest hash does not match the declaration")


def _require_analysis_dir(analysis_dir: str) -> None:
    parts = analysis_dir.split("/")
    if len(parts) != 2 or parts[0] != "analysis" or not parts[1]:
        raise MalformedInput("analysis_dir must be directly under analysis/")
    name = parts[1]
    if name.startswith("_") or name in _PROHIBITED_DIRS:
        raise MalformedInput(f"{analysis_dir} is not an Arm R source directory")
