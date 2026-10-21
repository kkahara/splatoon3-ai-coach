"""Citation contract: every path the production prompt teaches must resolve.

Contract (see ``docs/CITATION_NAMESPACES.md``): a citation form taught by the
production prompt, or demonstrated by the view, must make the production
validator bind the cited path to the intended supplied fact and accept that
fact's value (``citation_contract_helpers.check``). Citations the prompt
presents as CoachInput paths must also name that CoachInput field with the
same value.

Current violations are pinned, not fixed: each failing case is a strict
``xfail`` whose reason is a ``KNOWN_VIOLATIONS`` id, so a behavior change that
fixes one shows up as XPASS and forces this table to be updated. Nothing here
aliases, strips prefixes or otherwise changes the validator.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import coaching_units_experiment_i as xi
import pytest
from citation_contract_helpers import (
    EXPERIMENT_I_FIXTURE,
    FixtureUnit,
    check,
    fixture_units,
    labelled_blocks,
    leaves,
    same_value,
    source_blocks_order_matches,
    walk,
)

from splatoon3_ai_coach.coach.claim_validation import (
    evidence_value_hits,
    resolve_evidence_reference,
)
from splatoon3_ai_coach.coach.coach import _USER_PREAMBLE_LLM_VIEW
from splatoon3_ai_coach.coach.prompts import load_system_prompt

KNOWN_VIOLATIONS = {
    "CV-NOT-PROJECTED": (
        "citation example names an engagement-unit field: death views project "
        "neither primary relations nor combat, and death-unit CoachInputs leave "
        "primary combat null"
    ),
    "CV-RELATIVE": "prose field reference omits its block; does not resolve as written",
    "CV-EXAMPLE-NAMESPACE": (
        "example listed under 'exact CoachInput path' is not a CoachInput path "
        "with the cited value (view path or CoachInput/view hybrid)"
    ),
    "CV-UNLABELLED": (
        "projected field has no source_path label naming its CoachInput path"
    ),
    "CV-FLATTENED": "label block flattens CoachInput nesting (observation.*, scenario.*)",
    "CV-RENAMED": "view renames the CoachInput field under the same label",
    "CV-MIXED-SOURCE": (
        "label names one CoachInput object but the block holds fields projected "
        "from another (durations, score_facts, zone_control_facts, map status)"
    ),
    "CV-SUPPORT-LABEL": (
        "supporting_evidence items reuse CoachInput labels for display strings: "
        "citations collide with real blocks, bind the display item, and R7 checks "
        "only the first block carrying the label"
    ),
    "CV-SINGLE-SEGMENT": (
        "the validator's path pattern needs a dot: top-level scalars, null blocks "
        "and single-segment labels cannot be cited"
    ),
    "CV-PROSE-VALUE": (
        "R7 rejects exact prose values (trailing period stripped from the cited "
        "text only; ' (' and ';' remarks cut)"
    ),
    "CV-OBJECT-LEAF": (
        "claim evidence path names an object; R7 cannot check the cited value"
    ),
    "CV-NOT-COACHINPUT": (
        "label-form path resolves in the view but names no CoachInput field"
    ),
    "CV-CONTRADICTS-COACHINPUT": (
        "label-form path resolves to a view value that differs from the CoachInput "
        "value at the same path"
    ),
    "CV-MAP-AWARENESS-OPEN": (
        "experiment-only map_awareness has no CoachInput home; which namespace "
        "owns it is an open design question"
    ),
}


def _xfail(case_id: str, violation: str | None, *args: Any) -> Any:
    if violation is None:
        return pytest.param(*args, id=case_id)
    assert violation in KNOWN_VIOLATIONS
    reason = f"{violation}: {KNOWN_VIOLATIONS[violation]}"
    return pytest.param(
        *args, id=case_id, marks=pytest.mark.xfail(strict=True, reason=reason)
    )


# --- what the production prompt teaches --------------------------------------------

SYSTEM_PROMPT = load_system_prompt(roster_evidence=False)
_TOKEN = re.compile(r"[A-Za-z_]\w*(?:\[[^\]\s]+\])?(?:\.[A-Za-z_]\w*(?:\[[^\]\s]+\])?)+")
NOT_PATHS = {"e.g": "abbreviation in prose"}


@dataclass(frozen=True)
class Taught:
    """One dotted path in the production prompt and the view fact it names."""

    path: str
    kind: str  # citation_example | field_reference
    intended: str | None  # view leaf; None when no producible view carries it
    resolve: str | None = None  # known violation of the resolution contract
    exact: str | None = None  # known violation of CoachInput exactness (examples)


TAUGHT = (
    Taught(
        "primary_context.death_episode.death_to_respawn.value",
        "citation_example",
        "death.death_to_respawn.value",
        exact="CV-EXAMPLE-NAMESPACE",
    ),
    Taught(
        "death.death_to_active_again.value",
        "citation_example",
        "death.death_to_active_again.value",
        exact="CV-EXAMPLE-NAMESPACE",
    ),
    Taught(
        "primary_context.relations.leads_to_death_episode_id",
        "citation_example",
        None,
        resolve="CV-NOT-PROJECTED",
    ),
    Taught(
        "primary_context.combat.trade_candidate",
        "citation_example",
        None,
        resolve="CV-NOT-PROJECTED",
        exact="CV-NOT-PROJECTED",
    ),
    Taught(
        "related[0].context.relations.leads_to_death_episode_id",
        "citation_example",
        "related[0].relations.leads_to_death_episode_id",
    ),
    Taught("special.state_at_anchor", "field_reference", "special.state_at_anchor"),
    Taught(
        "durations.time_since_previous_death.value",
        "field_reference",
        "timeline.time_since_previous_death.value",
    ),
    Taught(
        "importance.factor_thresholds",
        "field_reference",
        "importance.factor_thresholds",
    ),
    Taught("low_ink.at_death", "field_reference", "low_ink.at_death"),
    Taught("map.map_check_before_death", "field_reference", "map.map_check_before_death"),
    Taught(
        "map_check_before_death.status",
        "field_reference",
        "map.map_check_before_death.status",
        resolve="CV-RELATIVE",
    ),
    Taught("playback.status", "field_reference", "playback.status"),
    Taught(
        "relations.leads_to_death_episode_id",
        "field_reference",
        "related[0].relations.leads_to_death_episode_id",
        resolve="CV-RELATIVE",
    ),
    Taught("score.lookback_video_time", "field_reference", "score.lookback_video_time"),
    Taught("score.video_time", "field_reference", "score.video_time"),
)


def _applicable(intended: str | None) -> dict[str, FixtureUnit]:
    if intended is None:
        return {}
    return {k: u for k, u in fixture_units().items() if walk(u.view, intended)[0]}


def test_fixture_traversal_matches_validator() -> None:
    for unit in fixture_units().values():
        assert source_blocks_order_matches(unit.view)


def test_prompt_inventory_is_complete() -> None:
    """Every dotted path in the production prompt has a contract entry."""
    found = set(_TOKEN.findall(SYSTEM_PROMPT)) | set(
        _TOKEN.findall(_USER_PREAMBLE_LLM_VIEW)
    )
    assert found == {t.path for t in TAUGHT} | set(NOT_PATHS)


@pytest.mark.parametrize("case", [_xfail(t.path, t.resolve, t) for t in TAUGHT])
def test_taught_path_resolves_to_intended_fact(case: Taught) -> None:
    units = _applicable(case.intended)
    assert units, "no fixture view supplies the fact this path names"
    failures = {k: check(u.view, case.path, str(case.intended)) for k, u in units.items()}
    assert {k: r for k, r in failures.items() if r} == {}


EXAMPLES = [t for t in TAUGHT if t.kind == "citation_example"]


@pytest.mark.parametrize("case", [_xfail(t.path, t.exact, t) for t in EXAMPLES])
def test_citation_example_is_exact_coachinput_path(case: Taught) -> None:
    """The rule heading these examples says 'the exact CoachInput path and value'."""
    present = [u for u in fixture_units().values() if walk(u.coach_input, case.path)[0]]
    assert present, "not a CoachInput path in any fixture"
    if case.intended is None:
        return
    for unit in present:
        found, view_value = walk(unit.view, case.intended)
        if found:
            assert same_value(walk(unit.coach_input, case.path)[1], view_value)


# --- "prefer the exact CoachInput path": a sample of projected fields ----------------
#
# Hand-written (CoachInput path, view leaf it is projected to) pairs. The pairing
# itself is checked against the fixtures; a generated table is fix 2 / fix 4.

COACHINPUT_CASES: tuple[tuple[str, str, str | None], ...] = (
    ("primary_context.death_episode.respawn_reason", "death.respawn_reason", None),
    ("primary_context.death_episode.death_time", "death.death_time_video", "CV-RENAMED"),
    (
        "durations.death_to_respawn.value",
        "death.death_to_respawn.value",
        "CV-MIXED-SOURCE",
    ),
    (
        "durations.time_since_previous_death.value",
        "timeline.time_since_previous_death.value",
        None,
    ),
    ("game_clock_samples[label=death].video_time", "clock.video_time", None),
    (
        "game_clock_samples[label=death].observation.seconds_remaining",
        "clock.seconds_remaining",
        "CV-FLATTENED",
    ),
    (
        "game_clock_samples[label=death].observation.display",
        "clock.display",
        "CV-FLATTENED",
    ),
    (
        "primary_context.special.state_at_anchor",
        "special.state_at_anchor",
        "CV-UNLABELLED",
    ),
    (
        "primary_context.special.nearest_before_anchor.fill_fraction",
        "special.nearest_before_anchor.fill_fraction",
        None,
    ),
    (
        "primary_context.timeline.time_since_previous_death",
        "timeline.time_since_previous_death.video_seconds",
        "CV-UNLABELLED",
    ),
    (
        "primary_context.low_ink.intervals[0].start_time",
        "low_ink.intervals[0].start_time",
        None,
    ),
    ("primary_context.map.map_check_count", "map.map_check_count", None),
    (
        "primary_context.map.map_check_status",
        "map.map_check_before_death.status",
        "CV-MIXED-SOURCE",
    ),
    ("primary_scenario.start_time", "death.death_time_video", "CV-SUPPORT-LABEL"),
    (
        "primary_context.score.pre_death.ally_remaining",
        "score.ally_remaining",
        None,
    ),
    ("score_facts.remaining_diff", "score.remaining_diff", "CV-MIXED-SOURCE"),
    (
        "zone_control_facts.control_at_death",
        "zone_control.control_at_death",
        "CV-MIXED-SOURCE",
    ),
    ("playback_clock.status", "playback.status", None),
    (
        "related[0].context.relations.leads_to_death_episode_id",
        "related[0].relations.leads_to_death_episode_id",
        None,
    ),
    (
        "related[0].context.combat.splat_death_gap",
        "related[0].combat.splat_death_gap",
        None,
    ),
    ("related[0].scenario.start_time", "related[0].start_time", "CV-FLATTENED"),
)


def _paired(coachinput_path: str, intended: str) -> dict[str, FixtureUnit]:
    return {
        k: u
        for k, u in fixture_units().items()
        if walk(u.coach_input, coachinput_path)[0] and walk(u.view, intended)[0]
    }


@pytest.mark.parametrize(("coachinput_path", "intended", "_v"), COACHINPUT_CASES)
def test_coachinput_case_pairing_is_correct(
    coachinput_path: str, intended: str, _v: str | None
) -> None:
    """The table's pairing holds: same value at both ends wherever both exist."""
    units = _paired(coachinput_path, intended)
    assert units
    for unit in units.values():
        a = walk(unit.coach_input, coachinput_path)[1]
        assert same_value(a, walk(unit.view, intended)[1])


