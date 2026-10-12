"""Review to gameplay clock alignment (contract: alignment/ALIGNMENT_CONTRACT.md)."""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from splatoon3_ai_coach.alignment import gameplay_clock, review_alignment
from splatoon3_ai_coach.alignment.gameplay_clock import calibrate_gameplay_clock
from splatoon3_ai_coach.alignment.review_alignment import (
    AlignedReviewTime,
    align_pair,
)
from splatoon3_ai_coach.config.loader import load_config
from splatoon3_ai_coach.config.models import AppConfig
from splatoon3_ai_coach.config.paths import default_config_path
from splatoon3_ai_coach.review.models import (
    AxisCalibrationEvidence,
    CoverageReport,
    ReviewTimelineDataset,
    ReviewTimelineSample,
)
from splatoon3_ai_coach.vision.models import (
    AnalysisIdentity,
    DetectorResult,
    GameEvent,
    GameEventReason,
    GameEventType,
    TimerReading,
    VisionFrameResult,
    VisionManifest,
)

REPO = Path(__file__).resolve().parents[1]
C_TRUE = 320.2  # video_t = c - remaining; GO (remaining 300) at video 20.2


def _config(**alignment: float) -> AppConfig:
    config = load_config(default_config_path())
    return config.model_copy(
        update={"alignment": config.alignment.model_copy(update=alignment)}
    )


def _frame(t: float, value: int | None) -> VisionFrameResult:
    detections = []
    if value is not None:
        detections.append(
            DetectorResult(
                id=f"timer:{t}",
                detector_name="timer",
                detector_version="timer@test",
                confidence=0.95,
                reading=TimerReading(display=str(value), seconds_remaining=value),
            )
        )
    return VisionFrameResult(
        frame_id=f"f{t}", timestamp=t, source="cadence", detections=detections
    )


def _display(t: float, c: float, opening: int = 300) -> int:
    """HUD shows k while remaining is in (k - 1, k]; frozen opening before GO."""
    return min(opening, max(0, math.ceil(c - t)))


def _manifest(
    *,
    c: float = C_TRUE,
    t_start: float = 10.0,
    t_end: float = 320.0,
    overrides: dict[float, int | None] | None = None,
    clock_zero: bool = True,
    lowest: int = 0,
    opening: int = 300,
) -> VisionManifest:
    frames = []
    t = t_start
    while t <= t_end:
        value = max(_display(t, c, opening), lowest)
        if overrides and round(t, 1) in overrides:
            value = overrides[round(t, 1)]
        frames.append(_frame(round(t, 1), value))
        t += 0.5
    events = []
    if clock_zero:
        events.append(
            GameEvent(
                start_time=round(t_end + 0.5, 1),
                event_type=GameEventType.MATCH_END,
                reason=GameEventReason.CLOCK_ZERO,
                confidence=1.0,
            )
        )
    return VisionManifest(
        analysis=AnalysisIdentity(
            analysis_id="g1",
            package_version="test",
            vision_config_sha256="x",
            video_identity="v",
        ),
        video_identity="v",
        frame_results=frames,
        game_events=events,
    )


def _review(
    *,
    labels: tuple[int, ...] = (300, 200, 100, 0),
    overtime: tuple[str, ...] = (),
    mode: str | None = None,
    axis: bool = True,
) -> ReviewTimelineDataset:
    displays = [f"{v // 60}:{v % 60:02d}" for v in labels] + list(overtime)
    values = list(labels) + [-int(d[3:]) - 60 * int(d[1]) for d in overtime]
    samples = [
        ReviewTimelineSample(
            sample_id=f"s{i}",
            capture_index=i,
            image_path=f"s{i}.png",
            image_sha256="0" * 64,
            clock_status="ok",
            displayed_clock=d,
            elapsed_seconds=v,
        )
        for i, (d, v) in enumerate(zip(displays, values, strict=True))
    ]
    return ReviewTimelineDataset(
        battle_mode_id=mode,
        samples=samples,
        axis_calibration=(
            AxisCalibrationEvidence(
                slope=-300.0,
                intercept=300.0,
                point_count=4,
                mean_absolute_error_seconds=0.05,
                max_absolute_error_seconds=0.1,
            )
            if axis
            else None
        ),
        coverage=CoverageReport(
            sample_count=len(samples), clocked_sample_count=len(samples)
        ),
    )


