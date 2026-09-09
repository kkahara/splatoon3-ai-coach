"""Load vision manifests into a normalized viewer model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from vision_manifest_viewer.diagnose import (
    apply_episode_completeness,
    build_death_diagnostics,
    enrich_near_misses_with_death,
)
from vision_manifest_viewer.explain import (
    attach_latch_points,
    build_detector_lanes,
    build_lifecycle_marks,
    build_near_misses,
    build_transitions,
)
from vision_manifest_viewer.model import (
    DeathEpisode,
    ManifestSummary,
    ManifestView,
    ManifestWarning,
    MarkerCategory,
    ObservationView,
    RoiBox,
    RosterSampleView,
    ScenarioEvidenceView,
)
from vision_manifest_viewer.timeline import (
    build_death_episodes,
    build_lifecycle_segments,
    build_markers,
)

_DEFAULT_ROIS: dict[str, tuple[float, float, float, float]] = {
    "respawn": (0.840, 0.825, 1.000, 0.950),
    "death": (0.0104167, 0.8148148, 0.1041667, 0.8703704),
    "splat": (0.35, 0.85, 0.65, 0.98),
    "active_gameplay": (0.32, 0.48, 0.68, 0.90),
    "map_overlay": (0.0138889, 0.0171875, 0.1777778, 0.16875),
}


def load_manifest_view(
    manifest_path: Path,
    *,
    config_path: Path | None = None,
) -> ManifestView:
    """Load and normalize a vision_manifest.json for the HTML viewer."""
    manifest_path = manifest_path.resolve()
    analysis_dir = manifest_path.parent
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))

    frame_results: list[dict[str, Any]] = list(raw.get("frame_results") or [])
    snapshots: list[dict[str, Any]] = list(raw.get("state_snapshots") or [])
    events: list[dict[str, Any]] = list(raw.get("game_events") or [])

    snap_by_ts = _index_snapshots(snapshots)
    rois = _load_rois(config_path)
    life_cfg = _load_lifecycle_config(config_path)
    death_thresholds = _load_death_thresholds(config_path)
    observations = _build_observations(frame_results, snap_by_ts, analysis_dir, rois)
    transitions = build_transitions(
        snapshots,
        events,
        observations,
        present_min=int(life_cfg.get("countdown_present_min_observations", 2)),
        absent_min=int(life_cfg.get("countdown_absent_min_observations", 2)),
        active_min=int(life_cfg.get("active_again_min_observations", 3)),
    )
    _link_observations_to_transitions(observations, transitions)
    markers = build_markers(observations, events, transitions)
    segments = build_lifecycle_segments(snapshots)
    lifecycle_marks = build_lifecycle_marks(transitions)
    detector_lanes = build_detector_lanes(observations)
    near_misses = build_near_misses(observations)
    episodes = build_death_episodes(events, observations, snapshots, transitions)
    attach_latch_points(episodes, snapshots, transitions)
    apply_episode_completeness(episodes)
    death_diagnostics = build_death_diagnostics(
        observations,
        events,
        snapshots,
        death_thresholds=death_thresholds,
    )
    near_misses = enrich_near_misses_with_death(near_misses, death_diagnostics)
    summary = _build_summary(
        raw, frame_results, observations, events, episodes, death_diagnostics
    )

    detectors = sorted({o.detector for o in observations if o.detector})
    categories = sorted({o.category for o in observations})

    return ManifestView(
        summary=summary,
        observations=observations,
        roster_timeline=_build_roster_timeline(snapshots),
        markers=markers,
        lifecycle_segments=segments,
        lifecycle_marks=lifecycle_marks,
        detector_lanes=detector_lanes,
        transitions=transitions,
        near_misses=near_misses,
        episodes=episodes,
        death_diagnostics=death_diagnostics,
        detectors=detectors,
        categories=categories,
        default_rois=rois,
        lifecycle_config=life_cfg,
        death_thresholds=death_thresholds,
        analysis_dir=str(analysis_dir),
        manifest_path=str(manifest_path),
        scenario_evidence=_load_scenario_evidence(analysis_dir),
    )


_SCENARIOS_JSON = "scenarios.json"
_SCENARIO_CONTEXTS_JSON = "scenario_contexts.json"


def _load_scenario_evidence(analysis_dir: Path) -> list[ScenarioEvidenceView]:
    """Join sibling scenarios.json + scenario_contexts.json when present."""
    contexts = _read_json_list(analysis_dir / _SCENARIO_CONTEXTS_JSON)
    scenarios = {
        str(item["scenario_id"]): item
        for item in _read_json_list(analysis_dir / _SCENARIOS_JSON)
        if isinstance(item, dict) and item.get("scenario_id")
    }
    cards: list[ScenarioEvidenceView] = []
    for ctx in contexts:
        if not isinstance(ctx, dict) or not ctx.get("scenario_id"):
            continue
        cards.append(_join_scenario_card(str(ctx["scenario_id"]), ctx, scenarios))
    return cards


def _join_scenario_card(
    scenario_id: str,
    ctx: dict[str, Any],
    scenarios: dict[str, dict[str, Any]],
) -> ScenarioEvidenceView:
    """Attach Scenario times/outcome onto one ScenarioContext record."""
    meta = scenarios.get(scenario_id) or {}
    parsed_type, parsed_start = _split_scenario_id(scenario_id)
    start = meta.get("start_time")
    if start is None:
        start = parsed_start
    return ScenarioEvidenceView(
        scenario_id=scenario_id,
        scenario_type=str(meta.get("scenario_type") or parsed_type),
        start_time=_as_float(start),
        end_time=_as_float(meta.get("end_time")),
        outcome=str(meta["outcome"]) if meta.get("outcome") is not None else None,
        event_ids=_event_ids(meta.get("event_ids")),
        following_death_id=_following_death_id(meta.get("context")),
        timeline=_as_dict(ctx.get("timeline")),
        map=_as_dict(ctx.get("map")),
        combat=_as_dict(ctx.get("combat")),
        recovery=_as_dict(ctx.get("death_episode") or ctx.get("recovery")),
        relations=_as_dict(ctx.get("relations")),
    )


def _split_scenario_id(scenario_id: str) -> tuple[str, float | None]:
    """Parse ``{type}:{start:.3f}`` without requiring scenarios.json."""
    kind, sep, rest = scenario_id.partition(":")
    if not sep:
        return scenario_id, None
    return kind, _as_float(rest)


def _read_json_list(path: Path) -> list[Any]:
    """Load a JSON array file; missing or invalid files yield []."""
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, list):
        return payload
    return []


def _event_ids(value: Any) -> list[str]:
    """Copy scenario event_ids for the review event list."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _following_death_id(context: Any) -> str | None:
    """Compat id from Scenario.context only (not ScenarioContext)."""
    if not isinstance(context, dict):
        return None
    value = context.get("following_death_id")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_dict(value: Any) -> dict[str, Any] | None:
    """Return a dict payload, or None when the nest is absent."""
    if isinstance(value, dict):
        return value
    return None


