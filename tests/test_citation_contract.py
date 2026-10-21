"""Citation contract: measured over every citation form the prompt teaches.

Decision (``docs/CITATION_NAMESPACES.md``): the only citation namespace is
N2, the literal CoachLlmView path. A citation names a supplied fact by its
view path, and the cited value must equal the scalar at that path. CoachInput
(N1) paths and ``source_path`` labels are provenance only; the validator's
legacy label fallback is removed, so every non-literal form is rejected.

The production prompt teaches N2 only (Fix 3): ``TAUGHT`` must match its
inventory exactly, and every path and example must resolve literally.

Fix 1 (``db5fa8c``) measured the forms the prompt taught then
(``TAUGHT_DB5FA8C``), including N1 / hybrid examples. Those measurements are
kept as evidence for the decision: each violation in ``VIOLATIONS`` has a
classification and a status. ``open`` and ``not_taught`` violations are strict
``xfail`` cases (an XPASS forces the table to be updated); ``fixed`` ones are
asserted absent; ``rejected`` ones (legacy forms the fallback used to resolve)
are asserted rejected. ``HISTORY_DB5FA8C`` records the counts measured before
the validator-correctness step.
"""

from __future__ import annotations

import json
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
LEGACY = "legacy"  # N1 / label behavior; the validator rejects it (N2 only)
DESIGN = "design"  # open question; nothing implemented
CONTENT = "content"  # claim_basis / prompt content to migrate; validator correct


@dataclass(frozen=True)
class Violation:
    """One contract violation found by Fix 1 or the correctness step."""

    classification: str
    # open | fixed | not_taught (a defect of the db5fa8c prompt text, gone with
    # Fix 3) | rejected (a legacy form the removed label fallback resolved; the
    # validator now rejects it)
    status: str
    description: str


