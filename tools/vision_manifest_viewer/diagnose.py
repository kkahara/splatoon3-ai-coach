"""Death diagnostic chains: detector → lifecycle → event explanations."""

from __future__ import annotations

from typing import Any, Literal

from vision_manifest_viewer.model import (
    CausalStep,
    CueBar,
    DeathDiagnostic,
    DeathEpisode,
    EvidenceCheck,
    NearMiss,
    ObservationView,
)

DecisionKind = Literal["confirmed", "rejected", "suppressed", "near_miss"]


def build_death_diagnostics(
    observations: list[ObservationView],
    events: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    *,
    death_thresholds: dict[str, float] | None = None,
    event_tol: float = 1.25,
) -> list[DeathDiagnostic]:
    """Build a Death Diagnostic for every death-detector observation."""
    thresholds = {
        "ouch_white_ratio": 0.12,
        "banner_dark_threshold": 0.72,
        "banner_near_margin": 0.05,
        **(death_thresholds or {}),
    }
    death_events = sorted(
        float(e["start_time"])
        for e in events
        if str(e.get("event_type")) == "death" and e.get("start_time") is not None
    )
    snap_by_ts = _index_snapshots(snapshots)
    by_ts = _index_observations(observations)

    diagnostics: list[DeathDiagnostic] = []
    for obs in observations:
        if obs.detector != "death":
            continue
        diag = _diagnose_one(
            obs,
            death_events=death_events,
            snap_by_ts=snap_by_ts,
            by_ts=by_ts,
            thresholds=thresholds,
            event_tol=event_tol,
        )
        diagnostics.append(diag)
        obs.diagnostic_id = diag.id
        obs.diagnostic_flags = list(diag.flags)
    diagnostics.sort(key=lambda d: d.timestamp)
    return diagnostics


def enrich_near_misses_with_death(
    near_misses: list[NearMiss],
    diagnostics: list[DeathDiagnostic],
) -> list[NearMiss]:
    """Append detector+/event− and near-threshold death cases to near misses."""
    existing = {(m.kind, round(m.timestamp, 3), m.observation_id) for m in near_misses}
    for diag in diagnostics:
        for flag in diag.flags:
            if flag not in {
                "detector_positive_event_negative",
                "near_threshold",
                "weak_ouch",
            }:
                continue
            key = (flag, round(diag.timestamp, 3), diag.observation_id)
            if key in existing:
                continue
            near_misses.append(
                NearMiss(
                    id=f"near:{diag.id}:{flag}",
                    timestamp=diag.timestamp,
                    kind=flag,
                    summary=diag.decision_reason,
                    observation_id=diag.observation_id,
                    score=diag.cues[0].value if diag.cues else None,
                    detail=_flag_detail(flag, diag),
                )
            )
            existing.add(key)
    near_misses.sort(key=lambda m: m.timestamp)
    return near_misses


def apply_episode_completeness(episodes: list[DeathEpisode]) -> None:
    """Fill completeness score / incomplete warning on death episodes."""
    for episode in episodes:
        checks = {
            "death": True,
            "countdown": episode.has_countdown,
            "respawn": episode.has_respawn,
            "active": episode.has_active_again,
        }
        episode.completeness = {
            "death": checks["death"],
            "countdown": checks["countdown"],
            "respawn": checks["respawn"],
            "active": checks["active"],
        }
        episode.completeness_score = sum(1 for ok in checks.values() if ok)
        episode.completeness_total = 4
        if episode.completeness_score < 4:
            missing = [name.upper() for name, ok in checks.items() if not ok]
            incomplete = f"INCOMPLETE — missing {', '.join(missing)}"
            if episode.warning:
                episode.warning = f"{episode.warning}; {incomplete}"
            else:
                episode.warning = incomplete


