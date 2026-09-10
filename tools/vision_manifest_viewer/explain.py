"""Infer transition explanations, detector lanes, latch points, and near misses."""

from __future__ import annotations

from typing import Any

from vision_manifest_viewer.model import (
    DeathEpisode,
    DetectorLane,
    DetectorSample,
    EvidenceCheck,
    LatchPoint,
    LifecycleTransitionMark,
    NearMiss,
    ObservationView,
    TransitionExplanation,
)


def build_detector_lanes(
    observations: list[ObservationView],
) -> list[DetectorLane]:
    """Build Death / Countdown / Active detector timeline rows."""
    specs = [
        ("death", "Death"),
        ("respawn", "Respawn"),
        ("active_gameplay", "Active"),
        ("special_gauge", "Special"),
    ]
    lanes: list[DetectorLane] = []
    for detector, label in specs:
        samples = [
            DetectorSample(
                timestamp=o.timestamp,
                positive=o.positive,
                confidence=o.confidence,
                observation_id=o.id,
                score=_score_of(o),
            )
            for o in observations
            if o.detector == detector
        ]
        samples.sort(key=lambda s: s.timestamp)
        lanes.append(DetectorLane(detector=detector, label=label, samples=samples))
    return lanes


def build_transitions(
    snapshots: list[dict[str, Any]],
    events: list[dict[str, Any]],
    observations: list[ObservationView],
    *,
    present_min: int = 2,
    absent_min: int = 2,
    active_min: int = 3,
) -> list[TransitionExplanation]:
    """Explain lifecycle phase changes and attach to events when possible."""
    ordered = sorted(snapshots, key=lambda s: float(s.get("timestamp") or 0.0))
    transitions: list[TransitionExplanation] = []
    if not ordered:
        return _transitions_from_events_only(events, observations)

    prev = ordered[0]
    prev_phase = _phase(prev)
    for snap in ordered[1:]:
        phase = _phase(snap)
        if phase == prev_phase:
            prev = snap
            continue
        ts = float(snap.get("timestamp") or 0.0)
        explanation = _explain_phase_change(
            from_phase=prev_phase,
            to_phase=phase,
            timestamp=ts,
            snap=snap,
            prev_snap=prev,
            observations=observations,
            present_min=present_min,
            absent_min=absent_min,
            active_min=active_min,
        )
        if explanation is not None:
            transitions.append(explanation)
        prev = snap
        prev_phase = phase

    # Prefer event timestamps / types when present.
    by_ts = {round(t.timestamp, 3): t for t in transitions}
    for event in events:
        et = str(event.get("event_type") or "")
        ts = float(event.get("start_time") or 0.0)
        key = round(ts, 3)
        if et in {"death", "respawn", "active_again"}:
            existing = by_ts.get(key)
            if existing is not None:
                existing.event_type = et
                existing.title = et.upper().replace("_", " ")
            elif et == "death":
                transitions.append(
                    TransitionExplanation(
                        id=f"event:death:{ts}",
                        timestamp=ts,
                        event_type="death",
                        from_phase="alive",
                        to_phase="dead",
                        title="DEATH",
                        checks=[
                            EvidenceCheck(ok=True, label="DeathReading detected"),
                        ],
                        player_alive=False,
                        latch=False,
                        meaning="Death confirmed. New death episode; latch reset.",
                        observation_id=_nearest(
                            observations, ts, detector="death", positive_only=True
                        ),
                    )
                )
        elif et == "splat":
            transitions.append(
                TransitionExplanation(
                    id=f"event:splat:{ts}",
                    timestamp=ts,
                    event_type="splat",
                    title="SPLAT",
                    checks=[EvidenceCheck(ok=True, label="SplatReading rising edge")],
                    meaning=(
                        "Local kill confirmed. Orthogonal to death/respawn lifecycle."
                    ),
                    observation_id=_nearest(
                        observations, ts, detector="splat", positive_only=True
                    ),
                )
            )
    transitions.sort(key=lambda t: t.timestamp)
    return transitions


