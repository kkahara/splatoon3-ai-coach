"""Score and rank every death-episode coaching unit of one analyzed match."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from splatoon3_ai_coach.coach.claim_catalog import CoachingUnitResult
from splatoon3_ai_coach.coach.coach_input import (
    CoachInput,
    build_coach_input_for_scenario,
)
from splatoon3_ai_coach.coach.coaching_candidates import rank_candidates
from splatoon3_ai_coach.coach.death_importance import (
    apply_candidate_ranking,
    resolve_match_duration_seconds,
    score_death_candidate,
)
from splatoon3_ai_coach.coach.importance_config import (
    importance_config_hash,
    importance_config_payload,
)
from splatoon3_ai_coach.coach.load_analysis import (
    CoachAnalysisBundle,
    select_primary_scenario_ids,
)
from splatoon3_ai_coach.config.models import AppConfig


@dataclass(frozen=True)
class MatchScoring:
    """Ranked units for one match, plus the configuration that ranked them."""

    match_duration_seconds: int | None
    max_llm_units: int
    units: list[CoachingUnitResult]
    coach_inputs: dict[str, CoachInput]
    importance_config: dict[str, Any]
    importance_config_hash: str

    def units_by_rank(self) -> list[CoachingUnitResult]:
        """Units ordered by rank, unranked last."""
        return sorted(
            self.units,
            key=lambda u: (u.rank is None, u.rank if u.rank is not None else 10**9),
        )


def score_match(
    bundle: CoachAnalysisBundle,
    app_config: AppConfig,
    *,
    limit: int,
    max_llm_units: int | None = None,
) -> MatchScoring:
    """Build CoachInputs, score each death episode, and rank them for the LLM."""
    coach = app_config.coach
    top_n = int(coach.max_llm_units if max_llm_units is None else max_llm_units)
    match_duration = resolve_match_duration_seconds(
        bundle.game_clock,
        candidates=tuple(app_config.vision.lifecycle.opening_clock_seconds),
    )
    scored: list[CoachingUnitResult] = []
    inputs: dict[str, CoachInput] = {}
    for scenario_id in select_primary_scenario_ids(bundle.scenarios, limit=limit):
        coach_input = _coach_input(bundle, app_config, scenario_id)
        scored.append(
            score_death_candidate(
                coach_input,
                match_duration_seconds=match_duration,
                weights=coach.death_importance_weights,
                thresholds=coach.death_factor_thresholds,
                modifier_factors=coach.death_modifier_factors,
                ranking_excluded_factors=coach.death_ranking_excluded_factors,
            )
        )
        inputs[scenario_id] = coach_input
    ranked = rank_candidates(
        [u.to_candidate() for u in scored],
        max_llm_units=top_n,
        require_positive_score=coach.llm_units_require_positive_score,
    )
    payload = importance_config_payload(coach, max_llm_units=top_n)
    return MatchScoring(
        match_duration_seconds=match_duration,
        max_llm_units=top_n,
        units=apply_candidate_ranking(scored, ranked),
        coach_inputs=inputs,
        importance_config=payload,
        importance_config_hash=importance_config_hash(payload),
    )


def _coach_input(
    bundle: CoachAnalysisBundle, app_config: AppConfig, scenario_id: str
) -> CoachInput:
    coach = app_config.coach
    return build_coach_input_for_scenario(
        scenario_id,
        bundle.scenarios,
        bundle.contexts,
        bundle.game_clock,
        max_gap_seconds=coach.game_clock_max_lookup_gap_seconds,
        player_count_clock=bundle.player_count_clock,
        player_count_max_gap_seconds=coach.player_count_max_lookup_gap_seconds,
        player_count_window_offsets_seconds=coach.player_count_window_offsets_seconds,
        player_count_context_lookback_seconds=(
            coach.player_count_context_lookback_seconds
        ),
        battle_mode_id=bundle.battle_mode_id,
        include_score_facts=True,
        include_zone_control_facts=True,
    )
