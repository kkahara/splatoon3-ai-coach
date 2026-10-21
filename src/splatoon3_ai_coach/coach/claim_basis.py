"""Claim definitions, eligibility and the per-unit ``claim_basis``.

Flow: raw evidence → deterministic eligibility → ``claim_basis`` → LLM wording
→ deterministic validation. The model chooses wording, emphasis and ordering
among authorized claims; it never creates a new factual, causal, judgmental or
counterfactual claim.

Levels:

* **A** observed fact — state directly, no hedge required.
* **B** supported interpretation — a general principle or process tied to an
  A fact; never a causal claim about this death.
* **C** plausible hypothesis — only through a whitelisted ``ClaimDefinition``
  with ``level=C`` (none today). Hedging an A/B fact never creates C.
* **D** undetermined — never stated. Topics live in ``UNDETERMINED_TOPICS``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.map_coverage import MapCheckStatus
from splatoon3_ai_coach.analysis.scenario_evidence import SpecialGaugeState
from splatoon3_ai_coach.coach.claim_catalog import (
    CoachingUnitResult,
    DeathImportanceFactorId,
)
from splatoon3_ai_coach.coach.coach_input import CoachInput
from splatoon3_ai_coach.coach.death_importance import (
    death_game_clock_sample,
    own_splat_gap,
    previous_death_gap,
)

Values = dict[str, Any]
Eligibility = Callable[[CoachInput, CoachingUnitResult], Values | None]
_F = DeathImportanceFactorId


class ClaimLevel(StrEnum):
    """Claim strength; A is strongest. A stated level may not exceed its basis."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"


LEVEL_RANK: dict[str, int] = {level.value: i for i, level in enumerate(ClaimLevel)}


@dataclass(frozen=True)
class ClaimDefinition:
    """One authorizable claim and the evidence it needs.

    ``coachable`` separates "the fact exists" from "the fact is worth offering
    as coaching content". ``authorizes`` lists judgment subcategories (rule R6)
    this claim licenses; ``rec_template_ids`` lists recommendation template IDs
    (rule R10) it grounds.

    ``evidence_paths`` are literal scalar CoachLlmView leaves, one for every
    value the claim carries; ``unit_paths`` adds leaves whose index depends on
    the unit (which LOW_INK interval).
    """

    claim_id: str
    level: ClaimLevel
    frame: str
    evidence_paths: tuple[str, ...]
    eligible: Eligibility
    coachable: bool
    public_ok: bool = True
    contradicted_by: tuple[str, ...] = ()
    unit_paths: Callable[[CoachInput], tuple[str, ...]] | None = None
    authorizes: tuple[str, ...] = ()
    rec_template_ids: tuple[str, ...] = ()


class ClaimBasisEntry(BaseModel):
    """One eligible claim as exposed to the model.

    ``rec_frames`` is the wording of each ``rec_template_ids`` template, in the
    same order; R10 only accepts recommendations that reword one of them.
    """

    claim_id: str
    level: ClaimLevel
    coachable: bool
    frame: str
    evidence_paths: list[str] = Field(default_factory=list)
    values: Values = Field(default_factory=dict)
    authorizes: list[str] = Field(default_factory=list)
    rec_template_ids: list[str] = Field(default_factory=list)
    rec_frames: list[str] = Field(default_factory=list)


class UndeterminedTopic(StrEnum):
    """Closed set of topics the current evidence cannot establish."""

    CAUSE_OF_DEATH = "cause_of_death"
    SPECIAL_USAGE = "special_usage"
    TEAMMATE_STATE = "teammate_state"
    OPPONENT_LOCATION = "opponent_location"
    POSITIONING = "positioning"
    FIGHT_OUTCOME = "fight_outcome"
    ROSTER = "roster"


class UndeterminedEntry(BaseModel):
    """A topic the model must not touch, with eligible A/B alternatives."""

    topic: UndeterminedTopic
    reason: str
    alternative_claim_ids: list[str] = Field(default_factory=list)


def _active(unit: CoachingUnitResult, factor: _F) -> bool:
    return any(f.factor_id == factor.value and f.active for f in unit.factors)


def _factor(factor: _F, values: Callable[[CoachInput], Values]) -> Eligibility:
    def check(coach_input: CoachInput, unit: CoachingUnitResult) -> Values | None:
        return values(coach_input) if _active(unit, factor) else None

    return check


