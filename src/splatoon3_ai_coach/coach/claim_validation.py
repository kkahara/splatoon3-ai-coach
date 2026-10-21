"""Deterministic validation of model coaching output against its LLM view.

Annotation only: nothing here rewrites model text. Claim-family rules
(``claim_rules``: causation, roster, teammate/opponent, special usage,
judgment) are *blocking*: any counting hit gives ``blocked_unsupported_claim``
and the public page withholds the prose. These rules sit beside them, with the
prohibited-phrase patterns in ``evidence_contract``:

* causal map claims — a map-absence term and a causal form in one sentence
  (hedged wording still counts; negations and limitations are recorded but
  exempt);
* unsupported map recommendations — map-usage advice in ``recommendations``
  or prescriptive ``assessment`` sentences, unless an active map factor has a
  catalog recommendation;
* evidence paths — every ``evidence_used`` entry must resolve against the
  serialized view: a literal view path (the model-facing contract), or,
  only when that path does not exist, a legacy ``source_path`` label +
  remainder; a cited ``path = value`` must equal the value at the resolved
  location (R7);
* claim integrity — R5, R7, R8 and R9 in ``claim_integrity``.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from splatoon3_ai_coach.coach.claim_catalog import (
    FACTOR_ANNOTATIONS,
    MAP_FACTOR_IDS,
    DeathImportanceFactorId,
    FactorAnnotation,
)
from splatoon3_ai_coach.coach.claim_integrity import (
    claim_integrity_hits,
    grounded_templates,
)
from splatoon3_ai_coach.coach.claim_rules import blocking_hits, claim_rule_hits
from splatoon3_ai_coach.coach.evidence_contract import (
    ClaimHitClassification,
    classify_prohibited_hit,
    split_sentences,
)


class ValidationStatus(StrEnum):
    """Public-facing validator outcome for one assessment."""

    VALIDATED = "validated"
    CONTAINS_UNVERIFIED_WORDING = "contains_unverified_wording"
    BLOCKED_UNSUPPORTED_CLAIM = "blocked_unsupported_claim"
    NOT_VALIDATED = "not_validated"


class ReferenceKind(StrEnum):
    """How an ``evidence_used`` entry maps onto the LLM view."""

    VIEW_NATIVE = "view_native"
    SOURCE_PATH_REF = "source_path_ref"
    UNRESOLVED = "unresolved"


_MAP_ABSENCE = re.compile(
    r"(?i)\b(?:"
    r"lack(?:ing)?\s+(?:of\s+)?(?:a\s+|any\s+)?map(?:\s+(?:awareness|checks?|usage))?"
    r"|absence\s+of\s+(?:a\s+|any\s+)?map(?:\s+(?:awareness|checks?))?"
    r"|no\s+(?:prior\s+|recent\s+)?map\s+(?:checks?|awareness)"
    r"|without\s+(?:a\s+|any\s+)?(?:prior\s+|recent\s+)?map\s+checks?"
    r"|without\s+checking\s+(?:the\s+|your\s+)?map"
    r"|(?:didn'?t|did\s+not|not|never)\s+check(?:ing|ed)?\s+(?:the\s+|your\s+)?map"
    r"|(?:poor|limited|missing)\s+map\s+(?:awareness|checks?)"
    r")"
)
_CAUSAL = re.compile(
    r"(?i)\b(?:contribut\w*|led\s+to|lead(?:s|ing)?\s+to|caus\w*|because"
    r"|due\s+to|result\w*\s+(?:in|from))\b"
)
_MAP_ADVICE = re.compile(
    r"(?i)\b(?:"
    r"review(?:ing)?\s+(?:your\s+)?map\s+usage"
    r"|should\s+have\s+checked\s+(?:the\s+|your\s+)?map"
    r"|check(?:ing)?\s+(?:the\s+|your\s+)?map"
    r"|use\s+(?:the\s+|your\s+)?map\s+more"
    r"|watch(?:ing)?\s+(?:the\s+|your\s+)?map"
    r"|(?:improve|work\s+on)\s+(?:your\s+)?map\s+awareness"
    r")\b"
)
_PRESCRIPTIVE = re.compile(
    r"(?i)\b(?:should|try\s+to|make\s+sure|remember\s+to|need\s+to|must|consider)\b"
)
_IMPERATIVE_START = re.compile(
    r"(?i)^\s*(?:check|review|use|watch|look|keep|glance|open|practice|focus|"
    r"remember|try|avoid)\b"
)
_PATH = re.compile(r"[A-Za-z_]\w*(?:\[[^\]]+\])?(?:\.[A-Za-z_]\w*(?:\[[^\]]+\])?)+")
_SEGMENT = re.compile(r"^([A-Za-z_]\w*)(?:\[([^\]]+)\])?$")
_EXEMPT = (ClaimHitClassification.NEGATED, ClaimHitClassification.LIMITATION)


def _sentences(text: str) -> list[tuple[int, str]]:
    """Sentences with start offsets (shared, decimal-safe splitter)."""
    return split_sentences(text)


def _classify(
    sentence: str, start: int, end: int, *, masks: list[re.Match[str]], field: str
) -> ClaimHitClassification:
    """Reuse contract negation cues, ignoring cues inside the matched terms.

    Absence terms such as "absence of map" or "did not check the map" contain
    the contract's own negation cues; masking them keeps the claim itself from
    reading as its own negation.
    """
    masked = list(sentence)
    for item in masks:
        masked[item.start() : item.end()] = "x" * (item.end() - item.start())
    return classify_prohibited_hit("".join(masked), start, end, field=field)


def causal_map_claim_hits(text: str, *, field: str) -> list[dict[str, Any]]:
    """Sentences pairing a map-absence term with a causal form."""
    hits: list[dict[str, Any]] = []
    for _, sentence in _sentences(text):
        absence = _MAP_ABSENCE.search(sentence)
        causal = _CAUSAL.search(sentence)
        if absence is None or causal is None:
            continue
        first = min(absence.start(), causal.start())
        classification = _classify(
            sentence, first, first + 1, masks=[absence], field=field
        )
        counts = classification not in _EXEMPT
        hits.append(
            {
                "field": field,
                "sentence": sentence.strip(),
                "absence_term": absence.group(0),
                "causal_term": causal.group(0),
                "classification": (
                    ClaimHitClassification.ASSERTION.value
                    if counts
                    else classification.value
                ),
                "counts_as_violation": counts,
            }
        )
    return hits


def map_recommendation_supported(
    active_factor_ids: list[str],
    annotations: Mapping[DeathImportanceFactorId, FactorAnnotation] | None = None,
) -> bool:
    """True when an active map factor carries a catalog recommendation."""
    catalog = FACTOR_ANNOTATIONS if annotations is None else annotations
    for factor_id in active_factor_ids:
        if factor_id not in MAP_FACTOR_IDS:
            continue
        annotation = catalog.get(DeathImportanceFactorId(factor_id))
        if annotation is not None and annotation.recommendation is not None:
            return True
    return False


def _advice_candidates(assessment: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``recommendations`` items plus prescriptive ``assessment`` sentences."""
    items = assessment.get("recommendations") or []
    out = [("recommendations", str(item)) for item in items]
    for _, sentence in _sentences(str(assessment.get("assessment") or "")):
        if _PRESCRIPTIVE.search(sentence) or _IMPERATIVE_START.search(sentence):
            out.append(("assessment", sentence.strip()))
    return out


