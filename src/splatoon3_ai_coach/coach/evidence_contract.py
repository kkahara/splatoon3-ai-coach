"""Coaching evidence contract: permitted claims over ScenarioContext.

ScenarioContext remains observed + deterministic + relational evidence only.
This module documents and enforces how the coaching layer may *speak* about
that evidence. It does not change scenario grouping or schema.

Evidence classes
----------------
1. Observed — GameEvent timestamps / reasons / ids
2. Deterministic — mechanical derivations (durations, counts, flags)
3. Comparative / relational — gaps and scenario links

Temporal relations (``leads_to_death_episode_id``, ``trade_candidate``) are
associations under configured rules, never causal proof.
"""

from __future__ import annotations

import re
from enum import StrEnum

from splatoon3_ai_coach.analysis.scenario_context import (
    CombatContext,
    ScenarioContext,
)
from splatoon3_ai_coach.analysis.scenario_models import Scenario, ScenarioType


class EvidenceClass(StrEnum):
    """How a ScenarioContext fact relates to raw GameEvents."""

    OBSERVED = "observed"
    DETERMINISTIC = "deterministic"
    RELATIONAL = "relational"


class ClaimSupport(StrEnum):
    """Whether a coaching claim is allowed as a factual statement."""

    PERMITTED = "permitted"
    PROHIBITED = "prohibited"
    REQUIRES_NEW_EVIDENCE = "requires_new_evidence"
    REQUIRES_INTERPRETATION = "requires_interpretation"


# Phrases that must not appear as factual coaching claims.
PROHIBITED_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\byou won (the |a )?fight\b",
        r"\byou lost (the |a )?fight\b",
        r"\bbad engagement\b",
        r"\bgood engagement\b",
        r"\boverextend(?:ed|ing)?\b",
        r"\boutnumbered\b",
        r"\bshould have retreated\b",
        r"\bshould have checked (the )?map\b",
        r"\bmap usage was (good|bad)\b",
        r"\b(your )?gear made (your )?respawn\b",
        r"\bquick respawn\b",
        r"\byou rushed\b",
        r"\byou hesitated\b",
        r"\bspawn[- ]?camp(?:ed|ing)?\b",
        r"\bsplat caused (your |the )?death\b",
        r"\bcaused your death\b",
        r"\bcaused the death\b",
        r"\bsame fight\b",
        r"\bclean duel\b",
        r"\btraded poorly\b",
        r"\byou traded\b",
    )
)

# Lightweight cues that the prohibited phrase is being rejected, not asserted.
_NEGATION_OR_LIMIT_CUES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bcannot\s+infer\b",
        r"\bcannot\s+conclude\b",
        r"\bcannot\s+determine\b",
        r"\bcannot\s+say\b",
        r"\bcannot\s+claim\b",
        r"\bcan\s+not\s+(?:infer|conclude|determine|say|claim)\b",
        r"\bnot\s+evidence\s+of\b",
        r"\bno\s+evidence\b",
        r"\bthere\s+is\s+no\s+evidence\b",
        r"\bdoes\s+not\s+imply\b",
        r"\bdoes\s+not\s+establish\b",
        r"\bdoes\s+not\s+prove\b",
        r"\bdo\s+not\s+(?:imply|establish|prove)\b",
        r"\bdid\s+not\b",
        r"\bnot\s+proven\b",
        r"\bnot\s+necessarily\b",
        r"\bnot\s+enough\s+evidence\b",
        r"\binsufficient\s+evidence\b",
        r"\babsence\s+of\b",
        r"\bwithout\s+(?:enough\s+)?evidence\b",
        r"\bunable\s+to\s+(?:infer|conclude|determine|claim)\b",
        r"\bno\s+way\s+to\s+(?:infer|conclude|determine)\b",
    )
)

_LOCAL_NEGATION = re.compile(
    r"(?i)\b(?:not|never|no)\s+(?:\w+\s+){0,3}$"
)

_UNCERTAIN_CUES = re.compile(
    r"(?i)\b(?:uncertain|unclear|unknown|maybe|might|possibly|perhaps)\b"
)


class ClaimHitClassification(StrEnum):
    """How a prohibited-phrase regex hit relates to an asserted claim."""

    ASSERTION = "assertion"
    NEGATED = "negated"
    LIMITATION = "limitation"
    UNCERTAIN = "uncertain"


# Safe templates for temporal association wording.
LEADS_TO_ASSOCIATION_TEMPLATE = (
    "This splat is associated with the subsequent death under the "
    "configured temporal rule."
)
LEADS_TO_WINDOW_TEMPLATE = (
    "A splat was observed within the configured pre-death window."
)
TRADE_CANDIDATE_TEMPLATE = (
    "A splat-to-death gap falls within the configured temporal window "
    "(trade_candidate); this does not prove an actual trade occurred."
)