def _index_snapshots(
    snapshots: list[dict[str, Any]],
) -> dict[float, dict[str, Any]]:
    """Map timestamp → snapshot (last write wins on collisions)."""
    indexed: dict[float, dict[str, Any]] = {}
    for snap in snapshots:
        try:
            indexed[float(snap["timestamp"])] = snap
        except (KeyError, TypeError, ValueError):
            continue
    return indexed


def _load_rois(config_path: Path | None) -> dict[str, RoiBox]:
    """Load detector ROIs from YAML config when available."""
    rois: dict[str, RoiBox] = {
        name: RoiBox(x1=box[0], y1=box[1], x2=box[2], y2=box[3], label=name)
        for name, box in _DEFAULT_ROIS.items()
    }
    path = config_path
    if path is None:
        candidate = Path("configs/default.yaml")
        if candidate.exists():
            path = candidate
    if path is None or not path.exists():
        return rois

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    vision = data.get("vision") or {}
    mapping = {
        "respawn": ("respawn", "roi"),
        "death": ("death", "ouch_roi"),
        "splat": ("splat", "banner_roi"),
        "active_gameplay": ("active_gameplay", "weapon_roi"),
        "map_overlay": ("map_overlay", "map_roi"),
    }
    for label, (section, key) in mapping.items():
        box = (vision.get(section) or {}).get(key)
        if isinstance(box, list | tuple) and len(box) == 4:
            rois[label] = RoiBox(
                x1=float(box[0]),
                y1=float(box[1]),
                x2=float(box[2]),
                y2=float(box[3]),
                label=label,
            )
    return rois


