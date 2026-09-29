"""EvidenceLimit non-claims for one coaching unit."""

from __future__ import annotations

from pydantic import BaseModel

from splatoon3_ai_coach.analysis.scenario_context import ScenarioContext
from splatoon3_ai_coach.analysis.scenario_models import (
    Scenario,
    ScenarioOutcome,
    ScenarioType,
)
from splatoon3_ai_coach.coach.coach_samples import GameClockSample, PlayerCountSample
from splatoon3_ai_coach.coach.evidence_contract import (
    describe_leads_to_association,
    describe_trade_candidate,
    engagement_proves_complete_fight,
)
from splatoon3_ai_coach.coach.player_count_clock import PlayerCountWindowPoint
from splatoon3_ai_coach.coach.player_count_context import PlayerCountContext
from splatoon3_ai_coach.coach.score_facts import ScoreFacts
from splatoon3_ai_coach.coach.zone_control_facts import ZoneControlFacts


class EvidenceLimit(BaseModel):
    """Structured non-claim: what this unit's evidence cannot establish."""

    code: str
    statement: str


def collect_evidence_limits(
    scenario: Scenario,
    context: ScenarioContext,
    game_clock_samples: list[GameClockSample],
    player_count_samples: list[PlayerCountSample] | None = None,
    *,
    player_count_window: list[PlayerCountWindowPoint] | None = None,
    player_count_context: PlayerCountContext | None = None,
    score_facts: ScoreFacts | None = None,
    zone_control_facts: ZoneControlFacts | None = None,
) -> list[EvidenceLimit]:
    """Derive contract-safe non-claims for this coaching unit."""
    limits: list[EvidenceLimit] = []
    if score_facts is not None:
        limits.extend(score_evidence_limits(score_facts))
    if zone_control_facts is not None:
        limits.extend(zone_control_evidence_limits(zone_control_facts))

    if scenario.scenario_type is ScenarioType.ENGAGEMENT:
        limits.append(
            EvidenceLimit(
                code="engagement_not_complete_fight",
                statement=(
                    "ENGAGEMENT is a splat observation cluster, not a proven "
                    "complete fight; splat observations do not establish win/lose."
                ),
            )
        )
        _ = engagement_proves_complete_fight(context.combat)

    if context.relations.leads_to_death_episode_id is not None:
        limits.append(
            EvidenceLimit(
                code="leads_to_association_not_causation",
                statement=describe_leads_to_association(),
            )
        )

    if context.combat is not None and context.combat.trade_candidate:
        limits.append(
            EvidenceLimit(
                code="trade_candidate_window_only",
                statement=describe_trade_candidate(),
            )
        )

    if context.map is not None and context.map.map_check_before_death is not None:
        limits.append(
            EvidenceLimit(
                code="map_check_before_death_unbounded",
                statement=(
                    "map_check_before_death means any map overlay occurred before "
                    "the death (unbounded lookback); it is not evidence of a "
                    "recent check or map-usage quality."
                ),
            )
        )
    elif (
        context.death_episode is not None
        and context.map is not None
        and context.map.map_check_before_death is None
    ):
        limits.append(
            EvidenceLimit(
                code="map_check_unobservable",
                statement=(
                    "Map-check evidence is unavailable or not assertable for this "
                    "video source (unobservable or potentially_observable). "
                    "Absence of MAP_OVERLAY must not be treated as proof the "
                    "player did not check the map."
                ),
            )
        )

    if scenario.outcome in (ScenarioOutcome.FRAGGED, ScenarioOutcome.DIED):
        limits.append(
            EvidenceLimit(
                code="outcome_not_fight_quality",
                statement=(
                    f"Scenario outcome '{scenario.outcome.value}' is linkage "
                    "vocabulary only; it is not fight-quality evidence."
                ),
            )
        )

    for sample in game_clock_samples:
        if sample.observation is None:
            limits.append(
                EvidenceLimit(
                    code="game_clock_missing",
                    statement=(
                        f"Match remaining time unknown at video time "
                        f"{sample.video_time:.1f}s (label={sample.label})."
                    ),
                )
            )

    for sample in player_count_samples or []:
        if sample.observation is None:
            limits.append(
                EvidenceLimit(
                    code="player_count_missing",
                    statement=(
                        f"Roster alive counts unknown at video time "
                        f"{sample.video_time:.1f}s (label={sample.label}). "
                        "Do not infer alive counts from splat or death events."
                    ),
                )
            )

    if player_count_context is not None:
        valid = player_count_context.valid_point_count
        total = player_count_context.window_point_count
        if total > 0 and valid <= 1:
            limits.append(
                EvidenceLimit(
                    code="player_count_window_sparse",
                    statement=(
                        f"Player-count window has {valid}/{total} valid points; "
                        "do not treat it as a reliable roster trajectory. "
                        "A fused roster change is not proof of a particular "
                        "teammate death or trade."
                    ),
                )
            )
        elif player_count_window:
            limits.append(
                EvidenceLimit(
                    code="player_count_not_causal",
                    statement=(
                        "Fused roster count changes describe team alive totals "
                        "only; they do not identify which player died, prove a "
                        "trade, or establish fight quality. "
                        "duration_since_present_by means the numbers_state was "
                        "observed by state_present_by that many seconds before "
                        "the anchor — not that the team was continuously "
                        "disadvantaged for that entire interval."
                ),
            )
        )

    return limits


