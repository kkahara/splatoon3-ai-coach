"""Splat Zones count facts derived in the coaching layer (arithmetic only).

Inputs are ``ScenarioContext.score`` observed samples; nothing is re-read
from snapshots. ``remaining_diff`` and counts before progress are derived
here, never stored as evidence. A ``not_shown`` penalty becomes 0 only in
``*_count_before_progress`` and only with ``penalty_not_shown_treated_as_zero``
set, so an ``EvidenceLimit`` can state it. Counter decreases are counter
changes — they never identify who held the zone.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from splatoon3_ai_coach.analysis.scenario_context import ScenarioContext
from splatoon3_ai_coach.analysis.score_context import ScoreSample
from splatoon3_ai_coach.vision.models import PenaltyQuality

SPLAT_ZONES = "splat_zones"


class ScoreFacts(BaseModel):
    """Count facts at the pre-death (or anchor) sample of one unit."""

    battle_mode_id: str | None = None
    sample_label: Literal["pre_death", "at_anchor"] | None = None
    video_time: float | None = None
    ally_remaining: int | None = None
    opponent_remaining: int | None = None
    remaining_diff: int | None = None
    ally_penalty: int | None = None
    ally_penalty_quality: PenaltyQuality = "unknown"
    opponent_penalty: int | None = None
    opponent_penalty_quality: PenaltyQuality = "unknown"
    ally_count_before_progress: int | None = None
    opponent_count_before_progress: int | None = None
    ally_work_remaining: int | None = None
    opponent_work_remaining: int | None = None
    penalty_not_shown_treated_as_zero: bool = False
    lookback_video_time: float | None = None
    ally_counter_decreased_before_death: bool | None = None
    opponent_counter_decreased_before_death: bool | None = None
    source_path: str | None = None

    @property
    def is_splat_zones(self) -> bool:
        """True when the resolved battle mode is Splat Zones."""
        return self.battle_mode_id == SPLAT_ZONES

    @property
    def has_both_counts(self) -> bool:
        """True when both remaining counts were observed at the sample."""
        return self.remaining_diff is not None


def derive_score_facts(
    context: ScenarioContext, *, battle_mode_id: str | None
) -> ScoreFacts:
    """Derive count facts; outside Splat Zones only the mode is recorded."""
    facts = ScoreFacts(battle_mode_id=battle_mode_id)
    score = context.score
    if battle_mode_id != SPLAT_ZONES or score is None:
        return facts
    label: Literal["pre_death", "at_anchor"] = "pre_death"
    sample = score.pre_death
    if sample is None:
        label, sample = "at_anchor", score.at_anchor
    if sample is None:
        return facts
    ally_before, ally_zero = _before_progress(
        sample.ally_penalty, sample.ally_penalty_quality
    )
    opp_before, opp_zero = _before_progress(
        sample.opponent_penalty, sample.opponent_penalty_quality
    )
    lookback = score.lookback if label == "pre_death" else None
    return facts.model_copy(
        update={
            "sample_label": label,
            "video_time": sample.video_time,
            "ally_remaining": sample.ally_remaining,
            "opponent_remaining": sample.opponent_remaining,
            "remaining_diff": _diff(sample.opponent_remaining, sample.ally_remaining),
            "ally_penalty": sample.ally_penalty,
            "ally_penalty_quality": sample.ally_penalty_quality,
            "opponent_penalty": sample.opponent_penalty,
            "opponent_penalty_quality": sample.opponent_penalty_quality,
            "ally_count_before_progress": ally_before,
            "opponent_count_before_progress": opp_before,
            "ally_work_remaining": _sum(sample.ally_remaining, ally_before),
            "opponent_work_remaining": _sum(sample.opponent_remaining, opp_before),
            "penalty_not_shown_treated_as_zero": ally_zero or opp_zero,
            "lookback_video_time": lookback.video_time if lookback else None,
            "ally_counter_decreased_before_death": _decreased(
                lookback, sample, "ally_remaining"
            ),
            "opponent_counter_decreased_before_death": _decreased(
                lookback, sample, "opponent_remaining"
            ),
            "source_path": f"primary_context.score.{label}",
        }
    )


def _before_progress(
    value: int | None, quality: PenaltyQuality
) -> tuple[int | None, bool]:
    """Counts a team must hold before its counter can drop; not_shown → 0."""
    if quality == "observed" and value is not None:
        return value, False
    if quality == "not_shown":
        return 0, True
    return None, False


def _diff(opponent: int | None, ally: int | None) -> int | None:
    if opponent is None or ally is None:
        return None
    return opponent - ally


def _sum(a: int | None, b: int | None) -> int | None:
    if a is None or b is None:
        return None
    return a + b


def _decreased(
    earlier: ScoreSample | None, later: ScoreSample, field: str
) -> bool | None:
    """Observed value at ``earlier`` greater than at ``later``; None if unknown."""
    if earlier is None:
        return None
    before, after = getattr(earlier, field), getattr(later, field)
    if before is None or after is None:
        return None
    return after < before