def claim_contains_prohibited_language(text: str) -> bool:
    """Return True when ``text`` asserts a prohibited claim (not a negation)."""
    return any(
        item["counts_as_violation"] for item in iter_prohibited_claim_hits(text)
    )


def find_prohibited_matches(text: str) -> list[str]:
    """Return matched prohibited substrings that count as violations."""
    return [
        str(item["match"])
        for item in iter_prohibited_claim_hits(text)
        if item["counts_as_violation"]
    ]


def iter_prohibited_claim_hits(
    text: str, *, field: str | None = None
) -> list[dict[str, object]]:
    """Find prohibited-pattern hits with assertion vs negation classification."""
    hits: list[dict[str, object]] = []
    for pattern in PROHIBITED_CLAIM_PATTERNS:
        for match in pattern.finditer(text):
            classification = classify_prohibited_hit(
                text, match.start(), match.end(), field=field
            )
            counts = classification is ClaimHitClassification.ASSERTION
            hits.append(
                {
                    "match": match.group(0),
                    "text": text,
                    "classification": classification.value,
                    "counts_as_violation": counts,
                    "span": (match.start(), match.end()),
                }
            )
    return hits


def classify_prohibited_hit(
    text: str,
    match_start: int,
    match_end: int,
    *,
    field: str | None = None,
) -> ClaimHitClassification:
    """Classify a regex hit as assertion, negated, limitation, or uncertain.

    Lightweight sentence heuristics only — not general NLI.
    """
    _ = match_end
    sent_start, sent_end = _sentence_bounds(text, match_start)
    sentence = text[sent_start:sent_end]
    prefix = text[sent_start:match_start]
    prefix_l = prefix.lower()
    sentence_l = sentence.lower()

    for cue in _NEGATION_OR_LIMIT_CUES:
        for cue_match in cue.finditer(sentence):
            abs_start = sent_start + cue_match.start()
            if abs_start < match_start:
                return ClaimHitClassification.NEGATED

    if _LOCAL_NEGATION.search(prefix_l):
        return ClaimHitClassification.NEGATED

    imply_at = sentence_l.find("does not imply")
    if imply_at >= 0 and sent_start + imply_at < match_start:
        return ClaimHitClassification.NEGATED

    if field == "limitations":
        return ClaimHitClassification.LIMITATION

    if _UNCERTAIN_CUES.search(prefix):
        return ClaimHitClassification.UNCERTAIN

    return ClaimHitClassification.ASSERTION


def _sentence_bounds(text: str, index: int) -> tuple[int, int]:
    """Return ``[start, end)`` for the sentence containing ``index``."""
    if not text:
        return 0, 0
    index = max(0, min(index, len(text) - 1))
    start = index
    while start > 0 and text[start - 1] not in ".!?\n":
        start -= 1
    end = index
    while end < len(text) and text[end] not in ".!?\n":
        end += 1
    if end < len(text) and text[end] in ".!?":
        end += 1
    return start, end


def describe_leads_to_association(*, use_window_phrasing: bool = False) -> str:
    """Safe wording for ``leads_to_death_episode_id`` (association, not cause)."""
    if use_window_phrasing:
        return LEADS_TO_WINDOW_TEMPLATE
    return LEADS_TO_ASSOCIATION_TEMPLATE


def describe_trade_candidate() -> str:
    """Safe wording for ``trade_candidate`` (window flag, not a proven trade)."""
    return TRADE_CANDIDATE_TEMPLATE


def engagement_proves_complete_fight(combat: CombatContext | None) -> bool:
    """ENGAGEMENT never proves a complete combat encounter.

    Always False: splat clusters are observations, not fight boundaries.
    """
    _ = combat
    return False


def death_lifecycle_statements(ctx: ScenarioContext) -> list[str]:
    """Permitted factual statements from a death-episode context."""
    death = ctx.death_episode
    if death is None:
        return []
    lines: list[str] = []
    if death.death_to_active_again is not None:
        lines.append(
            f"This death lasted {death.death_to_active_again:.1f} seconds "
            "until active again."
        )
    if death.death_to_respawn is not None:
        lines.append(
            f"Respawn occurred {death.death_to_respawn:.1f} seconds after death."
        )
    if death.respawn_to_active_again is not None:
        lines.append(
            f"It took {death.respawn_to_active_again:.1f} seconds from respawn "
            "to active control."
        )
    if death.complete:
        lines.append("The death episode recovered (respawn and active_again observed).")
    elif death.has_respawn or death.has_active_again:
        lines.append("The death episode is incomplete (missing lifecycle edges).")
    else:
        lines.append("The death episode is incomplete (death only).")
    if death.respawn_reason is not None:
        lines.append(f"Observed respawn path: {death.respawn_reason}.")
    return lines