@pytest.mark.parametrize(
    ("coachinput_path", "intended"),
    [_xfail(p, v, p, i) for p, i, v in COACHINPUT_CASES],
)
def test_coachinput_path_resolves_to_projected_fact(
    coachinput_path: str, intended: str
) -> None:
    units = _paired(coachinput_path, intended)
    failures = {k: check(u.view, coachinput_path, intended) for k, u in units.items()}
    assert {k: r for k, r in failures.items() if r} == {}


# --- Experiment I postmortem rows -------------------------------------------------------


def _experiment_i_m_view() -> dict[str, Any]:
    unit = fixture_units()[EXPERIMENT_I_FIXTURE]
    facts = unit.entry["experiment_i_map_awareness"]["facts"]
    return xi.m_view(unit.view, facts)


MAP_AWARENESS_CASES = (
    ("map_awareness.seconds_back_in_play", "map_awareness.seconds_back_in_play", None),
    (
        "map_awareness.map_checks_since_back_in_play",
        "map_awareness.map_checks_since_back_in_play",
        None,
    ),
    (
        "primary_context.map_awareness.seconds_back_in_play",
        "map_awareness.seconds_back_in_play",
        "CV-MAP-AWARENESS-OPEN",
    ),
    (
        "primary_context.map_awareness.map_checks_since_back_in_play",
        "map_awareness.map_checks_since_back_in_play",
        "CV-MAP-AWARENESS-OPEN",
    ),
)


