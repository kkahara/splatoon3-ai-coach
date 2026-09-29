"""Sparse Splat Zones score evidence for ScenarioContext (facts only).

Reads fused ``GameStateSnapshot`` score/penalty fields; never re-fuses. A
value is exposed only when that side's quality is ``observed`` — held,
rejected and unknown values stay ``None`` with their quality recorded.
``not_shown`` penalty is carried as a quality, never as zero. No zone-holder,
no remaining difference, no interpolation, no ``GameEvent``. Fusion is
mode-gated, so non-Splat-Zones matches yield no samples.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from splatoon3_ai_coach.vision.models import (
    GameStateSnapshot,
    PenaltyQuality,
    ScoreQuality,
)

SCORE_EVIDENCE_TAG = ":score:"


class ScoreSample(BaseModel):
    """Remaining counts + penalties from one fused snapshot at ``video_time``."""

    video_time: float = Field(ge=0)
    ally_remaining: int | None = Field(default=None, ge=0, le=100)
    ally_score_quality: ScoreQuality = "unknown"
    opponent_remaining: int | None = Field(default=None, ge=0, le=100)
    opponent_score_quality: ScoreQuality = "unknown"
    ally_penalty: int | None = Field(default=None, ge=0)
    ally_penalty_quality: PenaltyQuality = "unknown"
    opponent_penalty: int | None = Field(default=None, ge=0)
    opponent_penalty_quality: PenaltyQuality = "unknown"
    evidence_ids: list[str] = Field(default_factory=list)


class ScoreEvidence(BaseModel):
    """Sparse score samples around a scenario (Splat Zones only)."""

    at_anchor: ScoreSample | None = None
    pre_death: ScoreSample | None = None
    lookback: ScoreSample | None = None


def build_score_evidence(
    snapshots: list[GameStateSnapshot],
    *,
    anchor: float,
    death_time: float | None,
    pre_death_offset_seconds: float,
    max_gap_seconds: float,
    lookback_seconds: float = 5.0,
) -> ScoreEvidence | None:
    """Anchor sample; death episodes add pre-death and lookback samples."""
    ordered = sorted(snapshots, key=lambda s: s.timestamp)
    at_anchor = score_sample_before(ordered, anchor, max_gap_seconds)
    pre_death = lookback = None
    if death_time is not None:
        target = float(death_time) - float(pre_death_offset_seconds)
        pre_death = score_sample_before(ordered, target, max_gap_seconds)
        lookback = score_sample_before(
            ordered, target - float(lookback_seconds), max_gap_seconds
        )
    if at_anchor is None and pre_death is None and lookback is None:
        return None
    return ScoreEvidence(at_anchor=at_anchor, pre_death=pre_death, lookback=lookback)


def score_sample_before(
    snapshots: list[GameStateSnapshot], target: float, max_gap_seconds: float
) -> ScoreSample | None:
    """Latest snapshot in ``[target - max_gap, target]`` that observed something.

    Prefers a snapshot where both main counters were observed, so the two
    sides share one video time; otherwise the latest with any observation.
    """
    window = [
        s
        for s in snapshots
        if target - max_gap_seconds <= s.timestamp <= target and _observes_any(s)
    ]
    if not window:
        return None
    both = [s for s in window if _observes_both_main(s)]
    return _sample(both[-1] if both else window[-1])


def _observes_any(snap: GameStateSnapshot) -> bool:
    return "observed" in (
        snap.ally_score_quality,
        snap.opponent_score_quality,
        snap.ally_penalty_quality,
        snap.opponent_penalty_quality,
    )


def _observes_both_main(snap: GameStateSnapshot) -> bool:
    return snap.ally_score_quality == snap.opponent_score_quality == "observed"


def _observed(value: int | None, quality: str) -> int | None:
    return value if quality == "observed" else None


def _sample(snap: GameStateSnapshot) -> ScoreSample:
    return ScoreSample(
        video_time=float(snap.timestamp),
        ally_remaining=_observed(snap.ally_remaining, snap.ally_score_quality),
        ally_score_quality=snap.ally_score_quality,
        opponent_remaining=_observed(
            snap.opponent_remaining, snap.opponent_score_quality
        ),
        opponent_score_quality=snap.opponent_score_quality,
        ally_penalty=_observed(snap.ally_penalty, snap.ally_penalty_quality),
        ally_penalty_quality=snap.ally_penalty_quality,
        opponent_penalty=_observed(snap.opponent_penalty, snap.opponent_penalty_quality),
        opponent_penalty_quality=snap.opponent_penalty_quality,
        evidence_ids=[i for i in snap.evidence_ids if SCORE_EVIDENCE_TAG in i],
    )