def _build_observations(
    frame_results: list[dict[str, Any]],
    snap_by_ts: dict[float, dict[str, Any]],
    analysis_dir: Path,
    rois: dict[str, RoiBox],
) -> list[ObservationView]:
    """Flatten frame detections into selectable observations."""
    observations: list[ObservationView] = []
    for frame in frame_results:
        timestamp = float(frame.get("timestamp") or 0.0)
        frame_index = frame.get("source_frame_index")
        frame_id = frame.get("frame_id")
        frame_path = frame.get("frame_path")
        image_relpath = _resolve_image_relpath(analysis_dir, frame_path)
        snap = snap_by_ts.get(timestamp) or {}
        lifecycle = snap.get("player_lifecycle")
        player_alive = snap.get("player_alive")
        ally_alive_count = _as_int_or_none(snap.get("ally_alive_count"))
        opponent_alive_count = _as_int_or_none(snap.get("opponent_alive_count"))
        countdown_present = snap.get("countdown_present")
        active_gameplay = snap.get("active_gameplay")
        match_phase = snap.get("match_phase")
        latch = snap.get("countdown_confirmed_this_death_episode")
        detections = list(frame.get("detections") or [])

        if not detections:
            observations.append(
                ObservationView(
                    id=f"frame:{frame_id or timestamp}",
                    timestamp=timestamp,
                    frame_index=frame_index,
                    frame_id=frame_id,
                    frame_path=frame_path,
                    image_relpath=image_relpath,
                    category="other",
                    lifecycle=lifecycle,
                    player_alive=player_alive,
                    ally_alive_count=ally_alive_count,
                    opponent_alive_count=opponent_alive_count,
                    countdown_present=countdown_present,
                    active_gameplay=active_gameplay,
                    match_phase=match_phase,
                    latch=latch,
                    source=frame.get("source"),
                    layer="observation",
                )
            )
            continue

        for det in detections:
            detector = str(det.get("detector_name") or "unknown")
            reading = dict(det.get("reading") or {})
            kind = reading.get("kind")
            positive = _is_positive(detector, reading)
            category = _category_for(detector, reading)
            obs_id = str(det.get("id") or f"{frame_id}:{detector}")
            observations.append(
                ObservationView(
                    id=obs_id,
                    timestamp=timestamp,
                    frame_index=frame_index,
                    frame_id=frame_id,
                    frame_path=frame_path,
                    image_relpath=image_relpath,
                    detector=detector,
                    category=category,
                    confidence=_as_float(det.get("confidence")),
                    positive=positive,
                    reading=reading,
                    reading_kind=str(kind) if kind else None,
                    roi=rois.get(detector),
                    lifecycle=lifecycle,
                    player_alive=player_alive,
                    ally_alive_count=ally_alive_count,
                    opponent_alive_count=opponent_alive_count,
                    countdown_present=countdown_present,
                    active_gameplay=active_gameplay,
                    match_phase=match_phase,
                    latch=latch,
                    source=frame.get("source"),
                    layer="observation",
                )
            )
    return observations


def _build_roster_timeline(
    snapshots: list[dict[str, Any]],
) -> list[RosterSampleView]:
    """Ordered fused roster samples for scenario-panel lookups."""
    samples: list[RosterSampleView] = []
    for snap in sorted(snapshots, key=lambda item: float(item.get("timestamp") or 0.0)):
        ally = _as_int_or_none(snap.get("ally_alive_count"))
        opponent = _as_int_or_none(snap.get("opponent_alive_count"))
        if ally is None or opponent is None:
            continue
        samples.append(
            RosterSampleView(
                video_time=float(snap.get("timestamp") or 0.0),
                ally_alive_count=ally,
                opponent_alive_count=opponent,
            )
        )
    return samples