@pytest.mark.parametrize(
    ("cited", "intended"), [_xfail(c, v, c, i) for c, i, v in MAP_AWARENESS_CASES]
)
def test_experiment_i_map_awareness_citation(cited: str, intended: str) -> None:
    """Experiment-only arm-M view (production view + map_awareness block)."""
    assert check(_experiment_i_m_view(), cited, intended) is None


POSTMORTEM_CASES = (
    # output_050 / output_054 (arm M), output_055 (arm A)
    (
        "game_clock_samples[label=death].observation.display",
        "clock.display",
        "CV-FLATTENED",
    ),
    # output_029
    (
        "primary_context.special.state_at_anchor",
        "special.state_at_anchor",
        "CV-UNLABELLED",
    ),
    # output_088
    (
        "primary_context.timeline.time_since_previous_death.value",
        "timeline.time_since_previous_death.value",
        "CV-UNLABELLED",
    ),
)


@pytest.mark.parametrize(
    ("cited", "intended"), [_xfail(c, v, c, i) for c, i, v in POSTMORTEM_CASES]
)
def test_experiment_i_postmortem_citation(cited: str, intended: str) -> None:
    units = _applicable(intended)
    assert EXPERIMENT_I_FIXTURE in units
    failures = {k: check(u.view, cited, intended) for k, u in units.items()}
    assert {k: r for k, r in failures.items() if r} == {}