VIOLATIONS: dict[str, Violation] = {
    "CV-NOT-PROJECTED": Violation(
        LEGACY,
        "not_taught",
        "N1 citation example names an engagement-unit field: death views project "
        "neither primary relations nor combat, and death-unit CoachInputs leave "
        "primary combat null",
    ),
    "CV-RELATIVE": Violation(
        SYNTAX,
        "not_taught",
        "prompt prose field reference omits its block; not a literal view path "
        "(prompt rewrite)",
    ),
    "CV-EXAMPLE-NAMESPACE": Violation(
        LEGACY,
        "not_taught",
        "example listed under 'exact CoachInput path' is not a CoachInput path "
        "with the cited value (view path or CoachInput/view hybrid)",
    ),
    "CV-UNLABELLED": Violation(
        LEGACY,
        "rejected",
        "projected field has no source_path label naming its N1 path",
    ),
    "CV-FLATTENED": Violation(
        LEGACY,
        "rejected",
        "label block flattens N1 nesting (observation.*, scenario.*)",
    ),
    "CV-RENAMED": Violation(
        LEGACY, "rejected", "view renames the N1 field under the same label"
    ),
    "CV-MIXED-SOURCE": Violation(
        LEGACY,
        "rejected",
        "label names one N1 object but the block holds fields projected from "
        "another (durations, score_facts, zone_control_facts, map status)",
    ),
    "CV-SUPPORT-LABEL": Violation(
        LEGACY,
        "rejected",
        "supporting_evidence items reuse N1 labels for display strings: one label "
        "string names two facts; the legacy form bound the first that walked",
    ),
    "CV-R7-LOCATION": Violation(
        CORRECTNESS,
        "fixed",
        "R7 walked the first block carrying the label, not the block resolution "
        "used, so a wrong value cited through a duplicated label went unchecked",
    ),
    "CV-BARE-LABEL": Violation(
        LEGACY,
        "rejected",
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
        "fixed",
        "claim_basis evidence path named an object while the claim asserted "
        "scalars at its leaves (migrated to the leaves; MIGRATED_CLAIMS)",
    ),
    "CV-BARE-OBJECT-REF": Violation(
        SYNTAX,
        "open",
        "a path-only reference to a single-segment object path (related[0], "
        "death) is malformed: unanchored paths need a dot (grammar not "
        "expanded; the prompt teaches no path-only references)",
    ),
    "CV-NOT-COACHINPUT": Violation(
        LEGACY,
        "rejected",
        "label-form path resolves in the view but names no N1 field",
    ),
    "CV-CONTRADICTS-COACHINPUT": Violation(
        LEGACY,
        "rejected",
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
# Forms measured at db5fa8c through the label fallback, now rejected outright.
HISTORICAL_FORMS = {
    "label_form": "label and label + leaf citations ('prefer source_path values')",
    "label_exactness": "label-form citations the fallback accepted, vs CoachInput",
}


def _strict(violation: str) -> Any:
    found = VIOLATIONS[violation]
    assert found.status in ("open", "not_taught")
    reason = f"{violation} ({found.classification}, {found.status}): "
    return pytest.mark.xfail(strict=True, reason=reason + found.description)


def _xfail(case_id: str, violation: str | None, *args: Any) -> Any:
    if violation is None or VIOLATIONS[violation].status == "fixed":
        return pytest.param(*args, id=case_id)
    return pytest.param(*args, id=case_id, marks=_strict(violation))


def _rejected_everywhere(cited: str, units: dict[str, FixtureUnit]) -> dict[str, str]:
    """Units whose validator does not reject ``cited`` (expected: none)."""
    out = {}
    for key, unit in units.items():
        resolution = resolve_evidence_reference(unit.view, cited)
        if resolution["kind"] != "unresolved":
            out[key] = resolution["kind"]
    return out


# --- what the production prompt teaches --------------------------------------------

SYSTEM_PROMPT = load_system_prompt(roster_evidence=False)
ROSTER_SECTION = load_system_prompt(roster_evidence=True).removeprefix(SYSTEM_PROMPT)
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


# The current production prompt (Fix 3, N2 only): every path is literal.
TAUGHT = (
    Taught(
        "death.death_to_active_again.value",
        "citation_example",
        "death.death_to_active_again.value",
    ),
    Taught("clock.display", "citation_example", "clock.display"),
    Taught("special.state_at_anchor", "citation_example", "special.state_at_anchor"),
    Taught(
        "map.map_check_before_death.status",
        "citation_example",
        "map.map_check_before_death.status",
    ),
    Taught(
        "related[0].relations.leads_to_death_episode_id",
        "citation_example",
        "related[0].relations.leads_to_death_episode_id",
    ),
    Taught(
        "timeline.time_since_previous_death.value",
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
    Taught("playback.status", "field_reference", "playback.status"),
    Taught("score.lookback_video_time", "field_reference", "score.lookback_video_time"),
    Taught("score.video_time", "field_reference", "score.video_time"),
)

# The prompt at db5fa8c (before Fix 3). It no longer teaches these forms; they
# stay measured against current views as the record behind the N2 decision.
TAUGHT_DB5FA8C = (
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

# Roster section (only with coach.roster_evidence on; no fixture view has a
# roster block, so these are checked against the projected keys only).
ROSTER_TAUGHT = {
    "roster.before_death",
    "roster.at_death",
    "roster.player_count_context",
}


def _applicable(intended: str | None) -> dict[str, FixtureUnit]:
    if intended is None:
        return {}
    return {k: u for k, u in fixture_units().items() if walk(u.view, intended)[0]}


def test_prompt_inventory_is_complete() -> None:
    """Every dotted path in the production prompt has a contract entry."""
    found = set(_TOKEN.findall(SYSTEM_PROMPT)) | set(
        _TOKEN.findall(_USER_PREAMBLE_LLM_VIEW)
    )
    assert found == {t.path for t in TAUGHT} | set(NOT_PATHS)


def test_roster_section_inventory_is_complete() -> None:
    assert set(_TOKEN.findall(ROSTER_SECTION)) == ROSTER_TAUGHT | set(NOT_PATHS)
    roster_keys = {"before_death", "at_death", "player_count_window"}
    roster_keys.add("player_count_context")
    assert {path.split(".", 1)[1] for path in ROSTER_TAUGHT} <= roster_keys


@pytest.mark.parametrize("case", TAUGHT, ids=lambda t: t.path)
def test_taught_path_is_a_literal_view_path(case: Taught) -> None:
    """Fix 3: every path the prompt teaches is the literal N2 path of its fact,
    resolved without any label (``view_native``) on every view that has it."""
    assert case.path == case.intended and case.resolve is None
    units = _applicable(case.intended)
    assert units, "no fixture view supplies the fact this path names"
    for key, unit in units.items():
        resolution = resolve_evidence_reference(unit.view, case.path)
        assert resolution["kind"] == "view_native", key
        assert resolution["location"] == case.path, key
        assert check(unit.view, case.path, case.path) is None, key


_EXAMPLE = re.compile(r'^"(.+ = .+)"$', re.MULTILINE)


def prompt_examples() -> list[str]:
    """The citation strings listed under EVIDENCE CITATION RULE, unescaped."""
    rule = SYSTEM_PROMPT.split("EVIDENCE CITATION RULE", 1)[1].split("Output format")[0]
    return [text.replace('\\"', '"') for text in _EXAMPLE.findall(rule)]


def test_examples_are_the_taught_citation_examples() -> None:
    paths = [example.split(" = ", 1)[0] for example in prompt_examples()]
    assert paths == [t.path for t in TAUGHT if t.kind == "citation_example"]


@pytest.mark.parametrize("example", prompt_examples())
def test_example_is_a_valid_n2_citation(example: str) -> None:
    """The whole example is a literal ``path = value`` on a scalar leaf; the
    value has the leaf's JSON type and passes R7 where the view holds it."""
    path, raw = example.split(" = ", 1)
    value = json.loads(raw)
    units = _applicable(path)
    assert units
    for unit in units.values():
        resolution = resolve_evidence_reference(unit.view, example)
        assert (resolution["kind"], resolution["location"]) == ("view_native", path)
        actual = walk(unit.view, path)[1]
        assert not isinstance(actual, dict | list)
        hits = evidence_value_hits(unit.view, [resolution])
        assert hits == [] if same_value(actual, value) else len(hits) == 1
    assert any(type(walk(u.view, path)[1]) is type(value) for u in units.values()), (
        "example value type never matches the leaf"
    )


@pytest.mark.parametrize("case", TAUGHT_DB5FA8C, ids=lambda t: t.path)
def test_historical_taught_path_is_literal_or_rejected(case: Taught) -> None:
    """The forms the prompt taught at db5fa8c, against current views: a literal
    view path satisfies the contract; every N1, hybrid or relative form is
    rejected wherever it could have named a fact."""
    if case.path != case.intended:
        units = _applicable(case.intended) or fixture_units()
        assert _rejected_everywhere(case.path, units) == {}
        return
    units = _applicable(case.intended)
    assert units, "no fixture view supplies the fact this path names"
    failures = {k: check(u.view, case.path, case.path) for k, u in units.items()}
    assert {k: r for k, r in failures.items() if r} == {}


EXAMPLES = [t for t in TAUGHT_DB5FA8C if t.kind == "citation_example"]


@pytest.mark.parametrize("case", [_xfail(t.path, t.exact, t) for t in EXAMPLES])
def test_citation_example_is_exact_coachinput_path(case: Taught) -> None:
    """Legacy: the db5fa8c rule heading these examples said 'the exact
    CoachInput path and value'."""
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
    [pytest.param(p, i, id=p) for p, i, _ in COACHINPUT_CASES],
)
def test_coachinput_path_is_rejected(coachinput_path: str, intended: str) -> None:
    """N1 paths are provenance only. Each is rejected on every view holding the
    projected fact, which its literal view path still states (the violation
    column records how the removed label fallback used to mis-bind it)."""
    units = _paired(coachinput_path, intended)
    assert _rejected_everywhere(coachinput_path, units) == {}
    assert {k: check(u.view, intended, intended) for k, u in units.items()} == {
        k: None for k in units
    }


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
    ("cited", "intended"), [pytest.param(c, i, id=c) for c, i, _ in POSTMORTEM_CASES]
)
def test_experiment_i_postmortem_citation_is_rejected(cited: str, intended: str) -> None:
    """Old-prompt citations from Experiment I outputs: rejected, while the
    literal view path of the same fact is accepted."""
    units = _applicable(intended)
    assert EXPERIMENT_I_FIXTURE in units
    assert _rejected_everywhere(cited, units) == {}
    assert all(check(u.view, intended, intended) is None for u in units.values())


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


FORMS: dict[str, Callable[[], Counter[tuple[str, str]]]] = {
    "view_form": view_form_failures,
    "claim_paths": claim_path_failures,
}
FORM_VIOLATIONS: dict[str, tuple[str, ...]] = {
    "view_form": ("CV-SINGLE-SEGMENT", "CV-PROSE-VALUE"),
    "claim_paths": ("CV-OBJECT-LEAF", "CV-CLAIM-OBJECT-PATH"),
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


# Measured at db5fa8c through the label fallback (label_form / label_exactness).
LABEL_FORM_VIOLATIONS = ("CV-BARE-LABEL", "CV-SUPPORT-LABEL", "CV-NOT-COACHINPUT")


def label_form_citations(view: dict[str, Any]) -> list[tuple[str, Any]]:
    """'prefer source_path values' (db5fa8c): each label not sitting at its own
    view path, and label + each leaf below it, with the value it names."""
    out = []
    for location, block in labelled_blocks(view):
        label = block["source_path"]
        if label == location:
            continue
        out.append((label, block))
        out += [(f"{label}.{rel}", v) for rel, v in leaves(block) if rel != "source_path"]
    return out


def test_label_form_citations_are_rejected() -> None:
    """Every legacy label form over every fixture view is unresolved, even
    with the correct value (bare labels, duplicated labels, labels naming no
    N1 field alike)."""
    accepted: Counter[str] = Counter()
    total = 0
    for unit in fixture_units().values():
        for cited, value in label_form_citations(unit.view):
            total += 1
            scalar = not isinstance(value, dict | list)
            text = f"{cited} = {render(value)}" if scalar else cited
            if resolve_evidence_reference(unit.view, text)["kind"] != "unresolved":
                accepted[_shape(cited)] += 1
    assert total > 0
    assert accepted == Counter()


@_strict("CV-BARE-OBJECT-REF")
def test_single_segment_object_reference_resolves() -> None:
    """A path-only reference to a top-level object (or ``related[0]``) is a
    literal view path, but the grammar reads it as malformed."""
    for unit in fixture_units().values():
        for path in ("death", "related[0]"):
            if isinstance(walk(unit.view, path)[1], dict):
                assert resolve_evidence_reference(unit.view, path)["kind"] == (
                    "view_native"
                )


def _rejected(view: dict[str, Any], text: str) -> bool:
    resolution = resolve_evidence_reference(view, text)
    return resolution["kind"] == "unresolved" or bool(
        evidence_value_hits(view, [resolution])
    )


SUPPORT_LABEL_WRONG_VALUE = (
    'primary_context.special.nearest_before_anchor.value = "not a real value"'
)


def test_wrong_value_through_duplicated_label_is_rejected() -> None:
    """CV-R7-LOCATION (fixed): the citation used to resolve via
    ``supporting_evidence`` while R7 walked the first block with the label and
    skipped the check; the label form is now unresolved outright."""
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


def test_contradicting_label_citation_is_rejected() -> None:
    """CV-CONTRADICTS-COACHINPUT (rejected): ``= null`` contradicts the
    CoachInput value at that path; the label fallback used to accept it. The
    literal ``death.death_to_respawn = null`` is the supplied fact."""
    units = _contradiction_units()
    assert all(_rejected(u.view, CONTRADICTION) for u in units)
    assert not any(_rejected(u.view, "death.death_to_respawn = null") for u in units)


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
    """A label that is itself a literal view path sits at that path, so the
    provenance label never names a different fact than the citation would."""
    for unit in fixture_units().values():
        for location, block in labelled_blocks(unit.view):
            label = block["source_path"]
            if resolve_view_path(unit.view, label)[0]:
                assert label == location


_OUT_OF_PLAY = {
    "death_to_active_again": "death.death_to_active_again.value",
    "clock": "death.death_to_active_again.clock",
}
_OWN_SPLAT = {
    "time_since_previous_splat": "death.time_since_previous_splat.video_seconds"
}
# CV-CLAIM-OBJECT-PATH (fixed by the claim_basis migration): (fixture unit,
# claim_basis index) -> claim id, the object path it used to name, and the
# leaf now carrying each claim value. The own-splat gap is the raw video gap
# (``video_seconds``; its ``value`` is null in this unit), not ``.value``.
MIGRATED_CLAIMS: dict[tuple[str, int], tuple[str, str, dict[str, str]]] = {
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
        _OWN_SPLAT,
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


def test_no_claim_evidence_path_names_an_object() -> None:
    """The CV-CLAIM-OBJECT-PATH exact set is empty after the migration."""
    found = {
        (key, index)
        for key, unit in fixture_units().items()
        for index, claim in enumerate(unit.view["claim_basis"])
        if any(
            isinstance(walk(unit.view, path)[1], dict | list)
            for path in claim["evidence_paths"]
        )
    }
    assert found == set()


@pytest.mark.parametrize(("key", "index"), sorted(MIGRATED_CLAIMS))
def test_migrated_claim_is_cited_by_its_leaves(key: str, index: int) -> None:
    """Each formerly object-valued claim names the leaf of every value; the
    old object path still rejects an asserted value (leaf only)."""
    view = fixture_units()[key].view
    claim = view["claim_basis"][index]
    claim_id, old_path, leaf_of = MIGRATED_CLAIMS[(key, index)]
    assert claim["claim_id"] == claim_id
    assert claim["evidence_paths"] == list(leaf_of.values())
    assert set(claim["values"]) == set(leaf_of)
    for name, value in claim["values"].items():
        leaf = leaf_of[name]
        assert walk(view, leaf) == (True, value)
        assert check(view, leaf, leaf, value) is None
        assert _object_value_check(view, f"{old_path} = {render(value)}") == (
            "object_path_value"
        )


def test_every_claim_value_traces_to_an_evidence_leaf() -> None:
    """Every claim_basis evidence path is a literal scalar view leaf, and every
    claim value equals the value at one of its evidence paths."""
    for key, unit in fixture_units().items():
        for claim in unit.view["claim_basis"]:
            leaves_ = [walk(unit.view, path) for path in claim["evidence_paths"]]
            assert all(
                found and not isinstance(value, dict | list) for found, value in leaves_
            ), (key, claim["claim_id"])
            for name, value in claim["values"].items():
                assert any(same_value(v, value) for _, v in leaves_), (key, name)


@pytest.mark.parametrize(("key", "count"), sorted(HISTORY_DB5FA8C.items()))
def test_history_classes_are_known(key: tuple[str, str], count: int) -> None:
    form, violation = key
    assert (form in FORMS or form in HISTORICAL_FORMS) and count > 0
    assert violation in VIOLATIONS


def test_no_legacy_violation_is_open() -> None:
    """Legacy forms are rejected or were prompt defects; none is still
    measured as a citation the validator might accept."""
    statuses = {v.status for v in VIOLATIONS.values() if v.classification == LEGACY}
    assert statuses <= {"rejected", "not_taught"}


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
    """A new or reshaped projection cannot lose its provenance silently."""
    unlabelled = {
        key
        for unit in fixture_units().values()
        for key, value in unit.view.items()
        if value not in (None, [], {}) and not _labelled(value)
    }
    assert unlabelled == set(UNLABELLED_BLOCKS)


def test_every_known_violation_is_exercised() -> None:
    used = {t.resolve for t in TAUGHT_DB5FA8C} | {t.exact for t in TAUGHT_DB5FA8C}
    used |= {v for *_, v in COACHINPUT_CASES} | {v for *_, v in MAP_AWARENESS_CASES}
    used |= {v for *_, v in POSTMORTEM_CASES}
    used |= {v for violations in FORM_VIOLATIONS.values() for v in violations}
    used |= set(LABEL_FORM_VIOLATIONS)
    used |= {"CV-R7-LOCATION"}  # test_wrong_value_through_duplicated_label_is_rejected
    used |= {"CV-CONTRADICTS-COACHINPUT"}  # test_contradicting_label_citation_...
    used |= {"CV-BARE-OBJECT-REF"}  # test_single_segment_object_reference_resolves
    assert used - {None} == set(VIOLATIONS)
