"""Citation contract: measured over every citation form the prompt teaches.

Decision (``docs/CITATION_NAMESPACES.md``): the model-facing citation
namespace is N2, the literal CoachLlmView path. A citation names a supplied
fact by its view path, and the cited value must equal the value at that path.
CoachInput (N1) paths are provenance only; ``source_path`` label resolution is
legacy, kept until the prompt rewrite and label cleanup.

Fix 1 (``db5fa8c``) measured the forms the production prompt teaches today,
including N1 / hybrid examples. Those measurements are kept as evidence for
the decision: each violation in ``VIOLATIONS`` has a classification and a
status. ``open`` violations are strict ``xfail`` cases (an XPASS forces the
table to be updated); ``fixed`` ones are asserted absent; ``HISTORY_DB5FA8C``
records the counts measured before the validator-correctness step.
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
    render,
    same_value,
    source_blocks_order_matches,
    walk,
)

from splatoon3_ai_coach.coach.claim_validation import (
    evidence_value_hits,
    resolve_evidence_reference,
    resolve_view_path,
)
from splatoon3_ai_coach.coach.coach import _USER_PREAMBLE_LLM_VIEW
from splatoon3_ai_coach.coach.prompts import load_system_prompt

CORRECTNESS = "correctness"  # wrong value could pass / correct value rejected
SYNTAX = "syntax"  # the grammar could not express a permitted citation
LEGACY = "legacy"  # N1 / label behavior; irrelevant under N2, cleanup pending
DESIGN = "design"  # open question; nothing implemented
CONTENT = "content"  # claim_basis / prompt content to migrate; validator correct


@dataclass(frozen=True)
class Violation:
    """One contract violation found by Fix 1 or the correctness step."""

    classification: str
    status: str  # open | fixed
    description: str


VIOLATIONS: dict[str, Violation] = {
    "CV-NOT-PROJECTED": Violation(
        LEGACY,
        "open",
        "N1 citation example names an engagement-unit field: death views project "
        "neither primary relations nor combat, and death-unit CoachInputs leave "
        "primary combat null",
    ),
    "CV-RELATIVE": Violation(
        SYNTAX,
        "open",
        "prompt prose field reference omits its block; not a literal view path "
        "(prompt rewrite)",
    ),
    "CV-EXAMPLE-NAMESPACE": Violation(
        LEGACY,
        "open",
        "example listed under 'exact CoachInput path' is not a CoachInput path "
        "with the cited value (view path or CoachInput/view hybrid)",
    ),
    "CV-UNLABELLED": Violation(
        LEGACY, "open", "projected field has no source_path label naming its N1 path"
    ),
    "CV-FLATTENED": Violation(
        LEGACY, "open", "label block flattens N1 nesting (observation.*, scenario.*)"
    ),
    "CV-RENAMED": Violation(
        LEGACY, "open", "view renames the N1 field under the same label"
    ),
    "CV-MIXED-SOURCE": Violation(
        LEGACY,
        "open",
        "label names one N1 object but the block holds fields projected from "
        "another (durations, score_facts, zone_control_facts, map status)",
    ),
    "CV-SUPPORT-LABEL": Violation(
        LEGACY,
        "open",
        "supporting_evidence items reuse N1 labels for display strings: one label "
        "string names two facts; the legacy form binds the first that walks",
    ),
    "CV-R7-LOCATION": Violation(
        CORRECTNESS,
        "fixed",
        "R7 walked the first block carrying the label, not the block resolution "
        "used, so a wrong value cited through a duplicated label went unchecked",
    ),
    "CV-BARE-LABEL": Violation(
        LEGACY,
        "open",
        "single-segment legacy labels (durations, playback_clock, "
        "game_clock_samples[label=death]) are not citable",
    ),
    "CV-SINGLE-SEGMENT": Violation(
        SYNTAX,
        "fixed",
        "the path pattern needed a dot: top-level scalars and null blocks could "
        "not be cited",
    ),
    "CV-PROSE-VALUE": Violation(
        CORRECTNESS,
        "fixed",
        "R7 rejected exact prose values (trailing period stripped from the cited "
        "text only; quoted values cut at ' (' and ';')",
    ),
    "CV-OBJECT-LEAF": Violation(
        CORRECTNESS,
        "fixed",
        "R7 skipped any value asserted on an object path, so a wrong scalar "
        "passed unchecked (design decided: leaf only; now object_path_value)",
    ),
    "CV-CLAIM-OBJECT-PATH": Violation(
        CONTENT,
        "open",
        "claim_basis evidence path names an object while the claim asserts "
        "scalars at its leaves; citing the path with the claim's value is "
        "rejected (claim_basis content to move to the leaves)",
    ),
    "CV-BARE-OBJECT-REF": Violation(
        SYNTAX,
        "open",
        "a path-only reference to a single-segment object path (related[0]) is "
        "malformed: unanchored paths need a dot (grammar not expanded)",
    ),
    "CV-NOT-COACHINPUT": Violation(
        LEGACY, "open", "label-form path resolves in the view but names no N1 field"
    ),
    "CV-CONTRADICTS-COACHINPUT": Violation(
        LEGACY,
        "open",
        "label-form path resolves to a view value that differs from the N1 value "
        "at the same path",
    ),
    "CV-MAP-AWARENESS-OPEN": Violation(
        DESIGN,
        "open",
        "experiment-only map_awareness has no N1 home; its ownership is open",
    ),
}
KNOWN_VIOLATIONS = {k: v.description for k, v in VIOLATIONS.items()}

# Measured at db5fa8c (Fix 1), before the validator-correctness step:
# (form, violation) -> failing citations over the 26 fixture views.
HISTORY_DB5FA8C: dict[tuple[str, str], int] = {
    ("view_form", "CV-SINGLE-SEGMENT"): 170,
    ("view_form", "CV-PROSE-VALUE"): 437,
    ("label_form", "CV-SINGLE-SEGMENT"): 75,
    ("label_form", "CV-SUPPORT-LABEL"): 99,
    ("label_form", "CV-PROSE-VALUE"): 5,
    ("claim_paths", "CV-OBJECT-LEAF"): 6,
    ("label_exactness", "CV-NOT-COACHINPUT"): 893,
    ("label_exactness", "CV-CONTRADICTS-COACHINPUT"): 61,
}


def _xfail(case_id: str, violation: str | None, *args: Any) -> Any:
    if violation is None or VIOLATIONS[violation].status == "fixed":
        return pytest.param(*args, id=case_id)
    found = VIOLATIONS[violation]
    reason = f"{violation} ({found.classification}): {found.description}"
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


def _classify(
    form: str, cited: str, reason: str, value: Any, in_view: bool = False
) -> str:
    if "." not in cited and in_view and isinstance(value, dict | list):
        return "CV-BARE-OBJECT-REF"
    if "." not in cited:
        return "CV-BARE-LABEL" if form == "label_form" else "CV-SINGLE-SEGMENT"
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
                out[(_classify("view_form", path, reason, value), _shape(path))] += 1
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
                    in_view = resolve_view_path(unit.view, cited)[0]
                    kind = _classify("label_form", cited, reason, value, in_view)
                    out[(kind, _shape(cited))] += 1
    return out


def _object_value_check(view: dict[str, Any], text: str) -> str | None:
    resolution = resolve_evidence_reference(view, text)
    hits = evidence_value_hits(view, [resolution])
    return hits[0]["check"] if hits else None


def claim_path_failures() -> Counter[tuple[str, str]]:
    """``claim_basis[*].evidence_paths`` cited with the claim's own value."""
    out: Counter[tuple[str, str]] = Counter()
    for unit in fixture_units().values():
        for claim in unit.view["claim_basis"]:
            for path in claim["evidence_paths"]:
                value = claim["values"].get(path.split(".")[-1])
                if isinstance(walk(unit.view, path)[1], dict | list):
                    text = f"{path} = {render(value)}"
                    rejected = _object_value_check(unit.view, text)
                    kind = "CV-CLAIM-OBJECT-PATH"
                    if rejected != "object_path_value":
                        kind = "CV-OBJECT-LEAF"
                    out[(kind, _shape(path))] += 1
                elif reason := check(unit.view, path, path, value):
                    out[(reason, _shape(path))] += 1
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
    "label_form": (
        "CV-BARE-LABEL",
        "CV-BARE-OBJECT-REF",
        "CV-SUPPORT-LABEL",
        "CV-PROSE-VALUE",
    ),
    "claim_paths": ("CV-OBJECT-LEAF", "CV-CLAIM-OBJECT-PATH"),
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
    found = VIOLATIONS[violation]
    assert found.status == "open"
    reason = f"{violation} ({found.classification}): {found.description}"
    return pytest.mark.xfail(strict=True, reason=reason)


