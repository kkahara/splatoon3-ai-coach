"""Helpers for ``test_citation_contract``: fixture units and citation checks.

A citation *satisfies the contract* for an intended view leaf when the
production validator resolves it (``resolve_evidence_reference`` is not
``unresolved``), the location it binds (``resolution["location"]``, the one R7
checks) is that leaf, and R7 accepts the leaf's value cited after it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from claim_corpus import corpus_unit, load_corpus

from splatoon3_ai_coach.coach.claim_catalog import CoachingUnitResult
from splatoon3_ai_coach.coach.claim_validation import (
    ReferenceKind,
    _source_blocks,
    evidence_value_hits,
    resolve_evidence_reference,
)
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view
from splatoon3_ai_coach.config import default_config_path, load_config

FIXTURES = Path(__file__).parent / "fixtures" / "citation_contract"
EXPERIMENT_I_FIXTURE = "contract/2026-10-11_22-57-30_death_episode_308.000"
_SEGMENT = re.compile(r"^([A-Za-z_]\w*)(?:\[([^\]]+)\])?$")


@dataclass(frozen=True)
class FixtureUnit:
    """One fixture: the production view and the CoachInput it was built from."""

    view: dict[str, Any]
    coach_input: dict[str, Any]
    entry: dict[str, Any]


def contract_entries() -> list[dict[str, Any]]:
    """The real-unit fixtures exported by ``citation_contract_fixtures.py``."""
    return [json.loads(p.read_text()) for p in sorted(FIXTURES.glob("*.json"))]


def _contract_unit(entry: dict[str, Any]) -> FixtureUnit:
    thresholds = load_config(default_config_path()).coach.death_factor_thresholds
    coach_input = CoachInput.model_validate(entry["coach_input"])
    unit = CoachingUnitResult.model_validate(entry["unit"])
    view = build_coach_llm_view(coach_input, unit, thresholds)
    dump = coach_input.model_dump(mode="json")
    return FixtureUnit(view.model_dump(mode="json"), dump, entry)


def _corpus_unit(entry: dict[str, Any]) -> FixtureUnit:
    coach_input, unit = corpus_unit(entry)
    view = build_coach_llm_view(coach_input, unit).model_dump(mode="json")
    return FixtureUnit(view, coach_input.model_dump(mode="json"), entry)


@cache
def fixture_units() -> dict[str, FixtureUnit]:
    """23 claim-corpus units plus the real contract units, current code."""
    units = {f"corpus/{e['id']}": _corpus_unit(e) for e in load_corpus()}
    for entry in contract_entries():
        units[f"contract/{entry['id']}"] = _contract_unit(entry)
    return units


def walk(node: Any, path: str) -> tuple[bool, Any]:
    """Follow a dotted path with ``[i]`` indexes or ``[key=value]`` selectors.

    The validator itself only follows ``[i]``; selectors exist for walking
    CoachInput paths such as ``game_clock_samples[label=death]``.
    """
    for segment in path.split("."):
        match = _SEGMENT.match(segment)
        if match is None or not isinstance(node, dict) or match.group(1) not in node:
            return False, None
        node = node[match.group(1)]
        index = match.group(2)
        if index is None:
            continue
        if not isinstance(node, list):
            return False, None
        if index.isdigit():
            if int(index) >= len(node):
                return False, None
            node = node[int(index)]
            continue
        key, _, wanted = index.partition("=")
        hits = [x for x in node if isinstance(x, dict) and str(x.get(key)) == wanted]
        if not hits:
            return False, None
        node = hits[0]
    return True, node


def leaves(node: Any, path: str = "") -> Iterator[tuple[str, Any]]:
    """Every scalar (non-dict, non-list) leaf with its path."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from leaves(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from leaves(value, f"{path}[{i}]")
    else:
        yield path, node


def labelled_blocks(node: Any, path: str = "") -> Iterator[tuple[str, dict]]:
    """``(view path, block)`` for every ``source_path``-labelled dict, in the
    validator's traversal order."""
    if isinstance(node, dict):
        if isinstance(node.get("source_path"), str):
            yield path, node
        for key, value in node.items():
            yield from labelled_blocks(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from labelled_blocks(value, f"{path}[{i}]")


def source_blocks_order_matches(view: Mapping[str, Any]) -> bool:
    """``labelled_blocks`` visits exactly the validator's blocks, in order."""
    ours = [block for _, block in labelled_blocks(view)]
    theirs = _source_blocks(view)
    if len(ours) != len(theirs):
        return False
    return all(a is b for a, b in zip(ours, theirs, strict=True))


def render(value: Any) -> str:
    """A value written the way a model writes it after ``path =``."""
    return json.dumps(value, ensure_ascii=False)


def resolved_location(resolution: Mapping[str, Any]) -> str:
    """View path of the fact the validator bound the citation to (R7 checks it)."""
    return str(resolution["location"])


def check(
    view: Mapping[str, Any], cited_path: str, intended: str, value: Any = None
) -> str | None:
    """``None`` if ``cited_path`` satisfies the contract for ``intended``,
    else why not. ``value`` overrides the value cited (default: the leaf's).

    An object or list is cited as a bare path (a reference): under the
    leaf-only rule it has no value a citation can assert.
    """
    found, actual = walk(view, intended)
    if not found:
        return "intended_leaf_absent"
    if value is None and isinstance(actual, dict | list):
        text = cited_path
    else:
        text = f"{cited_path} = {render(actual if value is None else value)}"
    resolution = resolve_evidence_reference(view, text)
    if resolution["kind"] == ReferenceKind.UNRESOLVED.value:
        return f"unresolved:{resolution['reason']}"
    if resolution["path"] != cited_path:
        return "path_parsed_differently"
    if resolved_location(resolution) != intended:
        return "binds_other_fact"
    if evidence_value_hits(view, [resolution]):
        return "value_rejected"
    return None


def same_value(a: Any, b: Any) -> bool:
    """Equal JSON values (numbers within 1e-9)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(a - b) <= 1e-9
    return a == b