# --- general forms over every fixture view ------------------------------------------


def _shape(path: str) -> str:
    return re.sub(r"\[\d+\]", "[i]", path)


def _prose(value: Any) -> bool:
    text = value.rstrip() if isinstance(value, str) else ""
    return text.endswith(".") or " (" in text or ";" in text


def _classify(cited: str, reason: str, value: Any) -> str:
    if "." not in cited:
        return "CV-SINGLE-SEGMENT"
    if reason == "value_rejected" and _prose(value):
        return "CV-PROSE-VALUE"
    if reason == "binds_other_fact":
        return "CV-SUPPORT-LABEL"
    return f"unclassified:{reason}"


def view_form_failures() -> Counter[tuple[str, str]]:
    """'cite CoachLlmView paths': every leaf cited by its literal view path."""
    out: Counter[tuple[str, str]] = Counter()
    for unit in fixture_units().values():
        for path, value in leaves(unit.view):
            reason = check(unit.view, path, path)
            if reason:
                out[(_classify(path, reason, value), _shape(path))] += 1
    return out


def label_form_failures() -> Counter[tuple[str, str]]:
    """'prefer source_path values': every label, and label + each leaf below it."""
    out: Counter[tuple[str, str]] = Counter()
    for unit in fixture_units().values():
        for loc, block in labelled_blocks(unit.view):
            label = block["source_path"]
            cases = [(label, loc, block)] + [
                (f"{label}.{rel}", f"{loc}.{rel}", value)
                for rel, value in leaves(block)
                if rel != "source_path"
            ]
            for cited, intended, value in cases:
                reason = check(unit.view, cited, intended)
                if reason:
                    out[(_classify(cited, reason, value), _shape(cited))] += 1
    return out