def build_lifecycle_marks(
    transitions: list[TransitionExplanation],
) -> list[LifecycleTransitionMark]:
    """Compact labels for the top lifecycle strip."""
    marks: list[LifecycleTransitionMark] = []
    for tr in transitions:
        event_label = (tr.event_type or tr.title).upper().replace("_", " ")
        phase_label = (tr.to_phase or "").upper()
        if tr.event_type == "splat":
            event_label = "SPLAT"
            phase_label = "splat"
        elif tr.to_phase == "countdown":
            event_label = "PRESENT"
        elif tr.from_phase == "countdown" and tr.to_phase == "respawned":
            event_label = "ABSENT / RESPAWN"
        elif tr.from_phase == "respawned" and tr.to_phase == "awaiting_control":
            event_label = "AWAITING CONTROL"
        elif tr.event_type == "active_again" or tr.to_phase == "alive":
            event_label = "ACTIVE_AGAIN"
        elif tr.event_type == "death" or tr.to_phase == "dead":
            event_label = "DEATH"
        marks.append(
            LifecycleTransitionMark(
                timestamp=tr.timestamp,
                phase_label=phase_label,
                event_label=event_label,
                transition_id=tr.id,
            )
        )
    return marks


def build_near_misses(
    observations: list[ObservationView],
    *,
    conf_floor: float = 0.50,
    present_score_near: float = 0.55,
) -> list[NearMiss]:
    """Collect borderline countdown/active cases for calibration."""
    misses: list[NearMiss] = []
    for obs in observations:
        if obs.detector in {"respawn", "countdown"}:
            score = float(obs.reading.get("presence_score") or obs.confidence or 0.0)
            present = bool(obs.reading.get("present"))
            if present and obs.confidence is not None and obs.confidence < conf_floor:
                misses.append(
                    NearMiss(
                        id=f"near:{obs.id}",
                        timestamp=obs.timestamp,
                        kind="countdown",
                        summary=f"present=true score={score:.2f} ← below usable confidence",
                        observation_id=obs.id,
                        score=score,
                        detail="Countdown structure fired but confidence < min_usable",
                    )
                )
            elif (
                not present
                and score >= 0.35
                and score < present_score_near
            ):
                misses.append(
                    NearMiss(
                        id=f"near:{obs.id}",
                        timestamp=obs.timestamp,
                        kind="countdown",
                        summary=f"present=false score={score:.2f}",
                        observation_id=obs.id,
                        score=score,
                        detail="Near structure thresholds but not present",
                    )
                )
        if obs.detector == "active_gameplay" and obs.positive:
            if obs.countdown_present is True:
                misses.append(
                    NearMiss(
                        id=f"near:{obs.id}:cd",
                        timestamp=obs.timestamp,
                        kind="active",
                        summary="active=true but countdown=true",
                        observation_id=obs.id,
                        score=obs.confidence,
                        detail="Conflict: countdown present blocks ACTIVE composite",
                    )
                )
            if obs.lifecycle in {"dead", "countdown"} and obs.player_alive is False:
                # Death conflict is harder without per-frame death flag; use lifecycle.
                pass
        if obs.detector == "active_gameplay" and not obs.positive:
            if obs.lifecycle == "respawned":
                misses.append(
                    NearMiss(
                        id=f"near:{obs.id}:resp",
                        timestamp=obs.timestamp,
                        kind="active",
                        summary="active=false during RESPAWNED",
                        observation_id=obs.id,
                        score=obs.confidence,
                        detail="No positive ActiveGameplayReading while waiting for ACTIVE_AGAIN",
                    )
                )
    # Death conflict near-misses: active positive on same timestamp as death positive.
    death_ts = {
        round(o.timestamp, 3)
        for o in observations
        if o.detector == "death" and o.positive
    }
    for obs in observations:
        if obs.detector == "active_gameplay" and obs.positive:
            if round(obs.timestamp, 3) in death_ts:
                misses.append(
                    NearMiss(
                        id=f"near:{obs.id}:death",
                        timestamp=obs.timestamp,
                        kind="active",
                        summary="active=true but death=true",
                        observation_id=obs.id,
                        score=obs.confidence,
                        detail="Conflict: death detection blocks ACTIVE composite",
                    )
                )
    misses.sort(key=lambda m: m.timestamp)
    return misses


