"""Sparse factual Splat Zones control evidence for ScenarioContext."""

from __future__ import annotations

from pydantic import BaseModel, Field

from splatoon3_ai_coach.vision.events import (
    next_zone_control_chain,
    zone_control_transition_type,
)
from splatoon3_ai_coach.vision.models import (
    GameStateSnapshot,
    ZoneControlQuality,
    ZoneControlState,
)

ZONE_CONTROL_EVIDENCE_TAG = ":zone_control:"


class ZoneControlSample(BaseModel):
    """One persisted ownership state sample and its fusion quality."""

    video_time: float = Field(ge=0)
    state: ZoneControlState = "unknown"
    quality: ZoneControlQuality = "unknown"
    evidence_ids: list[str] = Field(default_factory=list)


class ZoneControlTransition(BaseModel):
    """A factual team-level ownership transition in the evidence window."""

    video_time: float = Field(ge=0)
    from_state: ZoneControlState
    to_state: ZoneControlState
    evidence_ids: list[str] = Field(default_factory=list)


class ZoneControlEvidence(BaseModel):
    """Sparse control state and transition evidence around a scenario."""

    trajectory: list[ZoneControlSample] = Field(default_factory=list)
    at_anchor: ZoneControlSample | None = None
    pre_death: ZoneControlSample | None = None
    lookback: ZoneControlSample | None = None
    transitions: list[ZoneControlTransition] = Field(default_factory=list)


def build_zone_control_evidence(
    snapshots: list[GameStateSnapshot],
    *,
    window_start: float,
    window_end: float,
    anchor: float,
    death_time: float | None,
    pre_death_offset_seconds: float,
    lookback_seconds: float,
    max_gap_seconds: float,
) -> ZoneControlEvidence | None:
    """Build sparse control samples without interpolation or re-fusion."""
    ordered = sorted(snapshots, key=lambda item: item.timestamp)
    in_window = [
        item
        for item in ordered
        if window_start <= item.timestamp <= window_end
    ]
    trajectory = [
        _sample(item)
        for item in in_window
        if item.zone_control_quality != "unknown"
        and item.zone_control_state != "unknown"
    ]
    at_anchor = _sample_before(ordered, anchor, max_gap_seconds)
    pre_death = lookback = None
    if death_time is not None:
        target = death_time - pre_death_offset_seconds
        pre_death = _sample_before(ordered, target, max_gap_seconds)
        lookback = _sample_before(
            ordered, target - lookback_seconds, max_gap_seconds
        )
    transitions = _transitions(ordered, window_start, window_end)
    if not trajectory and at_anchor is None and pre_death is None and not transitions:
        return None
    return ZoneControlEvidence(
        trajectory=trajectory,
        at_anchor=at_anchor,
        pre_death=pre_death,
        lookback=lookback,
        transitions=transitions,
    )


def _sample_before(
    snapshots: list[GameStateSnapshot],
    target: float,
    max_gap_seconds: float,
) -> ZoneControlSample | None:
    """Return the latest non-unknown fused sample before a target."""
    candidates = [
        item
        for item in snapshots
        if target - max_gap_seconds <= item.timestamp <= target
        and item.zone_control_quality != "unknown"
        and item.zone_control_state != "unknown"
    ]
    return _sample(candidates[-1]) if candidates else None


def _sample(snapshot: GameStateSnapshot) -> ZoneControlSample:
    """Copy factual state and provenance from a persisted snapshot."""
    return ZoneControlSample(
        video_time=float(snapshot.timestamp),
        state=snapshot.zone_control_state,
        quality=snapshot.zone_control_quality,
        evidence_ids=_zone_ids(snapshot),
    )


def _zone_ids(snapshot: GameStateSnapshot) -> list[str]:
    """Zone-control detector evidence IDs carried by a snapshot."""
    return [
        item for item in snapshot.evidence_ids if ZONE_CONTROL_EVIDENCE_TAG in item
    ]


def _transitions(
    snapshots: list[GameStateSnapshot],
    window_start: float,
    window_end: float,
) -> list[ZoneControlTransition]:
    """Confirmed transitions in the window, using the GameEvent chain rule.

    The chain runs over the whole snapshot stream so a state established
    before the window can support a transition inside it. Held samples keep
    the established state without being fresh evidence; unknown breaks the
    chain. Provenance is the confirming snapshot's zone-control evidence.
    """
    out: list[ZoneControlTransition] = []
    established = "unknown"
    for snapshot in snapshots:
        state = snapshot.zone_control_state
        if (
            snapshot.zone_control_quality == "observed"
            and window_start <= snapshot.timestamp <= window_end
            and zone_control_transition_type(established, state) is not None
        ):
            out.append(
                ZoneControlTransition(
                    video_time=float(snapshot.timestamp),
                    from_state=established,
                    to_state=state,
                    evidence_ids=_zone_ids(snapshot),
                )
            )
        established = next_zone_control_chain(
            established, state, snapshot.zone_control_quality
        )
    return out
