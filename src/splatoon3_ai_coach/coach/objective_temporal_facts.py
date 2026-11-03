"""Generic deterministic temporal objective facts for coaching.

The module consumes normalized adapter observations; it has no knowledge of
Splat Zones remaining-count fields.
"""

from __future__ import annotations

from pydantic import BaseModel

from splatoon3_ai_coach.analysis.objective_progress import ObjectiveTemporalSample
from splatoon3_ai_coach.analysis.scenario_context import ScenarioContext


class ObjectiveProgressInterval(BaseModel):
    """Observation-bounded interval carrying native values and normalized progress."""

    game_mode_id: str
    team: str
    start_video_time: float
    end_video_time: float
    value_label: str
    starting_value: float | int | None
    ending_value: float | int | None
    normalized_progress_amount: float | None
    objective_evidence_ids: list[str]
    roster_evidence_ids: list[str]
    lifecycle_evidence_ids: list[str]


class ObjectiveStopBounds(BaseModel):
    """Bounded objective-stop timing, never an invented exact transition."""

    last_progress_observed_at: float
    first_no_progress_observed_at: float
    no_progress_confirmed_through: float


class ObjectiveTemporalFacts(BaseModel):
    """Optional factual temporal relationships for one coaching unit."""

    number_advantage_without_progress: ObjectiveProgressInterval | None = None
    sole_ally_alive_during_progress: ObjectiveProgressInterval | None = None
    progress_then_stop_after_death: ObjectiveProgressInterval | None = None
    stop_bounds: ObjectiveStopBounds | None = None


def derive_objective_temporal_facts(
    context: ScenarioContext, *, roster_evidence: bool, roster_min_gap: int
) -> ObjectiveTemporalFacts | None:
    """Derive generic facts from windowed normalized objective observations."""
    evidence = context.objective_progress
    if evidence is None or not roster_evidence:
        return None
    allies = [s for s in evidence.observations if s.objective.team == "ally"]
    facts = ObjectiveTemporalFacts(
        number_advantage_without_progress=_advantage_without_progress(
            allies, evidence.max_gap_seconds, roster_min_gap
        ),
        sole_ally_alive_during_progress=_sole_alive_progress(
            allies, evidence.max_gap_seconds
        ),
    )
    death = context.death_episode
    if death is not None and death.death_time is not None:
        interval, bounds = _progress_then_stop(
            allies, float(death.death_time), evidence.max_gap_seconds
        )
        facts.progress_then_stop_after_death = interval
        facts.stop_bounds = bounds
    return facts if any(facts.model_dump().values()) else None


def _connected(
    a: ObjectiveTemporalSample, b: ObjectiveTemporalSample, gap: float
) -> bool:
    return 0 < b.objective.video_time - a.objective.video_time <= gap


def _interval(
    a: ObjectiveTemporalSample, b: ObjectiveTemporalSample
) -> ObjectiveProgressInterval:
    amount = b.objective.normalized_progress_amount
    return ObjectiveProgressInterval(
        game_mode_id=a.objective.game_mode_id,
        team=a.objective.team,
        start_video_time=a.objective.video_time,
        end_video_time=b.objective.video_time,
        value_label=a.objective.value_label,
        starting_value=a.objective.original_value,
        ending_value=b.objective.original_value,
        normalized_progress_amount=amount,
        objective_evidence_ids=[
            *a.objective.objective_evidence_ids,
            *b.objective.objective_evidence_ids,
        ],
        roster_evidence_ids=[*a.roster_evidence_ids, *b.roster_evidence_ids],
        lifecycle_evidence_ids=[*a.lifecycle_evidence_ids, *b.lifecycle_evidence_ids],
    )


def _advantage_without_progress(
    samples: list[ObjectiveTemporalSample], gap: float, minimum: int
) -> ObjectiveProgressInterval | None:
    """First bounded pair with reliable +N advantage and fresh no-progress."""
    for a, b in zip(samples, samples[1:], strict=False):
        if (
            not _connected(a, b, gap)
            or b.objective.progress_state_from_previous != "no_progress"
        ):
            continue
        supports = (a, b)
        if all(
            s.roster_reliable
            and s.ally_alive_count is not None
            and s.opponent_alive_count is not None
            and s.ally_alive_count - s.opponent_alive_count >= minimum
            for s in supports
        ):
            return _interval(a, b)
    return None


def _sole_alive_progress(
    samples: list[ObjectiveTemporalSample], gap: float
) -> ObjectiveProgressInterval | None:
    """First bounded allied progress pair with reliable alive sole-ally evidence."""
    for a, b in zip(samples, samples[1:], strict=False):
        if (
            not _connected(a, b, gap)
            or b.objective.progress_state_from_previous != "progress"
        ):
            continue
        if all(
            s.roster_reliable
            and s.ally_alive_count == 1
            and s.player_lifecycle == "alive"
            for s in (a, b)
        ):
            return _interval(a, b)
    return None


def _progress_then_stop(
    samples: list[ObjectiveTemporalSample], death: float, gap: float
) -> tuple[ObjectiveProgressInterval | None, ObjectiveStopBounds | None]:
    """Find post-death progress followed by two fresh no-progress observations."""
    for index, sample in enumerate(samples):
        if (
            sample.objective.video_time <= death
            or sample.objective.progress_state_from_previous != "progress"
            or index == 0
        ):
            continue
        progress = _interval(samples[index - 1], sample)
        later = samples[index + 1 :]
        for first, second in zip(later, later[1:], strict=False):
            if not _connected(sample, first, gap) or not _connected(first, second, gap):
                continue
            if (
                first.objective.progress_state_from_previous
                == second.objective.progress_state_from_previous
                == "no_progress"
            ):
                return progress, ObjectiveStopBounds(
                    last_progress_observed_at=sample.objective.video_time,
                    first_no_progress_observed_at=first.objective.video_time,
                    no_progress_confirmed_through=second.objective.video_time,
                )
    return None, None