def _redeath_values(coach_input: CoachInput) -> Values:
    gap = previous_death_gap(coach_input)
    if gap is None:
        return {}
    return {"time_since_previous_death": gap.value, "clock": gap.clock}


def _clock_values(coach_input: CoachInput) -> Values:
    sample = death_game_clock_sample(coach_input)
    obs = sample.observation if sample is not None else None
    if obs is None:
        return {}
    return {"seconds_remaining": obs.seconds_remaining, "display": obs.display}


def _clock_sample(coach_input: CoachInput, _unit: CoachingUnitResult) -> Values | None:
    values = _clock_values(coach_input)
    return values or None


def _special_state(state: SpecialGaugeState) -> Eligibility:
    def check(coach_input: CoachInput, _unit: CoachingUnitResult) -> Values | None:
        special = coach_input.primary_context.special
        if special is None or special.nearest_before_anchor is None:
            return None
        if special.state_at_anchor is not state:
            return None
        return {"state_at_anchor": state.value}

    return check


def _map_status(status: MapCheckStatus) -> Eligibility:
    def check(coach_input: CoachInput, _unit: CoachingUnitResult) -> Values | None:
        map_ctx = coach_input.primary_context.map
        if map_ctx is None or map_ctx.map_check_status != status:
            return None
        values: Values = {"status": status.value}
        if status is MapCheckStatus.OBSERVED:
            seconds = map_ctx.seconds_since_map_check_before_death
            values["seconds_since_map_check_before_death"] = seconds
        return values

    return check


def _out_of_play(coach_input: CoachInput, _unit: CoachingUnitResult) -> Values | None:
    durations = coach_input.durations
    span = durations.death_to_active_again if durations is not None else None
    if span is None or span.value is None:
        return None
    return {"death_to_active_again": span.value, "clock": span.clock}


def _score_values(coach_input: CoachInput) -> Values:
    facts = coach_input.score_facts
    if facts is None:
        return {}
    return {
        "ally_remaining": facts.ally_remaining,
        "opponent_remaining": facts.opponent_remaining,
    }


def _roster_factor(factor: _F) -> Eligibility:
    def check(coach_input: CoachInput, unit: CoachingUnitResult) -> Values | None:
        if not coach_input.roster_evidence or not _active(unit, factor):
            return None
        before = unit.roster_before_death
        if before is None:
            return {}
        return {
            "ally_alive_count": before.ally_alive_count,
            "opponent_alive_count": before.opponent_alive_count,
        }

    return check


def _own_splat_values(coach_input: CoachInput) -> Values:
    return {"time_since_previous_splat": own_splat_gap(coach_input)}


def _low_ink_hit(coach_input: CoachInput) -> tuple[int, float, float] | None:
    """(index, start, death time) of the first LOW_INK interval containing
    the death (closed interval)."""
    ctx = coach_input.primary_context
    death = ctx.death_episode
    if ctx.low_ink is None or death is None or death.death_time is None:
        return None
    death_time = float(death.death_time)
    for index, interval in enumerate(ctx.low_ink.intervals):
        if interval.start_time <= death_time <= interval.end_time:
            return index, interval.start_time, death_time
    return None


def _low_ink_at_death(
    coach_input: CoachInput, _unit: CoachingUnitResult
) -> Values | None:
    """A LOW_INK interval that had started by the death (closed interval)."""
    hit = _low_ink_hit(coach_input)
    return None if hit is None else {"start_time": hit[1], "death_time": hit[2]}


def _low_ink_paths(coach_input: CoachInput) -> tuple[str, ...]:
    hit = _low_ink_hit(coach_input)
    return () if hit is None else (f"low_ink.intervals[{hit[0]}].start_time",)


def _requires(claim_id: str) -> Eligibility:
    """B claims ride on an eligible A claim; values are copied from it."""

    def check(coach_input: CoachInput, unit: CoachingUnitResult) -> Values | None:
        return CLAIMS_BY_ID[claim_id].eligible(coach_input, unit)

    return check


_SPECIAL_PATH = "special.state_at_anchor"
_MAP_PATH = "map.map_check_before_death.status"
_REDEATH_PATHS = (
    "timeline.time_since_previous_death.value",
    "timeline.time_since_previous_death.clock",
)
_CLOCK_PATHS = ("clock.seconds_remaining", "clock.display")
_SCORE_PATHS = ("score.ally_remaining", "score.opponent_remaining")
_ROSTER_PATHS = (
    "roster.before_death.ally_alive_count",
    "roster.before_death.opponent_alive_count",
)
_LOW_INK_PATHS = ("low_ink.at_death", "death.death_time_video")

