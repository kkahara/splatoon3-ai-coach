"""Match-level side effects during cadence scan: Ready gate, team color, map ink."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.media.video import VideoFrame
from splatoon3_ai_coach.vision.base import BaseDetector
from splatoon3_ai_coach.vision.map_ink import (
    MAP_OBSERVATIONS_FILENAME,
    MapInkClassifier,
    MapObservation,
    analyze_map_ink,
    write_map_ink_diagnostic,
    write_map_observations,
)
from splatoon3_ai_coach.vision.match_intro import MatchIdentityTracker
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    MapOverlayReading,
    MatchIntroReading,
    ReadyReading,
    TimerReading,
)
from splatoon3_ai_coach.vision.stage_maps import (
    MATCH_IDENTITY_FILENAME,
    resolve_stage_map_geometry,
)
from splatoon3_ai_coach.vision.stage_mask import resolve_stage_mask
from splatoon3_ai_coach.vision.team_color_calibration import (
    TeamColorCalibrationResult,
    TeamColorCalibrator,
)


@dataclass
class MapInkScanContext:
    """Mutable intro identity + sparse map-ink observations for one run."""

    identity: MatchIdentityTracker
    classifier: MapInkClassifier | None
    observations: list[MapObservation] = field(default_factory=list)
    last_sample_at: float | None = None
    diagnostics_dir: Path | None = None
    config: AppConfig | None = None
    calibrator: TeamColorCalibrator | None = None
    calibration: TeamColorCalibrationResult | None = None
    # Ready? search: stage known → first non-opening timer tick.
    ready_seen: bool = False
    opening_clock_ticked: bool = False
    # When Ready? is never seen, colors + map ink are discarded for the match.
    map_ink_dropped: bool = False


def detectors_for_frame(
    detectors: Sequence[BaseDetector],
    map_ctx: MapInkScanContext | None,
    video_time: float,
) -> list[BaseDetector]:
    """Skip match_intro / ready once their search windows close."""
    if map_ctx is None:
        return list(detectors)
    skip: set[str] = set()
    if not map_ctx.identity.should_run_intro_detector(video_time):
        skip.add("match_intro")
    if not should_run_ready_detector(map_ctx):
        skip.add("ready")
    if not skip:
        return list(detectors)
    return [d for d in detectors if d.name not in skip]


def should_run_ready_detector(map_ctx: MapInkScanContext) -> bool:
    """Ready? only after stage identity and before the opening clock ticks."""
    if map_ctx.opening_clock_ticked:
        return False
    return map_ctx.identity.identity.stage_id is not None


def update_identity_from_detections(
    map_ctx: MapInkScanContext,
    detections: list[DetectorResult],
    video_time: float,
) -> None:
    """Latch stage/mode from match_intro readings."""
    for det in detections:
        if det.detector_name != "match_intro":
            continue
        reading = det.reading
        if not isinstance(reading, MatchIntroReading):
            continue
        map_ctx.identity.update(reading, video_time=video_time)


def update_ready_gate_from_detections(
    map_ctx: MapInkScanContext,
    detections: list[DetectorResult],
) -> None:
    """Track Ready? sightings and the first non-opening timer tick."""
    opening = opening_clock_values(map_ctx)
    for det in detections:
        if det.detector_name == "ready":
            reading = det.reading
            if isinstance(reading, ReadyReading) and reading.present:
                if not map_ctx.ready_seen:
                    logger.info(
                        "Ready? plate seen (score={:.2f})",
                        reading.template_score,
                    )
                map_ctx.ready_seen = True
            continue
        if det.detector_name != "timer" or map_ctx.opening_clock_ticked:
            continue
        reading = det.reading
        if not isinstance(reading, TimerReading):
            continue
        seconds = float(reading.seconds_remaining)
        if seconds in opening:
            continue
        map_ctx.opening_clock_ticked = True
        logger.info(
            "Opening clock ticked (remaining={:.0f}s); stop looking for Ready?",
            seconds,
        )
        if not map_ctx.ready_seen:
            drop_colors_and_map_ink(
                map_ctx,
                reason="opening clock ticked without Ready? detection",
            )


def opening_clock_values(map_ctx: MapInkScanContext) -> set[float]:
    """Frozen spawn-clock remaining-second values from lifecycle config."""
    if map_ctx.config is None:
        return {180.0, 300.0}
    return {
        float(value) for value in map_ctx.config.vision.lifecycle.opening_clock_seconds
    }


def drop_colors_and_map_ink(map_ctx: MapInkScanContext, *, reason: str) -> None:
    """Clear team colors and map-ink observations for this match."""
    had_color = map_ctx.calibration is not None
    had_obs = bool(map_ctx.observations)
    map_ctx.calibration = None
    map_ctx.observations.clear()
    map_ctx.last_sample_at = None
    map_ctx.map_ink_dropped = True
    if map_ctx.classifier is not None:
        map_ctx.classifier.clear_calibration()
    logger.warning(
        "Dropping team colors and map ink for this match ({}){}",
        reason,
        f"; had_calibration={had_color} observations_cleared={had_obs}",
    )


def finalize_ready_gated_map_artifacts(map_ctx: MapInkScanContext) -> None:
    """If Ready? never appeared, discard colors and map-ink for the match."""
    cfg = map_ctx.config
    if cfg is None or not cfg.vision.map_ink.enabled:
        return
    if map_ctx.ready_seen:
        return
    if map_ctx.map_ink_dropped and map_ctx.calibration is None and not map_ctx.observations:
        return
    drop_colors_and_map_ink(map_ctx, reason="Ready? never detected")


def maybe_calibrate_team_colors(
    map_ctx: MapInkScanContext,
    video_frame: VideoFrame,
    detections: list[DetectorResult],
) -> None:
    """Sample timer-adjacent HUD hues after Ready? until the clock ticks."""
    _ = detections
    cfg = map_ctx.config
    if cfg is None or not cfg.vision.map_ink.enabled:
        return
    if not cfg.vision.map_ink.team_color_calibration_enabled:
        return
    if map_ctx.map_ink_dropped:
        return
    calibrator = map_ctx.calibrator
    if calibrator is None or calibrator.is_latched:
        return
    if not map_ctx.ready_seen or map_ctx.opening_clock_ticked:
        return
    result = calibrator.observe(video_frame.image, float(video_frame.timestamp))
    if result is None:
        return
    map_ctx.calibration = result
    if map_ctx.classifier is not None:
        map_ctx.classifier.apply_calibration(result)


def maybe_sample_map_ink(
    map_ctx: MapInkScanContext,
    video_frame: VideoFrame,
    detections: list[DetectorResult],
) -> None:
    """Emit a MapObservation only after Ready? + colors, while map is open.

    Battle mode is optional: missing mode uses ``configs/stage_maps/<stage>/default.yaml``.
    Does not interpolate. Does not create GameEvents. Skipped entirely when Ready?
    was never detected for the match.
    """
    cfg = map_ctx.config
    if cfg is None or not cfg.vision.map_ink.enabled:
        return
    if map_ctx.map_ink_dropped or not map_ctx.ready_seen:
        return
    if map_ctx.calibration is None:
        return
    if not map_ctx.identity.identity.map_ink_enabled:
        return
    if map_ctx.classifier is None:
        return
    overlay = map_overlay_present(detections)
    if not overlay:
        return
    interval = cfg.vision.map_ink.sample_interval_seconds
    t = float(video_frame.timestamp)
    if map_ctx.last_sample_at is not None and (t - map_ctx.last_sample_at) < interval:
        return
    stage_id = map_ctx.identity.identity.stage_id
    mode_id = map_ctx.identity.identity.battle_mode_id
    if stage_id is None:
        return
    geometry = resolve_stage_map_geometry(
        cfg.vision.map_ink.geometry_dir,
        stage_id=stage_id,
        battle_mode_id=mode_id,
    )
    if geometry is None:
        return
    stage_mask = resolve_stage_mask(
        cfg.vision.map_ink.geometry_dir,
        stage_id=stage_id,
    )
    evidence_ids = [
        d.id for d in detections if d.detector_name == "map_overlay"
    ]
    observation = analyze_map_ink(
        video_frame.image,
        geometry,
        map_ctx.classifier,
        video_time=t,
        battle_mode_id=mode_id,
        evidence_ids=evidence_ids,
        stage_mask=stage_mask,
    )
    if observation.confidence < cfg.vision.map_ink.min_usable_confidence:
        return
    map_ctx.observations.append(observation)
    map_ctx.last_sample_at = t
    if map_ctx.diagnostics_dir is not None:
        write_map_ink_diagnostic(
            map_ctx.diagnostics_dir,
            image=video_frame.image,
            observation=observation,
            geometry=geometry,
            classifier=map_ctx.classifier,
            stage_mask=stage_mask,
        )


def map_overlay_present(detections: list[DetectorResult]) -> bool:
    """True when this frame's map_overlay reading asserts present."""
    for det in detections:
        if det.detector_name != "map_overlay":
            continue
        reading = det.reading
        if isinstance(reading, MapOverlayReading) and reading.present:
            return True
    return False