def _calibrate(manifest: VisionManifest, config: AppConfig | None = None):
    config = config or _config()
    return calibrate_gameplay_clock(
        manifest,
        min_usable_confidence=0.5,
        config=config.alignment,
        playback=config.coach.playback_clock,
    )


# --- Layer 1: gameplay clock calibration (HUD only) ------------------------


def test_c_interval_recovers_truth_with_cadence_width() -> None:
    cal = _calibrate(_manifest())
    assert cal.fit_status == "calibrated"
    lo, hi = cal.c_interval
    assert lo < C_TRUE <= hi
    assert hi - lo == pytest.approx(0.5)
    assert cal.inconsistent_edge_share == 0
    assert cal.playback_status == "normal"


def test_one_ocr_glitch_is_tolerated_and_counted() -> None:
    glitch = {150.0: _display(150.0, C_TRUE) - 1}
    cal = _calibrate(_manifest(overrides=glitch))
    assert cal.fit_status == "calibrated"
    assert cal.consistent_edge_count == cal.edge_count - 1
    lo, hi = cal.c_interval
    assert lo < C_TRUE <= hi


def test_many_inconsistent_edges_are_inconsistent_gameplay_clock() -> None:
    glitches = {float(t): _display(float(t), C_TRUE) - 1 for t in range(40, 300, 6)}
    cal = _calibrate(_manifest(overrides=glitches))
    assert (cal.fit_status, cal.reason) == ("unknown", "inconsistent_gameplay_clock")
    assert cal.c_interval is None


def test_too_few_edges_is_insufficient_gameplay_clock() -> None:
    cal = _calibrate(_manifest(t_end=30.0))
    assert (cal.fit_status, cal.reason) == ("unknown", "insufficient_gameplay_clock")


def test_review_axis_never_changes_c() -> None:
    config = _config()
    manifest = _manifest()
    a = align_pair(_review(), manifest, None, config)
    b = align_pair(_review(axis=False), manifest, None, config)
    assert a.calibration == b.calibration


# --- Layer 2: mapping, intervals, direction ---------------------------------


def test_interval_propagates_c_width_and_axis_error() -> None:
    alignment = align_pair(_review(), _manifest(), None, _config())
    lo_c, hi_c = alignment.calibration.c_interval
    out = alignment.map_review_remaining(150.0)
    assert out.alignment_status == "aligned"
    assert out.video_interval == pytest.approx((lo_c - 150.0 - 0.1, hi_c - 150.0 + 0.1))
    assert out.video_seconds == pytest.approx(sum(out.video_interval) / 2)
    assert out.alignment_error_s == pytest.approx(0.25 + 0.1)
    assert out.video_interval[0] < C_TRUE - 150.0 <= out.video_interval[1]


def test_delta_shifts_the_interval_and_is_not_fitted() -> None:
    base = align_pair(_review(), _manifest(), None, _config())
    shifted = align_pair(_review(), _manifest(), None, _config(delta=0.5))
    assert shifted.delta == 0.5
    a = base.map_review_remaining(100.0).video_seconds
    b = shifted.map_review_remaining(100.0).video_seconds
    assert b == pytest.approx(a - 0.5)


def test_direction_larger_remaining_is_an_earlier_video_interval() -> None:
    alignment = align_pair(_review(), _manifest(), None, _config())
    early = alignment.map_review_remaining(200.0)
    late = alignment.map_review_remaining(100.0)
    assert early.video_interval[1] < late.video_interval[0]
    assert late.video_seconds - early.video_seconds == pytest.approx(100.0)