def attach_latch_points(
    episodes: list[DeathEpisode],
    snapshots: list[dict[str, Any]],
    transitions: list[TransitionExplanation],
) -> None:
    """Fill episode latch strips from snapshots / transitions."""
    for episode in episodes:
        points: list[LatchPoint] = [
            LatchPoint(timestamp=episode.start, value=False, label="DEATH")
        ]
        for tr in transitions:
            if not (episode.start <= tr.timestamp <= (episode.end or tr.timestamp)):
                continue
            if tr.to_phase == "countdown" and tr.latch is True:
                points.append(
                    LatchPoint(
                        timestamp=tr.timestamp,
                        value=True,
                        label="COUNTDOWN",
                        observation_id=tr.observation_id,
                    )
                )
            if tr.to_phase == "respawned":
                points.append(
                    LatchPoint(
                        timestamp=tr.timestamp,
                        value=True if tr.latch is None else tr.latch,
                        label="RESPAWN",
                        observation_id=tr.observation_id,
                    )
                )
            if tr.to_phase == "alive" or tr.event_type == "active_again":
                points.append(
                    LatchPoint(
                        timestamp=tr.timestamp,
                        value=False,
                        label="ACTIVE_AGAIN",
                        observation_id=tr.observation_id,
                    )
                )
        # Deduplicate by label keeping first.
        seen: set[str] = set()
        unique: list[LatchPoint] = []
        for point in sorted(points, key=lambda p: p.timestamp):
            if point.label in seen:
                continue
            seen.add(point.label)
            unique.append(point)
        episode.latch_points = unique