def _as_int_or_none(value: Any) -> int | None:
    """Parse an optional integer field."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _resolve_image_relpath(analysis_dir: Path, frame_path: str | None) -> str | None:
    """Return a path relative to the HTML output directory when the file exists."""
    if not frame_path:
        return None
    candidate = Path(frame_path)
    if not candidate.is_absolute():
        candidate = analysis_dir / frame_path
    if not candidate.exists():
        return None
    try:
        return str(candidate.resolve().relative_to(analysis_dir.resolve()))
    except ValueError:
        return str(candidate)


def _load_lifecycle_config(config_path: Path | None) -> dict[str, int | float | bool]:
    """Load lifecycle persistence knobs for Why? panels."""
    defaults: dict[str, int | float | bool] = {
        "countdown_present_min_observations": 2,
        "countdown_absent_min_observations": 2,
        "awaiting_control_absent_min_observations": 2,
        "active_again_min_observations": 3,
        "active_again_max_gap_observations": 1,
        "max_respawn_observation_seconds": 30.0,
        "stale_to_unknown": True,
    }
    path = config_path
    if path is None:
        candidate = Path("configs/default.yaml")
        if candidate.exists():
            path = candidate
    if path is None or not path.exists():
        return defaults
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    life = (data.get("vision") or {}).get("lifecycle") or {}
    for key in defaults:
        if key in life:
            defaults[key] = life[key]
    return defaults


def _load_death_thresholds(config_path: Path | None) -> dict[str, float]:
    """Load death detector thresholds for diagnostic bars."""
    defaults: dict[str, float] = {
        "ouch_white_ratio": 0.12,
        "banner_dark_threshold": 0.72,
        "ouch_match_threshold": 0.70,
        "banner_match_threshold": 0.55,
        "banner_near_margin": 0.05,
    }
    path = config_path
    if path is None:
        candidate = Path("configs/default.yaml")
        if candidate.exists():
            path = candidate
    if path is None or not path.exists():
        return defaults
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    death = (data.get("vision") or {}).get("death") or {}
    for key in (
        "ouch_white_ratio",
        "banner_dark_threshold",
        "ouch_match_threshold",
        "banner_match_threshold",
    ):
        if key in death:
            defaults[key] = float(death[key])
    return defaults


def _link_observations_to_transitions(
    observations: list[ObservationView],
    transitions: list,
) -> None:
    """Attach transition ids onto nearest observations for click-through."""
    for tr in transitions:
        if not tr.observation_id:
            continue
        for obs in observations:
            if obs.id == tr.observation_id:
                obs.transition_id = tr.id
                break


def _is_positive(detector: str, reading: dict[str, Any]) -> bool:
    """Whether a reading counts as a positive detection for filters/gallery."""
    if detector == "respawn":
        return bool(reading.get("detected"))
    if detector in {"countdown", "map_overlay"}:
        return bool(reading.get("present"))
    if "detected" in reading:
        return bool(reading.get("detected"))
    if detector == "timer":
        return reading.get("seconds_remaining") is not None
    return False


def _category_for(detector: str, reading: dict[str, Any]) -> MarkerCategory:
    """Map detector/reading to a timeline category."""
    known: dict[str, MarkerCategory] = {
        "death": "death",
        "respawn": "countdown",
        "countdown": "countdown",
        "splat": "splat",
        "active_gameplay": "active_gameplay",
        "spawn_view": "spawn_view",
        "timer": "timer",
    }
    if detector in known:
        return known[detector]
    kind = reading.get("kind")
    if isinstance(kind, str) and kind in known:
        return known[kind]
    return "other"


def _as_float(value: Any) -> float | None:
    """Best-effort float conversion."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_summary(
    raw: dict[str, Any],
    frame_results: list[dict[str, Any]],
    observations: list[ObservationView],
    events: list[dict[str, Any]],
    episodes: list[DeathEpisode],
    death_diagnostics: list | None = None,
) -> ManifestSummary:
    """Compute top-of-page diagnostic summary and warnings."""
    timestamps = [float(f.get("timestamp") or 0.0) for f in frame_results]
    duration = max(timestamps) if timestamps else 0.0
    video_identity = str(raw.get("video_identity") or "")
    video_label = _video_label(raw, analysis_dir_hint=video_identity)

    death_pos = sum(1 for o in observations if o.detector == "death" and o.positive)
    cd_obs = sum(1 for o in observations if o.detector in {"respawn", "countdown"})
    splat_pos = sum(1 for o in observations if o.detector == "splat" and o.positive)
    active_pos = sum(
        1 for o in observations if o.detector == "active_gameplay" and o.positive
    )
    spawn_pos = sum(1 for o in observations if o.detector == "spawn_view" and o.positive)
    event_counts: dict[str, int] = {}
    for event in events:
        key = str(event.get("event_type") or "unknown")
        event_counts[key] = event_counts.get(key, 0) + 1

    diagnostics = death_diagnostics or []
    det_pos_event_neg = sum(
        1
        for d in diagnostics
        if "detector_positive_event_negative" in d.flags
    )
    confirmed = sum(1 for d in diagnostics if d.decision == "confirmed")
    rejected = sum(1 for d in diagnostics if d.decision == "rejected")
    suppressed = sum(1 for d in diagnostics if d.decision == "suppressed")

    warnings = _build_warnings(
        observations, episodes, event_counts, det_pos_event_neg=det_pos_event_neg
    )
    return ManifestSummary(
        video_label=video_label,
        video_identity=video_identity,
        duration_seconds=duration,
        frame_count=len(frame_results),
        detection_count=sum(1 for o in observations if o.detector),
        death_detections=death_pos,
        countdown_observations=cd_obs,
        splat_detections=splat_pos,
        active_observations=active_pos,
        spawn_observations=spawn_pos,
        lifecycle_episodes=len(episodes),
        event_counts=event_counts,
        warnings=warnings,
        detector_positive_event_negative=det_pos_event_neg,
        death_confirmed=confirmed,
        death_rejected=rejected,
        death_suppressed=suppressed,
    )