def test_mapping_result_has_no_game_remaining() -> None:
    fields = set(AlignedReviewTime.model_fields)
    assert fields == {
        "review_remaining",
        "video_interval",
        "video_seconds",
        "alignment_error_s",
        "alignment_status",
        "reason",
    }


def test_no_review_axis_is_unknown() -> None:
    alignment = align_pair(_review(axis=False), _manifest(), None, _config())
    out = alignment.map_review_remaining(150.0)
    assert (out.alignment_status, out.reason) == ("unknown", "no_review_axis")
    assert out.video_interval is None


def test_recording_cut_makes_unobserved_values_unknown() -> None:
    # Gameplay recording stops at remaining ~150; no extrapolation beyond it.
    alignment = align_pair(
        _review(labels=(300, 250, 200, 150)),
        _manifest(t_end=170.0, clock_zero=False, lowest=150),
        None,
        _config(),
    )
    assert alignment.pair.pair_status == "consistent"
    assert alignment.map_review_remaining(200.0).alignment_status == "aligned"
    out = alignment.map_review_remaining(100.0)
    assert (out.alignment_status, out.reason) == (
        "unknown",
        "outside_observed_gameplay_clock",
    )


def test_unobserved_stretch_inside_the_match_is_unknown() -> None:
    # No HUD reads for video 140-170 s, i.e. remaining ~180 down to ~150.
    hidden = {round(t * 0.5, 1): None for t in range(2 * 140, 2 * 170)}
    alignment = align_pair(_review(), _manifest(overrides=hidden), None, _config())
    assert alignment.calibration.fit_status == "calibrated"
    assert alignment.map_review_remaining(185.0).alignment_status == "aligned"
    gap = alignment.map_review_remaining(165.0)
    assert (gap.alignment_status, gap.reason) == (
        "unknown",
        "outside_observed_gameplay_clock",
    )


def test_fit_unknown_makes_every_value_unknown() -> None:
    glitches = {float(t): _display(float(t), C_TRUE) - 1 for t in range(40, 300, 6)}
    alignment = align_pair(_review(), _manifest(overrides=glitches), None, _config())
    assert alignment.pair.pair_status == "consistent"
    out = alignment.map_review_remaining(250.0)
    assert (out.alignment_status, out.reason) == ("unknown", "fit_unknown")


# --- statuses: pair vs fit vs value ------------------------------------------


def test_overtime_match_is_consistent_and_overtime_values_unknown() -> None:
    alignment = align_pair(
        _review(overtime=("+0:05", "+0:20")),
        _manifest(clock_zero=False, lowest=1),
        None,
        _config(),
    )
    assert alignment.pair.pair_status == "consistent"
    assert alignment.pair.review_overtime is True
    assert alignment.pair.gameplay_overtime is None
    assert alignment.calibration.fit_status == "calibrated"
    assert alignment.map_review_remaining(120.0).alignment_status == "aligned"
    out = alignment.map_review_remaining(-12.0)
    assert (out.alignment_status, out.reason) == ("unknown", "overtime_no_gameplay_timer")


@pytest.mark.parametrize(
    ("review_kwargs", "manifest_kwargs", "gameplay_mode", "reason"),
    [
        ({"mode": "rainmaker"}, {}, "splat_zones", "mode_mismatch"),
        ({"overtime": ("+0:05",)}, {}, None, "overtime_mismatch"),
        (
            {"overtime": ("+0:05",)},
            {"t_end": 200.0, "clock_zero": False, "lowest": 120},
            None,
            "overtime_mismatch",
        ),
        (
            {"overtime": ("+0:05",)},
            {"clock_zero": False, "lowest": 1},
            "turf_war",
            "overtime_mismatch",
        ),
        (
            {},
            {"t_end": 80.0, "clock_zero": False, "lowest": 240},
            None,
            "insufficient_clock_coverage",
        ),
        ({"labels": (180, 100, 0)}, {}, None, "clock_domain_mismatch"),
    ],
)
def test_structural_pair_mismatch(
    review_kwargs: dict, manifest_kwargs: dict, gameplay_mode: str | None, reason: str
) -> None:
    alignment = align_pair(
        _review(**review_kwargs), _manifest(**manifest_kwargs), gameplay_mode, _config()
    )
    assert alignment.pair.pair_status == "inconsistent"
    assert reason in alignment.pair.reasons
    out = alignment.map_review_remaining(250.0)
    assert (out.alignment_status, out.reason) == ("unknown", "pair_inconsistent")


