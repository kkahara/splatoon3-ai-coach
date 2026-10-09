"""Claim-integrity rules R5, R7, R8 and R9 (non-blocking; badge only).

* R5 field consistency — state words must match the view ("ready" needs
  ``special.state_at_anchor == ready``; "no map check" needs status
  ``not_observed``; a positive map check is never "recent"; clock windows
  need their factor).
* R7 claim integrity — ``claims[]`` and ``selected_claim_ids`` must name
  ``claim_basis`` entries without upgrading the level.
* R8 internal identifiers — no snake_case IDs or dotted paths in player text.
* R9 abstention honesty — "unknown / not available" must match the view.
* R10 recommendation grounding — each recommendation matches a closed
  template grounded by an eligible ``claim_basis`` entry.
* R11 missed supported claim — "nothing notable" while a coachable A/B
  claim is eligible (evidence conservatism must not become blindness).

Every hit is an annotation; nothing here rewrites model text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from splatoon3_ai_coach.coach.claim_basis import LEVEL_RANK, RECOMMENDATION_TEMPLATES
from splatoon3_ai_coach.coach.evidence_contract import split_sentences

_PLAYER_FIELDS = ("assessment", "recommendations", "claims")
_ALL_FIELDS = (*_PLAYER_FIELDS, "limitations")

_SPECIAL = re.compile(r"(?i)\bspecial\b")
_READY_WORD = re.compile(r"(?i)\b(?:ready|charged|full)\b")
_NOT_BEFORE = re.compile(
    r"(?i)\b(?:not|n't|no\s+longer|never)\s+(?:\w+\s+){0,2}$|n't\s+(?:\w+\s+){0,2}$"
)
_GAUGE_UNSEEN = re.compile(r"(?i)\bnot\s+(?:visible|observed|shown|seen)\b|\bhidden\b")
_MAP = re.compile(r"(?i)\bmap\b")
_MAP_ABSENT = re.compile(
    r"(?i)\bno\s+(?:prior\s+|recent\s+)?map\s+checks?\b"
    r"|\b(?:didn'?t|did\s+not|never)\s+(?:check|open|look\s+at)\s+(?:the\s+|your\s+)?map\b"
    r"|\bwithout\s+(?:checking|opening)\s+(?:the\s+|your\s+)?map\b"
)
_RECENT = re.compile(
    r"(?i)\b(?:recent(?:ly)?|immediately\s+before|just\s+before|right\s+before"
    r"|shortly\s+before|moments?\s+before)\b"
)
# Abstentions ("no evidence that…", "whether…") are not state assertions;
# R9 judges them against the view instead.
_ABSTAINS = re.compile(
    r"(?i)\bno\s+(?:evidence|information|data)\b|\bwhether\b|\bnot\s+possible\b"
    r"|\b(?:cannot|can\s+not|can'?t|unable\s+to)\b"
)
_NEGATION = re.compile(r"(?i)\b(?:no|not|without|never)\b|n't\b")
_FINAL = re.compile(r"(?i)\b(?:final|last|closing)\s+30\s*(?:seconds|s)\b")
_FIRST = re.compile(r"(?i)\b(?:first|opening)\s+30\s*(?:seconds|s)\b")
_WINDOWS = ((_FINAL, "death_final_30s"), (_FIRST, "death_first_30s"))
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+){2,}\b")
_DOTTED = re.compile(r"\b[a-z_]+\[?\d*\]?(?:\.[a-z_]+\[?\d*\]?)+\b")
_UNKNOWN = re.compile(
    r"(?i)\b(?:unknown|not\s+known|not\s+available|unavailable|undetermined"
    r"|(?:cannot|can\s+not|can'?t|could\s+not)\s+be\s+(?:determined|known|"
    r"established|confirmed|assessed)|not\s+(?:determinable|recorded))\b"
)
_TOPICS = {
    "special": re.compile(r"(?i)\bspecial\b"),
    "clock": re.compile(
        r"(?i)\b(?:clock|timer|time\s+remaining|remaining\s+time|match\s+phase"
        r"|phase\s+of\s+the\s+match)\b"
    ),
    "map": re.compile(r"(?i)\bmap[-\s]check"),
}


def _texts(
    assessment: Mapping[str, Any], fields: Iterable[str]
) -> Iterable[tuple[str, str]]:
    for field in fields:
        value = assessment.get(field)
        items = value if isinstance(value, list) else [value or ""]
        for item in items:
            if isinstance(item, Mapping):
                item = item.get("sentence") or ""
            for _, sentence in split_sentences(str(item)):
                yield field, sentence.strip()


def _hit(rule: str, check: str, field: str, text: str, **extra: Any) -> dict[str, Any]:
    return {
        "rule": rule,
        "check": check,
        "field": field,
        "text": text,
        "counts_as_violation": True,
        **extra,
    }


def _special_state(view: Mapping[str, Any]) -> str | None:
    special = view.get("special")
    return special.get("state_at_anchor") if isinstance(special, Mapping) else None


def _map_status(view: Mapping[str, Any]) -> str | None:
    block = (view.get("map") or {}).get("map_check_before_death")
    return block.get("status") if isinstance(block, Mapping) else None


def _active_factors(view: Mapping[str, Any]) -> set[str]:
    return set((view.get("importance") or {}).get("active_factor_ids") or [])


def _special_claim(sentence: str) -> str | None:
    """"ready" / "not_ready" when the sentence states the gauge state."""
    if not _SPECIAL.search(sentence) or _GAUGE_UNSEEN.search(sentence):
        return None
    word = _READY_WORD.search(sentence)
    if word is None or _UNKNOWN.search(sentence) or _ABSTAINS.search(sentence):
        return None
    negated = _NOT_BEFORE.search(sentence[: word.start()])
    return "not_ready" if negated else "ready"


def field_consistency_hits(
    assessment: Mapping[str, Any], view: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """R5: special / map / clock-window wording must match the view."""
    hits: list[dict[str, Any]] = []
    state, status = _special_state(view), _map_status(view)
    active = _active_factors(view)
    for field, sentence in _texts(assessment, _PLAYER_FIELDS):
        claimed = _special_claim(sentence)
        if claimed is not None and claimed != state:
            hits.append(_hit("R5", "special_state", field, sentence, view_state=state))
        if _MAP.search(sentence):
            absent = _MAP_ABSENT.search(sentence)
            if absent and status != "not_observed":
                hits.append(_hit("R5", "map_absence", field, sentence, view_state=status))
            recent = _RECENT.search(sentence)
            if not absent and recent and not _NEGATION.search(sentence[: recent.start()]):
                hits.append(_hit("R5", "map_recency", field, sentence))
        for pattern, factor in _WINDOWS:
            if _ABSTAINS.search(sentence) or _UNKNOWN.search(sentence):
                continue
            if pattern.search(sentence) and factor not in active:
                hits.append(_hit("R5", "clock_window", field, sentence, factor=factor))
    return hits


def _observed(topic: str, view: Mapping[str, Any]) -> bool:
    if topic == "special":
        return _special_state(view) in ("ready", "not_ready")
    if topic == "clock":
        clock = view.get("clock")
        return isinstance(clock, Mapping) and clock.get("seconds_remaining") is not None
    return _map_status(view) in ("observed", "not_observed")


def _listed_unavailable(topic: re.Pattern[str], sentence: str) -> bool:
    """ "no evidence (such as <topic>) is available": the topic sits in the list."""
    body = topic.pattern.removeprefix("(?i)")
    listed = re.compile(
        rf"(?i)\bno\b[^.;]{{0,80}}?{body}[^.;]{{0,40}}?\b(?:is|are|was|were)\s+available"
    )
    return bool(listed.search(sentence))


def abstention_hits(
    assessment: Mapping[str, Any], view: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """R9: "unknown / not available" about a topic the view observed."""
    hits: list[dict[str, Any]] = []
    for field, sentence in _texts(assessment, _ALL_FIELDS):
        unknown = _UNKNOWN.search(sentence)
        unseen = _GAUGE_UNSEEN.search(sentence)
        for topic, pattern in _TOPICS.items():
            if not pattern.search(sentence) or not _observed(topic, view):
                continue
            listed = _listed_unavailable(pattern, sentence)
            if unknown or listed or (topic == "special" and unseen):
                hits.append(_hit("R9", f"false_unknown_{topic}", field, sentence))
    return hits


def internal_identifier_hits(assessment: Mapping[str, Any]) -> list[dict[str, Any]]:
    """R8: snake_case IDs (two or more underscores) or dotted field paths."""
    hits: list[dict[str, Any]] = []
    for field, sentence in _texts(assessment, _PLAYER_FIELDS):
        tokens = [m.group(0) for m in _SNAKE.finditer(sentence)]
        tokens += [
            m.group(0)
            for m in _DOTTED.finditer(sentence)
            if "_" in m.group(0) or m.group(0).count(".") >= 2
        ]
        for token in tokens:
            hits.append(_hit("R8", "internal_identifier", field, sentence, token=token))
    return hits


def claim_reference_hits(
    assessment: Mapping[str, Any], view: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """R7: claims and selected IDs must be in ``claim_basis``; no level upgrade.

    Views written before ``claim_basis`` existed are not checked.
    """
    if "claim_basis" not in view:
        return []
    basis = {
        str(e.get("claim_id")): str(e.get("level"))
        for e in view.get("claim_basis") or []
        if isinstance(e, Mapping)
    }
    hits: list[dict[str, Any]] = []
    for claim_id in assessment.get("selected_claim_ids") or []:
        if str(claim_id) not in basis:
            hits.append(_hit("R7", "unknown_claim", "selected_claim_ids", str(claim_id)))
    for claim in assessment.get("claims") or []:
        if not isinstance(claim, Mapping):
            continue
        claim_id, level = str(claim.get("claim_id")), str(claim.get("level") or "")
        if claim_id not in basis:
            hits.append(_hit("R7", "unknown_claim", "claims", claim_id))
            continue
        floor = basis[claim_id]
        if level and LEVEL_RANK.get(level, -1) < LEVEL_RANK.get(floor, 0):
            hit = _hit("R7", "level_upgrade", "claims", claim_id, basis_level=floor)
            hits.append(hit)
    return hits


_NOTHING_SUPPORTED = re.compile(
    r"(?i)\bnothing\s+(?:notable|noteworthy|actionable|stands\s+out|to\s+coach)\b"
    r"|\bno\s+(?:\w+\s+){0,2}(?:claims?|points?|insights?|feedback|takeaways?)\s+"
    r"(?:is\s+|are\s+|can\s+be\s+)?(?:supported|available|possible|warranted)\b"
    r"|\bno\s+coachable\b"
)


def _basis(view: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [e for e in view.get("claim_basis") or [] if isinstance(e, Mapping)]


def grounded_templates(view: Mapping[str, Any]) -> set[str]:
    """Recommendation template IDs grounded by eligible ``claim_basis`` entries."""
    return {str(t) for e in _basis(view) for t in e.get("rec_template_ids") or []}


def recommendation_grounding_hits(
    assessment: Mapping[str, Any], view: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """R10: every recommendation fits a template an eligible claim grounds."""
    if "claim_basis" not in view:
        return []
    grounded = grounded_templates(view)
    hits: list[dict[str, Any]] = []
    for item in assessment.get("recommendations") or []:
        text = str(item)
        matched = [
            t.template_id
            for t in RECOMMENDATION_TEMPLATES.values()
            if t.pattern.search(text)
        ]
        if not matched:
            hits.append(_hit("R10", "no_template", "recommendations", text))
        elif not grounded.intersection(matched):
            hits.append(
                _hit("R10", "ungrounded_template", "recommendations", text,
                     templates=matched)
            )
    return hits


def missed_claim_hits(
    assessment: Mapping[str, Any], view: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """R11: "nothing notable" although a coachable A/B claim is eligible."""
    coachable = [
        str(e.get("claim_id"))
        for e in _basis(view)
        if e.get("coachable") and e.get("level") in ("A", "B")
    ]
    if not coachable:
        return []
    return [
        _hit("R11", "missed_supported_claim", field, sentence, coachable=coachable)
        for field, sentence in _texts(assessment, ("assessment",))
        if _NOTHING_SUPPORTED.search(sentence)
    ]


def claim_integrity_hits(
    assessment: Mapping[str, Any], view: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    """R5, R7-R11 together; without a view only R8 can run."""
    hits = internal_identifier_hits(assessment)
    if view is None:
        return hits
    return [
        *field_consistency_hits(assessment, view),
        *claim_reference_hits(assessment, view),
        *hits,
        *abstention_hits(assessment, view),
        *recommendation_grounding_hits(assessment, view),
        *missed_claim_hits(assessment, view),
    ]
