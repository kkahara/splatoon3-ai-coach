"""Death-episode claim catalog: IDs, priority, and locked triad templates.

Eligibility and selection live in ``claim_selection``. Locked claim means:
if selected and emitted, the Statement must be accurate — not that advice
is required.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum

from pydantic import BaseModel, Field


class ClaimPriority(IntEnum):
    """Higher values are preferred when selecting coaching points."""

    HIGH = 100
    MEDIUM = 50
    LOW = 10


class ClaimId(StrEnum):
    """Stable claim IDs for selection, VMV, and evaluation."""

    DEATH_LAST_ALLY_ALIVE = "death_last_ally_alive"
    DEATH_REDEATH_LE_10S = "death_redeath_le_10s"
    DEATH_SPECIAL_READY = "death_special_ready"
    DEATH_FINAL_30S = "death_final_30s"
    DEATH_FIRST_30S = "death_first_30s"
    DEATH_MAP_OVERLAY_BEFORE_FALSE = "death_map_overlay_before_false"


NO_RECOMMENDATION_MESSAGE = (
    "No recommendation supported by the available evidence."
)

# Cap unless evaluation demonstrates a need for more.
DEFAULT_MAX_COACHING_POINTS = 3

# Consecutive death spacing gate (video seconds).
REDEATH_MAX_GAP_SECONDS = 10.0

# Match clock windows (observed timer remaining).
FINAL_30S_REMAINING = 30
FIRST_30S_ELAPSED = 30

# Candidate match durations (seconds) when resolving D from observed timers.
DEFAULT_MATCH_DURATION_CANDIDATES: tuple[int, ...] = (180, 300)


class ClaimTemplate(BaseModel):
    """Catalog entry for one claim pattern."""

    claim_id: ClaimId
    priority: ClaimPriority
    # When True, interpretation/recommendation stay null even if candidate text exists.
    interpretation_locked: bool = False
    recommendation_locked: bool = False
    # Locked player-facing copy (None → filled at selection from evidence).
    statement_player: str | None = None
    statement_internal: str | None = None
    interpretation: str | None = None
    recommendation: str | None = None
    # Prefer as supporting evidence when a higher-priority point is selected.
    support_only_when_outranked: bool = False


CLAIM_TEMPLATES: dict[ClaimId, ClaimTemplate] = {
    ClaimId.DEATH_LAST_ALLY_ALIVE: ClaimTemplate(
        claim_id=ClaimId.DEATH_LAST_ALLY_ALIVE,
        priority=ClaimPriority.HIGH,
        interpretation_locked=True,
        recommendation_locked=True,
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
    ClaimId.DEATH_REDEATH_LE_10S: ClaimTemplate(
        claim_id=ClaimId.DEATH_REDEATH_LE_10S,
        priority=ClaimPriority.MEDIUM,
        # Candidate advice — not locked; leave triad interpretation/rec null.
        statement_player=None,
        statement_internal=None,
    ),
    ClaimId.DEATH_SPECIAL_READY: ClaimTemplate(
        claim_id=ClaimId.DEATH_SPECIAL_READY,
        priority=ClaimPriority.LOW,
        interpretation_locked=True,
        recommendation_locked=True,
        statement_player=(
            "Your special gauge was ready immediately before death."
        ),
        statement_internal=(
            "special.nearest_before_anchor.ready was true at/before death."
        ),
        interpretation=None,
        recommendation=None,
        support_only_when_outranked=True,
    ),
    ClaimId.DEATH_FINAL_30S: ClaimTemplate(
        claim_id=ClaimId.DEATH_FINAL_30S,
        priority=ClaimPriority.LOW,
        interpretation_locked=False,
        recommendation_locked=True,
        statement_player=(
            "Death occurred during the final 30 seconds of the match."
        ),
        statement_internal=(
            "Death-labeled game clock seconds_remaining <= 30."
        ),
        interpretation=None,
        recommendation=None,
        support_only_when_outranked=True,
    ),
    ClaimId.DEATH_FIRST_30S: ClaimTemplate(
        claim_id=ClaimId.DEATH_FIRST_30S,
        priority=ClaimPriority.LOW,
        interpretation_locked=False,
        recommendation_locked=True,
        statement_player=(
            "Death occurred during the first 30 seconds of the match."
        ),
        statement_internal=(
            "Death-labeled game clock seconds_remaining >= D - 30 "
            "(match duration D known)."
        ),
        interpretation=None,
        recommendation=None,
        support_only_when_outranked=True,
    ),
    ClaimId.DEATH_MAP_OVERLAY_BEFORE_FALSE: ClaimTemplate(
        claim_id=ClaimId.DEATH_MAP_OVERLAY_BEFORE_FALSE,
        priority=ClaimPriority.LOW,
        # Candidate soft principle — not locked.
        statement_player=(
            "No map overlay was observed before death."
        ),
        statement_internal=(
            "map_check_before_death was false while map overlay is observable."
        ),
        support_only_when_outranked=True,
    ),
}


class CoachingPoint(BaseModel):
    """One selected coaching point (Statement required; rest optional)."""

    claim_id: ClaimId
    statement: str
    statement_internal: str | None = None
    interpretation: str | None = None
    recommendation: str | None = None
    evidence_paths: list[str] = Field(default_factory=list)


class SupportingEvidenceItem(BaseModel):
    """Fact shown as 'why', not as a coaching card."""

    label: str
    value: str
    path: str | None = None


class CoachingUnitResult(BaseModel):
    """Deterministic coaching output for one primary scenario."""

    scenario_id: str
    scenario_type: str
    video_time: float = Field(ge=0)
    eligible_claim_ids: list[ClaimId] = Field(default_factory=list)
    coaching_points: list[CoachingPoint] = Field(default_factory=list)
    supporting_evidence: list[SupportingEvidenceItem] = Field(default_factory=list)
    match_duration_seconds: int | None = None