def _explain_phase_change(
    *,
    from_phase: str,
    to_phase: str,
    timestamp: float,
    snap: dict[str, Any],
    prev_snap: dict[str, Any],
    observations: list[ObservationView],
    present_min: int,
    absent_min: int,
    active_min: int,
) -> TransitionExplanation | None:
    """Build a Why? panel for one fused phase change."""
    latch = snap.get("countdown_confirmed_this_death_episode")
    if latch is None:
        latch = prev_snap.get("countdown_confirmed_this_death_episode")
    latch_bool = bool(latch) if latch is not None else None

    if to_phase == "dead":
        return TransitionExplanation(
            id=f"tr:{timestamp}:dead",
            timestamp=timestamp,
            event_type="death",
            from_phase=from_phase,
            to_phase=to_phase,
            title="DEATH",
            checks=[EvidenceCheck(ok=True, label="DeathReading / death UI confirmed")],
            player_alive=False,
            latch=False,
            meaning="Death confirmed. Episode latch reset to false.",
            observation_id=_nearest(
                observations, timestamp, detector="death", positive_only=True
            ),
        )

    if from_phase == "dead" and to_phase == "countdown":
        return TransitionExplanation(
            id=f"tr:{timestamp}:countdown",
            timestamp=timestamp,
            from_phase=from_phase,
            to_phase=to_phase,
            title="COUNTDOWN",
            checks=[
                EvidenceCheck(
                    ok=True,
                    label=f"countdown present sustained ({present_min} observations)",
                ),
                EvidenceCheck(ok=True, label="countdown_confirmed_this_death_episode armed"),
            ],
            player_alive=False,
            latch=True,
            meaning="Countdown presence confirmed. Latch armed for this death episode.",
            observation_id=_nearest(
                observations, timestamp, detector="respawn", positive_only=True
            ),
        )

    if from_phase == "countdown" and to_phase == "respawned":
        latch_ok = latch_bool is not False
        anomaly = latch_bool is False
        return TransitionExplanation(
            id=f"tr:{timestamp}:respawn",
            timestamp=timestamp,
            event_type="respawn",
            from_phase=from_phase,
            to_phase=to_phase,
            title="RESPAWN",
            checks=[
                EvidenceCheck(
                    ok=latch_ok,
                    label="countdown_confirmed_this_death_episode",
                ),
                EvidenceCheck(
                    ok=True,
                    label=f"countdown absent: {absent_min}/{absent_min} observations",
                ),
                EvidenceCheck(ok=True, label="no death reassertion"),
            ],
            player_alive=False,
            latch=True if latch_bool is None else latch_bool,
            meaning=(
                "Countdown lifecycle anchor crossed. "
                "Player is NOT yet considered active."
            ),
            observation_id=_nearest(
                observations, timestamp, detector="respawn", positive_only=False
            ),
            anomaly=anomaly,
            anomaly_message=(
                "RESPAWN while latch is false — violates anti-FP invariant"
                if anomaly
                else None
            ),
        )

    if from_phase == "respawned" and to_phase == "awaiting_control":
        sj = snap.get("awaiting_control_confirmed_this_death_episode")
        return TransitionExplanation(
            id=f"tr:{timestamp}:awaiting_control",
            timestamp=timestamp,
            from_phase=from_phase,
            to_phase=to_phase,
            title="AWAITING CONTROL",
            checks=[
                EvidenceCheck(
                    ok=True,
                    label="control absent sustained (awaiting_control_absent_min)",
                ),
                EvidenceCheck(
                    ok=sj is not False,
                    label="awaiting_control_confirmed_this_death_episode",
                ),
            ],
            player_alive=False,
            latch=True if latch_bool is None else latch_bool,
            meaning=(
                "Post-respawn control-return latch armed (not Super Jump detection). "
                "ACTIVE_AGAIN requires control return from this phase only. "
                "Map viewing is independent evidence and does not drive this transition."
            ),
            observation_id=_nearest(
                observations, timestamp, detector="active_gameplay", positive_only=False
            ),
        )

    if from_phase == "awaiting_control" and to_phase == "alive":
        return TransitionExplanation(
            id=f"tr:{timestamp}:active",
            timestamp=timestamp,
            event_type="active_again",
            from_phase=from_phase,
            to_phase=to_phase,
            title="ACTIVE AGAIN",
            checks=[
                EvidenceCheck(ok=True, label="previous_phase == awaiting_control"),
                EvidenceCheck(
                    ok=True,
                    label="return_control (weapon+center; HUD optional)",
                ),
                EvidenceCheck(
                    ok=True,
                    label=(
                        f"control present: {active_min}/{active_min} "
                        "(1-frame gap allowed)"
                    ),
                ),
                EvidenceCheck(ok=True, label="no countdown conflict"),
                EvidenceCheck(ok=True, label="no death conflict"),
            ],
            player_alive=True,
            latch=False,
            meaning=(
                "Control returned after post-respawn latch using soft return_control "
                "with gap-tolerant streak. ACTIVE_AGAIN requires previous_phase == "
                "awaiting_control."
            ),
            observation_id=_nearest(
                observations, timestamp, detector="active_gameplay", positive_only=True
            ),
        )

    if from_phase == "respawned" and to_phase == "alive":
        return TransitionExplanation(
            id=f"tr:{timestamp}:active_anomaly",
            timestamp=timestamp,
            event_type="active_again",
            from_phase=from_phase,
            to_phase=to_phase,
            title="ACTIVE AGAIN (ANOMALY)",
            checks=[
                EvidenceCheck(
                    ok=False,
                    label="previous_phase == awaiting_control (required)",
                ),
            ],
            player_alive=True,
            latch=False,
            meaning="Alive without SUPER_JUMP latch — violates ACTIVE_AGAIN invariant.",
            observation_id=_nearest(
                observations, timestamp, detector="active_gameplay", positive_only=True
            ),
            anomaly=True,
            anomaly_message="ACTIVE without SUPER_JUMP latch",
        )

    if to_phase == "unknown":
        return TransitionExplanation(
            id=f"tr:{timestamp}:unknown",
            timestamp=timestamp,
            from_phase=from_phase,
            to_phase=to_phase,
            title="UNKNOWN",
            checks=[
                EvidenceCheck(
                    ok=True,
                    label="max_respawn_observation_seconds elapsed since DEATH",
                )
            ],
            player_alive=None,
            latch=False,
            meaning="Stale death-side episode abandoned. Never implies RESPAWN/ACTIVE.",
        )
    return None


