"""Team-color calibration + calibrated map-ink classification."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import (
    MapInkAnalyzerConfig,
    PlayerCountDetectorConfig,
)
from splatoon3_ai_coach.media.video import VideoFrame
from splatoon3_ai_coach.vision.map_ink import MapInkClassifier
from splatoon3_ai_coach.vision.match_intro import MatchIdentityTracker
from splatoon3_ai_coach.vision.pipeline import (
    MapInkScanContext,
    _maybe_calibrate_team_colors,
    _persist_map_artifacts,
)
from splatoon3_ai_coach.vision.team_color_calibration import (
    ColorProfile,
    TeamColorCalibrationResult,
    TeamColorCalibrator,
    circular_hue_distance,
)


def _default_slots() -> tuple[list, list]:
    cfg = PlayerCountDetectorConfig()
    return list(cfg.ally_slots), list(cfg.opponent_slots)


def _paint_slot(
    hsv: np.ndarray,
    box: tuple[float, float, float, float],
    h: int,
    *,
    s: int = 200,
    v: int = 200,
) -> None:
    height, width = hsv.shape[:2]
    x1, y1, x2, y2 = box
    left, top = int(x1 * width), int(y1 * height)
    right, bottom = int(x2 * width), int(y2 * height)
    hsv[top:bottom, left:right] = (h, s, v)


def _synthetic_hud(
    ally_h: int = 11,
    opp_h: int = 119,
    *,
    width: int = 1920,
    height: int = 1080,
) -> np.ndarray:
    """BGR frame with filled roster slots at given OpenCV hues."""
    hsv = np.zeros((height, width, 3), dtype=np.uint8)
    hsv[:] = (0, 0, 20)
    ally_slots, opp_slots = _default_slots()
    for box in ally_slots:
        _paint_slot(hsv, box, ally_h)
    for box in opp_slots:
        _paint_slot(hsv, box, opp_h)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def _calibrator(**overrides: object) -> TeamColorCalibrator:
    ally_slots, opp_slots = _default_slots()
    kwargs: dict = {
        "ally_slots": ally_slots,
        "opponent_slots": opp_slots,
        "window_seconds": 60.0,
        "min_accepted_frames": 3,
        "min_hue_separation": 30.0,
        "s_min": 70,
        "v_min": 40,
    }
    kwargs.update(overrides)
    return TeamColorCalibrator(**kwargs)  # type: ignore[arg-type]


def test_a_synthetic_hud_latches_h11_h119_separation_108() -> None:
    """Case A: ally≈11 / opp≈119 latch; shortest circular separation ≈72°."""
    cal = _calibrator()
    frame = _synthetic_hud(11, 119)
    assert cal.observe(frame, 2.0) is None
    assert cal.observe(frame, 2.5) is None
    result = cal.observe(frame, 3.0)
    assert result is not None
    assert abs(result.ally_h - 11.0) <= 2.0
    assert abs(result.opponent_h - 119.0) <= 2.0
    # |119-11|=108 long arc; shortest OpenCV-H circle distance is 72.
    assert abs(result.separation_degrees - 72.0) <= 2.0
    assert result.source == "hud_roster_slots"
    assert result.calibrated_at == pytest.approx(3.0)


def test_b_sticky_ignores_post_latch_hues() -> None:
    """Case B: after latch, different HUD hues are ignored."""
    cal = _calibrator()
    good = _synthetic_hud(11, 119)
    for t in (1.0, 1.5, 2.0):
        cal.observe(good, t)
    assert cal.is_latched
    latched = cal.result
    assert latched is not None
    weird = _synthetic_hud(60, 100)
    assert cal.observe(weird, 2.5) is None
    assert cal.result is latched
    assert abs(cal.result.ally_h - 11.0) <= 2.0


def test_c_wraparound_distance_small() -> None:
    """Case C: circular distance near H wrap is small."""
    assert circular_hue_distance(179.0, 2.0) == pytest.approx(3.0)
    assert circular_hue_distance(11.0, 119.0) == pytest.approx(72.0)


def test_d_calibrated_classify_orange_ally_blue_opponent() -> None:
    """Case D: calibrated profiles map orange→ally, blue→opponent."""
    cfg = MapInkAnalyzerConfig(h_tolerance=15, s_min=70, v_min=40)
    clf = MapInkClassifier(cfg)
    clf.apply_calibration(
        TeamColorCalibrationResult(
            ally_h=11.0,
            opponent_h=119.0,
            separation_degrees=108.0,
            calibrated_at=3.0,
        )
    )
    orange = cv2.cvtColor(np.uint8([[[11, 200, 220]]]), cv2.COLOR_HSV2BGR)
    blue = cv2.cvtColor(np.uint8([[[119, 200, 220]]]), cv2.COLOR_HSV2BGR)

    a, o, _ = clf.classify_bgr(orange)
    assert bool(a[0, 0]) and not bool(o[0, 0])
    a, o, _ = clf.classify_bgr(blue)
    assert bool(o[0, 0]) and not bool(a[0, 0])


def test_e_overlap_tiebreak_nearer_center() -> None:
    """Case E: pixel in both bands assigns to nearer center."""
    cfg = MapInkAnalyzerConfig(h_tolerance=40, s_min=70, v_min=40)
    clf = MapInkClassifier(cfg)
    clf.apply_calibration(
        TeamColorCalibrationResult(
            ally_h=10.0,
            opponent_h=50.0,
            separation_degrees=40.0,
            calibrated_at=1.0,
        )
    )
    # Midpoint H=30 is equidistant (20°); ally preferred on exact tie.
    mid = cv2.cvtColor(np.uint8([[[30, 200, 220]]]), cv2.COLOR_HSV2BGR)
    a, o, _ = clf.classify_bgr(mid)
    assert bool(a[0, 0]) and not bool(o[0, 0])

    nearer_opp = cv2.cvtColor(np.uint8([[[40, 200, 220]]]), cv2.COLOR_HSV2BGR)
    a, o, _ = clf.classify_bgr(nearer_opp)
    assert bool(o[0, 0]) and not bool(a[0, 0])


def test_f_fallback_yaml_unchanged_without_calibration() -> None:
    """Case F: no calibration keeps YAML range behavior."""
    cfg = MapInkAnalyzerConfig()
    clf = MapInkClassifier(cfg)
    lime = cv2.cvtColor(np.uint8([[[60, 200, 220]]]), cv2.COLOR_HSV2BGR)
    purple = cv2.cvtColor(np.uint8([[[140, 200, 220]]]), cv2.COLOR_HSV2BGR)
    orange = cv2.cvtColor(np.uint8([[[11, 200, 220]]]), cv2.COLOR_HSV2BGR)

    a, o, _ = clf.classify_bgr(lime)
    assert bool(a[0, 0])
    a, o, _ = clf.classify_bgr(purple)
    assert bool(o[0, 0])
    # Orange is outside lime ally band and in red opponent fallback.
    a, o, _ = clf.classify_bgr(orange)
    assert not bool(a[0, 0])
    assert bool(o[0, 0])


def test_g_persistence_roundtrip_separation_108(tmp_path: Path) -> None:
    """Case G: team_color_calibration round-trips with circular separation 72."""
    result = TeamColorCalibrationResult(
        ally_h=11.0,
        opponent_h=119.0,
        separation_degrees=circular_hue_distance(11.0, 119.0),
        calibrated_at=3.0,
    )
    assert result.separation_degrees == pytest.approx(72.0)
    payload = result.to_dict()
    restored = TeamColorCalibrationResult.from_dict(payload)
    assert restored.ally_h == pytest.approx(11.0)
    assert restored.opponent_h == pytest.approx(119.0)
    assert restored.separation_degrees == pytest.approx(72.0)
    assert restored.source == "hud_roster_slots"

    cfg = load_config(default_config_path())
    cfg.vision.map_ink.enabled = True
    map_ctx = MapInkScanContext(
        identity=MatchIdentityTracker(),
        classifier=MapInkClassifier(cfg.vision.map_ink),
        config=cfg,
        calibration=result,
    )
    _persist_map_artifacts(map_ctx, tmp_path)
    raw = (tmp_path / "match_identity.json").read_text(encoding="utf-8")
    assert '"source": "hud_roster_slots"' in raw
    assert "72" in raw
    assert "11" in raw
    assert "119" in raw


def test_h_pipeline_yaml_then_latch_then_calibrated() -> None:
    """Case H: classify YAML before latch; apply once; calibrated after."""
    cfg = MapInkAnalyzerConfig(
        enabled=True,
        team_color_calibration_enabled=True,
        h_tolerance=15,
        s_min=70,
        v_min=40,
    )
    clf = MapInkClassifier(cfg)
    orange = cv2.cvtColor(np.uint8([[[11, 200, 220]]]), cv2.COLOR_HSV2BGR)

    a, o, _ = clf.classify_bgr(orange)
    assert not bool(a[0, 0])
    assert bool(o[0, 0])

    app = load_config(default_config_path())
    app.vision.map_ink = cfg
    cal = _calibrator(min_accepted_frames=3)
    map_ctx = MapInkScanContext(
        identity=MatchIdentityTracker(),
        classifier=clf,
        config=app,
        calibrator=cal,
    )
    frame_img = _synthetic_hud(11, 119)
    for t in (2.0, 2.5, 3.0):
        vf = VideoFrame(
            timestamp=t,
            frame_index=int(t * 60),
            image=frame_img,
        )
        _maybe_calibrate_team_colors(map_ctx, vf)

    assert map_ctx.calibration is not None
    assert clf.calibration is map_ctx.calibration
    a, o, _ = clf.classify_bgr(orange)
    assert bool(a[0, 0]) and not bool(o[0, 0])

    first = map_ctx.calibration
    weird = _synthetic_hud(60, 100)
    vf2 = VideoFrame(
        timestamp=4.0,
        frame_index=400,
        image=weird,
    )
    _maybe_calibrate_team_colors(map_ctx, vf2)
    assert map_ctx.calibration is first


def test_color_profile_contains_hsv() -> None:
    """ColorProfile soft gates reject low S/V and out-of-band H."""
    profile = ColorProfile(h_center=11.0, h_tolerance=15.0, s_min=70, v_min=40)
    assert profile.contains_hsv(11, 200, 200)
    assert not profile.contains_hsv(11, 50, 200)
    assert not profile.contains_hsv(11, 200, 20)
    assert not profile.contains_hsv(50, 200, 200)


def test_window_rejects_outside_bounds() -> None:
    """Calibration window is strict: only 0 <= t <= window_seconds."""
    cal = _calibrator(window_seconds=60.0, min_accepted_frames=1)
    frame = _synthetic_hud()
    assert cal.observe(frame, -0.1) is None
    assert cal.observe(frame, 60.1) is None
    assert cal.observe(frame, 0.0) is not None
