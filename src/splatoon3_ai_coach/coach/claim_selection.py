"""Death importance scoring facade (compat imports).

Prefer ``death_importance`` / ``coaching_candidates`` for new call sites.
"""

from __future__ import annotations

from splatoon3_ai_coach.coach.death_importance import (
    apply_candidate_ranking,
    build_supporting_evidence,
    death_game_clock_sample,
    detect_death_importance_factors,
    resolve_match_duration_seconds,
    score_death_candidate,
)

__all__ = [
    "apply_candidate_ranking",
    "build_supporting_evidence",
    "death_game_clock_sample",
    "detect_death_importance_factors",
    "resolve_match_duration_seconds",
    "score_death_candidate",
]