def _rejected(view: dict[str, Any], text: str) -> bool:
    resolution = resolve_evidence_reference(view, text)
    return resolution["kind"] == "unresolved" or bool(
        evidence_value_hits(view, [resolution])
    )


SUPPORT_LABEL_WRONG_VALUE = (
    'primary_context.special.nearest_before_anchor.value = "not a real value"'
)


def test_wrong_value_through_duplicated_label_is_rejected() -> None:
    """CV-R7-LOCATION (fixed): the citation resolves via ``supporting_evidence``;
    R7 used to walk the first block with the label and skip the check."""
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
    """``= null`` contradicts the CoachInput value at that path (legacy form;
    the literal ``death.death_to_respawn = null`` is the supplied fact)."""
    assert all(_rejected(u.view, CONTRADICTION) for u in _contradiction_units())


# --- N2 contract over every fixture view ----------------------------------------------


def _wrong(value: Any) -> Any:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int | float):
        return value + 1000
    if isinstance(value, str):
        return value + " (altered)" if value else "altered"
    return 1234


def test_every_view_leaf_is_a_valid_n2_citation() -> None:
    """Each scalar leaf, cited by its literal path, binds exactly that location
    and its exact value is accepted (top-level scalars and prose included)."""
    for unit in fixture_units().values():
        for path, _value in leaves(unit.view):
            resolution = resolve_evidence_reference(unit.view, f"{path} = x")
            assert resolution["kind"] == "view_native", path
            assert resolution["location"] == path
            assert check(unit.view, path, path) is None, path


