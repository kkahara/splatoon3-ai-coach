"""When a missing MAP_OVERLAY before a death is a reliable negative.

Rule ``respawn_map_coverage`` layers three checks; all must hold before an
absence becomes ``not_observed``:

1. Source observability (``media.source_capabilities``): the acquisition
   source can show the map overlay at all.
2. Match coverage: the live respawn screen shows the map, so a working
   detector sees overlays during most completed respawns of this match.
3. Episode observability: usable map_overlay readings cover the pre-death
   segment of *this* death, and (for a complete episode) its own respawn
   window contains an overlay.

Anything short of that is ``unknown`` with a reason code. A positive overlay
before the death is ``observed`` regardless of coverage.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from splatoon3_ai_coach.config.models import MapCoverageConfig
from splatoon3_ai_coach.media.source_capabilities import absence_is_reliable_negative
from splatoon3_ai_coach.media.video_source import Observability
from splatoon3_ai_coach.vision.models import GameEvent, GameEventType

RULE_ID = "respawn_map_coverage"


class MapCheckStatus(StrEnum):
    """Map check before a death: observed, reliably absent, or unknown."""

    OBSERVED = "observed"
    NOT_OBSERVED = "not_observed"
    UNKNOWN = "unknown"


class CoverageStatus(StrEnum):
    """Whether detector coverage supports reading absence as a negative."""

    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"


class MapUnknownReason(StrEnum):
    """Why a map-check absence is not assertable."""

    SOURCE_UNOBSERVABLE = "source_unobservable"
    MATCH_COVERAGE_INSUFFICIENT = "match_coverage_insufficient"
    MAP_READINGS_UNAVAILABLE = "map_readings_unavailable"
    EPISODE_PRE_DEATH_COVERAGE_GAP = "episode_pre_death_coverage_gap"
    EPISODE_RESPAWN_MAP_MISSING = "episode_respawn_map_missing"


class MapOverlaySample(BaseModel):
    """One persisted map_overlay detector reading (raw, not a GameEvent)."""

    video_time: float
    present: bool
    confidence: float
    usable: bool


class MapOverlayCoverage(BaseModel):
    """Match-level detector coverage measured on completed respawns."""

    status: CoverageStatus
    rule: str = RULE_ID
    source_observability: Observability
    completed_respawns: int = 0
    respawns_with_overlay: int = 0
    reason: str


class EpisodeMapObservability(BaseModel):
    """Whether this death's own observation window was covered."""

    status: CoverageStatus
    reason: MapUnknownReason | None = None
    segment_start: float | None = None
    pre_death_frame_fraction: float | None = None
    max_pre_death_gap_seconds: float | None = None
    own_respawn_overlay: bool | None = None


def match_map_coverage(
    events: list[GameEvent],
    *,
    source_observability: Observability,
    config: MapCoverageConfig,
) -> MapOverlayCoverage:
    """Share of completed respawns (death → active again) containing an overlay."""
    deaths = sorted(e.start_time for e in events if e.event_type is GameEventType.DEATH)
    actives = sorted(
        e.start_time for e in events if e.event_type is GameEventType.ACTIVE_AGAIN
    )
    overlays = sorted(
        e.start_time for e in events if e.event_type is GameEventType.MAP_OVERLAY
    )
    completed = 0
    with_overlay = 0
    for index, death in enumerate(deaths):
        next_death = deaths[index + 1] if index + 1 < len(deaths) else float("inf")
        active = next((t for t in actives if death < t < next_death), None)
        if active is None:
            continue
        completed += 1
        if any(death < t <= active for t in overlays):
            with_overlay += 1
    fraction = with_overlay / completed if completed else 0.0
    if not absence_is_reliable_negative(source_observability):
        status, reason = CoverageStatus.INSUFFICIENT, (
            f"Source observability is {source_observability.value}."
        )
    elif completed < config.min_completed_respawns:
        status, reason = CoverageStatus.INSUFFICIENT, (
            f"{completed} completed respawns; need {config.min_completed_respawns}."
        )
    elif fraction < config.min_respawn_overlay_fraction:
        status, reason = CoverageStatus.INSUFFICIENT, (
            f"Map overlay seen in {with_overlay}/{completed} respawns "
            f"(< {config.min_respawn_overlay_fraction:g})."
        )
    else:
        status, reason = CoverageStatus.SUFFICIENT, (
            f"Map overlay seen in {with_overlay}/{completed} respawns."
        )
    return MapOverlayCoverage(
        status=status,
        source_observability=source_observability,
        completed_respawns=completed,
        respawns_with_overlay=with_overlay,
        reason=reason,
    )