def unsupported_recommendation_hits(
    assessment: Mapping[str, Any],
    *,
    active_factor_ids: list[str],
    annotations: Mapping[DeathImportanceFactorId, FactorAnnotation] | None = None,
) -> list[dict[str, Any]]:
    """Map-usage advice not backed by an active map factor recommendation."""
    if map_recommendation_supported(active_factor_ids, annotations):
        return []
    hits: list[dict[str, Any]] = []
    for field, text in _advice_candidates(assessment):
        for match in _MAP_ADVICE.finditer(text):
            classification = _classify(
                text, match.start(), match.end(), masks=[], field=field
            )
            hits.append(
                {
                    "field": field,
                    "text": text,
                    "phrase": match.group(0),
                    "classification": classification.value,
                    "counts_as_violation": classification
                    is not ClaimHitClassification.NEGATED,
                }
            )
    return hits


def _walk(node: Any, segments: list[str]) -> tuple[bool, Any]:
    """Follow dotted segments (with ``[i]`` list indexes) through JSON."""
    for raw in segments:
        match = _SEGMENT.match(raw)
        if match is None or not isinstance(node, dict) or match.group(1) not in node:
            return False, None
        node = node[match.group(1)]
        index = match.group(2)
        if index is not None:
            valid = isinstance(node, list) and index.isdigit()
            if not valid or int(index) >= len(node):
                return False, None
            node = node[int(index)]
    return True, node


def _labelled_blocks(node: Any, location: str = "") -> list[tuple[str, dict[str, Any]]]:
    """``(view path, block)`` for every dict carrying a string ``source_path``."""
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        if isinstance(node.get("source_path"), str):
            found.append((location, node))
        for key, value in node.items():
            found.extend(
                _labelled_blocks(value, f"{location}.{key}" if location else key)
            )
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_labelled_blocks(value, f"{location}[{index}]"))
    return found