def _transitions_from_events_only(
    events: list[dict[str, Any]],
    observations: list[ObservationView],
) -> list[TransitionExplanation]:
    """Fallback when snapshots lack lifecycle fields (pre-Phase-2 manifests)."""
    out: list[TransitionExplanation] = []
    for event in events:
        et = str(event.get("event_type") or "")
        ts = float(event.get("start_time") or 0.0)
        if et == "death":
            out.append(
                TransitionExplanation(
                    id=f"event:death:{ts}",
                    timestamp=ts,
                    event_type="death",
                    from_phase="alive",
                    to_phase="dead",
                    title="DEATH",
                    checks=[EvidenceCheck(ok=True, label="DEATH event in manifest")],
                    player_alive=False,
                    latch=False,
                    meaning="Death event present (pre-Phase-2 snapshot fields may be missing).",
                    observation_id=_nearest(
                        observations, ts, detector="death", positive_only=True
                    ),
                )
            )
        elif et == "respawn":
            out.append(
                TransitionExplanation(
                    id=f"event:respawn:{ts}",
                    timestamp=ts,
                    event_type="respawn",
                    from_phase="countdown",
                    to_phase="respawned",
                    title="RESPAWN",
                    checks=[
                        EvidenceCheck(ok=True, label="RESPAWN event in manifest"),
                    ],
                    player_alive=False,
                    meaning="Countdown lifecycle anchor crossed. Player is NOT yet active.",
                )
            )
        elif et == "active_again":
            out.append(
                TransitionExplanation(
                    id=f"event:active_again:{ts}",
                    timestamp=ts,
                    event_type="active_again",
                    from_phase="respawned",
                    to_phase="alive",
                    title="ACTIVE AGAIN",
                    checks=[
                        EvidenceCheck(ok=True, label="ACTIVE_AGAIN event in manifest"),
                    ],
                    player_alive=True,
                    latch=False,
                    meaning="Positive normal-gameplay evidence confirmed.",
                )
            )
        elif et == "splat":
            out.append(
                TransitionExplanation(
                    id=f"event:splat:{ts}",
                    timestamp=ts,
                    event_type="splat",
                    title="SPLAT",
                    checks=[EvidenceCheck(ok=True, label="SPLAT event in manifest")],
                    meaning="Local kill confirmed. Orthogonal to death/respawn lifecycle.",
                    observation_id=_nearest(
                        observations, ts, detector="splat", positive_only=True
                    ),
                )
            )
    return out


def _phase(snap: dict[str, Any]) -> str:
    """Lifecycle phase with pre-Phase-2 fallback."""
    phase = snap.get("player_lifecycle")
    if isinstance(phase, str) and phase:
        return phase
    alive = snap.get("player_alive")
    if alive is False:
        return "dead"
    if alive is True:
        return "alive"
    return "unknown"


def _score_of(obs: ObservationView) -> float | None:
    """Best scalar score for detector-lane tooltips."""
    reading = obs.reading or {}
    for key in ("presence_score", "score", "skull_score"):
        if key in reading and reading[key] is not None:
            try:
                return float(reading[key])
            except (TypeError, ValueError):
                pass
    return obs.confidence


def _nearest(
    observations: list[ObservationView],
    timestamp: float,
    *,
    detector: str,
    positive_only: bool,
    tol: float = 1.5,
) -> str | None:
    """Find a nearby observation for linking explanations to evidence."""
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
