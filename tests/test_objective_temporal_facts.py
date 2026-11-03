"""Focused tests for generic objective temporal relationships."""

from splatoon3_ai_coach.analysis.objective_progress import (
    ObjectiveProgressEvidence,
    ObjectiveProgressObservation,
    ObjectiveTemporalSample,
)
from splatoon3_ai_coach.analysis.scenario_context import (
    DeathEpisodeContext,
    ScenarioContext,
)
from splatoon3_ai_coach.analysis.splat_zones_objective_progress import (
    splat_zones_objective_observations,
)
from splatoon3_ai_coach.coach.objective_temporal_facts import (
    derive_objective_temporal_facts,
)
from splatoon3_ai_coach.vision.models import GameStateSnapshot


def _sample(
    t: float,
    value: int,
    state: str,
    *,
    ally: int = 4,
    opponent: int = 2,
    life: str = "alive",
) -> ObjectiveTemporalSample:
    return ObjectiveTemporalSample(
        objective=ObjectiveProgressObservation(
            game_mode_id="splat_zones",
            team="ally",
            video_time=t,
            value_kind="remaining_count",
            value_label="ally remaining count",
            original_value=value,
            progress_state_from_previous=state,
            normalized_progress_amount=4.0 if state == "progress" else 0.0,
            objective_evidence_ids=[f"score:{t}"],
        ),
        ally_alive_count=ally,
        opponent_alive_count=opponent,
        roster_reliable=True,
        roster_evidence_ids=[f"roster:{t}"],
        player_lifecycle=life,
        lifecycle_evidence_ids=[f"life:{t}"],
    )


def _context(
    samples: list[ObjectiveTemporalSample], death: float | None = None
) -> ScenarioContext:
    return ScenarioContext(
        scenario_id="unit",
        objective_progress=ObjectiveProgressEvidence(
            observations=samples, max_gap_seconds=1.0
        ),
        death_episode=DeathEpisodeContext(death_time=death)
        if death is not None
        else None,
    )


def test_generic_advantage_without_progress_never_reads_score_fields() -> None:
    facts = derive_objective_temporal_facts(
        _context([_sample(1, 72, "unknown"), _sample(1.5, 72, "no_progress")]),
        roster_evidence=True,
        roster_min_gap=2,
    )
    assert facts is not None
    assert facts.number_advantage_without_progress is not None
    assert facts.number_advantage_without_progress.starting_value == 72


def test_sole_alive_requires_local_player_alive() -> None:
    facts = derive_objective_temporal_facts(
        _context(
            [
                _sample(1, 72, "unknown", ally=1, life="dead"),
                _sample(1.5, 68, "progress", ally=1, life="dead"),
            ]
        ),
        roster_evidence=True,
        roster_min_gap=2,
    )
    assert facts is None


def test_stop_requires_two_fresh_no_progress_observations() -> None:
    samples = [
        _sample(1, 72, "unknown"),
        _sample(1.5, 68, "progress"),
        _sample(2, 68, "no_progress"),
    ]
    early = derive_objective_temporal_facts(
        _context(samples, death=1.0), roster_evidence=True, roster_min_gap=2
    )
    assert early is not None and early.progress_then_stop_after_death is None
    samples.append(_sample(2.5, 68, "no_progress"))
    facts = derive_objective_temporal_facts(
        _context(samples, death=1.0), roster_evidence=True, roster_min_gap=2
    )
    assert facts is not None and facts.stop_bounds is not None
    assert facts.stop_bounds.first_no_progress_observed_at == 2.0


def test_splat_zones_adapter_keeps_native_value_and_positive_direction() -> None:
    snapshots = [
        GameStateSnapshot(
            timestamp=1.0, ally_remaining=72, ally_score_quality="observed"
        ),
        GameStateSnapshot(
            timestamp=1.5, ally_remaining=68, ally_score_quality="observed"
        ),
    ]
    values = splat_zones_objective_observations(snapshots, max_gap_seconds=1.0)
    ally = [item for item in values if item.team == "ally"]
    assert ally[1].original_value == 68
    assert ally[1].value_label == "ally remaining count"
    assert ally[1].normalized_progress_amount == 4.0
    assert ally[1].progress_state_from_previous == "progress"


def test_no_adapter_evidence_yields_no_generic_temporal_facts() -> None:
    context = ScenarioContext(scenario_id="tower_without_adapter")
    assert (
        derive_objective_temporal_facts(context, roster_evidence=True, roster_min_gap=2)
        is None
    )
