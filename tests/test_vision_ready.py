"""Ready? detector + stage→first-tick search gate."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.vision.match_intro import MatchIdentityTracker
from splatoon3_ai_coach.vision.models import ReadyReading, TimerReading
from splatoon3_ai_coach.vision.pipeline import (
    MapInkScanContext,
    _detectors_for_frame,
    _should_run_ready_detector,
    _update_ready_gate_from_detections,
)
from splatoon3_ai_coach.vision.ready import ReadyDetector
from splatoon3_ai_coach.vision.registry import build_detectors


def _repo_ready_frame() -> Path | None:
    """Optional real Ready? snapshot from the Sep-10 analysis."""
    path = Path(
        "analysis/2026-09-10 15-58-36/debug_snapshots/00000840_000014.000.jpg"
    )
    return path if path.is_file() else None


def test_ready_detector_matches_real_snapshot_when_present() -> None:
    """EN Ready? template fires on the known 14.0s frame."""
    snap = _repo_ready_frame()
    if snap is None:
        pytest.skip("analysis Ready? snapshot not present")
    cfg = load_config(default_config_path()).vision.ready
    detector = ReadyDetector(cfg, language="en")
    image = cv2.imread(str(snap))
    assert image is not None
    reading, confidence = detector.detect(image, 14.0)
    assert isinstance(reading, ReadyReading)
    assert reading.present is True
    assert reading.template_score >= cfg.match_threshold
    assert confidence >= cfg.match_threshold


def test_ready_detector_negative_on_blank() -> None:
    """Blank frame does not claim Ready?."""
    cfg = load_config(default_config_path()).vision.ready
    detector = ReadyDetector(cfg, language="en")
    blank = np.zeros((1080, 1920, 3), dtype=np.uint8)
    reading, _ = detector.detect(blank, 1.0)
    assert reading.present is False


def test_ready_gate_stage_then_clock_tick() -> None:
    """Search window: stage known → first non-opening timer tick."""
    app = load_config(default_config_path())
    identity = MatchIdentityTracker()
    map_ctx = MapInkScanContext(identity=identity, classifier=None, config=app)

    assert _should_run_ready_detector(map_ctx) is False
    identity.identity.stage_id = "mahi_mahi_resort"
    assert _should_run_ready_detector(map_ctx) is True

    from splatoon3_ai_coach.vision.models import DetectorResult

    _update_ready_gate_from_detections(
        map_ctx,
        [
            DetectorResult(
                id="t1",
                detector_name="timer",
                detector_version="1",
                confidence=0.9,
                reading=TimerReading(display="5:00", seconds_remaining=300.0),
            )
        ],
    )
    assert map_ctx.opening_clock_ticked is False
    assert _should_run_ready_detector(map_ctx) is True

    _update_ready_gate_from_detections(
        map_ctx,
        [
            DetectorResult(
                id="t2",
                detector_name="timer",
                detector_version="1",
                confidence=0.9,
                reading=TimerReading(display="4:59", seconds_remaining=299.0),
            )
        ],
    )
    assert map_ctx.opening_clock_ticked is True
    assert _should_run_ready_detector(map_ctx) is False


def test_ready_gate_marks_ready_seen() -> None:
    """present Ready? reading flips ready_seen for color calibration."""
    app = load_config(default_config_path())
    map_ctx = MapInkScanContext(
        identity=MatchIdentityTracker(),
        classifier=None,
        config=app,
    )
    from splatoon3_ai_coach.vision.models import DetectorResult

    _update_ready_gate_from_detections(
        map_ctx,
        [
            DetectorResult(
                id="r1",
                detector_name="ready",
                detector_version="1",
                confidence=0.95,
                reading=ReadyReading(present=True, template_score=0.95),
            )
        ],
    )
    assert map_ctx.ready_seen is True


def test_detectors_for_frame_skips_ready_outside_window() -> None:
    """Ready detector is omitted before stage / after clock tick."""
    app = load_config(default_config_path())
    app.vision.enabled_detectors = ["timer", "ready"]
    detectors = build_detectors(app.vision)
    names = {d.name for d in detectors}
    assert names == {"timer", "ready"}

    map_ctx = MapInkScanContext(
        identity=MatchIdentityTracker(),
        classifier=None,
        config=app,
    )
    active = _detectors_for_frame(detectors, map_ctx, 5.0)
    assert [d.name for d in active] == ["timer"]

    map_ctx.identity.identity.stage_id = "mahi_mahi_resort"
    active = _detectors_for_frame(detectors, map_ctx, 5.0)
    assert {d.name for d in active} == {"timer", "ready"}

    map_ctx.opening_clock_ticked = True
    active = _detectors_for_frame(detectors, map_ctx, 20.0)
    assert [d.name for d in active] == ["timer"]


def test_ready_config_from_yaml() -> None:
    """Ready ROI comes from YAML, not a code default."""
    cfg = load_config(default_config_path()).vision.ready
    assert cfg is not None
    assert cfg.roi == pytest.approx((0.390104, 0.450926, 0.541667, 0.567593))
    assert cfg.match_threshold == pytest.approx(0.70)
