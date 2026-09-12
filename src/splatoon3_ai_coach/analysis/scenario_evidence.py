"""Sparse secondary evidence builders for ScenarioContext (facts only).

No OpenCV, detectors, fusion, or video readers. Consumers of persisted
map observations, fused roster snapshots, and special-gauge readings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeVar

from pydantic import BaseModel, Field

from splatoon3_ai_coach.analysis.player_count_series import (
    PlayerCountObservation,
    compress_player_count_observations,
    nearest_player_count_observation,
    observations_between,
    observations_from_snapshots,
)
from splatoon3_ai_coach.analysis.scenario_models import Scenario, ScenarioType
from splatoon3_ai_coach.analysis.special_ready_markers import (
    ReadySample,
    derive_special_ready_onsets,
)
from splatoon3_ai_coach.config.models import ScenarioBuilderConfig
from splatoon3_ai_coach.vision.map_ink import MapObservation
from splatoon3_ai_coach.vision.models import GameStateSnapshot, SpecialGaugeReading

T = TypeVar("T")


class MapInkSample(BaseModel):
    """Pass-through map ink sample for ScenarioContext (not recomputed)."""

    video_time: float = Field(ge=0)
    ally_classified_fraction: float | None = None
    opponent_classified_fraction: float | None = None
    classified_fraction: float | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)


class MapInkEvidence(BaseModel):
    """Sparse map-ink facts in a scenario window. Not MAP_OVERLAY."""

    observations: list[MapInkSample] = Field(default_factory=list)
    nearest_before_anchor: MapInkSample | None = None


class PlayerCountPoint(BaseModel):
    """One fused roster alive-count sample (ScenarioContext trajectory)."""

    video_time: float = Field(ge=0)
    ally_alive_count: int = Field(ge=0, le=4)
    opponent_alive_count: int = Field(ge=0, le=4)


class PlayersEvidence(BaseModel):
    """Sparse roster trajectory around a scenario. Not player GameEvents."""

    trajectory: list[PlayerCountPoint] = Field(default_factory=list)
    at_anchor: PlayerCountPoint | None = None
    at_death: PlayerCountPoint | None = None


class SpecialReading(BaseModel):
    """Persisted special-gauge reading at a video time."""

    video_time: float = Field(ge=0)
    visible: bool = False
    fill_fraction: float | None = None
    ready: bool = False
    dial_score: float = 0.0
    lit_sector_fraction: float = 0.0
    charged_score: float = 0.0
    press_score: float = 0.0
    ready_prompt_score: float = 0.0
    observation_id: str | None = None
    confidence: float | None = None


class SpecialReadyOnset(BaseModel):
    """Presentation-only ready onset. Not a GameEvent."""

    video_time: float = Field(ge=0)
    observation_id: str | None = None


class SpecialEvidence(BaseModel):
    """Sparse special-gauge facts around a scenario."""

    observations: list[SpecialReading] = Field(default_factory=list)
    ready_onsets: list[SpecialReadyOnset] = Field(default_factory=list)
    nearest_before_anchor: SpecialReading | None = None


@dataclass(frozen=True)
class ScenarioEvidencePack:
    """Persisted secondary evidence for ScenarioContext enrichment."""

    map_observations: list[MapObservation] = field(default_factory=list)
    state_snapshots: list[GameStateSnapshot] = field(default_factory=list)
    special_readings: list[SpecialReading] = field(default_factory=list)


def scenario_window(
    scenario: Scenario, config: ScenarioBuilderConfig
) -> tuple[float, float]:
    """Inclusive evidence window: start-lookback … end+lookforward."""
    start = float(scenario.start_time) - float(config.context_lookback_seconds)
    end = float(scenario.end_time) + float(config.context_lookforward_seconds)
    return start, end


def scenario_anchor_time(
    scenario: Scenario, *, death_time: float | None = None
) -> float:
    """Anchor already used by ScenarioContext semantics (no new ownership)."""
    if scenario.scenario_type is ScenarioType.DEATH_EPISODE:
        if death_time is not None:
            return float(death_time)
        return float(scenario.start_time)
    return float(scenario.start_time)


def build_map_ink_evidence(
    observations: list[MapObservation],
    *,
    window_start: float,
    window_end: float,
    anchor: float,
    max_gap_seconds: float,
) -> MapInkEvidence | None:
    """Windowed map-ink samples; never invents continuous ink state."""
    samples = [
        _map_ink_sample(obs)
        for obs in sorted(observations, key=lambda o: o.video_time)
        if window_start <= float(obs.video_time) <= window_end
    ]
    nearest = _nearest_before(samples, anchor, max_gap_seconds)
    if not samples and nearest is None:
        return None
    return MapInkEvidence(observations=samples, nearest_before_anchor=nearest)


def build_players_evidence(
    snapshots: list[GameStateSnapshot],
    *,
    window_start: float,
    window_end: float,
    anchor: float,
    death_time: float | None,
    max_gap_seconds: float,
) -> PlayersEvidence | None:
    """Compressed roster trajectory from fused snapshots (shared series)."""
    all_obs = observations_from_snapshots(snapshots)
    in_window = observations_between(all_obs, window_start, window_end)
    trajectory: list[PlayerCountPoint] = []
    for obs in compress_player_count_observations(in_window):
        point = _point_from_obs(obs)
        if point is not None:
            trajectory.append(point)
    at_anchor = _point_from_obs(
        nearest_player_count_observation(
            all_obs, anchor, max_gap_seconds=max_gap_seconds
        )
    )
    at_death = None
    if death_time is not None:
        at_death = _point_from_obs(
            nearest_player_count_observation(
                all_obs, death_time, max_gap_seconds=max_gap_seconds
            )
        )
    if not trajectory and at_anchor is None and at_death is None:
        return None
    return PlayersEvidence(
        trajectory=trajectory,
        at_anchor=at_anchor,
        at_death=at_death,
    )


def build_special_evidence(
    readings: list[SpecialReading],
    *,
    window_start: float,
    window_end: float,
    anchor: float,
    max_gap_seconds: float,
) -> SpecialEvidence | None:
    """Windowed special readings + shared presentation-only ready onsets.

    Onsets are derived from the complete ordered reading stream, then
    restricted to the evidence window so a ready=true sample that begins
    the window does not invent an onset when the transition was earlier.
    """
    ordered = sorted(readings, key=lambda x: x.video_time)
    in_window = [
        r for r in ordered if window_start <= float(r.video_time) <= window_end
    ]
    onsets_all = derive_special_ready_onsets(
        [
            ReadySample(
                video_time=r.video_time,
                ready=r.ready,
                observation_id=r.observation_id,
            )
            for r in ordered
        ]
    )
    onsets = [
        SpecialReadyOnset(video_time=m.video_time, observation_id=m.observation_id)
        for m in onsets_all
        if window_start <= float(m.video_time) <= window_end
    ]
    nearest = _nearest_before(in_window, anchor, max_gap_seconds)
    if not in_window and not onsets and nearest is None:
        return None
    return SpecialEvidence(
        observations=in_window,
        ready_onsets=onsets,
        nearest_before_anchor=nearest,
    )


def _special_reading(
    reading: SpecialGaugeReading,
    *,
    video_time: float,
    observation_id: str | None,
    confidence: float | None,
) -> SpecialReading:
    """Pass-through ScenarioContext view of a persisted SpecialGaugeReading.

    ``video_time`` must be the frame's persisted video timestamp
    (``VisionFrameResult.timestamp``). ``SpecialGaugeReading.timestamp`` is an
    optional detector echo and is not substituted for the frame clock.

    Copies stored factual fields only. Does not recompute fill/ready scores
    or infer SPECIAL_READY / SPECIAL_USED.
    """
    return SpecialReading(
        video_time=float(video_time),
        visible=bool(reading.visible),
        fill_fraction=reading.fill_fraction,
        ready=bool(reading.ready),
        dial_score=float(reading.dial_score),
        lit_sector_fraction=float(reading.lit_sector_fraction),
        charged_score=float(reading.charged_score),
        press_score=float(reading.press_score),
        ready_prompt_score=float(reading.ready_prompt_score),
        observation_id=observation_id,
        confidence=confidence,
    )


# Public alias for pipeline / tests.
special_reading_from_persisted = _special_reading


def _map_ink_sample(obs: MapObservation) -> MapInkSample:
    return MapInkSample(
        video_time=float(obs.video_time),
        ally_classified_fraction=obs.ally_classified_fraction,
        opponent_classified_fraction=obs.opponent_classified_fraction,
        classified_fraction=obs.classified_fraction,
        confidence=float(obs.confidence),
    )


def _point_from_obs(obs: PlayerCountObservation | None) -> PlayerCountPoint | None:
    if obs is None:
        return None
    return PlayerCountPoint(
        video_time=float(obs.video_time),
        ally_alive_count=int(obs.ally_alive_count),
        opponent_alive_count=int(obs.opponent_alive_count),
    )


def _nearest_before(
    samples: list[T],
    anchor: float,
    max_gap_seconds: float,
) -> T | None:
    """Latest sample with video_time <= anchor and gap <= max_gap."""
    if max_gap_seconds < 0:
        return None
    best: T | None = None
    best_gap = float("inf")
    for sample in samples:
        ts = float(getattr(sample, "video_time"))
        if ts > anchor:
            continue
        gap = anchor - ts
        if gap > max_gap_seconds:
            continue
        if gap < best_gap:
            best = sample
            best_gap = gap
    return best