def score_evidence_limits(facts: ScoreFacts) -> list[EvidenceLimit]:
    """Non-claims for Splat Zones counts (mode, availability, zone holder)."""
    if not facts.is_splat_zones:
        mode = facts.battle_mode_id or "unresolved"
        return [
            EvidenceLimit(
                code="score_not_splat_zones",
                statement=(
                    f"Battle mode is {mode}, not Splat Zones; no remaining-count "
                    "evidence exists for this match. Do not mention counts."
                ),
            )
        ]
    limits: list[EvidenceLimit] = []
    if not facts.has_both_counts:
        limits.append(
            EvidenceLimit(
                code="score_count_unavailable",
                statement=(
                    "At least one remaining count was not observed near the "
                    "death; do not state or estimate a count difference."
                ),
            )
        )
    limits.append(
        EvidenceLimit(
            code="score_not_zone_holder",
            statement=(
                "Remaining counts and counter changes do not identify who held "
                "the zone or why a counter changed. They are not evidence of "
                "zone control, of pushing or losing the objective, or of the "
                "count causing the death."
            ),
        )
    )
    if facts.penalty_not_shown_treated_as_zero:
        limits.append(
            EvidenceLimit(
                code="penalty_not_shown_as_zero",
                statement=(
                    "No penalty was visible where one side's count_before_progress "
                    "is 0; a missed penalty read would make it larger. Penalty "
                    "never counts toward the result."
                ),
            )
        )
    return limits


def zone_control_evidence_limits(
    facts: ZoneControlFacts,
) -> list[EvidenceLimit]:
    """Non-claims for sparse team-level control evidence."""
    if not facts.is_splat_zones:
        mode = facts.battle_mode_id or "unresolved"
        return [
            EvidenceLimit(
                code="zone_control_not_splat_zones",
                statement=(
                    f"Battle mode is {mode}, not Splat Zones; no zone-control "
                    "evidence exists for this match."
                ),
            )
        ]
    limits: list[EvidenceLimit] = []
    if facts.control_at_death is None and facts.control_before_death is None:
        limits.append(
            EvidenceLimit(
                code="zone_control_unavailable",
                statement=(
                    "No observed zone-control state is available near the "
                    "scenario anchor or death."
                ),
            )
        )
    if not facts.transition_times:
        limits.append(
            EvidenceLimit(
                code="zone_control_transition_unavailable",
                statement=(
                    "No confirmed control transition is available in the "
                    "scenario evidence window."
                ),
            )
        )
    limits.append(
        EvidenceLimit(
            code="zone_control_player_causality_unavailable",
            statement=(
                "Team-level control transitions do not identify which player "
                "caused a capture or loss, and do not prove causation."
            ),
        )
    )
    return limits
