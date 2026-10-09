"""Claim-family rules for model coaching text (R1-R4, R6 of the claim policy).

Each family names a kind of claim the current evidence cannot license:
causation (R1), roster (R2), teammate / opponent state (R3), special usage
(R4) and gameplay judgment (R6). Hedging never exempts a hit: "may have
contributed" is still a causal claim. Negations and the ``limitations``
field are recorded but exempt. For the state families (roster, teammate /
opponent) a sentence that declares the state unknown is an abstention, not
a claim, and is exempt too.

Judgment subcategories are banned by authorization, not forever: a
``claim_basis`` entry whose ``authorizes`` lists a subcategory licenses it.
The roster family applies unless the view says the roster capability is on.

Hits are annotations; nothing here rewrites model text. A counting hit is a
*blocking* violation: the public page shows deterministic statements instead.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from splatoon3_ai_coach.coach.evidence_contract import (
    ClaimHitClassification,
    classify_prohibited_hit,
    split_sentences,
)


class ClaimFamily(StrEnum):
    """Unsupported-claim families; each maps to one policy rule."""

    CAUSATION = "causation"
    ROSTER = "roster"
    TEAMMATE_OPPONENT = "teammate_opponent"
    SPECIAL_USAGE = "special_usage"
    JUDGMENT = "judgment"


RULE_BY_FAMILY: dict[ClaimFamily, str] = {
    ClaimFamily.CAUSATION: "R1",
    ClaimFamily.ROSTER: "R2",
    ClaimFamily.TEAMMATE_OPPONENT: "R3",
    ClaimFamily.SPECIAL_USAGE: "R4",
    ClaimFamily.JUDGMENT: "R6",
}


@dataclass(frozen=True)
class ClaimPattern:
    """One lexical cue for a claim family and subcategory."""

    family: ClaimFamily
    subcategory: str
    pattern: re.Pattern[str]
    needs_death_subject: bool = False


def _p(
    family: ClaimFamily, sub: str, regex: str, *, death_subject: bool = False
) -> ClaimPattern:
    return ClaimPattern(family, sub, re.compile(regex, re.IGNORECASE), death_subject)


_DEATH_SUBJECT = re.compile(
    r"(?i)\b(?:death|died|die|dying|splat\w*|killed|trade\w*|respawn\w*)\b"
)

CLAIM_PATTERNS: tuple[ClaimPattern, ...] = (
    _p(
        ClaimFamily.CAUSATION,
        "causation",
        r"\b(?:caus\w*|contribut\w*|led\s+to|lead(?:s|ing)?\s+to|result(?:ed|s|ing)?"
        r"\s+in|due\s+to|cost\s+(?:you|your\s+team)|responsible\s+for)\b",
        death_subject=True,
    ),
    _p(
        ClaimFamily.CAUSATION,
        "counterfactual",
        r"\b(?:because|which\s+is\s+why|that'?s\s+why|so\s+you\s+(?:died|were\s+"
        r"splatted|got\s+splatted))\b",
        death_subject=True,
    ),
    _p(
        ClaimFamily.CAUSATION,
        "counterfactual",
        r"\b(?:if\s+you\s+had|would\s+(?:not\s+)?have\s+(?:died|survived|been\s+"
        r"splatted|lived)|could\s+have\s+survived)\b",
    ),
    _p(
        ClaimFamily.ROSTER,
        "alive_counts",
        r"\b[0-4]\s*[-\u2011]?\s*v(?:s\.?|ersus)?\s*[-\u2011]?\s*[0-4]\b",
    ),
    _p(
        ClaimFamily.ROSTER,
        "alive_counts",
        r"\b[0-4]\s*[-\u2010\u2011\u2013]\s*[0-4]\s+(?:advantage|disadvantage|lead"
        r"|deficit|situation|numbers)\b",
    ),
    _p(
        ClaimFamily.ROSTER,
        "alive_counts",
        r"\b(?:players?\s+alive|alive\s+(?:count|players?)|outnumber\w*"
        r"|numbers?\s+(?:advantage|disadvantage)|(?:man|player)\s+advantage"
        r"|even\s+(?:teams|numbers)|roster)\b",
    ),
    _p(
        ClaimFamily.TEAMMATE_OPPONENT,
        "teammate_state",
        r"\b(?:teammates?|allies|team)\s+(?:was|were|had\s+been|had|got)\s+(?:all\s+)?"
        r"(?:dead|down|splatted|wiped|away|gone|far|elsewhere|out\s+of\s+position)\b"
        r"|\b(?:no|without\s+(?:any\s+)?)\s*(?:teammates?|allies)\s+(?:nearby|around|"
        r"alive|to\s+help)\b|\blast\s+(?:player|one|squid)\s+(?:alive|standing)\b"
        r"|\b(?:no|without)\s+(?:team\s+)?support\b|\b(?:were|was)\s+(?:alone|isolated)\b",
    ),
    _p(
        ClaimFamily.TEAMMATE_OPPONENT,
        "opponent_location",
        r"\b(?:enem(?:y|ies)|opponents?)\s+(?:was|were|is|are)\s+(?:behind|flanking|"
        r"waiting|positioned|hiding|camping)\b|\bflank(?:ed|ing)\b",
    ),
    _p(
        ClaimFamily.SPECIAL_USAGE,
        "special_usage",
        r"\b(?:should|could|might|would)\s+have\s+(?:used|popped|activated|fired)\s+"
        r"(?:your\s+|the\s+|a\s+|that\s+)?special\b"
        r"|\bspecial\s+(?:was\s+|got\s+|had\s+been\s+)?(?:used|wasted|fired|activated|"
        r"popped|saved|held)\b"
        r"|\b(?:didn'?t|did\s+not|never|failed\s+to)\s+use\s+(?:your\s+|the\s+)?special\b"
        r"|\b(?:unused|wasted)\s+special\b|\buse\s+(?:your\s+|the\s+)?special\s+"
        r"(?:earlier|sooner|first|before)\b",
    ),
    _p(
        ClaimFamily.JUDGMENT,
        "positioning",
        r"\b(?:out\s+of\s+position|over-?extend\w*|pushed\s+too\s+(?:far|deep)"
        r"|too\s+far\s+forward|beyond\s+(?:your|the)\s+(?:team'?s?\s+)?(?:battle\s+)?"
        r"line|(?:bad|poor)\s+position(?:ing)?)\b",
    ),
    _p(
        ClaimFamily.JUDGMENT,
        "pursuit",
        r"\b(?:over-?pursu\w*|chas\w*\s+too\s+(?:far|long|deep))\b",
    ),
    _p(
        ClaimFamily.JUDGMENT,
        "aggression",
        r"\b(?:too\s+(?:aggressive|passive)(?:ly)?|reckless\w*)\b",
    ),
    _p(
        ClaimFamily.JUDGMENT,
        "counterfactual_action",
        r"\bshould(?:n'?t|\s+not)?\s+have\s+(?:retreated|disengaged|regrouped|waited|"
        r"backed\s+(?:off|out)|pulled\s+back|fallen\s+back|stayed\s+back|pushed|"
        r"played\s+(?:more\s+)?(?:safe|passive)\w*)\b"
        r"|\bbetter\s+to\s+(?:retreat|wait|disengage|regroup)\b",
    ),
    _p(
        ClaimFamily.JUDGMENT,
        "outcome_judgment",
        r"\b(?:mistakes?|misplay\w*|(?:bad|avoidable|unnecessary|careless)\s+death"
        r"|disadvantageous\s+(?:fight|engagement|situation|matchups?)"
        r"|(?:bad|poor|losing|lost)\s+(?:fight|engagement|trade)|threw\s+away|feeding)\b",
    ),
)

_SCANNED_FIELDS = ("assessment", "recommendations", "limitations", "claims")
_STATE_FAMILIES = frozenset({ClaimFamily.ROSTER, ClaimFamily.TEAMMATE_OPPONENT})
_ABSTENTION = re.compile(
    r"(?i)\b(?:unknown|unavailable|not\s+(?:available|provided|observed|known|present)"
    r"|no\s+(?:\w+\s+){0,2}(?:information|data|evidence|samples?)"
    r"|(?:cannot|can\s+not|can'?t|unable\s+to|impossible\s+to|not\s+possible\s+to)"
    r"\s+(?:determine|tell|say|know|establish|assess|confirm))\b"
)
_EXEMPT = (ClaimHitClassification.NEGATED, ClaimHitClassification.LIMITATION)


def roster_capability_on(view: Mapping[str, Any] | None) -> bool:
    """Roster claims are licensed only when the view says the capability is on."""
    return bool(view) and view.get("roster_evidence") is True


def authorized_subcategories(view: Mapping[str, Any] | None) -> set[str]:
    """Judgment subcategories licensed by ``claim_basis`` entries."""
    out: set[str] = set()
    for entry in (view or {}).get("claim_basis") or []:
        if isinstance(entry, Mapping):
            out.update(str(item) for item in entry.get("authorizes") or [])
    return out


def _texts(assessment: Mapping[str, Any]) -> Iterable[tuple[str, str]]:
    for field in _SCANNED_FIELDS:
        value = assessment.get(field)
        for text in value if isinstance(value, list) else [value or ""]:
            if isinstance(text, Mapping):
                text = text.get("sentence") or ""
            yield field, str(text)


def _classify(text: str, start: int, end: int, field: str) -> ClaimHitClassification:
    """Classify with the match masked, so cues inside it ("did not use") count."""
    masked = text[:start] + "x" * (end - start) + text[end:]
    return classify_prohibited_hit(masked, start, end, field=field)


def _applies(item: ClaimPattern, view: Mapping[str, Any] | None) -> bool:
    if item.family is ClaimFamily.ROSTER:
        return not roster_capability_on(view)
    if item.family is ClaimFamily.JUDGMENT:
        return item.subcategory not in authorized_subcategories(view)
    return True


def claim_rule_hits(
    assessment: Mapping[str, Any], view: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    """Every family hit in the prose fields, with its classification."""
    hits: list[dict[str, Any]] = []
    active = [item for item in CLAIM_PATTERNS if _applies(item, view)]
    for field, text in _texts(assessment):
        for start, sentence in split_sentences(text):
            for item in active:
                if item.needs_death_subject and not _DEATH_SUBJECT.search(sentence):
                    continue
                for match in item.pattern.finditer(sentence):
                    hits.append(_hit(item, field, text, sentence, start, match))
    return hits


def _hit(
    item: ClaimPattern,
    field: str,
    text: str,
    sentence: str,
    start: int,
    match: re.Match[str],
) -> dict[str, Any]:
    if item.family in _STATE_FAMILIES and _ABSTENTION.search(sentence):
        label, counts = "abstention", False
    else:
        classification = _classify(
            text, start + match.start(), start + match.end(), field
        )
        counts = classification not in _EXEMPT
        label = ClaimHitClassification.ASSERTION.value if counts else classification.value
    return {
        "rule": RULE_BY_FAMILY[item.family],
        "family": item.family.value,
        "subcategory": item.subcategory,
        "field": field,
        "match": match.group(0),
        "sentence": sentence.strip(),
        "classification": label,
        "counts_as_violation": counts,
    }


def blocking_hits(hits: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Counting family hits; any one withholds the prose from public display."""
    return [hit for hit in hits if hit.get("counts_as_violation")]