CLAIM_DEFINITIONS: tuple[ClaimDefinition, ...] = (
    ClaimDefinition(
        claim_id=_F.DEATH_QUICK_REDEATH.value,
        level=ClaimLevel.A,
        frame="Your previous death was {time_since_previous_death}s earlier.",
        evidence_paths=_REDEATH_PATHS,
        eligible=_factor(_F.DEATH_QUICK_REDEATH, _redeath_values),
        coachable=True,
    ),
    ClaimDefinition(
        claim_id=_F.DEATH_OWN_SPLAT_BEFORE.value,
        level=ClaimLevel.A,
        frame="You got a splat {time_since_previous_splat}s before you were splatted.",
        evidence_paths=("death.time_since_previous_splat.video_seconds",),
        eligible=_factor(_F.DEATH_OWN_SPLAT_BEFORE, _own_splat_values),
        coachable=True,
        contradicted_by=("calling it a trade or an exchange",),
    ),
    ClaimDefinition(
        claim_id="death_low_ink",
        level=ClaimLevel.A,
        frame="The low-ink warning was showing when you were splatted.",
        evidence_paths=_LOW_INK_PATHS,
        unit_paths=_low_ink_paths,
        eligible=_low_ink_at_death,
        coachable=True,
    ),
    ClaimDefinition(
        claim_id="death_out_of_play",
        level=ClaimLevel.A,
        frame="You were out of play for {death_to_active_again}s after this death.",
        evidence_paths=(
            "death.death_to_active_again.value",
            "death.death_to_active_again.clock",
        ),
        eligible=_out_of_play,
        coachable=True,
    ),
    ClaimDefinition(
        claim_id=_F.DEATH_FINAL_30S.value,
        level=ClaimLevel.A,
        frame="This death happened with only {display} left in the match.",
        evidence_paths=_CLOCK_PATHS,
        eligible=_factor(_F.DEATH_FINAL_30S, _clock_values),
        coachable=True,
    ),
    ClaimDefinition(
        claim_id=_F.DEATH_FIRST_30S.value,
        level=ClaimLevel.A,
        frame="This death happened early in the match, with {display} left.",
        evidence_paths=_CLOCK_PATHS,
        eligible=_factor(_F.DEATH_FIRST_30S, _clock_values),
        coachable=True,
    ),
    ClaimDefinition(
        claim_id=_F.DEATH_SPECIAL_READY.value,
        level=ClaimLevel.A,
        frame="Your special was still charged when you were splatted.",
        evidence_paths=(_SPECIAL_PATH,),
        eligible=_factor(_F.DEATH_SPECIAL_READY, lambda _: {"state_at_anchor": "ready"}),
        coachable=True,
        contradicted_by=("special.state_at_anchor != ready",),
    ),
    ClaimDefinition(
        claim_id="special_not_ready_at_death",
        level=ClaimLevel.A,
        frame="Your special gauge was visible and not full just before the death.",
        evidence_paths=(_SPECIAL_PATH,),
        eligible=_special_state(SpecialGaugeState.NOT_READY),
        coachable=False,
        contradicted_by=("special.state_at_anchor != not_ready",),
    ),
    ClaimDefinition(
        claim_id="special_gauge_not_visible",
        level=ClaimLevel.A,
        frame="The special gauge was not visible just before the death.",
        evidence_paths=(_SPECIAL_PATH,),
        eligible=_special_state(SpecialGaugeState.NOT_OBSERVED),
        coachable=False,
    ),
    ClaimDefinition(
        claim_id=_F.DEATH_MAP_OVERLAY_BEFORE_FALSE.value,
        level=ClaimLevel.A,
        frame="No map check was observed before this death.",
        evidence_paths=(_MAP_PATH,),
        eligible=_factor(
            _F.DEATH_MAP_OVERLAY_BEFORE_FALSE, lambda _: {"status": "not_observed"}
        ),
        coachable=True,
        contradicted_by=("map.map_check_before_death.status != not_observed",),
    ),
    ClaimDefinition(
        claim_id="map_check_observed_before_death",
        level=ClaimLevel.A,
        frame=(
            "A map check was observed {seconds_since_map_check_before_death}s "
            "before this death (any earlier check, not necessarily a recent one)."
        ),
        evidence_paths=(_MAP_PATH, "map.seconds_since_map_check_before_death"),
        eligible=_map_status(MapCheckStatus.OBSERVED),
        coachable=False,
    ),
    ClaimDefinition(
        claim_id="timer_sample_exists",
        level=ClaimLevel.A,
        frame="The match timer showed {display} at this death.",
        evidence_paths=_CLOCK_PATHS,
        eligible=_clock_sample,
        coachable=False,
    ),
    ClaimDefinition(
        claim_id=_F.DEATH_WHILE_BEHIND_IN_COUNT.value,
        level=ClaimLevel.A,
        frame="Just before the death your team needed more counts than the opponents.",
        evidence_paths=_SCORE_PATHS,
        eligible=_factor(_F.DEATH_WHILE_BEHIND_IN_COUNT, _score_values),
        coachable=False,
    ),
    ClaimDefinition(
        claim_id=_F.DEATH_WHILE_AHEAD_IN_COUNT.value,
        level=ClaimLevel.A,
        frame="Just before the death your team needed fewer counts than the opponents.",
        evidence_paths=_SCORE_PATHS,
        eligible=_factor(_F.DEATH_WHILE_AHEAD_IN_COUNT, _score_values),
        coachable=False,
    ),
    ClaimDefinition(
        claim_id=_F.DEATH_OPPONENT_COUNTER_TICKED.value,
        level=ClaimLevel.A,
        frame="The opponent counter went down in the few seconds before the death.",
        evidence_paths=(
            "score.opponent_counter_decreased_before_death",
            *_SCORE_PATHS,
        ),
        eligible=_factor(_F.DEATH_OPPONENT_COUNTER_TICKED, _score_values),
        coachable=False,
    ),
    ClaimDefinition(
        claim_id=_F.DEATH_WHILE_OUTNUMBERED.value,
        level=ClaimLevel.A,
        frame="Just before the death your team had fewer players alive.",
        evidence_paths=_ROSTER_PATHS,
        eligible=_roster_factor(_F.DEATH_WHILE_OUTNUMBERED),
        coachable=True,
        public_ok=False,
    ),
    ClaimDefinition(
        claim_id=_F.DEATH_WHILE_AHEAD_IN_NUMBERS.value,
        level=ClaimLevel.A,
        frame="Just before the death your team had more players alive.",
        evidence_paths=_ROSTER_PATHS,
        eligible=_roster_factor(_F.DEATH_WHILE_AHEAD_IN_NUMBERS),
        coachable=False,
        public_ok=False,
    ),
    ClaimDefinition(
        claim_id="principle_redeath_out_of_play",
        level=ClaimLevel.B,
        frame="Back-to-back deaths keep you out of play for longer.",
        evidence_paths=_REDEATH_PATHS,
        eligible=_requires(_F.DEATH_QUICK_REDEATH.value),
        coachable=True,
        rec_template_ids=("rec_redeath_reset",),
    ),
    ClaimDefinition(
        claim_id="process_ink_before_engaging",
        level=ClaimLevel.B,
        frame="Refilling ink before re-engaging means starting the next fight with ink.",
        evidence_paths=_LOW_INK_PATHS,
        unit_paths=_low_ink_paths,
        eligible=_requires("death_low_ink"),
        coachable=True,
        rec_template_ids=("rec_refill_ink",),
    ),
    ClaimDefinition(
        claim_id="process_map_after_respawn",
        level=ClaimLevel.B,
        frame=(
            "A quick map check after respawning shows where the action is "
            "before you rejoin."
        ),
        evidence_paths=(_MAP_PATH,),
        eligible=_requires(_F.DEATH_MAP_OVERLAY_BEFORE_FALSE.value),
        coachable=True,
        rec_template_ids=("rec_map_after_respawn",),
    ),
)

