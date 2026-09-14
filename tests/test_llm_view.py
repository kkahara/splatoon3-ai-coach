"""CoachLlmView projection: compact, citeable, no invented facts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from splatoon3_ai_coach.analysis.scenario_context import (
    CombatContext,
    DeathEpisodeContext,
    MapContext,
    ScenarioContext,
    ScenarioRelations,
    TimelineContext,
)
from splatoon3_ai_coach.analysis.scenario_evidence import (
    PlayersEvidence,
    PlayerCountPoint,
    SpecialEvidence,
    SpecialReading,
)
from splatoon3_ai_coach.analysis.scenario_models import (
    Scenario,
    ScenarioOutcome,
    ScenarioType,
)
from splatoon3_ai_coach.coach.coach import (
    generate_coaching_assessment,
    serialize_llm_view_user_prompt,
)
from splatoon3_ai_coach.coach.coach_input import (
    CoachInput,
    GameClockSample,
    RelatedScenarioEvidence,
    build_coach_input_for_scenario,
)
from splatoon3_ai_coach.coach.coaching_candidates import rank_candidates
from splatoon3_ai_coach.coach.death_importance import (
    apply_candidate_ranking,
    resolve_match_duration_seconds,
    score_death_candidate,
)
from splatoon3_ai_coach.coach.game_clock import GameClockObservation
from splatoon3_ai_coach.coach.llm_client import LLMProvider
from splatoon3_ai_coach.coach.llm_view import build_coach_llm_view, build_death_llm_view
from splatoon3_ai_coach.coach.load_analysis import load_coach_analysis_bundle
from splatoon3_ai_coach.coach.player_count_clock import (
    PlayerCountObservation,
    PlayerCountWindowPoint,
)
from splatoon3_ai_coach.config import default_config_path, load_config

_REPO = Path(__file__).resolve().parents[1]
_ANALYSIS_SEP10 = _REPO / "analysis" / "2026-09-10 15-58-36"


def _death_input(
    *,
    start: float = 100.0,
    ally: int = 1,
    opponent: int = 3,
    special_ready: bool = True,
    map_before: bool = False,
    seconds_remaining: int = 200,
    with_special_series: bool = True,
    with_related: bool = True,
) -> CoachInput:
    scenario = Scenario(
        scenario_id=f"death_episode:{start:.3f}",
        scenario_type=ScenarioType.DEATH_EPISODE,
        start_time=start,
        end_time=start + 8.0,
        event_ids=[f"DEATH@{start:.3f}"],
        confidence=1.0,
        outcome=ScenarioOutcome.DIED,
    )
    observations = []
    if with_special_series:
        for i in range(20):
            observations.append(
                SpecialReading(
                    video_time=start - 10.0 + i * 0.5,
                    visible=True,
                    ready=False,
                    fill_fraction=0.5,
                )
            )
    special = SpecialEvidence(
        nearest_before_anchor=SpecialReading(
            video_time=start - 0.5,
            visible=True,
            ready=special_ready,
            fill_fraction=1.0 if special_ready else 0.5,
        ),
        observations=observations,
    )
    context = ScenarioContext(
        scenario_id=scenario.scenario_id,
        timeline=TimelineContext(duration=8.0, time_since_previous_death=8.0),
        map=MapContext(
            map_check_before_death=map_before,
            seconds_since_map_check_before_death=(
                12.0 if map_before is False else 20.0
            ),
            map_check_count=1,
        ),
        death_episode=DeathEpisodeContext(
            death_time=start,
            respawn_time=start + 6.0,
            active_again_time=start + 7.5,
            death_to_respawn=6.0,
            death_to_active_again=7.5,
            complete=True,
            has_respawn=True,
            has_active_again=True,
            is_first_death=False,
        ),
        players=PlayersEvidence(
            at_death=PlayerCountPoint(
                video_time=start,
                ally_alive_count=ally,
                opponent_alive_count=opponent,
            ),
            trajectory=[
                PlayerCountPoint(
                    video_time=start - i,
                    ally_alive_count=ally,
                    opponent_alive_count=opponent,
                )
                for i in range(15)
            ],
        ),
        special=special,
        relations=ScenarioRelations(next_engagement_id="engagement:200.000"),
    )
    related: list[RelatedScenarioEvidence] = []
    if with_related:
        eng = Scenario(
            scenario_id="engagement:200.000",
            scenario_type=ScenarioType.ENGAGEMENT,
            start_time=200.0,
            end_time=201.0,
            event_ids=["SPLAT@200.000"],
            confidence=1.0,
            outcome=ScenarioOutcome.FRAGGED,
        )
        related.append(
            RelatedScenarioEvidence(
                role="next_engagement",
                scenario=eng,
                context=ScenarioContext(
                    scenario_id=eng.scenario_id,
                    combat=CombatContext(
                        splat_count=1,
                        first_splat_time=200.0,
                        last_splat_time=200.0,
                        trade_candidate=False,
                    ),
                    special=SpecialEvidence(
                        observations=[
                            SpecialReading(
                                video_time=200.0 + i * 0.2,
                                visible=True,
                                ready=False,
                            )
                            for i in range(25)
                        ]
                    ),
                    relations=ScenarioRelations(),
                ),
            )
        )
    window = [
        PlayerCountWindowPoint(
            offset_seconds=offset,
            video_time=start + offset,
            observation=PlayerCountObservation(
                video_time=start + offset,
                ally_alive_count=ally,
                opponent_alive_count=opponent,
                confidence=1.0,
            ),
            gap_seconds=0.0,
        )
        for offset in (-5.0, 0.0, 3.0)
    ]
    return CoachInput(
        primary_scenario=scenario,
        primary_context=context,
        related=related,
        game_clock_samples=[
            GameClockSample(
                label="death",
                video_time=start,
                observation=GameClockObservation(
                    video_time=start,
                    seconds_remaining=seconds_remaining,
                    confidence=1.0,
                    display=f"{seconds_remaining // 60}:{seconds_remaining % 60:02d}",
                ),
                gap_seconds=0.0,
            )
        ],
        player_count_window=window,
    )


def test_death_view_drops_special_observations_and_related_series() -> None:
    coach_input = _death_input()
    unit = score_death_candidate(coach_input, match_duration_seconds=300)
    view = build_death_llm_view(coach_input, unit)
    assert view.special is not None
    assert "observations" not in json.dumps(view.special)
    assert coach_input.primary_context.special is not None
    assert len(coach_input.primary_context.special.observations) == 20
    related_json = json.dumps(view.related)
    assert "observations" not in related_json
    assert view.related[0]["combat"]["splat_count"] == 1
    assert view.roster is not None
    assert view.roster["at_death"]["ally_alive_count"] == 1
    assert view.clock is not None
    assert view.clock["seconds_remaining"] == 200
    assert view.map is not None
    assert view.map["map_check_before_death"] is False


def test_no_invention_at_death_and_special_match_source() -> None:
    coach_input = _death_input(ally=1, special_ready=True, map_before=False)
    unit = score_death_candidate(coach_input, match_duration_seconds=300)
    view = build_death_llm_view(coach_input, unit)
    assert view.roster is not None
    at = view.roster["at_death"]
    src = coach_input.primary_context.players.at_death
    assert src is not None
    assert at["ally_alive_count"] == src.ally_alive_count
    assert at["opponent_alive_count"] == src.opponent_alive_count
    assert at["source_path"] == "primary_context.players.at_death"
    assert view.special is not None
    nb = view.special["nearest_before_anchor"]
    src_nb = coach_input.primary_context.special.nearest_before_anchor
    assert src_nb is not None
    assert nb["ready"] is src_nb.ready
    assert nb["fill_fraction"] == src_nb.fill_fraction
    assert view.map is not None
    assert (
        view.map["map_check_before_death"]
        == coach_input.primary_context.map.map_check_before_death
    )
    assert view.death is not None
    assert view.death["death_time"] == coach_input.primary_context.death_episode.death_time


def test_view_smaller_than_full_coach_input() -> None:
    coach_input = _death_input()
    unit = score_death_candidate(coach_input, match_duration_seconds=300)
    view = build_death_llm_view(coach_input, unit)
    full = json.dumps(coach_input.model_dump(mode="json"), separators=(",", ":"))
    compact = json.dumps(view.model_dump(mode="json"), separators=(",", ":"))
    # Synthetic fixture is moderately large; require absolute cap + real drop.
    assert len(compact) < 8000
    assert len(compact) < len(full)
    assert "observations" not in compact


def test_serialize_llm_view_used_by_provider() -> None:
    class Recording(LLMProvider):
        def __init__(self) -> None:
            self.user: str | None = None

        def complete(self, system_prompt: str, user_prompt: str) -> str:
            self.user = user_prompt
            return (
                '{"assessment":"ok","evidence_used":[],"limitations":[],'
                '"recommendations":[],"selected_claim_ids":[]}'
            )

    coach_input = _death_input()
    unit = score_death_candidate(coach_input, match_duration_seconds=300)
    view = build_coach_llm_view(coach_input, unit)
    prompt = serialize_llm_view_user_prompt(view)
    provider = Recording()
    generate_coaching_assessment(
        coach_input, provider, llm_view=view, system_prompt="sys"
    )
    assert provider.user == prompt
    assert "CoachLlmView JSON follows" in (provider.user or "")
    full = json.dumps(coach_input.model_dump(mode="json"), separators=(",", ":"))
    assert "observations" not in (provider.user or "")
    assert len(provider.user or "") < len(full)



@pytest.mark.skipif(
    not (_ANALYSIS_SEP10 / "vision_manifest.json").is_file(),
    reason="Sep-10 analysis missing",
)
def test_sep10_llm_view_size_and_last_ally() -> None:
    config = load_config(default_config_path())
    bundle = load_coach_analysis_bundle(
        _ANALYSIS_SEP10,
        min_usable_confidence=config.vision.timer.min_usable_confidence,
    )
    match_duration = resolve_match_duration_seconds(
        bundle.game_clock,
        candidates=tuple(config.vision.lifecycle.opening_clock_seconds),
    )
    scenario_id = "death_episode:235.000"
    coach_input = build_coach_input_for_scenario(
        scenario_id,
        bundle.scenarios,
        bundle.contexts,
        bundle.game_clock,
        max_gap_seconds=config.coach.game_clock_max_lookup_gap_seconds,
        player_count_clock=bundle.player_count_clock,
        player_count_max_gap_seconds=config.coach.player_count_max_lookup_gap_seconds,
        player_count_window_offsets_seconds=(
            config.coach.player_count_window_offsets_seconds
        ),
        player_count_context_lookback_seconds=(
            config.coach.player_count_context_lookback_seconds
        ),
    )
    unit = score_death_candidate(
        coach_input,
        match_duration_seconds=match_duration,
        weights=config.coach.death_importance_weights,
    )
    ranked = rank_candidates([unit.to_candidate()], max_llm_units=3)
    unit = apply_candidate_ranking([unit], ranked)[0]
    view = build_death_llm_view(coach_input, unit)
    full = json.dumps(coach_input.model_dump(mode="json"), separators=(",", ":"))
    compact = json.dumps(view.model_dump(mode="json"), separators=(",", ":"))
    assert len(compact) < 0.2 * len(full)
    assert view.roster is not None
    assert view.roster["at_death"]["ally_alive_count"] == 1
    assert "death_last_ally_alive" in view.importance["active_factor_ids"]
    assert "observations" not in json.dumps(view.special or {})


def test_dispatch_uses_candidate_type_only() -> None:
    coach_input = _death_input()
    unit = score_death_candidate(coach_input, match_duration_seconds=300)
    assert coach_input.primary_scenario.scenario_type is ScenarioType.DEATH_EPISODE
    wrong = unit.model_copy(update={"candidate_type": "aggressiveness_episode"})
    with pytest.raises(ValueError, match="candidate_type"):
        build_coach_llm_view(coach_input, wrong)
    with pytest.raises(ValueError, match="candidate_type"):
        build_death_llm_view(coach_input, wrong)


def test_llm_view_excludes_preinterpreted_triad_copy() -> None:
    coach_input = _death_input(ally=1, special_ready=True)
    unit = score_death_candidate(coach_input, match_duration_seconds=300)
    # Unit still has locked triad annotations for VMV.
    assert any(f.statement_player for f in unit.factors if f.active)
    view = build_death_llm_view(coach_input, unit)
    dumped = json.dumps(view.model_dump(mode="json"))
    for banned in (
        "statement_player",
        "statement_internal",
        "interpretation",
        "recommendation",
        "last ally alive when you were splatted",
        "prioritize staying alive",
    ):
        assert banned not in dumped
    for factor in view.importance["active_factors"]:
        assert set(factor.keys()) == {"factor_id", "weight", "contribution"}


def _assert_no_leakage(payload: object) -> None:
    """Recursively forbid bulky / nested internal evidence shapes in the view."""
    if isinstance(payload, dict):
        keys = set(payload.keys())
        # Raw special series / roster trajectory / nested context dumps.
        assert "observations" not in keys
        assert "trajectory" not in keys
        assert "ready_onsets" not in keys
        assert "primary_context" not in keys
        assert "event_ids" not in keys
        # Full observation objects (window points expose counts, not obs blobs).
        assert "observation" not in keys
        assert "observation_id" not in keys
        # Nested ScenarioContext-shaped bundles.
        for nested_key in ("players", "special", "death_episode", "timeline", "map"):
            if nested_key in keys and nested_key == "special":
                # Only nearest_before_anchor subset allowed under special.
                special = payload["special"]
                assert isinstance(special, dict)
                assert set(special.keys()) <= {"nearest_before_anchor"}
            elif nested_key in keys and nested_key in {"players", "death_episode"}:
                raise AssertionError(f"nested ScenarioContext field leaked: {nested_key}")
        for value in payload.values():
            _assert_no_leakage(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_leakage(item)


def test_llm_view_forbids_raw_series_and_nested_context_leakage() -> None:
    coach_input = _death_input()
    unit = score_death_candidate(coach_input, match_duration_seconds=300)
    view = build_death_llm_view(coach_input, unit)
    payload = view.model_dump(mode="json")
    _assert_no_leakage(payload)
    # Related must not embed a full ScenarioContext.
    for related in payload["related"]:
        assert "context" not in related
        assert "special" not in related
        assert "players" not in related
        assert "death_episode" not in related
    # Roster must not include players.trajectory.
    roster = payload.get("roster") or {}
    assert "trajectory" not in roster
    assert "observations" not in json.dumps(roster)
    # Special must not include observations / ready_onsets.
    special = payload.get("special") or {}
    assert "observations" not in special
    assert "ready_onsets" not in special
    dumped = json.dumps(payload)
    assert "dial_score" not in dumped
    assert "lit_sector_fraction" not in dumped
    assert "charged_score" not in dumped
    assert "press_score" not in dumped
    assert "ready_prompt_score" not in dumped