def claim_path_failures() -> Counter[tuple[str, str]]:
    """``claim_basis[*].evidence_paths`` cited with the claim's own value."""
    out: Counter[tuple[str, str]] = Counter()
    for unit in fixture_units().values():
        for claim in unit.view["claim_basis"]:
            for path in claim["evidence_paths"]:
                value = claim["values"].get(path.split(".")[-1])
                reason = check(unit.view, path, path, value)
                leaf = walk(unit.view, path)[1]
                if reason is None and isinstance(leaf, dict | list):
                    reason = "object_leaf"
                if reason:
                    kind = "CV-OBJECT-LEAF" if reason == "object_leaf" else reason
                    out[(kind, _shape(path))] += 1
    return out


def _null_parent(coach_input: dict[str, Any], path: str) -> bool:
    """Some prefix of ``path`` is present and null in CoachInput (unavailable)."""
    segments = path.split(".")
    for end in range(1, len(segments)):
        found, value = walk(coach_input, ".".join(segments[:end]))
        if found and value is None:
            return True
        if not found:
            return False
    return False


def exactness_failures() -> Counter[tuple[str, str]]:
    """Label-form citations the validator accepts, checked against CoachInput."""
    out: Counter[tuple[str, str]] = Counter()
    for unit in fixture_units().values():
        for loc, block in labelled_blocks(unit.view):
            label = block["source_path"]
            for rel, value in leaves(block):
                cited = f"{label}.{rel}"
                if rel == "source_path" or check(unit.view, cited, f"{loc}.{rel}"):
                    continue
                found, ci_value = walk(unit.coach_input, cited)
                if not found and value is None and _null_parent(unit.coach_input, cited):
                    continue
                if not found:
                    out[("CV-NOT-COACHINPUT", _shape(cited))] += 1
                elif not same_value(ci_value, value):
                    out[("CV-CONTRADICTS-COACHINPUT", _shape(cited))] += 1
    return out


FORMS: dict[str, Callable[[], Counter[tuple[str, str]]]] = {
    "view_form": view_form_failures,
    "label_form": label_form_failures,
    "claim_paths": claim_path_failures,
    "label_exactness": exactness_failures,
}
FORM_VIOLATIONS: dict[str, tuple[str, ...]] = {
    "view_form": ("CV-SINGLE-SEGMENT", "CV-PROSE-VALUE"),
    "label_form": ("CV-SINGLE-SEGMENT", "CV-SUPPORT-LABEL", "CV-PROSE-VALUE"),
    "claim_paths": ("CV-OBJECT-LEAF",),
    "label_exactness": ("CV-NOT-COACHINPUT", "CV-CONTRADICTS-COACHINPUT"),
}


@pytest.mark.parametrize("form", sorted(FORMS))
def test_form_failures_are_all_known(form: str) -> None:
    """No failure outside the pinned violation classes for this form."""
    classes = {kind for kind, _ in FORMS[form]()}
    assert classes <= set(FORM_VIOLATIONS[form])