def _video_label(raw: dict[str, Any], *, analysis_dir_hint: str) -> str:
    """Human label for the video (path stem when available)."""
    _ = analysis_dir_hint
    # Prefer nothing inventing paths; show truncated identity.
    identity = str(raw.get("video_identity") or "unknown")
    return identity[:16] + "…" if len(identity) > 16 else identity


def _build_warnings(
    observations: list[ObservationView],
    episodes: list[DeathEpisode],
    event_counts: dict[str, int],
    *,
    det_pos_event_neg: int = 0,
) -> list[ManifestWarning]:
    """Derive Phase-2-oriented diagnostic warnings."""
    warnings: list[ManifestWarning] = []
    if det_pos_event_neg:
        warnings.append(
            ManifestWarning(
                level="warn",
                message=(
                    f"{det_pos_event_neg} detector-positive / event-negative "
                    "death observations (sticky-dead or suppressed)"
                ),
            )
        )
    low_cd = sum(
        1
        for o in observations
        if o.detector in {"respawn", "countdown"}
        and o.confidence is not None
        and o.confidence < 0.5
    )
    if low_cd:
        warnings.append(
            ManifestWarning(
                level="warn",
                message=f"{low_cd} countdown observations below usable confidence",
            )
        )

    for episode in episodes:
        if not episode.has_countdown:
            warnings.append(
                ManifestWarning(
                    level="warn",
                    message=(
                        f"Episode {episode.index} has DEATH but no confirmed countdown"
                    ),
                )
            )
        if episode.completeness_score and episode.completeness_score < 4:
            warnings.append(
                ManifestWarning(
                    level="info",
                    message=(
                        f"Episode {episode.index} completeness "
                        f"{episode.completeness_score}/{episode.completeness_total}"
                    ),
                )
            )

    if event_counts.get("respawn", 0) == 0 and any(e.has_countdown for e in episodes):
        warnings.append(
            ManifestWarning(
                level="info",
                message="Countdown seen but no RESPAWN events (pre-Phase-2 manifest?)",
            )
        )

    bad_respawn = False
    for episode in episodes:
        if episode.has_respawn and not episode.has_countdown:
            bad_respawn = True
            break
    if bad_respawn:
        warnings.append(
            ManifestWarning(
                level="warn",
                message="RESPAWN occurred without prior countdown presence",
            )
        )
    else:
        warnings.append(
            ManifestWarning(
                level="ok",
                message="No RESPAWN occurred without prior countdown presence",
            )
        )
    return warnings


__all__ = [
    "load_manifest_view",
]