CLAIMS_BY_ID: dict[str, ClaimDefinition] = {c.claim_id: c for c in CLAIM_DEFINITIONS}

# Level C exists only through this whitelist (empty today).
C_LEVEL_WHITELIST: frozenset[str] = frozenset(
    c.claim_id for c in CLAIM_DEFINITIONS if c.level is ClaimLevel.C
)


@dataclass(frozen=True)
class _Topic:
    reason: str
    alternatives: tuple[str, ...]


_ALTERNATIVES = (
    _F.DEATH_QUICK_REDEATH.value,
    _F.DEATH_OWN_SPLAT_BEFORE.value,
    "death_low_ink",
    "death_out_of_play",
    _F.DEATH_MAP_OVERLAY_BEFORE_FALSE.value,
    _F.DEATH_FINAL_30S.value,
)

UNDETERMINED_TOPICS: dict[UndeterminedTopic, _Topic] = {
    UndeterminedTopic.CAUSE_OF_DEATH: _Topic(
        "No evidence identifies why the death happened; temporal facts are "
        "associations, never causes.",
        _ALTERNATIVES,
    ),
    UndeterminedTopic.SPECIAL_USAGE: _Topic(
        "Special activations are not detected; a gauge reading is not a usage event.",
        (_F.DEATH_SPECIAL_READY.value, "special_not_ready_at_death"),
    ),
    UndeterminedTopic.TEAMMATE_STATE: _Topic(
        "Teammate positions, availability and support are not observed.",
        _ALTERNATIVES,
    ),
    UndeterminedTopic.OPPONENT_LOCATION: _Topic(
        "Opponent positions, flanks and sightlines are not observed.",
        _ALTERNATIVES,
    ),
    UndeterminedTopic.POSITIONING: _Topic(
        "Player position is not observed; overextending, pursuing or retreating "
        "cannot be assessed.",
        _ALTERNATIVES,
    ),
    UndeterminedTopic.FIGHT_OUTCOME: _Topic(
        "Splat clusters are not proven fights; win/loss and fight quality are "
        "not observed.",
        _ALTERNATIVES,
    ),
    UndeterminedTopic.ROSTER: _Topic(
        "Roster (alive-count) evidence is not reliable enough for coaching.",
        _ALTERNATIVES,
    ),
}


