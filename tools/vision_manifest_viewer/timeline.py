"""Timeline markers, lifecycle segments, and death-episode grouping."""

from __future__ import annotations

from typing import Any

from vision_manifest_viewer.model import (
    DeathEpisode,
    EpisodeStep,
    LifecycleSegment,
    ObservationView,
    TimelineMarker,
)


def build_markers(
    observations: list[ObservationView],
    events: list[dict[str, Any]],
    transitions: list | None = None,
) -> list[TimelineMarker]:
    """Build timeline markers from positive observations and game events."""
    transitions = transitions or []
    tr_by_event: dict[str, str] = {}
    for tr in transitions:
        if tr.event_type:
            tr_by_event[f"{tr.event_type}:{round(tr.timestamp, 3)}"] = tr.id

    markers: list[TimelineMarker] = []

    for event in events:
        event_type = str(event.get("event_type") or "event")
        timestamp = float(event.get("start_time") or 0.0)
        category = _event_category(event_type)
        markers.append(
            TimelineMarker(
                id=f"event:{event_type}:{timestamp}",
                timestamp=timestamp,
                category=category,
                label=event_type.upper().replace("_", " "),
                confidence=_as_float(event.get("confidence")),
                transition_id=tr_by_event.get(f"{event_type}:{round(timestamp, 3)}"),
            )
        )

    for obs in observations:
        if not obs.positive:
            continue
        if obs.detector in {None, "timer"}:
            continue
        label = (obs.detector or obs.category).upper()
        if obs.detector in {"respawn", "countdown"}:
            label = "RESPAWN UI PRESENT"
        markers.append(
            TimelineMarker(
                id=f"obs:{obs.id}",
                timestamp=obs.timestamp,
                category=obs.category,
                label=label,
                observation_id=obs.id,
                confidence=obs.confidence,
                transition_id=obs.transition_id,
            )
        )

    markers.sort(key=lambda item: (item.timestamp, item.label))
    return markers


def build_lifecycle_segments(
    snapshots: list[dict[str, Any]],
) -> list[LifecycleSegment]:
    """Collapse consecutive snapshots into lifecycle phase segments."""
    if not snapshots:
        return []

    ordered = sorted(snapshots, key=lambda item: float(item.get("timestamp") or 0.0))
    segments: list[LifecycleSegment] = []
    current_phase = _phase_of(ordered[0])
    start = float(ordered[0].get("timestamp") or 0.0)
    last_ts = start

    for snap in ordered[1:]:
        ts = float(snap.get("timestamp") or last_ts)
        phase = _phase_of(snap)
        if phase != current_phase:
            segments.append(
                LifecycleSegment(phase=current_phase, start=start, end=last_ts)
            )
            current_phase = phase
            start = ts
        last_ts = ts

    segments.append(LifecycleSegment(phase=current_phase, start=start, end=last_ts))
    return segments