def map_observation_statements(ctx: ScenarioContext) -> list[str]:
    """Permitted map statements without quality judgments."""
    nest = ctx.map
    if nest is None or ctx.death_episode is None:
        return []
    lines: list[str] = []
    during = nest.map_checks_during_death_episode
    if during is not None:
        lines.append(
            f"{during} map overlay(s) were observed during the death episode."
        )
    if nest.map_check_before_death is True:
        gap = nest.seconds_since_map_check_before_death
        if gap is not None:
            lines.append(
                "At least one map overlay occurred before this death "
                f"(unbounded lookback; last overlay {gap:.1f}s earlier)."
            )
        else:
            lines.append(
                "At least one map overlay occurred before this death "
                "(unbounded lookback)."
            )
    elif nest.map_check_before_death is False:
        lines.append("No map overlay was observed before this death.")
    return lines


def engagement_observation_statements(
    scenario: Scenario, ctx: ScenarioContext
) -> list[str]:
    """Permitted ENGAGEMENT statements (splat observations, not fight wins)."""
    if scenario.scenario_type is not ScenarioType.ENGAGEMENT:
        return []
    combat = ctx.combat
    if combat is None:
        return ["No combat nest is present for this ENGAGEMENT scenario."]
    lines = [
        f"This ENGAGEMENT scenario contains {combat.splat_count} observed "
        f"splat(s)."
    ]
    if combat.first_splat_time is not None:
        lines.append(f"First observed splat at {combat.first_splat_time:.1f}s.")
    if combat.duration is not None:
        lines.append(
            f"Observed splat span (duration) is {combat.duration:.1f}s; "
            "this is not a proven fight boundary."
        )
    if ctx.relations.leads_to_death_episode_id is not None:
        lines.append(describe_leads_to_association())
    if combat.trade_candidate:
        lines.append(describe_trade_candidate())
    if ctx.relations.follows_death_episode_id is not None:
        lines.append(
            "This ENGAGEMENT is the first observed ENGAGEMENT after return "
            f"to control for {ctx.relations.follows_death_episode_id}."
        )
    return lines


def classify_unsupported_desire(topic: str) -> ClaimSupport:
    """Classify why a common coaching desire is unsupported as a fact.

    ``topic`` is a short keyword such as ``fight_quality``, ``map_advice``,
    ``gear``, or ``causation``.
    """
    key = topic.strip().lower().replace(" ", "_")
    interpretation = {
        "map_advice",
        "map_quality",
        "engagement_quality",
        "overextension",
        "avoidable_death",
        "should_have",
    }
    new_evidence = {
        "fight_boundaries",
        "damage",
        "weapons",
        "positions",
        "enemy_count",
        "teammates",
        "loadout",
        "abilities",
        "objective",
        "score",
        "match_clock",
        "special",
        "map_content",
        "same_fight",
    }
    if key in interpretation:
        return ClaimSupport.REQUIRES_INTERPRETATION
    if key in new_evidence or key in {"fight_quality", "won_fight", "lost_fight"}:
        return ClaimSupport.REQUIRES_NEW_EVIDENCE
    if key in {"causation", "splat_caused_death"}:
        return ClaimSupport.PROHIBITED
    return ClaimSupport.REQUIRES_NEW_EVIDENCE


def evidence_contract_summary() -> dict[str, list[str]]:
    """Machine-readable permitted / prohibited categories for docs and tests."""
    return {
        "permitted": [
            "death lifecycle timings (death_to_respawn, death_to_active_again, …)",
            "respawn_reason as observed path",
            "map overlay counts and presence during / before death (no quality)",
            "death spacing (previous/next death gaps)",
            "ENGAGEMENT splat_count / first_splat_time / last_splat_time / duration",
            "relations as temporal associations (leads_to, follows, next_engagement)",
            "trade_candidate as configured window flag only",
            "comparisons across episodes when fields exist on both",
            "ally_alive_count / opponent_alive_count roster state when player_count_samples or player_count_window present",
            "player_count present_by / duration_since_present_by as sampled presence bounds ('observed by T, Δt before anchor'; not continuous disadvantaged time)",
        ],
        "prohibited_as_facts": [
            "won/lost fight, clean duel, outnumbered in the fight, overextended",
            "bad/good engagement or map usage judgments",
            "should have retreated / checked map",
            "gear / Quick Respawn / Super Jump attributions",
            "rushed / hesitated / spawn-camped intent",
            "splat caused death (use association wording instead)",
            "splats were the same fight without richer evidence",
            "inferring alive counts from splat/death/scenario membership",
            "treating a roster-count transition as a named teammate death or trade",
            "saying the player was continuously disadvantaged for duration_since_present_by seconds",
        ],
        "requires_new_evidence": [
            "fight boundaries, damage, weapons, positions",
            "generic enemy_count / fight participants (vs roster alive counts)",
            "loadout/abilities",
            "objective/score/special state",
            "richer map content / gaze",
        ],
        "requires_interpretation": [
            "whether map use or an engagement was good/bad",
            "whether a death was avoidable",
            "what the player should have done",
        ],
    }