def _diagnose_one(
    obs: ObservationView,
    *,
    death_events: list[float],
    snap_by_ts: dict[float, dict[str, Any]],
    by_ts: dict[float, list[ObservationView]],
    thresholds: dict[str, float],
    event_tol: float,
) -> DeathDiagnostic:
    """Explain one death observation end-to-end."""
    reading = obs.reading or {}
    detected = bool(reading.get("detected"))
    ouch = bool(reading.get("ouch_detected"))
    banner = bool(reading.get("banner_detected"))
    ouch_white = _f(reading.get("ouch_white_score"))
    ouch_template = _f(reading.get("ouch_template_score"))
    banner_dark = _f(reading.get("banner_dark_score"))
    white_thr = float(thresholds["ouch_white_ratio"])
    dark_thr = float(thresholds["banner_dark_threshold"])
    near_margin = float(thresholds["banner_near_margin"])

    siblings = by_ts.get(round(obs.timestamp, 3), [])
    splat = next((o for o in siblings if o.detector == "splat"), None)
    active = next((o for o in siblings if o.detector == "active_gameplay"), None)
    splat_score = _f((splat.reading or {}).get("skull_score")) if splat else None
    active_score = (
        _f((active.reading or {}).get("score") or active.confidence) if active else None
    )

    white_pass = ouch_white is not None and ouch_white >= white_thr
    # Older manifests lack template scores; infer glyph when ouch without white.
    template_inferred = ouch and not white_pass
    dark_pass = banner_dark is not None and banner_dark >= dark_thr
    banner_cue_pass = banner or dark_pass

    cues = [
        CueBar(
            label="Ouch template",
            value=ouch_template if ouch_template is not None else (
                1.0 if template_inferred else (0.35 if ouch else 0.0)
            ),
            threshold=float(thresholds.get("ouch_match_threshold", 0.70)),
            passed=(
                ouch_template is not None
                and ouch_template >= float(thresholds.get("ouch_match_threshold", 0.70))
            )
            if ouch_template is not None
            else template_inferred,
            detail=(
                "ouch_template_score"
                if ouch_template is not None
                else "inferred / not stored in older manifests"
            ),
        ),
        CueBar(
            label="Ouch white heuristic",
            value=ouch_white,
            threshold=white_thr,
            passed=bool(reading.get("ouch_heuristic"))
            if "ouch_heuristic" in reading
            else white_pass,
            detail=f"ouch_white_score vs ≥{white_thr:.2f} (not sufficient alone)",
        ),
        CueBar(
            label="Ouch evidence (glyph)",
            value=1.0 if ouch else 0.0,
            threshold=1.0,
            passed=ouch,
            detail="ouch_detected (template/OCR when require_glyph)",
        ),
        CueBar(
            label="Banner darkness",
            value=banner_dark,
            threshold=dark_thr,
            passed=dark_pass,
            detail=f"banner_dark_score vs ≥{dark_thr:.2f} (supporting)",
        ),
        CueBar(
            label="Banner template / OCR",
            value=_f(reading.get("banner_template_score"))
            if reading.get("banner_template_score") is not None
            else (1.0 if banner else 0.0),
            threshold=1.0,
            passed=banner,
            detail="banner_detected",
        ),
        CueBar(
            label="Splat detector",
            value=splat_score if splat_score is not None else (1.0 if splat and splat.positive else 0.0),
            threshold=0.55,
            passed=bool(splat and splat.positive),
            detail="supporting (not required by death rule)",
        ),
        CueBar(
            label="Active gameplay",
            value=active_score if active_score is not None else None,
            threshold=None,
            passed=bool(active and active.positive) if active else None,
            detail="context / supporting (not required on death frame)",
        ),
    ]

    rule_lines = [
        EvidenceCheck(ok=ouch, label="Ouch evidence"),
        EvidenceCheck(ok=banner_cue_pass, label="Banner darkness or banner cue"),
        EvidenceCheck(ok=detected, label="Death rule (ouch ∧ banner)"),
    ]

    event_near = _nearest_time(death_events, obs.timestamp, event_tol)
    event_emitted = event_near is not None
    snap = snap_by_ts.get(obs.timestamp) or _nearest_snap(snap_by_ts, obs.timestamp)
    phase = _phase(snap) if snap else (obs.lifecycle or "unknown")
    already_dead = phase == "dead" and not _is_rising_edge_death(
        snap_by_ts, obs.timestamp, phase
    )

    flags: list[str] = []
    if detected and not event_emitted:
        flags.append("detector_positive_event_negative")
    if (
        banner_dark is not None
        and abs(banner_dark - dark_thr) <= near_margin
        and not detected
    ):
        flags.append("near_threshold")
    if detected and white_pass and not template_inferred:
        flags.append("weak_ouch")
    if (
        detected
        and ouch_template is not None
        and ouch_template < float(thresholds.get("ouch_match_threshold", 0.70))
        and bool(reading.get("ouch_heuristic"))
    ):
        if "weak_ouch" not in flags:
            flags.append("weak_ouch")
    if detected and event_emitted:
        flags.append("detector_positive_event_positive")
    if not detected and (dark_pass or (banner_dark or 0) >= dark_thr - near_margin):
        flags.append("dark_without_death")

    causal, decision, reason = _causal_and_decision(
        detected=detected,
        confidence=obs.confidence,
        phase=phase,
        already_dead=already_dead,
        event_emitted=event_emitted,
        event_near=event_near,
        ouch=ouch,
        white_pass=white_pass,
        template_inferred=template_inferred,
        dark_pass=dark_pass,
        banner=banner,
    )

    return DeathDiagnostic(
        id=f"diag:{obs.id}",
        observation_id=obs.id,
        timestamp=obs.timestamp,
        cues=cues,
        rule_lines=rule_lines,
        causal=causal,
        decision=decision,
        decision_reason=reason,
        flags=flags,
        event_emitted=event_emitted,
        event_timestamp=event_near,
        lifecycle_phase=phase,
    )


