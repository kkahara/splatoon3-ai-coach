"""Roster facts, sparse ink timing, and unchanged ranking over saved evidence."""

from __future__ import annotations

from pathlib import Path

import pytest
from public_site.evidence_text import observed_ink_line, render_evidence
from public_site.results import _context

from splatoon3_ai_coach.analysis.scenario_context import build_scenario_contexts
from splatoon3_ai_coach.analysis.scenario_evidence import (
    MapInkEvidence,
    MapInkSample,
    ScenarioEvidencePack,
)
from splatoon3_ai_coach.analysis.scenario_models import ScenarioType
from splatoon3_ai_coach.analysis.scenarios import build_scenarios
from splatoon3_ai_coach.coach.claim_catalog import CoachingUnitResult
from splatoon3_ai_coach.coach.claim_integrity import (
    claim_reference_hits,
    video_clock_wording_hits,
)
from splatoon3_ai_coach.coach.coach_input import (
    CoachInput,
    GameClockSample,
    build_coach_input_for_scenario,
)
from splatoon3_ai_coach.coach.death_importance import score_death_candidate
from splatoon3_ai_coach.coach.game_clock import GameClock, GameClockObservation
from splatoon3_ai_coach.coach.llm_view import CoachLlmView, build_coach_llm_view
from splatoon3_ai_coach.coach.load_analysis import load_coach_analysis_bundle
from splatoon3_ai_coach.coach.match_scoring import score_match
from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig
from splatoon3_ai_coach.vision.models import GameEvent, GameStateSnapshot

_REPO = Path(__file__).resolve().parents[1]


def _input(ally: int = 3, *, unreliable: bool = False) -> CoachInput:
    events = [
        GameEvent(
            start_time=100, event_type="death", reason="alive_to_dead", confidence=1
        )
    ]
    config = ScenarioBuilderConfig()
    scenarios = build_scenarios(events, config)
    counts = [(92, ally), (99, ally)]
    if unreliable:
        counts = [(92, 4), (98, 2), (99, 3)]  # Too-fast rise: existing plausibility gate.
    pack = ScenarioEvidencePack(
        state_snapshots=[
            GameStateSnapshot(
                timestamp=t,
                match_phase="in_match",
                player_lifecycle="alive",
                ally_alive_count=a,
                opponent_alive_count=4,
                player_count_confidence=1,
            )
            for t, a in counts
        ]
    )
    contexts = build_scenario_contexts(events, scenarios, config, evidence=pack)
    scenario = next(s for s in scenarios if s.scenario_type is ScenarioType.DEATH_EPISODE)
    return build_coach_input_for_scenario(
        scenario.scenario_id,
        scenarios,
        contexts,
        GameClock(),
        max_gap_seconds=1,
        roster_evidence=True,
    )


def _view(coach_input: CoachInput) -> tuple[CoachingUnitResult, CoachLlmView]:
    unit = score_death_candidate(coach_input, roster_scoring=False)
    return unit, build_coach_llm_view(coach_input, unit)


def _with_ink(times: list[float], *, zero: bool = False) -> CoachInput:
    coach_input = _input()
    ink = MapInkEvidence(
        observations=[
            MapInkSample(
                video_time=t,
                visible_ink_fraction=0 if zero else 0.4,
                ally_share_of_detected_ink=None if zero else 0.75,
                opponent_share_of_detected_ink=None if zero else 0.25,
            )
            for t in times
        ]
    )
    coach_input.primary_context.map.ink = ink
    return coach_input


def test_reliable_3v4_is_a_fact_without_two_player_factor() -> None:
    coach_input = _input()
    unit, view = _view(coach_input)
    assert coach_input.primary_context.players is not None
    counts = view.roster["observed_counts"]
    assert (counts["ally_alive_count"], counts["opponent_alive_count"]) == (3, 4)
    assert counts["video_time"] == 92
    claim = next(c for c in view.claim_basis if c["claim_id"] == "observed_roster_counts")
    assert "video_time" not in claim["values"]
    assert "roster.observed_counts.video_time" in claim["evidence_paths"]
    assert claim["values"]["offset_from_anchor_seconds"] == -8
    assert claim["values"]["timing"] == "before"
    assert claim["coachable"] is False  # Optional context, never forces coaching.
    assert not next(
        f for f in unit.factors if f.factor_id == "death_while_outnumbered"
    ).active
    assert (
        claim_reference_hits(
            {"selected_claim_ids": [claim["claim_id"]]}, view.model_dump()
        )
        == []
    )


def test_unreliable_roster_stays_out_of_factual_claim() -> None:
    coach_input = _input(unreliable=True)
    _, view = _view(coach_input)
    assert coach_input.primary_context.players.unreliable_intervals
    assert view.roster["observed_counts"] is None
    assert "observed_roster_counts" not in {c["claim_id"] for c in view.claim_basis}


def test_roster_scoring_is_independent_and_does_not_support_modifiers() -> None:
    coach_input = _input(ally=2)
    off = score_death_candidate(coach_input, roster_scoring=False)
    on = score_death_candidate(coach_input, roster_scoring=True)
    assert next(f for f in off.factors if f.factor_id == "death_while_outnumbered").active
    assert off.importance_score == 0
    assert on.importance_score == 2.5
    coach_input.game_clock_samples = [
        GameClockSample(
            label="death",
            video_time=100,
            observation=GameClockObservation(
                video_time=100, seconds_remaining=295, confidence=1
            ),
            gap_seconds=0,
        )
    ]
    assert (
        score_death_candidate(
            coach_input, roster_scoring=False, match_duration_seconds=300
        ).importance_score
        == 0
    )
    assert (
        score_death_candidate(
            coach_input, roster_scoring=True, match_duration_seconds=300
        ).importance_score
        == 3
    )
    assert "observed_roster_counts" in {
        c["claim_id"] for c in build_coach_llm_view(coach_input, off).claim_basis
    }


