"""Literal CoachLlmView (N2) citations: validator correctness regressions.

N2 is the model-facing citation namespace (``docs/CITATION_NAMESPACES.md``):
a citation names a supplied fact by its literal view path and the cited value
must match the value at that path. Each test names the Fix 1 finding it
reproduces and its classification:

* correctness: a wrong value could pass, or a correct one was rejected (fixed);
* syntax: the citation grammar could not express a permitted path (fixed);
* legacy: N1 / ``source_path`` label behavior, irrelevant under N2, kept and
  characterized until the label cleanup;
* design: an open question, pinned as a strict ``xfail``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from splatoon3_ai_coach.coach.claim_validation import (
    evidence_value_hits,
    resolve_evidence_reference,
    resolve_view_path,
)

CLOCKED = {
    "video_seconds": 8.5,
    "value": 8.5,
    "clock": "video",
    "method": "video_elapsed",
}
VIEW: dict[str, Any] = {
    "schema_version": 3,
    "roster_evidence": False,
    "low_ink": None,
    "death": {
        "death_to_respawn": None,
        "death_to_active_again": CLOCKED,
        "source_path": "primary_context.death_episode",
    },
    "special": {
        "state_at_anchor": "not_ready",
        "nearest_before_anchor": {
            "fill_fraction": 0.43,
            "source_path": "primary_context.special.nearest_before_anchor",
        },
    },
    "map": {
        "map_check_before_death": None,
        "map_check_count": 0,
        "source_path": "primary_context.map",
    },
    "evidence_limits": [
        {"code": "x", "statement": "Map checks are counted; the respawn map is not."}
    ],
    "supporting_evidence": [
        {
            "label": "Special",
            "value": "not ready, fill=0.43 (last ready 5.0s before)",
            "source_path": "primary_context.special.nearest_before_anchor",
        }
    ],
}


def _r7(text: str, view: dict[str, Any] = VIEW) -> list[dict[str, Any]]:
    resolution = resolve_evidence_reference(view, text)
    assert resolution["kind"] != "unresolved", resolution
    return evidence_value_hits(view, [resolution])


# --- correctness: R7 checks the location resolution used (CV-R7-LOCATION) ----------


def test_duplicate_label_wrong_value_is_rejected() -> None:
    """Resolution binds ``supporting_evidence[0]``; R7 used to walk the first
    block with the label (``special.nearest_before_anchor``), find no
    ``value`` and skip the check."""
    text = 'primary_context.special.nearest_before_anchor.value = "full"'
    resolution = resolve_evidence_reference(VIEW, text)
    assert resolution["kind"] == "source_path_ref"
    assert resolution["location"] == "supporting_evidence[0].value"
    assert len(evidence_value_hits(VIEW, [resolution])) == 1


def test_duplicate_label_correct_value_passes() -> None:
    text = (
        "primary_context.special.nearest_before_anchor.value = "
        '"not ready, fill=0.43 (last ready 5.0s before)"'
    )
    assert _r7(text) == []


# --- legacy: label paths are not CoachInput truth (CV-CONTRADICTS-COACHINPUT) -------


@pytest.mark.parametrize(
    ("legacy", "literal", "wrong"),
    [
        (
            "primary_context.death_episode.death_to_respawn = null",
            "death.death_to_respawn = null",
            "death.death_to_respawn = 7.0",
        ),
        (
            "primary_context.map.map_check_before_death = null",
            "map.map_check_before_death = null",
            "map.map_check_before_death = true",
        ),
    ],
)
def test_contradiction_is_legacy_label_form(
    legacy: str, literal: str, wrong: str
) -> None:
    """CoachInput holds 7.0 / ``true`` here, the view holds null (playback
    unverified / no status). The legacy label form accepts null because it
    walks the view block; under N2 the literal path states exactly the
    supplied fact, and a value the view does not hold is rejected."""
    resolution = resolve_evidence_reference(VIEW, legacy)
    assert resolution["kind"] == "source_path_ref"
    assert _r7(legacy) == []
    assert resolve_evidence_reference(VIEW, literal)["kind"] == "view_native"
    assert _r7(literal) == []
    assert len(_r7(wrong)) == 1


# --- correctness: object-valued evidence, leaf only (CV-OBJECT-LEAF) ---------------


def _checks(text: str) -> list[str]:
    return [hit["check"] for hit in _r7(text)]


def test_scalar_leaf_of_object_is_value_checked() -> None:
    assert _checks("death.death_to_active_again.value = 8.5") == []
    assert _checks("death.death_to_active_again.value = 9.9") == ["value_mismatch"]


@pytest.mark.parametrize(
    "text",
    [
        "death.death_to_active_again = 8.5",
        "death.death_to_active_again = 999",
        'death.death_to_active_again = "8.5s"',
        "death.death_to_active_again: out of play for 8.5s",
        "death.death_to_active_again = null",
        f"death.death_to_active_again = {json.dumps(CLOCKED)}",
        'evidence_limits = [{"code": "x"}]',
        "death = {}",
    ],
)
def test_value_asserted_on_object_path_fails(text: str) -> None:
    """The literal path holds an object: no implicit ``.value``, no claim
    values, no serialization rule; any asserted value fails explicitly."""
    assert _checks(text) == ["object_path_value"]


def test_legacy_label_to_object_cannot_bypass() -> None:
    text = "primary_context.special.nearest_before_anchor = 0.43"
    resolution = resolve_evidence_reference(VIEW, text)
    assert resolution["location"] == "special.nearest_before_anchor"
    assert [h["check"] for h in evidence_value_hits(VIEW, [resolution])] == [
        "object_path_value"
    ]


CLOCK_LABEL = "game_clock_samples[label=death].observation.seconds_remaining"
SUPPORT_ONLY: dict[str, Any] = {
    "clock": {"seconds_remaining": 23, "source_path": "game_clock_samples[label=death]"},
    "supporting_evidence": [
        {"label": "Game clock", "value": "0:23", "source_path": CLOCK_LABEL}
    ],
}


@pytest.mark.parametrize("cited", ["23", "999"])
def test_legacy_label_of_a_display_item_asserts_on_an_object(cited: str) -> None:
    """Legacy (CV-SUPPORT-LABEL / CV-FLATTENED): the whole label is held only
    by a ``supporting_evidence`` item, so the label form binds that object.
    Correct or not, the value is no longer skipped; the N2 citation is
    ``clock.seconds_remaining``."""
    resolution = resolve_evidence_reference(SUPPORT_ONLY, f"{CLOCK_LABEL} = {cited}")
    assert resolution["location"] == "supporting_evidence[0]"
    hits = evidence_value_hits(SUPPORT_ONLY, [resolution])
    assert [h["check"] for h in hits] == ["object_path_value"]
    assert _r7("clock.seconds_remaining = 23", SUPPORT_ONLY) == []


@pytest.mark.parametrize(
    "text",
    [
        "death.death_to_active_again",
        "see death.death_to_active_again",
        "death.death_to_active_again was 8.5s",
    ],
)
def test_object_path_reference_without_value_is_allowed(text: str) -> None:
    """No ``=`` / ``:`` right after the path: a reference, not an assertion."""
    resolution = resolve_evidence_reference(VIEW, text)
    assert resolution["kind"] == "view_native"
    assert resolution["location"] == "death.death_to_active_again"
    assert evidence_value_hits(VIEW, [resolution]) == []


def test_single_segment_object_reference_is_not_expressible() -> None:
    """CV-BARE-OBJECT-REF (syntax, open; grammar not expanded): an unanchored
    path needs a dot, so a bare ``death`` reference is malformed."""
    resolution = resolve_evidence_reference(VIEW, "death")
    assert (resolution["kind"], resolution["reason"]) == ("unresolved", "malformed")


# --- correctness: prose values (CV-PROSE-VALUE) ------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        'supporting_evidence[0].value = "not ready, fill=0.43 (last ready 5.0s before)"',
        "supporting_evidence[0].value = not ready, fill=0.43 (last ready 5.0s before)",
        'evidence_limits[0].statement = "Map checks are counted; '
        'the respawn map is not."',
        "evidence_limits[0].statement = Map checks are counted; the respawn map is not.",
        "evidence_limits[0].statement = Map checks are counted; the respawn map is not",
    ],
)
def test_correct_prose_value_is_accepted(text: str) -> None:
    assert _r7(text) == []


@pytest.mark.parametrize(
    "text",
    [
        'supporting_evidence[0].value = "not ready, fill=0.43 (last ready 9.0s before)"',
        "supporting_evidence[0].value = not ready, fill=0.43 (last ready 9.0s before)",
        'supporting_evidence[0].value = "not ready, fill=0.43"',
        "evidence_limits[0].statement = Map checks are counted.",
    ],
)
def test_wrong_prose_value_is_still_rejected(text: str) -> None:
    assert len(_r7(text)) == 1


def test_scalar_with_trailing_remark_still_accepted() -> None:
    assert _r7("map.map_check_count = 0 (observed)") == []
    assert _r7("special.nearest_before_anchor.fill_fraction = 0.43; visible gauge") == []


# --- syntax: top-level and single-segment paths (CV-SINGLE-SEGMENT) ----------------


@pytest.mark.parametrize(
    ("text", "wrong"),
    [
        ("roster_evidence = false", "roster_evidence = true"),
        ("schema_version = 3", "schema_version = 4"),
        ("low_ink = null", "low_ink = true"),
    ],
)
def test_top_level_scalar_paths_are_citable(text: str, wrong: str) -> None:
    resolution = resolve_evidence_reference(VIEW, text)
    assert resolution["kind"] == "view_native"
    assert resolution["location"] == text.split(" ")[0]
    assert _r7(text) == []
    assert len(_r7(wrong)) == 1


@pytest.mark.parametrize(
    "text",
    [
        "Note: the map was not checked",
        "durations = 12.0",
        "primary_context = {}",
    ],
)
def test_single_segment_must_be_a_view_key(text: str) -> None:
    """Prose and legacy single-segment labels stay unresolved (not widened)."""
    assert resolve_evidence_reference(VIEW, text) == {
        "text": text,
        "kind": "unresolved",
        "reason": "malformed",
    }


def test_dotted_path_later_in_entry_still_found() -> None:
    text = "Special gauge: special.state_at_anchor = not_ready"
    resolution = resolve_evidence_reference(VIEW, text)
    assert resolution["path"] == "special.state_at_anchor"
    assert _r7(text) == []


# --- N2 independence from labels ---------------------------------------------------

ADVERSARIAL: dict[str, Any] = {
    "clock": {"display": "0:20", "source_path": "game_clock_samples[label=death]"},
    "decoy": {"display": "9:99", "source_path": "clock"},
}


def test_literal_path_is_never_redirected_by_a_label() -> None:
    """``decoy`` is labelled ``clock``; the literal path ``clock.display`` must
    bind ``clock.display`` and R7 must check that value."""
    resolution = resolve_evidence_reference(ADVERSARIAL, "clock.display = 0:20")
    assert resolution["kind"] == "view_native"
    assert resolution["location"] == "clock.display"
    assert _r7("clock.display = 0:20", ADVERSARIAL) == []
    assert len(_r7("clock.display = 9:99", ADVERSARIAL)) == 1


def test_resolve_view_path_ignores_labels() -> None:
    assert resolve_view_path(ADVERSARIAL, "clock.display") == (True, "0:20")
    assert resolve_view_path(VIEW, "primary_context.death_episode.death_to_respawn") == (
        False,
        None,
    )


def test_label_naming_another_view_path_cannot_capture_citations() -> None:
    """``decoy`` carries ``display``; a mistyped literal ``clock.<x>`` or a bare
    ``clock`` must not be resolved inside ``decoy`` through its label."""
    view = {**ADVERSARIAL, "decoy": {**ADVERSARIAL["decoy"], "extra": 1}}
    assert resolve_evidence_reference(view, "clock.extra = 1")["kind"] == "unresolved"
    bare = resolve_evidence_reference(view, "clock = {}")
    assert bare["kind"] == "view_native"
    assert bare["location"] == "clock"


def test_legacy_label_only_when_literal_path_is_absent() -> None:
    legacy = resolve_evidence_reference(ADVERSARIAL, "clock.missing = 1")
    assert legacy["kind"] == "unresolved"
    via_label = resolve_evidence_reference(
        ADVERSARIAL, "game_clock_samples[label=death].display = 0:20"
    )
    assert via_label["kind"] == "source_path_ref"
    assert via_label["location"] == "clock.display"
