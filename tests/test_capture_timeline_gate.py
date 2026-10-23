"""Frozen timeline-capture-v1 gate on synthetic observations."""

from collections.abc import Iterable
from pathlib import Path

import pytest

from splatoon3_ai_coach.capture import timeline_gate
from splatoon3_ai_coach.capture.gate_runner import (
    RESULT_FILENAME,
    confirm_check,
    gate_dir,
)
from splatoon3_ai_coach.capture.models import (
    TIMELINE_CAPTURE_V1,
    AcquisitionMethod,
    CheckStatus,
    QualityGateResult,
    SourceType,
    Verdict,
)
from splatoon3_ai_coach.capture.package_io import CapturePackageError, create_package
from splatoon3_ai_coach.capture.timeline_gate import (
    MANUAL_CHECKS,
    evaluate_timeline_capture,
)
from splatoon3_ai_coach.capture.timeline_observe import (
    FrameTiming,
    IntroObservation,
    TimelineObservation,
)
from splatoon3_ai_coach.config import load_config
from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.media.video import VideoStreamProperties
from splatoon3_ai_coach.review.clock import ClockSource, ReviewClock, parse_elapsed_clock
from splatoon3_ai_coach.review.video_adapter import VideoSample

STEP = 0.05


def _sample(time: float, display: str | None, status: str | None = None) -> VideoSample:
    clock = None
    if display is not None:
        clock = ReviewClock(
            display=display,
            elapsed_seconds=parse_elapsed_clock(display),
            source=ClockSource.image,
            confidence=0.9,
        )
    return VideoSample(
        source_video_time=time,
        frame_index=round(time * 60),
        timeline_confidence=0.9,
        cursor_confidence=0.9,
        clock=clock,
        status=status or ("clocked_timeline" if clock else "unclocked_timeline"),
    )


def _label(value: int) -> str:
    return f"{value // 60}:{value % 60:02d}"


def _labels(
    regulation: int, *, low: int = 0, skip: Iterable[int] = (), overtime: int = 0
) -> list[str]:
    skipped = set(skip)
    values = [_label(v) for v in range(regulation, low - 1, -1) if v not in skipped]
    return values + [f"+{_label(m)}" for m in range(1, overtime + 1)]


def _samples(
    outbound: list[str], back: list[str] | None = None, *, lead_in: bool = True
) -> list[VideoSample]:
    lead = (0.0, 0.5, 1.0, 1.5) if lead_in else ()
    samples = [_sample(t, None, "rejected") for t in lead]
    time = 2.0 if lead_in else 0.0
    for display in outbound + (back if back is not None else outbound[::-1]):
        samples.append(_sample(round(time, 3), display))
        time += STEP
    return samples


def _observation(
    samples: list[VideoSample],
    *,
    mode: str | None = "splat_zones",
    stage: str | None = "makomart",
    avg_fps: float = 60.0,
    size: tuple[int, int] = (1920, 1080),
    error: str | None = None,
) -> TimelineObservation:
    props = VideoStreamProperties(
        suffix=".mov",
        container="mov,mp4,m4a,3gp,3g2,mj2",
        codec="hevc",
        width=size[0],
        height=size[1],
        avg_frame_rate=f"{avg_fps}",
        avg_fps=avg_fps,
        r_frame_rate="60/1",
        r_fps=60.0,
        duration_seconds=60.0,
    )
    times = sorted({s.source_video_time for s in samples})
    intro = IntroObservation(
        battle_mode_id=mode,
        stage_id=stage,
        resolved_at=1.0 if mode and stage else None,
    )
    timing = FrameTiming(timestamps=times, repeats=[False] * len(times))
    return TimelineObservation(props, timing, intro, tuple(samples), error)


def _evaluate(observation: TimelineObservation) -> QualityGateResult:
    return evaluate_timeline_capture(
        observation,
        artifact_id="timeline",
        video_identity="abc",
        hash_verified=True,
        timeline_config=ReviewTimelineConfig(),
        package_version="test",
        git_sha=None,
    )