def build_map_ink_context(
    config: AppConfig,
    output_dir: Path,
    debug_persist: bool,
) -> MapInkScanContext:
    """Create intro latch + optional classifier for this analyze run."""
    intro_cfg = config.vision.match_intro
    identity = MatchIdentityTracker(
        intro_deadline_seconds=intro_cfg.intro_deadline_seconds
    )
    classifier = None
    diagnostics = None
    calibrator = None
    if config.vision.map_ink.enabled:
        classifier = MapInkClassifier(config.vision.map_ink)
        if config.vision.map_ink.team_color_calibration_enabled:
            calibrator = TeamColorCalibrator.from_configs(
                config.vision.map_ink,
                config.vision.player_count,
            )
        if config.vision.map_ink.write_diagnostics or debug_persist:
            diagnostics = output_dir / "debug_map_ink"
    return MapInkScanContext(
        identity=identity,
        classifier=classifier,
        diagnostics_dir=diagnostics,
        config=config,
        calibrator=calibrator,
    )


def persist_map_artifacts(map_ctx: MapInkScanContext, output_dir: Path) -> None:
    """Write match_identity.json and map_observations.json sidecars."""
    output_dir.mkdir(parents=True, exist_ok=True)
    identity = map_ctx.identity.identity
    calibration_payload = (
        map_ctx.calibration.to_dict() if map_ctx.calibration is not None else None
    )
    map_ink_enabled = bool(
        identity.map_ink_enabled and map_ctx.ready_seen and not map_ctx.map_ink_dropped
    )
    payload = {
        "stage_id": identity.stage_id,
        "battle_mode_id": identity.battle_mode_id,
        "stage_score": identity.stage_score,
        "battle_mode_score": identity.battle_mode_score,
        "resolved": identity.resolved,
        "resolved_at": identity.resolved_at,
        "intro_closed": identity.intro_closed,
        "map_ink_enabled": map_ink_enabled,
        "ready_seen": map_ctx.ready_seen,
        "team_color_calibration": calibration_payload,
    }
    path = output_dir / MATCH_IDENTITY_FILENAME
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if map_ctx.config is not None and map_ctx.config.vision.map_ink.enabled:
        write_map_observations(output_dir / MAP_OBSERVATIONS_FILENAME, map_ctx.observations)


# Backward-compatible private aliases for tests.
_detectors_for_frame = detectors_for_frame
_should_run_ready_detector = should_run_ready_detector
_update_identity_from_detections = update_identity_from_detections
_update_ready_gate_from_detections = update_ready_gate_from_detections
_finalize_ready_gated_map_artifacts = finalize_ready_gated_map_artifacts
_maybe_calibrate_team_colors = maybe_calibrate_team_colors
_persist_map_artifacts = persist_map_artifacts
_build_map_ink_context = build_map_ink_context
