"""Factual coaching-layer derivations from sparse zone-control evidence."""

from __future__ import annotations

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.scenario_context import ScenarioContext
from splatoon3_ai_coach.analysis.zone_control_context import (
    ZoneControlSample,
    ZoneControlTransition,
)

SPLAT_ZONES = "splat_zones"


class ZoneControlFacts(BaseModel):
    """Observed team-level control facts for one coaching unit."""

    battle_mode_id: str | None = None
    control_at_death: str | None = None
    control_before_death: str | None = None
    control_became_neutral: bool | None = None
    ally_regained_after_neutral: bool | None = None
    opponent_regained_after_neutral: bool | None = None
    transition_times: list[float] = Field(default_factory=list)
    source_path: str | None = None

    @property
    def is_splat_zones(self) -> bool:
        """Whether the resolved match mode supports these facts."""
        return self.battle_mode_id == SPLAT_ZONES


def derive_zone_control_facts(
    context: ScenarioContext,
    *,
    battle_mode_id: str | None,
) -> ZoneControlFacts:
    """Derive only observed team-level state and transition patterns."""
    facts = ZoneControlFacts(battle_mode_id=battle_mode_id)
    evidence = context.zone_control
    if battle_mode_id != SPLAT_ZONES or evidence is None:
        return facts
    pre_death = _observed_sample(evidence.pre_death)
    anchor = _observed_sample(evidence.at_anchor)
    observed_coverage = bool(evidence.transitions) or any(
        item.quality == "observed" for item in evidence.trajectory
    )
    became_neutral = (
        any(item.to_state == "neutral" for item in evidence.transitions)
        if observed_coverage
        else None
    )
    ally_regained = _regained(evidence.transitions, "ally_control")
    opponent_regained = _regained(evidence.transitions, "opponent_control")
    return facts.model_copy(
        update={
            "control_at_death": anchor.state if anchor else None,
            "control_before_death": pre_death.state if pre_death else None,
            "control_became_neutral": became_neutral,
            "ally_regained_after_neutral": ally_regained,
            "opponent_regained_after_neutral": opponent_regained,
            "transition_times": [
                item.video_time for item in evidence.transitions
            ],
            "source_path": "primary_context.zone_control",
        }
    )


def _observed_sample(
    sample: ZoneControlSample | None,
) -> ZoneControlSample | None:
    """Reject held samples when deriving fresh factual observations."""
    if sample is None or sample.quality != "observed":
        return None
    return sample


def _regained(
    transitions: list[ZoneControlTransition],
    state: str,
) -> bool | None:
    """Return whether a team regained control after an observed neutral."""
    saw_neutral = False
    for transition in transitions:
        if transition.to_state == "neutral":
            saw_neutral = True
        elif saw_neutral and transition.to_state == state:
            return True
    return False if transitions else None