def _causal_and_decision(
    *,
    detected: bool,
    confidence: float | None,
    phase: str,
    already_dead: bool,
    event_emitted: bool,
    event_near: float | None,
    ouch: bool,
    white_pass: bool,
    template_inferred: bool,
    dark_pass: bool,
    banner: bool,
) -> tuple[list[CausalStep], DecisionKind, str]:
    """Assemble Detector → Lifecycle → Event steps and final decision label."""
    conf_txt = f"{confidence:.2f}" if confidence is not None else "—"
    if not detected:
        why_parts: list[str] = []
        if not ouch:
            why_parts.append("Ouch evidence FAIL")
        if not (dark_pass or banner):
            why_parts.append("Banner cue FAIL")
        reason = "; ".join(why_parts) or "Death rule FAIL"
        causal = [
            CausalStep(
                layer="detector",
                ok=False,
                title="DEATH DETECTOR",
                detail=f"detected=False  confidence {conf_txt}",
                reason=reason,
            ),
            CausalStep(
                layer="lifecycle",
                ok=True,
                title="LIFECYCLE",
                detail=f"phase={phase.upper()}",
                reason="no death assertion to fuse",
            ),
            CausalStep(
                layer="event",
                ok=False,
                title="EVENT",
                detail="DEATH not emitted",
                reason="detector rejected",
            ),
        ]
        return causal, "rejected", reason

    # detected=True
    if already_dead and not event_emitted:
        causal = [
            CausalStep(
                layer="detector",
                ok=True,
                title="DEATH DETECTOR",
                detail=f"detected=True  confidence {conf_txt}",
                reason="",
            ),
            CausalStep(
                layer="lifecycle",
                ok=False,
                title="LIFECYCLE",
                detail=f"remained {phase.upper()}",
                reason="already in DEAD state",
            ),
            CausalStep(
                layer="event",
                ok=False,
                title="EVENT",
                detail="DEATH not emitted",
                reason="no rising edge (player already dead)",
            ),
        ]
        return (
            causal,
            "suppressed",
            "Detector positive but lifecycle already DEAD — no rising edge",
        )

    if event_emitted:
        weak = white_pass and not template_inferred
        causal = [
            CausalStep(
                layer="detector",
                ok=True,
                title="DEATH DETECTOR",
                detail=f"detected=True  confidence {conf_txt}",
                reason="weak Ouch (white heuristic only)" if weak else "",
            ),
            CausalStep(
                layer="lifecycle",
                ok=True,
                title="LIFECYCLE",
                detail=f"→ {phase.upper()}",
                reason="fused death / player_alive=false",
            ),
            CausalStep(
                layer="event",
                ok=True,
                title="EVENT",
                detail=f"DEATH emitted @ {event_near:.3f}s"
                if event_near is not None
                else "DEATH emitted",
                reason="",
            ),
        ]
        reason = "CONFIRMED — DEATH event emitted"
        if weak:
            reason += " (Ouch via white heuristic — review for FP)"
        return causal, "confirmed", reason

    # detected, not already_dead, no event — unusual (debounce / quality)
    causal = [
        CausalStep(
            layer="detector",
            ok=True,
            title="DEATH DETECTOR",
            detail=f"detected=True  confidence {conf_txt}",
            reason="",
        ),
        CausalStep(
            layer="lifecycle",
            ok=False,
            title="LIFECYCLE",
            detail=f"phase={phase.upper()}",
            reason="no DEATH fusion / rising edge observed",
        ),
        CausalStep(
            layer="event",
            ok=False,
            title="EVENT",
            detail="DEATH not emitted",
            reason="no matching DEATH event near this observation",
        ),
    ]
    return causal, "suppressed", "Detector positive / event negative"