def test_knockout_is_not_overtime() -> None:
    # Ends with time left: no clock_zero, lowest read well above 1.
    alignment = align_pair(
        _review(labels=(300, 200, 120)),
        _manifest(t_end=200.0, clock_zero=False, lowest=120),
        None,
        _config(),
    )
    assert alignment.pair.gameplay_overtime is False
    assert alignment.pair.pair_status == "consistent"


def test_missed_zero_read_leaves_gameplay_overtime_unknown() -> None:
    # Last read 0:01 and no 0:00 read: overtime or a missed frame; not compared.
    alignment = align_pair(
        _review(), _manifest(clock_zero=False, lowest=1), None, _config()
    )
    assert alignment.pair.gameplay_overtime is None
    assert "overtime_mismatch" not in alignment.pair.reasons
    assert alignment.pair.pair_status == "consistent"


def test_turf_war_is_never_overtime() -> None:
    alignment = align_pair(
        _review(), _manifest(clock_zero=False, lowest=1), "turf_war", _config()
    )
    assert alignment.pair.gameplay_overtime is False
    assert alignment.pair.pair_status == "consistent"


# --- v2: clock domain and coverage from consistent edges only ----------------


def _turf_war_22_31_53(overrides: dict[float, int | None]) -> VisionManifest:
    # 3:00 Turf War; GO near video 39.8 s, last read 0:01, no 0:00 read.
    return _manifest(
        c=219.8,
        t_start=16.5,
        t_end=219.0,
        overrides=overrides,
        clock_zero=False,
        lowest=1,
        opening=180,
    )


def test_observed_6_21_and_8_21_misreads_keep_turf_war_consistent() -> None:
    misreads = {149.5: 381, 150.0: 501, 160.0: 501}
    alignment = align_pair(
        _review(labels=(180, 120, 60, 0), mode="turf_war"),
        _turf_war_22_31_53(misreads),
        "turf_war",
        _config(),
    )
    pair = alignment.pair
    assert pair.pair_status == "consistent", pair.reasons
    assert pair.gameplay_consistent_clock_top == 180
    assert pair.gameplay_highest_hud_read == 501  # diagnostic only
    assert alignment.calibration.fit_status == "calibrated"


def test_scattered_impossible_high_reads_do_not_set_the_clock_domain() -> None:
    misreads = {12.0: 581, 80.0: 459, 150.0: 501, 230.0: 777, 290.0: 999}
    alignment = align_pair(_review(), _manifest(overrides=misreads), None, _config())
    assert alignment.pair.pair_status == "consistent", alignment.pair.reasons
    assert alignment.pair.gameplay_consistent_clock_top == 300
    assert alignment.pair.gameplay_highest_hud_read == 999


def test_glitch_edge_outside_the_c_cluster_is_not_trusted() -> None:
    alignment = align_pair(
        _review(), _manifest(overrides={150.0: 501, 150.5: 500}), None, _config()
    )
    cal = alignment.calibration
    assert cal.raw_edge_remaining_span[1] == 501  # the glitch formed a raw edge
    assert 500 not in cal.consistent_remaining
    assert alignment.pair.gameplay_consistent_clock_top == 300
    clean = align_pair(_review(), _manifest(), None, _config())
    assert alignment.pair.coverage_fraction == clean.pair.coverage_fraction
    assert alignment.pair.pair_status == "consistent"


