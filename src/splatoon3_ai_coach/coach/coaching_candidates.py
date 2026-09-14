"""Type-agnostic coaching candidates and top-N importance ranking.

Candidate generation and per-type scoring are domain-specific. Ranking only
sees ``importance_score`` — it must not assume death (or any single domain).
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field


class ImportanceFactorContribution(BaseModel):
    """One scored factor contribution on a coaching candidate."""

    factor_id: str
    weight: float
    contribution: float
    active: bool
    # Optional locked / catalog copy for VMV (not a separate LLM unit).
    statement_player: str | None = None
    statement_internal: str | None = None
    interpretation: str | None = None
    recommendation: str | None = None


class CoachingCandidate(BaseModel):
    """Rankable coaching unit produced by a domain candidate generator."""

    candidate_id: str
    candidate_type: str
    video_time: float = Field(ge=0)
    importance_score: float = 0.0
    factors: list[ImportanceFactorContribution] = Field(default_factory=list)
    rank: int | None = None
    selected_for_llm: bool = False


def rank_candidates(
    candidates: Sequence[CoachingCandidate],
    *,
    max_llm_units: int,
) -> list[CoachingCandidate]:
    """Sort by coaching importance and mark the top ``max_llm_units``.

    Tie-break (stable): higher score, then earlier ``video_time``, then
    ``candidate_id``. Does not interpret ``candidate_type``.
    """
    if max_llm_units < 0:
        raise ValueError("max_llm_units must be >= 0")
    ordered = sorted(
        candidates,
        key=lambda c: (-float(c.importance_score), float(c.video_time), c.candidate_id),
    )
    ranked: list[CoachingCandidate] = []
    for index, candidate in enumerate(ordered, start=1):
        ranked.append(
            candidate.model_copy(
                update={
                    "rank": index,
                    "selected_for_llm": index <= max_llm_units and max_llm_units > 0,
                }
            )
        )
    return ranked


def active_factor_ids(candidate: CoachingCandidate) -> list[str]:
    """Return factor IDs with ``active`` true (stable order as stored)."""
    return [f.factor_id for f in candidate.factors if f.active]