def _is_rising_edge_death(
    snap_by_ts: dict[float, dict[str, Any]],
    timestamp: float,
    phase: str,
) -> bool:
    """True when this timestamp is the first dead after non-dead."""
    if phase != "dead":
        return False
    ordered = sorted(snap_by_ts.items())
    prev_phase: str | None = None
    for ts, snap in ordered:
        cur = _phase(snap)
        if abs(ts - timestamp) <= 0.05:
            return prev_phase != "dead"
        if ts > timestamp + 0.05:
            break
        prev_phase = cur
    return prev_phase != "dead"


def _flag_detail(flag: str, diag: DeathDiagnostic) -> str:
    """Human detail for near-miss rows."""
    if flag == "detector_positive_event_negative":
        return "DeathReading.detected=True but no DEATH GameEvent (sticky-dead / no rising edge)"
    if flag == "near_threshold":
        return "Banner darkness near threshold with death rejected"
    if flag == "weak_ouch":
        return "Ouch likely from white HUD heuristic rather than glyph/template"
    return diag.decision_reason


def _index_observations(
    observations: list[ObservationView],
) -> dict[float, list[ObservationView]]:
    """Group observations by rounded timestamp for sibling lookup."""
    grouped: dict[float, list[ObservationView]] = {}
    for obs in observations:
        grouped.setdefault(round(obs.timestamp, 3), []).append(obs)
    return grouped


def _index_snapshots(
    snapshots: list[dict[str, Any]],
) -> dict[float, dict[str, Any]]:
    """Map timestamp → snapshot."""
    indexed: dict[float, dict[str, Any]] = {}
    for snap in snapshots:
        try:
            indexed[float(snap["timestamp"])] = snap
        except (KeyError, TypeError, ValueError):
            continue
    return indexed


def _nearest_snap(
    snap_by_ts: dict[float, dict[str, Any]],
    timestamp: float,
    tol: float = 0.75,
) -> dict[str, Any] | None:
    """Nearest snapshot within tolerance."""
    if not snap_by_ts:
        return None
    best_ts = min(snap_by_ts, key=lambda t: abs(t - timestamp))
    if abs(best_ts - timestamp) > tol:
        return None
    return snap_by_ts[best_ts]


def _nearest_time(times: list[float], timestamp: float, tol: float) -> float | None:
    """Nearest time in ``times`` within ``tol``, else None."""
    if not times:
        return None
    best = min(times, key=lambda t: abs(t - timestamp))
    if abs(best - timestamp) > tol:
        return None
    return best


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


def _f(value: Any) -> float | None:
    """Best-effort float."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