def _source_blocks(node: Any) -> list[dict[str, Any]]:
    """Every dict in the view that carries a string ``source_path`` label."""
    return [block for _, block in _labelled_blocks(node)]


def _citation_path(view: Mapping[str, Any], text: str) -> str | None:
    """The cited path: a literal view path written first (any depth, top-level
    keys included), else the first dotted path anywhere in the entry."""
    anchored = _ANCHORED_PATH.match(text)
    if anchored is not None and _walk(view, anchored.group(1).split("."))[0]:
        return anchored.group(1)
    match = _PATH.search(text)
    return None if match is None else match.group(0)


def resolve_view_path(view: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    """Literal CoachLlmView path: the one location it names, never via labels."""
    return _walk(view, path.split("."))


def _legacy_label_reference(
    view: Mapping[str, Any], path: str
) -> tuple[tuple[str, str] | None, bool]:
    """Legacy ``source_path`` label + remainder: ``((label, location), _)`` for
    the first labelled block it walks, and whether any label prefixed ``path``.

    Only consulted when the literal view path does not exist. A label that is
    itself a literal view path of some other location is ignored, so labels
    never redirect view-path citations.
    """
    block_hit = False
    for location, block in _labelled_blocks(view):
        label = block["source_path"]
        if label != location and resolve_view_path(view, label)[0]:
            continue
        if path == label:
            return (label, location), True
        if not path.startswith(label + "."):
            continue
        block_hit = True
        rest = path[len(label) + 1 :]
        if _walk(block, rest.split("."))[0]:
            return (label, f"{location}.{rest}" if location else rest), True
    return None, block_hit


def resolve_evidence_reference(view: Mapping[str, Any], text: str) -> dict[str, Any]:
    """Resolve one ``evidence_used`` entry against the serialized view.

    ``view_native``: the literal view path exists (model-facing contract).
    ``source_path_ref``: legacy; the path starts with a block's audit label
    and the remainder walks that block. It is never a CoachInput JSON path.
    ``location`` is the view path of the resolved fact; R7 checks it.
    """
    path = _citation_path(view, text)
    if path is None:
        kind = ReferenceKind.UNRESOLVED.value
        return {"text": text, "kind": kind, "reason": "malformed"}
    if resolve_view_path(view, path)[0]:
        kind = ReferenceKind.VIEW_NATIVE.value
        return {"text": text, "kind": kind, "path": path, "location": path}
    legacy, block_hit = _legacy_label_reference(view, path)
    if legacy is not None:
        return _ref(text, path, *legacy)
    top = _SEGMENT.match(path.split(".")[0])
    if block_hit or (top is not None and top.group(1) in view):
        reason = "unknown_key"
    else:
        reason = "unknown_block"
    return {
        "text": text,
        "kind": ReferenceKind.UNRESOLVED.value,
        "path": path,
        "reason": reason,
    }


_ANCHORED_PATH = re.compile(
    r"^\s*([A-Za-z_]\w*(?:\[[^\]]+\])?(?:\.[A-Za-z_]\w*(?:\[[^\]]+\])?)*)\s*[=:]"
)
_CITED_VALUE = re.compile(r"^\s*[=:]\s*(.+?)\s*$")
_VALUE_TAIL = re.compile(r"\s*(?:;|\s\().*$")
_UNIT = re.compile(r"(?i)\s*(?:s|secs?|seconds?)$")
_TOLERANCE = 0.05 + 1e-9


def _cited_values(text: str, path: str) -> list[str]:
    """Readings of the value written right after ``path``.

    A JSON string literal is read exactly. Otherwise the value without a
    trailing remark (``; ...`` or `` (...)``) and, when that differs, the
    whole remainder.
    """
    match = _CITED_VALUE.match(text[text.find(path) + len(path) :])
    if match is None:
        return []
    raw = match.group(1)
    if raw.startswith('"'):
        try:
            literal, _ = json.JSONDecoder().raw_decode(raw)
        except ValueError:
            literal = None
        if isinstance(literal, str):
            return [literal]
    cut = _VALUE_TAIL.sub("", raw)
    return [cut] if cut == raw else [cut, raw]


def _cited_value(text: str, path: str) -> str | None:
    """The first reading of the value written after ``path`` (or ``None``)."""
    values = _cited_values(text, path)
    return values[0] if values else None


def _number(text: str) -> float | None:
    try:
        return float(_UNIT.sub("", text))
    except ValueError:
        return None


def _resolved_value(view: Mapping[str, Any], resolution: Mapping[str, Any]) -> Any:
    """The value at the location the resolution bound, and nowhere else."""
    return _walk(view, str(resolution["location"]).split("."))


def _same(cited: str, actual: Any) -> bool:
    quoted = cited.strip().strip("\"'`")
    text = quoted.rstrip(".")
    if actual is None:
        return text.lower() in ("null", "none")
    if isinstance(actual, bool):
        return text.lower() == str(actual).lower()
    cited_number = _number(text)
    if isinstance(actual, int | float):
        return cited_number is not None and abs(cited_number - actual) <= _TOLERANCE
    if quoted == str(actual) or text == str(actual).rstrip("."):
        return True
    actual_number = _number(str(actual))
    if cited_number is None or actual_number is None:
        return False
    return abs(cited_number - actual_number) <= _TOLERANCE


def evidence_value_hits(
    view: Mapping[str, Any], resolutions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """R7: ``path = value`` citations whose value differs from the view.

    Object and list values are not compared (see
    ``docs/CITATION_NAMESPACES.md``, object-valued evidence).
    """
    hits: list[dict[str, Any]] = []
    for resolution in resolutions:
        if resolution["kind"] == ReferenceKind.UNRESOLVED.value:
            continue
        text = str(resolution["text"])
        cited = _cited_values(text, str(resolution["path"]))
        found, actual = _resolved_value(view, resolution)
        if not cited or not found or isinstance(actual, dict | list):
            continue
        if not any(_same(value, actual) for value in cited):
            hits.append(
                {
                    "rule": "R7",
                    "check": "value_mismatch",
                    "field": "evidence_used",
                    "text": text,
                    "view_value": actual,
                    "counts_as_violation": True,
                }
            )
    return hits


def _ref(text: str, path: str, label: str, location: str) -> dict[str, Any]:
    return {
        "text": text,
        "kind": ReferenceKind.SOURCE_PATH_REF.value,
        "path": path,
        "source_path": label,
        "location": location,
    }


def validate_assessment(
    assessment: Mapping[str, Any],
    view: Mapping[str, Any] | None,
    *,
    prohibited_hit_count: int = 0,
    annotations: Mapping[DeathImportanceFactorId, FactorAnnotation] | None = None,
) -> dict[str, Any]:
    """Run every rule; ``view=None`` leaves paths unchecked (not_validated).

    Claim-family hits block regardless of ``view``: without a view the roster
    capability is off and no judgment subcategory is authorized.
    """
    causal: list[dict[str, Any]] = []
    for field in ("assessment", "recommendations", "limitations"):
        value = assessment.get(field)
        for text in value if isinstance(value, list) else [value or ""]:
            causal.extend(causal_map_claim_hits(str(text), field=field))
    active = list((view or {}).get("importance", {}).get("active_factor_ids") or [])
    map_grounded = "rec_map_after_respawn" in grounded_templates(view or {})
    recommendations = (
        []
        if map_grounded
        else unsupported_recommendation_hits(
            assessment, active_factor_ids=active, annotations=annotations
        )
    )
    resolutions = (
        [
            resolve_evidence_reference(view, str(item))
            for item in assessment.get("evidence_used") or []
        ]
        if view is not None
        else []
    )
    unresolved = [r for r in resolutions if r["kind"] == ReferenceKind.UNRESOLVED.value]
    family_hits = claim_rule_hits(assessment, view)
    blocking = blocking_hits(family_hits)
    integrity = claim_integrity_hits(assessment, view)
    if view is not None:
        integrity += evidence_value_hits(view, resolutions)
    violations = (
        prohibited_hit_count
        + sum(1 for h in causal if h["counts_as_violation"])
        + sum(1 for h in recommendations if h["counts_as_violation"])
        + len(unresolved)
        + len(integrity)
    )
    if blocking:
        status = ValidationStatus.BLOCKED_UNSUPPORTED_CLAIM
    elif violations:
        status = ValidationStatus.CONTAINS_UNVERIFIED_WORDING
    elif view is None:
        status = ValidationStatus.NOT_VALIDATED
    else:
        status = ValidationStatus.VALIDATED
    return {
        "causal_claim_hits": causal,
        "unsupported_recommendation_hits": recommendations,
        "evidence_path_resolution": resolutions,
        "unresolved_evidence_paths": unresolved,
        "claim_rule_hits": family_hits,
        "integrity_hits": integrity,
        "blocked": bool(blocking),
        "validation_status": status.value,
    }
