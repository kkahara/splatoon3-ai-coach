"""Synthetic tests for operator-only Arm R declarations."""

from __future__ import annotations

import pytest
from map_ink_reference.errors import MalformedInput
from map_ink_reference.f11.eligibility import (
    INDEPENDENCE,
    require_declaration,
    require_hash_match,
)


def _entry() -> dict[str, str]:
    return {
        "analysis_dir": "analysis/example",
        "video_path": "/declared/example.mov",
        "video_sha256": "abc",
        "video_identity": "identity",
        "vision_manifest_sha256": "manifest",
        "language": "ja",
        "acquisition_session": "session-1",
        "authorization": (
            "development-independent media for Amendment 11 Arm R; "
            "not corpus, pilot or held-out media"
        ),
        "development_independence": INDEPENDENCE,
        "declared_by": "operator",
    }


def test_a_complete_declaration_is_accepted() -> None:
    require_declaration(_entry())


def test_independence_is_not_inferred() -> None:
    entry = _entry()
    entry["development_independence"] = ""
    with pytest.raises(MalformedInput, match="missing"):
        require_declaration(entry)


def test_unavailable_or_mismatched_media_is_ineligible() -> None:
    with pytest.raises(MalformedInput, match="hash"):
        require_hash_match(
            expected_sha256="abc",
            actual_sha256="def",
            expected_identity="identity",
            actual_identity="identity",
            manifest_identity="identity",
            expected_manifest_sha256="manifest",
            actual_manifest_sha256="manifest",
        )


def test_prohibited_directories_are_rejected() -> None:
    entry = _entry()
    entry["analysis_dir"] = "analysis/_private"
    with pytest.raises(MalformedInput, match="not an Arm R"):
        require_declaration(entry)