def episode_map_observability(
    *,
    death_time: float,
    segment_start: float | None,
    episode_complete: bool,
    overlays_during_episode: int,
    samples: list[MapOverlaySample] | None,
    cadence_times: list[float] | None,
    config: MapCoverageConfig,
) -> EpisodeMapObservability:
    """Pre-death reading coverage plus the episode's own respawn overlay."""
    own = overlays_during_episode > 0 if episode_complete else None
    if not samples or not cadence_times:
        return EpisodeMapObservability(
            status=CoverageStatus.INSUFFICIENT,
            reason=MapUnknownReason.MAP_READINGS_UNAVAILABLE,
            own_respawn_overlay=own,
        )
    start = segment_start if segment_start is not None else min(cadence_times)
    frames = [t for t in cadence_times if start <= t < death_time]
    usable = sorted(
        s.video_time for s in samples if s.usable and start <= s.video_time < death_time
    )
    fraction = len(usable) / len(frames) if frames else 0.0
    bounds = [start, *usable, death_time]
    max_gap = max(b - a for a, b in zip(bounds, bounds[1:], strict=False))
    base = {
        "segment_start": start,
        "pre_death_frame_fraction": round(fraction, 3),
        "max_pre_death_gap_seconds": round(max_gap, 3),
        "own_respawn_overlay": own,
    }
    if (
        fraction < config.min_pre_death_frame_fraction
        or max_gap > config.max_pre_death_gap_seconds
    ):
        return EpisodeMapObservability(
            status=CoverageStatus.INSUFFICIENT,
            reason=MapUnknownReason.EPISODE_PRE_DEATH_COVERAGE_GAP,
            **base,
        )
    if config.require_own_respawn_overlay and own is False:
        return EpisodeMapObservability(
            status=CoverageStatus.INSUFFICIENT,
            reason=MapUnknownReason.EPISODE_RESPAWN_MAP_MISSING,
            **base,
        )
    return EpisodeMapObservability(status=CoverageStatus.SUFFICIENT, **base)


def map_check_status(
    *,
    observed_before: bool,
    match: MapOverlayCoverage | None,
    episode: EpisodeMapObservability | None,
) -> tuple[MapCheckStatus, MapUnknownReason | None]:
    """Combine the three layers into one status and an unknown reason."""
    if observed_before:
        return MapCheckStatus.OBSERVED, None
    if match is None:
        return MapCheckStatus.UNKNOWN, MapUnknownReason.MATCH_COVERAGE_INSUFFICIENT
    if not absence_is_reliable_negative(match.source_observability):
        return MapCheckStatus.UNKNOWN, MapUnknownReason.SOURCE_UNOBSERVABLE
    if match.status is not CoverageStatus.SUFFICIENT:
        return MapCheckStatus.UNKNOWN, MapUnknownReason.MATCH_COVERAGE_INSUFFICIENT
    if episode is None or episode.status is not CoverageStatus.SUFFICIENT:
        reason = episode.reason if episode is not None else None
        return MapCheckStatus.UNKNOWN, reason or (
            MapUnknownReason.MAP_READINGS_UNAVAILABLE
        )
    return MapCheckStatus.NOT_OBSERVED, None