def build_death_episodes(
    events: list[dict[str, Any]],
    observations: list[ObservationView],
    snapshots: list[dict[str, Any]],
    transitions: list | None = None,
) -> list[DeathEpisode]:
    """Group DEATH → countdown → RESPAWN → ACTIVE_AGAIN into episodes."""
    transitions = transitions or []
    tr_by_ts = {round(t.timestamp, 3): t for t in transitions}
    death_times = sorted(
        float(e["start_time"])
        for e in events
        if str(e.get("event_type")) == "death" and e.get("start_time") is not None
    )
    if not death_times:
        # Fall back to positive death detections when events are missing.
        death_times = sorted(
            o.timestamp for o in observations if o.detector == "death" and o.positive
        )
    if not death_times:
        return []

    respawns = [
        float(e["start_time"])
        for e in events
        if str(e.get("event_type")) == "respawn" and e.get("start_time") is not None
    ]
    actives = [
        float(e["start_time"])
        for e in events
        if str(e.get("event_type")) == "active_again" and e.get("start_time") is not None
    ]

    episodes: list[DeathEpisode] = []
    for index, death_at in enumerate(death_times, start=1):
        next_death = (
            death_times[index] if index < len(death_times) else float("inf")
        )
        end_cap = next_death
        steps: list[EpisodeStep] = [
            EpisodeStep(
                timestamp=death_at,
                label="DEATH",
                detail="player_alive = false",
                kind="death",
                observation_id=_nearest_obs_id(
                    observations, death_at, detector="death", positive_only=True
                ),
                transition_id=getattr(tr_by_ts.get(round(death_at, 3)), "id", None),
            )
        ]

        cd_present = [
            o
            for o in observations
            if o.detector in {"respawn", "countdown"}
            and o.positive
            and death_at <= o.timestamp < end_cap
        ]
        cd_absent = [
            o
            for o in observations
            if o.detector in {"respawn", "countdown"}
            and not o.positive
            and death_at <= o.timestamp < end_cap
            and o.confidence is not None
        ]

        has_countdown = bool(cd_present)
        for obs in cd_present[:8]:
            score = obs.confidence if obs.confidence is not None else 0.0
            steps.append(
                EpisodeStep(
                    timestamp=obs.timestamp,
                    label="COUNTDOWN",
                    detail=f"present = true  score = {score:.2f}",
                    kind="countdown_present",
                    observation_id=obs.id,
                )
            )

        # First sustained-absent style marker after last present (or any absent).
        absent_after = None
        if cd_present:
            last_present = cd_present[-1].timestamp
            for obs in cd_absent:
                if obs.timestamp >= last_present:
                    absent_after = obs
                    break
        if absent_after is not None:
            steps.append(
                EpisodeStep(
                    timestamp=absent_after.timestamp,
                    label="COUNTDOWN",
                    detail="present = false",
                    kind="countdown_absent",
                    observation_id=absent_after.id,
                )
            )

        respawn_at = next((t for t in respawns if death_at < t < end_cap), None)
        has_respawn = respawn_at is not None
        if respawn_at is not None:
            steps.append(
                EpisodeStep(
                    timestamp=respawn_at,
                    label="RESPAWN",
                    detail="countdown anchor crossed; still not alive",
                    kind="respawn",
                    transition_id=getattr(
                        tr_by_ts.get(round(respawn_at, 3)), "id", None
                    ),
                )
            )

        active_at = next((t for t in actives if death_at < t < end_cap), None)
        has_active = active_at is not None
        if active_at is not None:
            steps.append(
                EpisodeStep(
                    timestamp=active_at,
                    label="ACTIVE AGAIN",
                    detail="player_alive = true",
                    kind="active_again",
                    transition_id=getattr(
                        tr_by_ts.get(round(active_at, 3)), "id", None
                    ),
                )
            )

        # Snapshot-derived lifecycle breadcrumbs when events are absent.
        if not has_respawn and not has_active:
            for snap in snapshots:
                ts = float(snap.get("timestamp") or -1)
                if not (death_at < ts < end_cap):
                    continue
                phase = str(snap.get("player_lifecycle") or "")
                if phase == "respawned":
                    steps.append(
                        EpisodeStep(
                            timestamp=ts,
                            label="RESPAWNED",
                            detail="lifecycle = respawned (internal)",
                            kind="respawned",
                        )
                    )
                    has_respawn = True
                    break

        steps.sort(key=lambda step: step.timestamp)
        end = steps[-1].timestamp if steps else death_at
        warning = None
        if not has_countdown:
            warning = "DEATH without confirmed countdown in this episode"
        episodes.append(
            DeathEpisode(
                index=index,
                start=death_at,
                end=end,
                steps=steps,
                has_countdown=has_countdown,
                has_respawn=has_respawn,
                has_active_again=has_active,
                warning=warning,
            )
        )
    return episodes


def _phase_of(snap: dict[str, Any]) -> str:
    """Lifecycle phase from snapshot, with pre-Phase-2 fallback."""
    phase = snap.get("player_lifecycle")
    if isinstance(phase, str) and phase:
        return phase
    alive = snap.get("player_alive")
    if alive is False:
        return "dead"
    if alive is True:
        return "alive"
    return "unknown"


def _event_category(event_type: str) -> str:
    """Map GameEventType values onto marker categories."""
    mapping = {
        "death": "death",
        "respawn": "lifecycle",
        "active_again": "lifecycle",
        "splat": "splat",
    }
    return mapping.get(event_type, "lifecycle")


def _nearest_obs_id(
    observations: list[ObservationView],
    timestamp: float,
    *,
    detector: str,
    positive_only: bool,
    tol: float = 1.0,
) -> str | None:
    """Find a nearby observation id for linking episode steps to evidence."""
    candidates = [
        o
        for o in observations
        if o.detector == detector and (o.positive or not positive_only)
    ]
    if not candidates:
        return None
    best = min(candidates, key=lambda o: abs(o.timestamp - timestamp))
    if abs(best.timestamp - timestamp) > tol:
        return None
    return best.id


def _as_float(value: Any) -> float | None:
    """Best-effort float conversion."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