def _status(result: QualityGateResult, check_id: str) -> CheckStatus:
    return next(c.status for c in result.checks if c.check_id == check_id)


def test_full_sweep_passes_every_automatic_check() -> None:
    result = _evaluate(_observation(_samples(["-0:03", *_labels(300)])))
    automatic = [c for c in result.checks if c.method.value == "automatic"]
    assert all(c.status is CheckStatus.PASS for c in automatic), [
        (c.check_id, c.status) for c in automatic
    ]
    assert result.contract_status.value == "frozen"
    assert result.automatic_verdict is Verdict.INCOMPLETE
    manual = {c.check_id: c.status for c in result.checks if c.method.value == "manual"}
    assert manual == dict.fromkeys(MANUAL_CHECKS, CheckStatus.NOT_RUN)


def test_recording_that_starts_inside_the_timeline_fails_entry() -> None:
    result = _evaluate(_observation(_samples(_labels(300), lead_in=False)))
    assert _status(result, "timeline_entry_observed") is CheckStatus.FAIL
    assert result.automatic_verdict is Verdict.FAIL


def test_unobserved_intro_is_unknown_not_fail() -> None:
    result = _evaluate(_observation(_samples(_labels(300)), mode=None, stage=None))
    assert _status(result, "intro_identity_observed") is CheckStatus.UNKNOWN
    assert result.observations["regulation"]["basis"] == "clock_reading"
    assert _status(result, "left_endpoint_observed") is CheckStatus.PASS


def test_regulation_unknown_leaves_left_endpoint_unknown() -> None:
    labels = _labels(300, skip=(300, 299))
    result = _evaluate(_observation(_samples(labels), mode=None, stage=None))
    assert _status(result, "left_endpoint_observed") is CheckStatus.UNKNOWN
    assert result.observations["continuity"]["expected_regulation_range"] is None


def test_turf_war_regulation_comes_from_the_intro_mode() -> None:
    result = _evaluate(_observation(_samples(_labels(180)), mode="turf_war"))
    assert result.observations["regulation"] == {"seconds": 180, "basis": "intro_mode"}
    assert _status(result, "left_endpoint_observed") is CheckStatus.PASS
    assert _status(result, "right_endpoint_observed") is CheckStatus.PASS


def test_overtime_right_endpoint_is_unknown_and_preserved() -> None:
    result = _evaluate(_observation(_samples(_labels(300, overtime=29))))
    assert _status(result, "right_endpoint_observed") is CheckStatus.UNKNOWN
    overtime = result.observations["overtime"]
    assert overtime["observed"] is True
    assert overtime["max_seconds"] == 29
    assert overtime["outbound_max_seconds"] == overtime["return_max_seconds"] == 29
    assert _status(result, "return_left_endpoint_observed") is CheckStatus.PASS


def test_knockout_right_endpoint_is_unknown() -> None:
    result = _evaluate(_observation(_samples(_labels(300, low=45))))
    assert _status(result, "right_endpoint_observed") is CheckStatus.UNKNOWN
    assert result.observations["continuity"]["expected_regulation_range"] == [45, 300]


def test_missing_return_pass_fails() -> None:
    result = _evaluate(_observation(_samples(_labels(300), back=[])))
    assert _status(result, "return_left_endpoint_observed") is CheckStatus.FAIL


def test_continuity_is_reported_per_pass_and_as_union() -> None:
    outbound = _labels(300, skip=(100, 101))
    back = _labels(300, skip=(200,))[::-1]
    continuity = _evaluate(_observation(_samples(outbound, back))).observations[
        "continuity"
    ]
    assert continuity["outbound"]["missing_regulation_ranges"] == [[100, 101]]
    assert continuity["return"]["missing_regulation_ranges"] == [[200, 200]]
    assert continuity["union_of_passes"]["missing_regulation_seconds"] == 0