@pytest.mark.parametrize(
    "times,expected", [([92, 101], 101), ([92, 105], 105), ([99, 101], 99), ([94], 94)]
)
def test_ink_uses_nearest_observation_with_pre_death_tie_break(times, expected) -> None:
    _, view = _view(_with_ink(times))
    fact = view.map["observed_ink"]
    assert fact["video_time"] == expected
    assert fact["offset_from_anchor_seconds"] == expected - 100
    assert fact["visible_ink_coverage_percent"] == 40
    assert fact["ally_share_of_detected_ink_percent"] == 75
    assert fact["opponent_share_of_detected_ink_percent"] == 25
    claim = next(c for c in view.claim_basis if c["claim_id"] == "observed_map_ink")
    assert "video_time" not in claim["values"]
    assert claim["values"]["ally_share_of_detected_ink_rounded_percent"] == 75
    assert claim["values"]["visible_ink_coverage_rounded_percent"] == 40
    text = render_evidence(observed_ink_line(claim["values"]), "en")
    assert "40.00% of the measured map area" in text
    assert "75% belonged to your team" in text
    assert "25% to the opposing team" in text
    if expected > 100:
        assert fact["temporal_relationship"] == "after_death"
        assert claim["values"]["timing"] == "after"
        assert "after the death" in text.lower()
        assert "before the death" not in text.lower()
    else:
        assert fact["temporal_relationship"] == "before_death"
        assert "before the death" in text.lower()


def test_zero_ink_keeps_zero_and_unavailable_team_shares() -> None:
    _, view = _view(_with_ink([101], zero=True))
    fact = view.map["observed_ink"]
    assert fact["visible_ink_coverage_percent"] == 0
    assert fact["ally_share_of_detected_ink_percent"] is None
    assert fact["opponent_share_of_detected_ink_percent"] is None
    assert fact["ally_share_of_detected_ink_rounded_percent"] is None
    claim = next(c for c in view.claim_basis if c["claim_id"] == "observed_map_ink")
    text = render_evidence(observed_ink_line(claim["values"]), "en")
    assert "0.00% of the measured map area" in text
    assert "unavailable belonged to your team" in text


def test_claim_values_are_literal_view_leaves_and_public_lines_keep_timing() -> None:
    coach_input = _with_ink([101])
    _, view = _view(coach_input)
    payload = view.model_dump(mode="json")
    for claim in view.claim_basis:
        if claim["claim_id"] not in {"observed_map_ink", "observed_roster_counts"}:
            continue
        leaves = {}
        for path in claim["evidence_paths"]:
            value = payload
            for part in path.split("."):
                value = value[part]
            assert not isinstance(value, dict | list)
            leaves[path.split(".")[-1]] = value
        for name, value in claim["values"].items():
            assert leaves[name] == value
    lines = [
        render_evidence(line, "en")
        for line in _context(coach_input.model_dump(mode="json"), [], payload)
    ]
    assert any("3 teammates and 4 opponents were alive" in line for line in lines)
    assert any("after the death" in line.lower() for line in lines)
    for line in lines:
        assert video_clock_wording_hits({"assessment": line}) == []
        for phrase in ("at video", "recording timeline", "registered polygon"):
            assert phrase not in line.lower()
    assert not any("Roster at death:" in line for line in lines)


@pytest.mark.parametrize(
    "relative",
    [
        "map_ink_validation/polygon_registration_2026-10-30/fresh/brinewater",
        "map_ink_validation/polygon_registration_2026-10-30/fresh/mahi_mahi",
        "_public/analysis/1ea3c666459a45d5a64b785fb5802fe2",
        "en-live-playerview-2026-10-11 22-57-30",
    ],
)
def test_saved_replay_preserves_scenario_ownership_and_match_ranking(relative) -> None:
    root = _REPO / "analysis" / relative
    if not (root / "scenario_contexts.json").is_file():
        pytest.skip("Local saved analysis is unavailable")
    bundle = load_coach_analysis_bundle(root, min_usable_confidence=0.5)
    snapshots = [s.model_dump_json() for s in bundle.scenarios]
    contexts = [s.model_dump_json() for s in bundle.contexts]
    config = load_config(_REPO / "configs/default.yaml")
    assert config.coach.roster_evidence and not config.coach.roster_scoring
    old = config.model_copy(deep=True)
    old.coach.roster_evidence = False
    before = score_match(bundle, old, limit=10000)
    after = score_match(bundle, config, limit=10000)

    def ranking(result):
        return [
            (u.candidate_id, u.importance_score, u.rank, u.selected_for_llm)
            for u in result.units_by_rank()
        ]

    assert ranking(after) == ranking(before)
    if relative.endswith("22-57-30"):
        assert [u.candidate_id for u in after.units_by_rank() if u.selected_for_llm] == [
            "death_episode:65.500",
            "death_episode:119.000",
            "death_episode:142.500",
        ]
    assert [s.model_dump_json() for s in bundle.scenarios] == snapshots
    assert [s.model_dump_json() for s in bundle.contexts] == contexts
    for unit in after.units:
        view = build_coach_llm_view(after.coach_inputs[unit.candidate_id], unit)
        assert view.roster_evidence
    if relative.endswith("brinewater"):
        view = build_coach_llm_view(
            after.coach_inputs["death_episode:64.000"],
            next(u for u in after.units if u.candidate_id == "death_episode:64.000"),
        )
        assert {"observed_roster_counts", "observed_map_ink"} <= {
            c["claim_id"] for c in view.claim_basis
        }