@dataclass(frozen=True)
class RecommendationTemplate:
    """A closed recommendation shape (rule R10) and the wording it matches."""

    template_id: str
    frame: str
    pattern: re.Pattern[str]


RECOMMENDATION_TEMPLATES: dict[str, RecommendationTemplate] = {
    t.template_id: t
    for t in (
        RecommendationTemplate(
            "rec_map_after_respawn",
            "After you respawn, take a quick look at the map before rejoining.",
            re.compile(
                r"(?i)\b(?:check|look\s+at|open|glance\s+at|use|watch)\s+(?:the\s+|your\s+)?"
                r"map\b|\bmap\s+(?:check|glance|look)\b"
            ),
        ),
        RecommendationTemplate(
            "rec_redeath_reset",
            "After being splatted, take a moment to reset before re-engaging.",
            re.compile(
                r"(?i)\b(?:reset|regroup|take\s+a\s+(?:moment|second|beat)|pause"
                r"|re-?enter\s+(?:carefully|with\s+care))\b"
            ),
        ),
        RecommendationTemplate(
            "rec_refill_ink",
            "Refill your ink before taking the next fight.",
            re.compile(
                r"(?i)\b(?:refill|recharge|top\s+up|restore)\w*\s+(?:your\s+)?ink\b"
                r"|\bink\s+(?:refill|tank)\b"
            ),
        ),
    )
}


def build_claim_basis(
    coach_input: CoachInput, unit: CoachingUnitResult
) -> list[ClaimBasisEntry]:
    """Every claim whose eligibility holds for this unit, in catalog order."""
    out: list[ClaimBasisEntry] = []
    for claim in CLAIM_DEFINITIONS:
        values = claim.eligible(coach_input, unit)
        if values is None:
            continue
        out.append(
            ClaimBasisEntry(
                claim_id=claim.claim_id,
                level=claim.level,
                coachable=claim.coachable,
                frame=claim.frame,
                evidence_paths=[
                    *claim.evidence_paths,
                    *(claim.unit_paths(coach_input) if claim.unit_paths else ()),
                ],
                values=values,
                authorizes=list(claim.authorizes),
                rec_template_ids=list(claim.rec_template_ids),
                rec_frames=[
                    RECOMMENDATION_TEMPLATES[t].frame for t in claim.rec_template_ids
                ],
            )
        )
    return out


def build_undetermined(
    coach_input: CoachInput, basis: list[ClaimBasisEntry]
) -> list[UndeterminedEntry]:
    """Closed undetermined topics; roster only while the capability is off."""
    eligible = {entry.claim_id for entry in basis}
    out: list[UndeterminedEntry] = []
    for topic, spec in UNDETERMINED_TOPICS.items():
        if topic is UndeterminedTopic.ROSTER and coach_input.roster_evidence:
            continue
        out.append(
            UndeterminedEntry(
                topic=topic,
                reason=spec.reason,
                alternative_claim_ids=[c for c in spec.alternatives if c in eligible],
            )
        )
    return out