def test_wrong_value_on_any_scalar_leaf_is_rejected() -> None:
    for unit in fixture_units().values():
        for path, value in leaves(unit.view):
            assert check(unit.view, path, path, _wrong(value)) == "value_rejected", path


def test_no_label_shadows_a_view_path() -> None:
    """A label that is itself a literal view path sits at that path, so legacy
    label lookup cannot redirect any literal citation in production views."""
    for unit in fixture_units().values():
        for location, block in labelled_blocks(unit.view):
            label = block["source_path"]
            if resolve_view_path(unit.view, label)[0]:
                assert label == location


_OUT_OF_PLAY = {"death_to_active_again": "value", "clock": "clock"}
# CV-CLAIM-OBJECT-PATH: (fixture unit, claim_basis index) -> claim id, the object
# path it names, and the leaf holding each value the claim asserts. The
# own-splat gap is raw video seconds (``video_seconds``; ``value`` is null).
OBJECT_PATH_CLAIMS: dict[tuple[str, int], tuple[str, str, dict[str, str]]] = {
    ("contract/2026-10-10_23-43-31_death_episode_205.000", 1): (
        "death_out_of_play",
        "death.death_to_active_again",
        _OUT_OF_PLAY,
    ),
    ("contract/2026-10-11_22-37-47_death_episode_58.000", 0): (
        "death_out_of_play",
        "death.death_to_active_again",
        _OUT_OF_PLAY,
    ),
    ("contract/2026-10-11_22-57-30_death_episode_308.000", 0): (
        "death_out_of_play",
        "death.death_to_active_again",
        _OUT_OF_PLAY,
    ),
    ("corpus/5d42_145.000", 0): (
        "death_own_splat_before",
        "death.time_since_previous_splat",
        {"time_since_previous_splat": "video_seconds"},
    ),
    ("corpus/ed95_156.000", 0): (
        "death_out_of_play",
        "death.death_to_active_again",
        _OUT_OF_PLAY,
    ),
    ("corpus/ed95_72.000", 1): (
        "death_out_of_play",
        "death.death_to_active_again",
        _OUT_OF_PLAY,
    ),
}


def test_object_path_claims_are_exactly_these() -> None:
    found = {
        (key, index)
        for key, unit in fixture_units().items()
        for index, claim in enumerate(unit.view["claim_basis"])
        if any(
            isinstance(walk(unit.view, path)[1], dict | list)
            for path in claim["evidence_paths"]
        )
    }
    assert found == set(OBJECT_PATH_CLAIMS)


@pytest.mark.parametrize(("key", "index"), sorted(OBJECT_PATH_CLAIMS))
def test_object_path_claim_is_cited_by_its_leaves(key: str, index: int) -> None:
    """Leaf only: the object path is a valid reference, a claim value asserted
    on it is ``object_path_value``, and the same value on its leaf passes."""
    view = fixture_units()[key].view
    claim = view["claim_basis"][index]
    claim_id, path, leaf_of = OBJECT_PATH_CLAIMS[(key, index)]
    assert (claim["claim_id"], claim["evidence_paths"]) == (claim_id, [path])
    assert set(claim["values"]) == set(leaf_of)
    assert _object_value_check(view, path) is None
    for name, value in claim["values"].items():
        assert _object_value_check(view, f"{path} = {render(value)}") == (
            "object_path_value"
        )
        leaf = f"{path}.{leaf_of[name]}"
        assert walk(view, leaf) == (True, value)
        assert check(view, leaf, leaf, value) is None


@pytest.mark.parametrize(("key", "count"), sorted(HISTORY_DB5FA8C.items()))
def test_history_classes_are_known(key: tuple[str, str], count: int) -> None:
    form, violation = key
    assert form in FORMS and count > 0
    assert violation in VIOLATIONS


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
    used |= {"CV-R7-LOCATION"}  # test_wrong_value_through_duplicated_label_is_rejected
    assert used - {None} == set(VIOLATIONS)