@pytest.mark.parametrize(
    ("review_labels", "manifest", "top"),
    [
        ((180, 100, 0), lambda: _manifest(), 300),
        ((300, 200, 100, 0), lambda: _turf_war_22_31_53({}), 180),
    ],
)
def test_genuinely_different_clock_domains_are_still_rejected(
    review_labels: tuple[int, ...], manifest, top: int
) -> None:
    alignment = align_pair(_review(labels=review_labels), manifest(), None, _config())
    assert alignment.pair.gameplay_consistent_clock_top == top
    assert "clock_domain_mismatch" in alignment.pair.reasons


def test_recording_started_after_go_is_still_a_clock_domain_mismatch() -> None:
    alignment = align_pair(_review(), _manifest(t_start=70.0), None, _config())
    assert alignment.pair.gameplay_consistent_clock_top == 251
    assert "clock_domain_mismatch" in alignment.pair.reasons


def test_no_consistent_edges_means_no_trusted_clock_statistics() -> None:
    alignment = align_pair(_review(), _manifest(t_end=30.0), None, _config())
    assert alignment.calibration.consistent_remaining == []
    assert alignment.pair.gameplay_consistent_clock_top is None
    assert {"clock_domain_mismatch", "insufficient_clock_coverage"} <= set(
        alignment.pair.reasons
    )


def test_unknown_mode_on_either_side_is_not_a_mismatch() -> None:
    alignment = align_pair(_review(mode=None), _manifest(), "splat_zones", _config())
    assert "mode_mismatch" not in alignment.pair.reasons


# --- CLI --------------------------------------------------------------------


def test_cli_writes_alignment_and_never_rewrites_inputs(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from splatoon3_ai_coach.cli.app import app
    from splatoon3_ai_coach.media.vision_manifest import save_vision_manifest

    review_dir, gameplay_dir, out_dir = (tmp_path / n for n in ("r", "g", "o"))
    review_dir.mkdir()
    gameplay_dir.mkdir()
    timeline = review_dir / "review_timeline.json"
    timeline.write_text(_review().model_dump_json(indent=2), encoding="utf-8")
    save_vision_manifest(_manifest(), gameplay_dir)
    before = {p: p.read_bytes() for d in (review_dir, gameplay_dir) for p in d.iterdir()}

    result = CliRunner().invoke(
        app,
        [
            "align",
            "review-clock",
            str(review_dir),
            str(gameplay_dir),
            "--out",
            str(out_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    after = {p: p.read_bytes() for d in (review_dir, gameplay_dir) for p in d.iterdir()}
    assert after == before
    written = review_alignment.ReviewGameplayAlignment.model_validate_json(
        (out_dir / review_alignment.ALIGNMENT_FILENAME).read_text("utf-8")
    )
    assert written.pair.pair_status == "consistent"
    assert written.review_dataset_sha256 and written.gameplay_manifest_sha256


# --- forbidden inputs ---------------------------------------------------------


def _alignment_sources() -> list[Path]:
    paths = [Path(gameplay_clock.__file__), Path(review_alignment.__file__)]
    tool = REPO / "tools/diagnostics/review_gameplay_alignment.py"
    return paths + ([tool] if tool.exists() else [])


def test_alignment_code_never_reads_special_use() -> None:
    for path in _alignment_sources():
        tree = ast.parse(path.read_text("utf-8"))
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
                names += [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names += [a.name for a in node.names]
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
            elif isinstance(node, ast.Name):
                names.append(node.id)
        hits = [n for n in names if "special" in n.lower()]
        assert hits == [], (path.name, hits)


def test_alignment_code_has_no_user_slot_or_game_events_emitted() -> None:
    for path in _alignment_sources():
        tree = ast.parse(path.read_text("utf-8"))
        calls = [
            n.func.id
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        ]
        assert "GameEvent" not in calls, path.name
        fields = [
            n.target.id
            for n in ast.walk(tree)
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
        ]
        assert not [f for f in fields if "user" in f.lower()], path.name


def test_review_and_coach_do_not_import_alignment() -> None:
    src = REPO / "src/splatoon3_ai_coach"
    for package in ("review", "coach", "analysis", "vision"):
        for path in (src / package).rglob("*.py"):
            assert "splatoon3_ai_coach.alignment" not in path.read_text("utf-8"), path