JA_CLAIM_PATTERNS: tuple[tuple[ClaimFamily, str, re.Pattern[str]], ...] = (
    (
        ClaimFamily.CAUSATION,
        "causation",
        re.compile(r"原因|せいで|招いた|につなが(?:った|り)|に起因|が響いた"),
    ),
    (
        ClaimFamily.CAUSATION,
        "counterfactual",
        re.compile(
            r"(?:して|使って|見て|確認して|下がって)(?:いれば|いたら)|防げた|避けられた"
            r"|生き残れた|やられずに済んだ"
        ),
    ),
    (
        ClaimFamily.ROSTER,
        "alive_counts",
        re.compile(r"人数(?:不利|有利|差)|数的(?:不利|有利)|[0-4]\s*(?:対|vs|v)\s*[0-4]"),
    ),
    (
        ClaimFamily.TEAMMATE_OPPONENT,
        "teammate_state",
        re.compile(r"味方(?:が|は)(?:全滅|やられ|倒され|いな(?:かった|い))|孤立|最後の一人"),
    ),
    (
        ClaimFamily.SPECIAL_USAGE,
        "special_usage",
        re.compile(r"スペシャルを(?:使う|吐く|使用する)べき|スペシャルを(?:温存|無駄に|使わな|吐かな)"),
    ),
    (
        ClaimFamily.JUDGMENT,
        "judgment",
        re.compile(
            r"ミス|無理な|深追い|前に出(?:すぎ|過ぎ)|突っ込みすぎ|無駄なデス|不用意|軽率"
            r"|(?:立ち位置|ポジション)が悪|べきだった|べきでした"
        ),
    ),
)


def ja_claim_hits(text: str) -> list[dict[str, str]]:
    """Family cues in Japanese text (translations of validated English)."""
    return [
        {"rule": RULE_BY_FAMILY[family], "family": family.value,
         "subcategory": sub, "match": match.group(0)}
        for family, sub, pattern in JA_CLAIM_PATTERNS
        for match in pattern.finditer(text)
    ]


def translation_introduced_hits(english: str, japanese: str) -> list[dict[str, str]]:
    """Japanese family hits whose family the English source never mentioned.

    Any English mention (even negated) of a family allows that family in the
    translation, so faithful renderings of limitations are not flagged.
    """
    source = {hit["family"] for hit in claim_rule_hits({"assessment": english}, None)}
    return [hit for hit in ja_claim_hits(japanese) if hit["family"] not in source]
