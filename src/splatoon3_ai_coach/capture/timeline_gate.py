"""FROZEN ``timeline-capture-v1`` quality gate (pure evaluation).

Frozen after the C1–C4 controlled captures (``CAPTURE_CONTRACT.md``). Checks
and values never change meaning; a change is ``timeline-capture-v2``. Names
containing ``provisional`` are kept verbatim from the characterized code.
Detector evidence is used positively only: a missing observation is
``unknown``, never ``fail``.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from splatoon3_ai_coach.capture.models import (
    TIMELINE_CAPTURE_V1,
    CheckLayer,
    CheckMethod,
    CheckStatus,
    ContractStatus,
    GateCheck,
    QualityGateResult,
    derive_verdict,
    utc_now,
)
from splatoon3_ai_coach.capture.timeline_observe import TimelineObservation
from splatoon3_ai_coach.capture.timeline_sweep import (
    TIMELINE_STATUSES,
    ClockSample,
    Sweep,
    expected_regulation_range,
    find_sweep,
    is_left_marker,
    pass_coverage,
    plausible_samples,
    regulation_length,
    split_passes,
)
from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.review.clock import ClockPhase

CONTRACT_STATUS = ContractStatus.FROZEN
REQUIRED_WIDTH, REQUIRED_HEIGHT = 1920, 1080
INTENDED_FPS = 60.0
# Provisional near-60 window; rates outside the invalid bounds clearly fail.
FPS_PROVISIONAL_WINDOW = (59.0, 61.0)
FPS_INVALID_BELOW, FPS_INVALID_ABOVE = 50.0, 70.0
ACCEPTED_SUFFIXES = frozenset({".mov", ".mp4"})
ACCEPTED_CODECS = frozenset({"h264", "hevc"})
PROVISIONAL_MAX_DURATION_SECONDS = 180.0
MIN_PRE_TIMELINE_SECONDS = 1.0

MANUAL_CHECKS: dict[str, tuple[CheckLayer, str]] = {
    "capture_path_unmodified": (
        CheckLayer.PROVENANCE,
        "OBS recording of the Cam Link 4K path, not trimmed, re-encoded or remuxed",
    ),
    "acquisition_profile_1080p60": (
        CheckLayer.TECHNICAL,
        "OBS canvas/output 1920x1080 at 60 fps and Cam Link source at 60 fps",
    ),
    "replay_matches_declared_code": (
        CheckLayer.PROVENANCE,
        "the replay shown is the one loaded from the declared review code",
    ),
}
AUTOMATIC_CHECK_IDS = (
    "video_decodable",
    "artifact_hash_verified",
    "resolution_1920x1080",
    "frame_rate_60",
    "container_codec",
    "duration_envelope",
    "intro_identity_observed",
    "timeline_entry_observed",
    "timeline_ui_observed",
    "left_endpoint_observed",
    "right_endpoint_observed",
    "return_left_endpoint_observed",
)
CHECK_IDS = AUTOMATIC_CHECK_IDS + tuple(MANUAL_CHECKS)


def evaluate_timeline_capture(
    observation: TimelineObservation,
    *,
    artifact_id: str,
    video_identity: str,
    hash_verified: bool,
    timeline_config: ReviewTimelineConfig,
    package_version: str,
    git_sha: str | None,
) -> QualityGateResult:
    """Score one decoded timeline capture against the frozen contract."""
    clocks = plausible_samples(observation.samples, timeline_config)
    regulation, basis = regulation_length(observation.intro.battle_mode_id, clocks)
    sweep = find_sweep(clocks, regulation)
    checks = [
        *_technical_checks(observation, hash_verified),
        *_content_checks(observation, clocks, regulation, sweep),
        *_manual_checks(),
    ]
    observations = _observations(observation, clocks, regulation, basis, sweep)
    return QualityGateResult(
        contract=TIMELINE_CAPTURE_V1,
        contract_status=CONTRACT_STATUS,
        artifact_id=artifact_id,
        video_identity=video_identity,
        checked_at=utc_now(),
        package_version=package_version,
        git_sha=git_sha,
        checks=checks,
        observations=observations,
        automatic_verdict=derive_verdict(checks),
    )


def _check(
    check_id: str,
    layer: CheckLayer,
    status: CheckStatus,
    detail: str,
    **values: Any,
) -> GateCheck:
    return GateCheck(
        check_id=check_id,
        layer=layer,
        method=CheckMethod.AUTOMATIC,
        status=status,
        detail=detail,
        values=values,
    )


def _pass_fail(ok: bool) -> CheckStatus:
    return CheckStatus.PASS if ok else CheckStatus.FAIL


def _technical_checks(obs: TimelineObservation, hash_verified: bool) -> list[GateCheck]:
    props = obs.properties
    tech = CheckLayer.TECHNICAL
    decoded = len(obs.timing.timestamps)
    return [
        _check(
            "video_decodable",
            tech,
            _pass_fail(obs.decode_error is None and decoded > 0),
            obs.decode_error or f"{decoded} frames decoded",
            decoded_frames=decoded,
        ),
        _check(
            "artifact_hash_verified",
            CheckLayer.PROVENANCE,
            _pass_fail(hash_verified),
            "stored bytes match the identity recorded at ingest",
        ),
        _check(
            "resolution_1920x1080",
            tech,
            _pass_fail((props.width, props.height) == (REQUIRED_WIDTH, REQUIRED_HEIGHT)),
            f"{props.width}x{props.height}",
            width=props.width,
            height=props.height,
        ),
        _frame_rate_check(obs),
        _check(
            "container_codec",
            tech,
            _pass_fail(
                props.suffix in ACCEPTED_SUFFIXES and props.codec in ACCEPTED_CODECS
            ),
            f"{props.suffix} / {props.codec}",
            suffix=props.suffix,
            container=props.container,
            major_brand=props.major_brand,
            codec=props.codec,
            pix_fmt=props.pix_fmt,
            encoder=props.encoder,
        ),
        _duration_check(obs),
    ]


def _frame_rate_check(obs: TimelineObservation) -> GateCheck:
    props = obs.properties
    measured = _measured_fps(obs.timing.timestamps)
    rate = props.avg_fps if props.avg_fps is not None else measured
    low, high = FPS_PROVISIONAL_WINDOW
    if rate is None:
        status, detail = CheckStatus.UNKNOWN, "no frame rate declared or measurable"
    elif rate < FPS_INVALID_BELOW or rate > FPS_INVALID_ABOVE:
        status, detail = CheckStatus.FAIL, f"{rate:.3f} fps is not a 60 fps capture"
    elif low <= rate <= high:
        status, detail = CheckStatus.PASS, f"{rate:.3f} fps (provisional window)"
    else:
        status, detail = (
            CheckStatus.UNKNOWN,
            f"{rate:.3f} fps is outside the provisional window",
        )
    return _check(
        "frame_rate_60",
        CheckLayer.TECHNICAL,
        status,
        detail,
        intended_fps=INTENDED_FPS,
        provisional_window=list(FPS_PROVISIONAL_WINDOW),
        avg_frame_rate=props.avg_frame_rate,
        avg_fps=props.avg_fps,
        r_frame_rate=props.r_frame_rate,
        r_fps=props.r_fps,
        time_base=props.time_base,
        measured_fps=measured,
    )


def _duration_check(obs: TimelineObservation) -> GateCheck:
    duration = obs.properties.duration_seconds
    if duration is None:
        status = CheckStatus.UNKNOWN
    elif duration <= PROVISIONAL_MAX_DURATION_SECONDS:
        status = CheckStatus.PASS
    else:
        status = CheckStatus.UNKNOWN
    return _check(
        "duration_envelope",
        CheckLayer.TECHNICAL,
        status,
        f"{duration}s (provisional envelope {PROVISIONAL_MAX_DURATION_SECONDS:.0f}s)",
        duration_seconds=duration,
        declared_frame_count=obs.properties.declared_frame_count,
    )


def _content_checks(
    obs: TimelineObservation,
    clocks: list[ClockSample],
    regulation: int | None,
    sweep: Sweep,
) -> list[GateCheck]:
    entry = _first_timeline_time(obs)
    return [
        _intro_check(obs, entry),
        _entry_check(obs, entry),
        _check(
            "timeline_ui_observed",
            CheckLayer.CONTENT,
            _pass_fail(bool(clocks)),
            f"{len(clocks)} plausible clock readings",
            plausible_clock_readings=len(clocks),
        ),
        _left_check(clocks, regulation, sweep),
        _right_check(clocks, sweep),
        _return_check(clocks, regulation, sweep),
    ]


def _intro_check(obs: TimelineObservation, entry: float | None) -> GateCheck:
    intro = obs.intro
    latched = intro.battle_mode_id is not None and intro.stage_id is not None
    before_entry = latched and (entry is None or (intro.resolved_at or 0.0) <= entry)
    status = CheckStatus.PASS if before_entry else CheckStatus.UNKNOWN
    detail = (
        f"mode={intro.battle_mode_id} stage={intro.stage_id}"
        if before_entry
        else "intro identity not observed; operator confirms mode and stage"
    )
    return _check(
        "intro_identity_observed",
        CheckLayer.CONTENT,
        status,
        detail,
        battle_mode_id=intro.battle_mode_id,
        stage_id=intro.stage_id,
        resolved_at=intro.resolved_at,
        best_battle_mode_score=round(intro.best_battle_mode_score, 4),
        best_battle_mode_time=intro.best_battle_mode_time,
        best_stage_score=round(intro.best_stage_score, 4),
        best_stage_time=intro.best_stage_time,
        match_threshold=intro.match_threshold,
        frames_checked=intro.frames_checked,
    )


def _entry_check(obs: TimelineObservation, entry: float | None) -> GateCheck:
    first = obs.timing.timestamps[0] if obs.timing.timestamps else None
    if entry is None or first is None:
        status, detail, lead = CheckStatus.FAIL, "no timeline frame observed", None
    else:
        lead = round(entry - first, 3)
        ok = lead >= MIN_PRE_TIMELINE_SECONDS
        status = _pass_fail(ok)
        detail = f"{lead}s of recording before the timeline opens" + (
            "" if ok else " (recording appears to start inside the timeline)"
        )
    return _check(
        "timeline_entry_observed",
        CheckLayer.CONTENT,
        status,
        detail,
        first_timeline_time=entry,
        pre_timeline_seconds=lead,
        provisional_min_seconds=MIN_PRE_TIMELINE_SECONDS,
    )


def _left_check(
    clocks: list[ClockSample], regulation: int | None, sweep: Sweep
) -> GateCheck:
    if sweep.left_at is not None:
        status, detail = CheckStatus.PASS, f"left edge at {sweep.left_at:.2f}s"
    elif regulation is None:
        status, detail = CheckStatus.UNKNOWN, "regulation length unknown"
    else:
        status, detail = CheckStatus.FAIL, "no pre-GO label or left-edge reading"
    top = max((s.value for s in clocks if s.phase is ClockPhase.regulation), default=None)
    return _check(
        "left_endpoint_observed",
        CheckLayer.CONTENT,
        status,
        detail,
        left_at=sweep.left_at,
        regulation_seconds=regulation,
        countdown_labels=sum(1 for s in clocks if s.phase is ClockPhase.countdown),
        highest_regulation_reading=top,
    )


def _right_check(clocks: list[ClockSample], sweep: Sweep) -> GateCheck:
    turn = sweep.turn_sample
    overtime = any(s.phase is ClockPhase.overtime for s in clocks)
    if turn is None:
        status, detail = CheckStatus.FAIL, "no clock readings"
    elif turn.phase is ClockPhase.overtime:
        status = CheckStatus.UNKNOWN
        detail = (
            f"overtime: furthest right read {turn.display}; "
            "end of overtime not identifiable"
        )
    elif turn.phase is ClockPhase.regulation and turn.value <= 1:
        status, detail = CheckStatus.PASS, f"reached {turn.display} at {turn.time:.2f}s"
    else:
        status = CheckStatus.UNKNOWN
        detail = f"furthest right read {turn.display}; knockout or short sweep"
    return _check(
        "right_endpoint_observed",
        CheckLayer.CONTENT,
        status,
        detail,
        turn_at=sweep.turn_at,
        turn_display=turn.display if turn else None,
        turn_phase=turn.phase.value if turn else None,
        overtime_labels_observed=overtime,
    )


def _return_check(
    clocks: list[ClockSample], regulation: int | None, sweep: Sweep
) -> GateCheck:
    if sweep.return_left_at is not None:
        status = CheckStatus.PASS
        detail = f"back at left edge at {sweep.return_left_at:.2f}s"
    elif regulation is None and sweep.turn_at is not None:
        status, detail = CheckStatus.UNKNOWN, "regulation length unknown"
    else:
        status, detail = CheckStatus.FAIL, "no left-edge reading after the right turn"
    last_left = max(
        (s.time for s in clocks if is_left_marker(s, regulation)), default=None
    )
    return _check(
        "return_left_endpoint_observed",
        CheckLayer.CONTENT,
        status,
        detail,
        return_left_at=sweep.return_left_at,
        last_left_marker_at=last_left,
    )


def _manual_checks() -> list[GateCheck]:
    return [
        GateCheck(
            check_id=check_id,
            layer=layer,
            method=CheckMethod.MANUAL,
            status=CheckStatus.NOT_RUN,
            detail=detail,
        )
        for check_id, (layer, detail) in MANUAL_CHECKS.items()
    ]


def _first_timeline_time(obs: TimelineObservation) -> float | None:
    return min(
        (s.source_video_time for s in obs.samples if s.status in TIMELINE_STATUSES),
        default=None,
    )


def _measured_fps(timestamps: list[float]) -> float | None:
    if len(timestamps) < 2 or timestamps[-1] <= timestamps[0]:
        return None
    return round((len(timestamps) - 1) / (timestamps[-1] - timestamps[0]), 4)


def _observations(
    obs: TimelineObservation,
    clocks: list[ClockSample],
    regulation: int | None,
    basis: str,
    sweep: Sweep,
) -> dict[str, Any]:
    outbound, back = split_passes(clocks, sweep)
    expected = expected_regulation_range(clocks, regulation)
    return {
        "regulation": {"seconds": regulation, "basis": basis},
        "sweep": {
            "left_at": sweep.left_at,
            "turn_at": sweep.turn_at,
            "return_left_at": sweep.return_left_at,
            "return_left_until": sweep.return_left_until,
        },
        "continuity": {
            "expected_regulation_range": list(expected) if expected else None,
            "outbound": pass_coverage(outbound, expected),
            "return": pass_coverage(back, expected),
            "union_of_passes": pass_coverage(outbound + back, expected),
            "whole_recording": pass_coverage(clocks, expected),
        },
        "overtime": _overtime_summary(clocks, outbound, back),
        "frames": _frame_summary(obs, sweep),
        "sample_status_counts": dict(Counter(s.status for s in obs.samples)),
        "implausible_clock_readings": sum(1 for s in obs.samples if s.clock is not None)
        - len(clocks),
    }


def _overtime_summary(
    clocks: list[ClockSample], outbound: list[ClockSample], back: list[ClockSample]
) -> dict[str, Any]:
    over = [s for s in clocks if s.phase is ClockPhase.overtime]
    regulation = [s for s in clocks if s.phase is ClockPhase.regulation]
    return {
        "observed": bool(over),
        "labels": len(over),
        "max_seconds": max((s.overtime_seconds for s in over), default=None),
        "first_label_at": over[0].time if over else None,
        "lowest_regulation_reading": min((s.value for s in regulation), default=None),
        "outbound_max_seconds": max((s.overtime_seconds for s in outbound), default=None),
        "return_max_seconds": max((s.overtime_seconds for s in back), default=None),
    }


def _frame_summary(obs: TimelineObservation, sweep: Sweep) -> dict[str, Any]:
    stamps = obs.timing.timestamps
    entry = _first_timeline_time(obs)
    gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    nominal = 1.0 / INTENDED_FPS
    return {
        "decoded": len(stamps),
        "first_timestamp": stamps[0] if stamps else None,
        "last_timestamp": stamps[-1] if stamps else None,
        "measured_fps": _measured_fps(stamps),
        "max_interval_seconds": round(max(gaps), 4) if gaps else None,
        "intervals_over_1_5x_nominal": sum(1 for g in gaps if g > 1.5 * nominal),
        "identical_repeat_fraction": _repeat_fraction(obs, None, None),
        "identical_repeat_fraction_before_timeline": _repeat_fraction(obs, None, entry),
        "identical_repeat_fraction_outbound_sweep": _repeat_fraction(
            obs, sweep.left_at, sweep.turn_at
        ),
        "identical_repeat_fraction_return_sweep": _repeat_fraction(
            obs, sweep.turn_at, sweep.return_left_at
        ),
    }


def _repeat_fraction(
    obs: TimelineObservation, start: float | None, end: float | None
) -> float | None:
    """Fraction of pixel-identical repeats among frames in ``[start, end)``."""
    flags = [
        repeat
        for time, repeat in zip(obs.timing.timestamps, obs.timing.repeats, strict=True)
        if (start is None or time >= start) and (end is None or time < end)
    ]
    return round(sum(flags) / len(flags), 4) if flags else None
