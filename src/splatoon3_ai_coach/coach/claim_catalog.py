"""Death-episode importance factor catalog (domain-scoped).

These IDs are **death candidate scoring factors**, not global eligibility gates
and not the definition of what is coachable. Locked copy is optional VMV
annotation when a factor is active — not a separate LLM claim card.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from splatoon3_ai_coach.coach.coaching_candidates import (
    CoachingCandidate,
    ImportanceFactorContribution,
)


class DeathImportanceFactorId(StrEnum):
    """Death-scoped importance factor IDs."""

    DEATH_LAST_ALLY_ALIVE = "death_last_ally_alive"
    DEATH_REDEATH_LE_10S = "death_redeath_le_10s"
    DEATH_SPECIAL_READY = "death_special_ready"
    DEATH_FINAL_30S = "death_final_30s"
    DEATH_FIRST_30S = "death_first_30s"
    DEATH_MAP_OVERLAY_BEFORE_FALSE = "death_map_overlay_before_false"


# Backward-compatible alias used by older imports / tests.
ClaimId = DeathImportanceFactorId

CANDIDATE_TYPE_DEATH_EPISODE = "death_episode"

NO_RECOMMENDATION_MESSAGE = (
    "No recommendation supported by the available evidence."
)

DEFAULT_MAX_LLM_UNITS = 3

# Consecutive death spacing (video seconds).
REDEATH_MAX_GAP_SECONDS = 10.0

# Match clock windows (observed timer remaining).
FINAL_30S_REMAINING = 30
FIRST_30S_ELAPSED = 30

# Candidate match durations (seconds) when resolving D from observed timers.
DEFAULT_MATCH_DURATION_CANDIDATES: tuple[int, ...] = (180, 300)

DEFAULT_DEATH_IMPORTANCE_WEIGHTS: dict[str, float] = {
    DeathImportanceFactorId.DEATH_REDEATH_LE_10S.value: 3.0,
    DeathImportanceFactorId.DEATH_LAST_ALLY_ALIVE.value: 2.5,
    DeathImportanceFactorId.DEATH_SPECIAL_READY.value: 2.0,
    DeathImportanceFactorId.DEATH_MAP_OVERLAY_BEFORE_FALSE.value: 1.5,
    DeathImportanceFactorId.DEATH_FINAL_30S.value: 1.0,
    DeathImportanceFactorId.DEATH_FIRST_30S.value: 0.5,
}


class FactorAnnotation(BaseModel):
    """Optional player/dev copy when a death importance factor is active."""

    factor_id: DeathImportanceFactorId
    statement_player: str | None = None
    statement_internal: str | None = None
    interpretation: str | None = None
    recommendation: str | None = None


FACTOR_ANNOTATIONS: dict[DeathImportanceFactorId, FactorAnnotation] = {
    DeathImportanceFactorId.DEATH_LAST_ALLY_ALIVE: FactorAnnotation(
        factor_id=DeathImportanceFactorId.DEATH_LAST_ALLY_ALIVE,
        statement_player=(
            "You were the last ally alive when you were splatted."
        ),
        statement_internal=(
            "At the death-anchored roster sample, ally_alive_count was 1."
        ),
        interpretation=(
            "Being the last ally alive can make survival especially important."
        ),
        recommendation=(
            "When you're the last ally alive, prioritize staying alive "
            "until teammates return."
        ),
    ),
    DeathImportanceFactorId.DEATH_REDEATH_LE_10S: FactorAnnotation(
        factor_id=DeathImportanceFactorId.DEATH_REDEATH_LE_10S,
    ),
    DeathImportanceFactorId.DEATH_SPECIAL_READY: FactorAnnotation(
        factor_id=DeathImportanceFactorId.DEATH_SPECIAL_READY,
        statement_player=(
            "Your special gauge was ready immediately before death."
        ),
        statement_internal=(
            "special.nearest_before_anchor.ready was true at/before death."
        ),
        interpretation=None,
        recommendation=None,
    ),
    DeathImportanceFactorId.DEATH_FINAL_30S: FactorAnnotation(
        factor_id=DeathImportanceFactorId.DEATH_FINAL_30S,
        statement_player=(
            "Death occurred during the final 30 seconds of the match."
        ),
        statement_internal=(
            "Death-labeled game clock seconds_remaining <= 30."
        ),
    ),
    DeathImportanceFactorId.DEATH_FIRST_30S: FactorAnnotation(
        factor_id=DeathImportanceFactorId.DEATH_FIRST_30S,
        statement_player=(
            "Death occurred during the first 30 seconds of the match."
        ),
        statement_internal=(
            "Death-labeled game clock seconds_remaining >= D - 30 "
            "(match duration D known)."
        ),
    ),
    DeathImportanceFactorId.DEATH_MAP_OVERLAY_BEFORE_FALSE: FactorAnnotation(
        factor_id=DeathImportanceFactorId.DEATH_MAP_OVERLAY_BEFORE_FALSE,
        statement_player=(
            "No map overlay was observed before death."
        ),
        statement_internal=(
            "map_check_before_death was false while map overlay is observable."
        ),
    ),
}


class SupportingEvidenceItem(BaseModel):
    """Fact shown as 'why', not as a coaching card."""

    label: str
    value: str
    path: str | None = None


class CoachingUnitResult(BaseModel):
    """Persisted coaching artifact for one candidate (any domain).

    Death is the first ``candidate_type``; ranking fields come from
    ``CoachingCandidate`` / ``rank_candidates``.
    """

    candidate_id: str
    candidate_type: str
    video_time: float = Field(ge=0)
    importance_score: float = 0.0
    factors: list[ImportanceFactorContribution] = Field(default_factory=list)
    rank: int | None = None
    selected_for_llm: bool = False
    supporting_evidence: list[SupportingEvidenceItem] = Field(default_factory=list)
    match_duration_seconds: int | None = None

    @property
    def scenario_id(self) -> str:
        """Alias for death-era call sites."""
        return self.candidate_id

    @property
    def scenario_type(self) -> str:
        """Alias for death-era call sites."""
        return self.candidate_type

    def to_candidate(self) -> CoachingCandidate:
        """Project rankable fields into a ``CoachingCandidate``."""
        return CoachingCandidate(
            candidate_id=self.candidate_id,
            candidate_type=self.candidate_type,
            video_time=self.video_time,
            importance_score=self.importance_score,
            factors=list(self.factors),
            rank=self.rank,
            selected_for_llm=self.selected_for_llm,
        )

    def with_ranking(self, candidate: CoachingCandidate) -> CoachingUnitResult:
        """Copy ranking fields from a ranked ``CoachingCandidate``."""
        return self.model_copy(
            update={
                "importance_score": candidate.importance_score,
                "factors": list(candidate.factors),
                "rank": candidate.rank,
                "selected_for_llm": candidate.selected_for_llm,
            }
        )


# Deprecated names kept for import compatibility in tools that still expect them.
class CoachingPoint(BaseModel):
    """Deprecated: former claim-card shape (do not use for selection)."""

    claim_id: DeathImportanceFactorId
    statement: str
    statement_internal: str | None = None
    interpretation: str | None = None
    recommendation: str | None = None
    evidence_paths: list[str] = Field(default_factory=list)