@pytest.mark.parametrize(
    ("form", "violation"),
    [
        _xfail(f"{form}-{v}", v, form, v)
        for form, violations in sorted(FORM_VIOLATIONS.items())
        for v in violations
    ],
)
def test_form_has_no_violation(form: str, violation: str) -> None:
    hits = {path: n for (kind, path), n in FORMS[form]().items() if kind == violation}
    assert hits == {}


def _strict(violation: str) -> Any:
    reason = f"{violation}: {KNOWN_VIOLATIONS[violation]}"
    return pytest.mark.xfail(strict=True, reason=reason)


def _rejected(view: dict[str, Any], text: str) -> bool:
    resolution = resolve_evidence_reference(view, text)
    return resolution["kind"] == "unresolved" or bool(
        evidence_value_hits(view, [resolution])
    )


SUPPORT_LABEL_WRONG_VALUE = (
    'primary_context.special.nearest_before_anchor.value = "not a real value"'
)


@_strict("CV-SUPPORT-LABEL")
def test_wrong_value_through_duplicated_label_is_rejected() -> None:
    """The citation resolves via ``supporting_evidence``; R7 walks the first
    block with the label (``special.nearest_before_anchor``) and skips it."""
    assert _rejected(
        fixture_units()[EXPERIMENT_I_FIXTURE].view, SUPPORT_LABEL_WRONG_VALUE
    )


CONTRADICTION = "primary_context.death_episode.death_to_respawn = null"


def _contradiction_units() -> list[FixtureUnit]:
    """Units whose CoachInput has the duration but whose view holds null."""
    path = "primary_context.death_episode.death_to_respawn"
    return [
        u
        for u in fixture_units().values()
        if walk(u.coach_input, path)[1] is not None
        and walk(u.view, "death.death_to_respawn") == (True, None)
    ]


def test_contradiction_is_exercised() -> None:
    assert _contradiction_units()


@_strict("CV-CONTRADICTS-COACHINPUT")
def test_contradicting_label_citation_is_rejected() -> None:
    """``= null`` contradicts the CoachInput value at that path."""
    assert all(_rejected(u.view, CONTRADICTION) for u in _contradiction_units())


# --- projection guard ----------------------------------------------------------------

UNLABELLED_BLOCKS = {
    "schema_version": "view metadata, not evidence",
    "roster_evidence": "capability flag",
    "unit": "unit identity (candidate / scenario ids, video_time)",
    "importance": "ranking context and factor thresholds; view-native only",
    "claim_basis": "authorized claims; their evidence_paths are view paths",
    "undetermined": "forbidden topics; not evidence",
    "evidence_limits": "limit statements; not facts",
    "special": "container: children labelled; state_at_anchor is not (CV-UNLABELLED)",
}


def _labelled(value: Any) -> bool:
    if isinstance(value, dict):
        return isinstance(value.get("source_path"), str)
    return isinstance(value, list) and all(_labelled(item) for item in value)


def test_every_top_level_block_is_labelled_or_pinned() -> None:
    """A new or reshaped projection cannot become uncitable silently."""
    unlabelled = {
        key
        for unit in fixture_units().values()
        for key, value in unit.view.items()
        if value not in (None, [], {}) and not _labelled(value)
    }
    assert unlabelled == set(UNLABELLED_BLOCKS)


def test_every_known_violation_is_exercised() -> None:
    used = {t.resolve for t in TAUGHT} | {t.exact for t in TAUGHT}
    used |= {v for *_, v in COACHINPUT_CASES} | {v for *_, v in MAP_AWARENESS_CASES}
    used |= {v for *_, v in POSTMORTEM_CASES}
    used |= {v for violations in FORM_VIOLATIONS.values() for v in violations}
    assert used - {None} == set(KNOWN_VIOLATIONS)
