"""Coaching-layer zone-control facts, limits, and projection."""

from __future__ import annotations

from splatoon3_ai_coach.analysis.scenario_context import ScenarioContext
from splatoon3_ai_coach.analysis.zone_control_context import (
    ZoneControlEvidence,
    ZoneControlSample,
    ZoneControlTransition,
    build_zone_control_evidence,
)
from splatoon3_ai_coach.coach.evidence_limits import (
    zone_control_evidence_limits,
)
from splatoon3_ai_coach.coach.llm_view import _project_zone_control
from splatoon3_ai_coach.coach.zone_control_facts import (
    derive_zone_control_facts,
)
from splatoon3_ai_coach.vision.models import GameStateSnapshot


def _sample(t: float, state: str, quality: str = "observed") -> ZoneControlSample:
    return ZoneControlSample(video_time=t, state=state, quality=quality)


def test_regain_facts_are_team_level_and_factual() -> None:
    evidence = ZoneControlEvidence(
        at_anchor=_sample(10.0, "opponent_control"),
        pre_death=_sample(9.0, "neutral"),
        transitions=[
            ZoneControlTransition(
                video_time=8.0,
                from_state="opponent_control",
                to_state="neutral",
            ),
            ZoneControlTransition(
                video_time=9.0,
                from_state="neutral",
                to_state="opponent_control",
            ),
        ],
    )
    facts = derive_zone_control_facts(
        ScenarioContext(scenario_id="death_episode:10", zone_control=evidence),
        battle_mode_id="splat_zones",
    )
    assert facts.control_at_death == "opponent_control"
    assert facts.control_became_neutral is True
    assert facts.opponent_regained_after_neutral is True
    assert facts.ally_regained_after_neutral is False
    assert _project_zone_control(facts)["opponent_regained_after_neutral"] is True


def test_held_sample_does_not_become_fresh_control_fact() -> None:
    evidence = ZoneControlEvidence(at_anchor=_sample(10.0, "ally_control", "held"))
    facts = derive_zone_control_facts(
        ScenarioContext(scenario_id="death_episode:10", zone_control=evidence),
        battle_mode_id="splat_zones",
    )
    assert facts.control_at_death is None
    assert "zone_control_unavailable" in {
        item.code for item in zone_control_evidence_limits(facts)
    }


def _held_only_evidence() -> ZoneControlEvidence:
    snapshots = [
        GameStateSnapshot(
            timestamp=t,
            zone_control_state=state,
            zone_control_quality="held",
            evidence_ids=[f"r:{t}:zone_control:read"],
        )
        for t, state in [
            (8.0, "opponent_control"),
            (9.0, "neutral"),
            (9.5, "ally_control"),
            (10.0, "ally_control"),
        ]
    ]
    evidence = build_zone_control_evidence(
        snapshots,
        window_start=7.0,
        window_end=10.0,
        anchor=10.0,
        death_time=10.0,
        pre_death_offset_seconds=0.5,
        lookback_seconds=1.0,
        max_gap_seconds=0.2,
    )
    assert evidence is not None
    return evidence


def test_held_only_samples_never_populate_control_or_regain_facts() -> None:
    evidence = _held_only_evidence()
    assert evidence.at_anchor is not None and evidence.pre_death is not None
    assert evidence.transitions == []
    facts = derive_zone_control_facts(
        ScenarioContext(scenario_id="death_episode:10", zone_control=evidence),
        battle_mode_id="splat_zones",
    )
    assert facts.control_at_death is None
    assert facts.control_before_death is None
    assert facts.control_became_neutral is None
    assert facts.ally_regained_after_neutral is None
    assert facts.opponent_regained_after_neutral is None
    assert facts.transition_times == []
    assert _project_zone_control(facts) is None


def test_non_splat_zones_is_unavailable() -> None:
    facts = derive_zone_control_facts(
        ScenarioContext(scenario_id="death_episode:10"),
        battle_mode_id="rainmaker",
    )
    assert _project_zone_control(facts) is None
    assert zone_control_evidence_limits(facts)[0].code == (
        "zone_control_not_splat_zones"
    )
