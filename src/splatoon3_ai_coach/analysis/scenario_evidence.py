"""Sparse secondary evidence builders for ScenarioContext (facts only).

No OpenCV, detectors, fusion, or video readers. Consumers of persisted
map observations, fused roster snapshots, and special-gauge readings.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, TypeVar

from pydantic import BaseModel, Field, computed_field

from splatoon3_ai_coach.analysis.map_coverage import MapOverlaySample
from splatoon3_ai_coach.analysis.player_count_series import (
    PlayerCountObservation,
    RosterInterval,
    assess_roster_plausibility,
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
from splatoon3_ai_coach.config.models import (
    ScenarioBuilderConfig,
    SpecialGaugeDetectorConfig,
)
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
    reliable: bool = True


class PlayersEvidence(BaseModel):
    """Sparse roster trajectory around a scenario. Not player GameEvents.

    ``unreliable_intervals`` mark counts no earlier death can explain (rule
    ``assess_roster_plausibility``); ``at_anchor`` / ``at_death`` are left
    empty when they fall inside one.
    """

    trajectory: list[PlayerCountPoint] = Field(default_factory=list)
    at_anchor: PlayerCountPoint | None = None
    at_death: PlayerCountPoint | None = None
    unreliable_intervals: list[RosterInterval] = Field(default_factory=list)

    def unreliable_at(self, video_time: float | None) -> bool:
        """True when ``video_time`` lies inside an unreliable interval."""
        if video_time is None:
            return False
        return any(item.contains(video_time) for item in self.unreliable_intervals)


class SpecialGaugeState(StrEnum):
    """Observed gauge state. ``not_observed`` is never a kind of not ready."""

    READY = "ready"
    NOT_READY = "not_ready"
    NOT_OBSERVED = "not_observed"


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
    lifecycle: str | None = Field(
        default=None,
        description=(
            "Fused player lifecycle at this reading's own timestamp (exact or "
            "nearest snapshot within half a cadence step, else unknown)."
        ),
    )
    coaching_valid: bool | None = Field(
        default=None,
        description=(
            "False while dead (after the first dead frame), on the respawn "
            "countdown, or awaiting control: the gauge then does not describe "
            "the player's state before the death, or for a prompt-only ready "
            "with no full ring yet in the current life. None = not "
            "contextualized."
        ),
    )
    coaching_invalid_reason: (
        Literal["lifecycle", "uncorroborated_prompt_ready"] | None
    ) = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def state(self) -> SpecialGaugeState:
        """``ready`` only counts when the gauge itself was visible."""
        return special_gauge_state(self)


def special_gauge_state(reading: SpecialReading | None) -> SpecialGaugeState:
    """Three-way gauge state; a missing or hidden gauge is ``not_observed``."""
    if reading is None or not reading.visible:
        return SpecialGaugeState.NOT_OBSERVED
    if reading.ready:
        return SpecialGaugeState.READY
    return SpecialGaugeState.NOT_READY


class SpecialReadyOnset(BaseModel):
    """Presentation-only ready onset. Not a GameEvent."""

    video_time: float = Field(ge=0)
    observation_id: str | None = None


class SpecialEvidence(BaseModel):
    """Sparse special-gauge facts around a scenario."""

    observations: list[SpecialReading] = Field(default_factory=list)
    ready_onsets: list[SpecialReadyOnset] = Field(default_factory=list)
    nearest_before_anchor: SpecialReading | None = None
    last_ready_before_anchor: SpecialReading | None = Field(
        default=None,
        description=(
            "Latest coaching-valid visible ready reading at/before the anchor "
            "inside the evidence window. A reading, not a usage event."
        ),
    )
    last_visible_before_anchor: SpecialReading | None = Field(
        default=None,
        description=(
            "Latest in-window reading at/before the anchor with a visible gauge. "
            "Lets renderers say what was last observed when the nearest reading "
            "is hidden; it is not a substitute state at the anchor."
        ),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def state_at_anchor(self) -> SpecialGaugeState:
        """State of ``nearest_before_anchor``; absent means not observed."""
        return special_gauge_state(self.nearest_before_anchor)


@dataclass(frozen=True)
class ScenarioEvidencePack:
    """Persisted secondary evidence for ScenarioContext enrichment."""

    map_observations: list[MapObservation] = field(default_factory=list)
    state_snapshots: list[GameStateSnapshot] = field(default_factory=list)
    special_readings: list[SpecialReading] = field(default_factory=list)
    map_overlay_samples: list[MapOverlaySample] | None = None
    cadence_times: list[float] | None = None
    special_ring_fill_threshold: float | None = None


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
    min_respawn_seconds: float | None = None,
    plausibility_window_seconds: float | None = None,
) -> PlayersEvidence | None:
    """Compressed roster trajectory from fused snapshots (shared series).

    With ``min_respawn_seconds``, implausible increases mark intervals whose
    points are ``reliable=False``; anchor / death samples there are dropped.
    """
    all_obs = observations_from_snapshots(snapshots)
    intervals: list[RosterInterval] = []
    if min_respawn_seconds is not None:
        intervals = [
            item
            for item in assess_roster_plausibility(
                all_obs,
                min_respawn_seconds=min_respawn_seconds,
                window_seconds=plausibility_window_seconds,
            )
            if item.end >= window_start and item.start <= window_end
        ]

    def unreliable(t: float | None) -> bool:
        return t is not None and any(item.contains(t) for item in intervals)

    in_window = observations_between(all_obs, window_start, window_end)
    trajectory: list[PlayerCountPoint] = []
    for obs in compress_player_count_observations(in_window):
        point = _point_from_obs(obs)
        if point is not None:
            trajectory.append(
                point.model_copy(update={"reliable": not unreliable(point.video_time)})
            )
    at_anchor = _reliable_point(all_obs, anchor, max_gap_seconds, unreliable)
    at_death = None
    if death_time is not None:
        at_death = _reliable_point(all_obs, death_time, max_gap_seconds, unreliable)
    if not trajectory and at_anchor is None and at_death is None and not intervals:
        return None
    return PlayersEvidence(
        trajectory=trajectory,
        at_anchor=at_anchor,
        at_death=at_death,
        unreliable_intervals=intervals,
    )


def _reliable_point(
    observations: list[PlayerCountObservation],
    reference: float,
    max_gap_seconds: float,
    unreliable: Callable[[float | None], bool],
) -> PlayerCountPoint | None:
    """Nearest sample, unless it or the reference time is unreliable."""
    point = _point_from_obs(
        nearest_player_count_observation(
            observations, reference, max_gap_seconds=max_gap_seconds
        )
    )
    if point is None or unreliable(reference) or unreliable(point.video_time):
        return None
    return point


def build_special_evidence(
    readings: list[SpecialReading],
    *,
    window_start: float,
    window_end: float,
    anchor: float,
    max_gap_seconds: float,
    snapshots: list[GameStateSnapshot] | None = None,
    ring_fill_threshold: float | None = None,
) -> SpecialEvidence | None:
    """Windowed special readings + shared presentation-only ready onsets.

    With ``snapshots``, each reading is copied with its lifecycle and
    ``coaching_valid``; summaries (nearest, last visible, last ready, onsets)
    use valid readings only. ``observations`` keeps every in-window reading.
    Onsets are derived from the complete ordered stream, then restricted to the
    window so a ready sample that begins the window does not invent an onset.
    """
    ordered = sorted(readings, key=lambda x: x.video_time)
    if snapshots:
        ordered = contextualize_special_readings(
            ordered, snapshots, ring_fill_threshold=ring_fill_threshold
        )
    valid = [r for r in ordered if r.coaching_valid is not False]
    in_window = [
        r for r in ordered if window_start <= float(r.video_time) <= window_end
    ]
    valid_window = [
        r for r in valid if window_start <= float(r.video_time) <= window_end
    ]
    onsets = [
        SpecialReadyOnset(video_time=m.video_time, observation_id=m.observation_id)
        for m in derive_special_ready_onsets(
            [
                ReadySample(
                    video_time=r.video_time,
                    ready=r.ready,
                    observation_id=r.observation_id,
                )
                for r in valid
            ]
        )
        if window_start <= float(m.video_time) <= window_end
    ]
    nearest = _nearest_before(valid_window, anchor, max_gap_seconds)
    if not in_window and not onsets and nearest is None:
        return None
    visible = [r for r in valid_window if r.visible and float(r.video_time) <= anchor]
    ready = [r for r in visible if r.ready]
    return SpecialEvidence(
        observations=in_window,
        ready_onsets=onsets,
        nearest_before_anchor=nearest,
        last_ready_before_anchor=ready[-1] if ready else None,
        last_visible_before_anchor=visible[-1] if visible else None,
    )


_VALID_LIFECYCLES = frozenset({"alive", "unknown"})
_NON_ALIVE_LIFECYCLES = frozenset({"dead", "countdown", "respawned", "awaiting_control"})


def contextualize_special_readings(
    readings: list[SpecialReading],
    snapshots: list[GameStateSnapshot],
    *,
    ring_fill_threshold: float | None = None,
) -> list[SpecialReading]:
    """Copies tagged with lifecycle and coaching validity; inputs are not mutated.

    Lifecycle gate: the first ``dead`` frame stays valid (it still shows the
    gauge as it was before the death); later dead, countdown, respawned and
    awaiting-control readings do not.

    Corroboration, per life: a prompt-only ``ready`` (fill below the
    detector's ``ready_fill_threshold``) is valid only after a full-ring
    ready in the same life. Life boundaries come from snapshots, so a death
    with no readings in it still starts a new life.
    """
    threshold = (
        SpecialGaugeDetectorConfig().ready_fill_threshold
        if ring_fill_threshold is None
        else ring_fill_threshold
    )
    ordered = sorted(snapshots, key=lambda snap: snap.timestamp)
    times = [float(snap.timestamp) for snap in ordered]
    tolerance = _half_cadence_step(times)
    starts = _life_starts(ordered)
    state = _LifeRingState()
    out: list[SpecialReading] = []
    for reading in sorted(readings, key=lambda r: float(r.video_time)):
        t = float(reading.video_time)
        state.enter(max(bisect_right(starts, t) - 1, 0))
        lifecycle, valid = _lifecycle_at(ordered, times, t, tolerance)
        reason = None if valid else "lifecycle"
        if valid and reading.ready:
            if _full_ring(reading, threshold):
                state.ring_seen = True
            elif not state.ring_seen:
                valid, reason = False, "uncorroborated_prompt_ready"
        out.append(
            reading.model_copy(
                update={
                    "lifecycle": lifecycle,
                    "coaching_valid": valid,
                    "coaching_invalid_reason": reason,
                }
            )
        )
    return out


@dataclass
class _LifeRingState:
    """Invariant: ``ring_seen`` means a full-ring ready was observed since the
    start of the player's current life."""

    life_index: int | None = None
    ring_seen: bool = False

    def enter(self, life_index: int) -> None:
        """Move to ``life_index``; the only place ``ring_seen`` resets."""
        if life_index != self.life_index:
            self.life_index = life_index
            self.ring_seen = False


def _life_starts(snapshots: list[GameStateSnapshot]) -> list[float]:
    """Match start plus every return to ``alive`` after a non-alive lifecycle.

    ``unknown`` neither starts nor ends a life.
    """
    if not snapshots:
        return []
    starts = [float(snapshots[0].timestamp)]
    between_lives = False
    for snap in snapshots:
        lifecycle = str(snap.player_lifecycle)
        if lifecycle in _NON_ALIVE_LIFECYCLES:
            between_lives = True
        elif lifecycle == "alive" and between_lives:
            starts.append(float(snap.timestamp))
            between_lives = False
    return starts


def _lifecycle_at(
    ordered: list[GameStateSnapshot],
    times: list[float],
    t: float,
    tolerance: float,
) -> tuple[str, bool]:
    """Lifecycle at ``t`` and whether the gauge then describes the live player."""
    index = _snapshot_index(times, t, tolerance)
    if index is None:
        return "unknown", True
    lifecycle = str(ordered[index].player_lifecycle)
    if lifecycle == "dead":
        previous = str(ordered[index - 1].player_lifecycle) if index > 0 else None
        return lifecycle, previous != "dead"
    return lifecycle, lifecycle in _VALID_LIFECYCLES


def _full_ring(reading: SpecialReading, threshold: float) -> bool:
    fill = reading.fill_fraction
    return reading.visible and fill is not None and fill >= threshold


def _half_cadence_step(times: list[float]) -> float:
    """Half the median snapshot spacing (0 when fewer than two snapshots)."""
    gaps = sorted(b - a for a, b in zip(times, times[1:], strict=False) if b > a)
    return gaps[len(gaps) // 2] / 2.0 if gaps else 0.0


def _snapshot_index(times: list[float], t: float, tolerance: float) -> int | None:
    """Exact snapshot match, else nearest within ``tolerance``, else None."""
    position = bisect_left(times, t)
    best: int | None = None
    best_gap = float("inf")
    for index in (position - 1, position):
        if 0 <= index < len(times) and abs(times[index] - t) < best_gap:
            best, best_gap = index, abs(times[index] - t)
    if best is None or best_gap > tolerance + 1e-9:
        return None
    return best


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
        ts = float(sample.video_time)
        if ts > anchor:
            continue
        gap = anchor - ts
        if gap > max_gap_seconds:
            continue
        if gap < best_gap:
            best = sample
            best_gap = gap
    return best