@pytest.mark.parametrize(
    ("fps", "expected"),
    [
        (60.0, CheckStatus.PASS),
        (59.94, CheckStatus.PASS),
        (58.0, CheckStatus.UNKNOWN),
        (30.0, CheckStatus.FAIL),
        (120.0, CheckStatus.FAIL),
    ],
)
def test_frame_rate_window_is_provisional(fps: float, expected: CheckStatus) -> None:
    result = _evaluate(_observation(_samples(_labels(300)), avg_fps=fps))
    assert _status(result, "frame_rate_60") is expected


def test_frozen_v1_values_and_reader_settings_are_pinned() -> None:
    """Changing any of these is a change in meaning: timeline-capture-v2."""
    assert timeline_gate.CHECK_IDS == (
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
        "capture_path_unmodified",
        "acquisition_profile_1080p60",
        "replay_matches_declared_code",
    )
    assert timeline_gate.FPS_PROVISIONAL_WINDOW == (59.0, 61.0)
    assert timeline_gate.FPS_INVALID_BELOW == 50.0
    assert timeline_gate.FPS_INVALID_ABOVE == 70.0
    assert timeline_gate.PROVISIONAL_MAX_DURATION_SECONDS == 180.0
    assert timeline_gate.MIN_PRE_TIMELINE_SECONDS == 1.0
    assert timeline_gate.ACCEPTED_SUFFIXES == {".mov", ".mp4"}
    assert timeline_gate.ACCEPTED_CODECS == {"h264", "hevc"}
    config = load_config(default_config_path())
    review = config.review
    assert (review.regulation_seconds, review.max_overtime_seconds) == (300, 300)
    assert review.max_countdown_seconds == 10
    assert review.cursor_confidence_floor == 0.55
    video = review.video
    assert video.sample_interval_seconds == 0.0
    assert video.frames_per_second_bucket == 1
    assert (
        video.layout_confidence_floor,
        video.structure_confidence_floor,
        video.clock_confidence_floor,
    ) == (0.55, 0.6, 0.55)
    assert config.vision.match_intro.match_threshold == 0.7


def test_resolution_and_decode_failures_fail() -> None:
    result = _evaluate(
        _observation(_samples(_labels(300)), size=(1280, 720), error="InvalidData: x")
    )
    assert _status(result, "resolution_1920x1080") is CheckStatus.FAIL
    assert _status(result, "video_decodable") is CheckStatus.FAIL


def _package_with_result(tmp_path: Path, result: QualityGateResult) -> Path:
    package_dir = tmp_path / "pkg"
    create_package(
        package_dir,
        source_type=SourceType.REVIEW_CODE,
        acquisition_method=AcquisitionMethod.MANUAL,
        language="en",
        review_code="ABCD-EFGH-1234-5678",
    )
    out = gate_dir(package_dir, TIMELINE_CAPTURE_V1)
    out.mkdir(parents=True)
    (out / RESULT_FILENAME).write_text(result.model_dump_json())
    return package_dir


def test_confirm_resolves_manual_checks_but_not_automatic_ones(tmp_path: Path) -> None:
    result = _evaluate(_observation(_samples(["-0:03", *_labels(300)])))
    package_dir = _package_with_result(tmp_path, result)
    with pytest.raises(CapturePackageError):
        confirm_check(
            package_dir,
            check_id="frame_rate_60",
            status=CheckStatus.FAIL,
            note="no",
        )
    summary = None
    for check_id in MANUAL_CHECKS:
        _, summary = confirm_check(
            package_dir, check_id=check_id, status=CheckStatus.PASS, note="checked"
        )
    assert summary is not None
    assert summary.automatic_verdict is Verdict.INCOMPLETE
    assert summary.verdict is Verdict.PASS
    stored = QualityGateResult.model_validate_json(
        (gate_dir(package_dir, TIMELINE_CAPTURE_V1) / RESULT_FILENAME).read_text()
    )
    assert stored == result
