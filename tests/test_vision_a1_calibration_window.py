"""A1 bounded post-``in_match`` calibration window (v4 Amendment 2).

Scenario tests drive frames through the production per-frame call order
(``observe_cadence_stream`` → ``_observe_cadence_frame``: Ready gate + scan
``MatchPhaseFuser`` step, then calibration) with scripted detectors. They
never set match phase, ``opening_clock_ticked`` or window state by hand;
the only seeded state is stage identity, which the Ready? search needs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.config.models import AppConfig, PlayerCountDetectorConfig
from splatoon3_ai_coach.media.video import VideoFrame
from splatoon3_ai_coach.vision.cadence_scan import observe_cadence_stream
from splatoon3_ai_coach.vision.calibration_window import (
    POST_IN_MATCH_WINDOW_SECONDS,
    CalibrationWindow,
)
from splatoon3_ai_coach.vision.match_side_effects import (
    MapInkScanContext,
    build_map_ink_context,
    persist_map_artifacts,
    qualifying_timer_seconds,
)
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    MapOverlayReading,
    PlayerCountReading,
    ReadyReading,
    TimerReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.state import fuse_game_state

_NAMES = ("timer", "ready", "player_count", "map_overlay")


def _hud(ally_h: int = 11, opp_h: int = 119) -> np.ndarray:
    """BGR frame with every roster slot filled at the given OpenCV hues."""
    hsv = np.zeros((1080, 1920, 3), dtype=np.uint8)
    hsv[:] = (0, 0, 20)
    cfg = PlayerCountDetectorConfig()
    for boxes, hue in ((cfg.ally_slots, ally_h), (cfg.opponent_slots, opp_h)):
        for x1, y1, x2, y2 in boxes:
            hsv[int(y1 * 1080) : int(y2 * 1080), int(x1 * 1920) : int(x2 * 1920)] = (
                hue,
                200,
                200,
            )
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


_GOOD = _hud(11, 119)
_OTHER = _hud(60, 100)


@dataclass(frozen=True)
class Frame:
    """Scripted detector output for one cadence frame."""

    t: float
    timer: float | None = None
    timer_conf: float = 0.9
    ready: bool = False
    roster: bool = False
    overlay: bool = False
    image: np.ndarray | None = None


class _Scripted:
    """Detector stub returning scripted readings by frame timestamp."""

    run_on_evidence = False
    cadence_fps = None

    def __init__(self, name: str, frames: dict[float, Frame]) -> None:
        self.name = name
        self._frames = frames

    def detect(self, image: np.ndarray, timestamp: float | None = None):
        spec = self._frames.get(float(timestamp or 0.0))
        if spec is None:
            return None, 0.0
        if self.name == "timer" and spec.timer is not None:
            return (
                TimerReading(display=str(spec.timer), seconds_remaining=spec.timer),
                spec.timer_conf,
            )
        if self.name == "ready" and spec.ready:
            return ReadyReading(present=True, template_score=0.9), 0.9
        if self.name == "player_count" and spec.roster:
            return PlayerCountReading(roster_visible=True), 0.9
        if self.name == "map_overlay" and spec.overlay:
            return MapOverlayReading(present=True, template_score=0.9), 0.9
        return None, 0.0


def _context(tmp_path: Path) -> tuple[AppConfig, MapInkScanContext]:
    """Production map-ink context; only stage identity is seeded."""
    app = load_config(default_config_path())
    map_ctx = build_map_ink_context(app, tmp_path, debug_persist=False)
    map_ctx.identity.identity.stage_id = "mahi_mahi_resort"
    return app, map_ctx


def _scan(map_ctx: MapInkScanContext, frames: list[Frame]) -> list[VisionFrameResult]:
    """Run frames through the production cadence path (2 fps)."""
    by_t = {f.t: f for f in frames}
    detectors = [_Scripted(name, by_t) for name in _NAMES]
    video = [
        VideoFrame(
            timestamp=f.t,
            frame_index=int(f.t * 60),
            image=f.image if f.image is not None else _GOOD,
        )
        for f in frames
    ]
    results, _ = observe_cadence_stream(
        iter(video),
        detectors,
        cadence_fps=2.0,
        analysis_id="a1",
        detector_versions={name: "test" for name in _NAMES},
        map_ctx=map_ctx,
    )
    assert len(results) == len(frames)
    return results


def _ts(start: float, stop: float) -> list[float]:
    """Cadence times ``start, start + 0.5, …`` up to and including ``stop``."""
    count = int(round((stop - start) / 0.5)) + 1
    return [start + 0.5 * i for i in range(count)]


def _pre_match(roster: bool = True) -> list[Frame]:
    """Frozen 5:00 from 10.0 s; roster visible; Ready? at 12.0–13.0 s."""
    return [
        Frame(t, timer=300.0, ready=12.0 <= t <= 13.0, roster=roster)
        for t in _ts(10.0, 14.5)
    ]


def _clock(t: float) -> float:
    """Ticking clock after GO at 15.0 s (299 at 15.0 s)."""
    return float(299 - int(t - 15.0))


def _match(start: float, stop: float, roster: bool = True, **kw: object) -> list[Frame]:
    return [Frame(t, timer=_clock(t), roster=roster, **kw) for t in _ts(start, stop)]


def _window(map_ctx: MapInkScanContext) -> CalibrationWindow:
    return map_ctx.calibration_window


# --- production reachability -------------------------------------------------


def test_production_path_reaches_latch_after_legitimate_in_match(
    tmp_path: Path,
) -> None:
    """Reachability: frozen 5:00 → first 4:59 read → latch inside the window."""
    _, map_ctx = _context(tmp_path)
    _scan(map_ctx, _pre_match())
    assert map_ctx.ready_seen is True
    assert map_ctx.match_phase == "opening_countdown"
    assert map_ctx.calibration is None
    assert _window(map_ctx).t0 is None

    _scan(map_ctx, _match(15.0, 17.0))
    cal = map_ctx.calibration
    assert cal is not None
    assert cal.accepted_frame_times == (15.0, 15.5, 16.0)
    assert cal.calibrated_at == pytest.approx(16.0)
    assert cal.accepted_match_phases == ("in_match", "in_match", "in_match")
    window = _window(map_ctx)
    assert window.t0 == pytest.approx(15.0)
    assert window.end == pytest.approx(15.0 + POST_IN_MATCH_WINDOW_SECONDS)
    assert (window.close_reason, window.closed_at) == ("latched", 16.0)
    assert map_ctx.opening_clock_ticked is True


# --- pre-match rejection and in_match entry ----------------------------------


def test_ready_and_frozen_clock_roster_evidence_is_rejected(tmp_path: Path) -> None:
    """Ready?/presentation and frozen-clock frames never contribute."""
    _, map_ctx = _context(tmp_path)
    _scan(map_ctx, _pre_match())
    assert map_ctx.calibration is None
    assert map_ctx.calibrator is not None and not map_ctx.calibrator.is_latched
    tally = _window(map_ctx).rejections["phase_ineligible:opening_countdown"]
    # Roster frames after Ready? (12.0–14.5 s); before Ready? nothing samples.
    assert (tally.count, tally.first_at, tally.last_at) == (6, 12.0, 14.5)


def test_first_qualifying_non_opening_read_enters_in_match(tmp_path: Path) -> None:
    """The first raw usable non-opening read sets in_match and t0."""
    _, map_ctx = _context(tmp_path)
    _scan(map_ctx, _pre_match(roster=False))
    assert map_ctx.match_phase == "opening_countdown"
    _scan(map_ctx, [Frame(15.0, timer=299.0)])
    assert map_ctx.match_phase == "in_match"
    assert _window(map_ctx).t0 == pytest.approx(15.0)
    assert _window(map_ctx).is_open


# --- window bounds -------------------------------------------------------------


def test_window_end_is_inclusive(tmp_path: Path) -> None:
    """A frame at exactly t0 + 3.0 is still eligible."""
    _, map_ctx = _context(tmp_path)
    _scan(map_ctx, _pre_match(roster=False))
    _scan(map_ctx, _match(15.0, 16.5, roster=False) + _match(17.0, 18.0))
    cal = map_ctx.calibration
    assert cal is not None
    assert cal.accepted_frame_times == (17.0, 17.5, 18.0)
    assert _window(map_ctx).close_reason == "latched"


def test_frame_after_window_cannot_latch(tmp_path: Path) -> None:
    """The first frame after t0 + 3.0 expires the window and is rejected."""
    _, map_ctx = _context(tmp_path)
    _scan(map_ctx, _pre_match(roster=False))
    frames = _match(15.0, 16.5, roster=False) + _match(17.0, 17.5) + _match(18.5, 18.5)
    _scan(map_ctx, frames)
    assert map_ctx.calibration is None
    window = _window(map_ctx)
    assert (window.close_reason, window.closed_at) == ("expired", 18.5)
    assert window.rejections["outside_window:expired"].count == 1


def test_expired_window_never_reopens(tmp_path: Path) -> None:
    """Later non-opening reads, gaps and a fuser return to in_match: no reopen."""
    app, map_ctx = _context(tmp_path)
    _scan(map_ctx, _pre_match(roster=False))
    _scan(map_ctx, _match(15.0, 18.5, roster=False))
    assert _window(map_ctx).close_reason == "expired"

    # Later qualifying non-opening reads with good roster frames.
    _scan(map_ctx, _match(19.0, 22.0))
    # A timer gap shorter than the hold keeps in_match.
    _scan(map_ctx, [Frame(t, roster=True) for t in _ts(22.5, 26.0)])
    assert map_ctx.match_phase == "in_match"
    # A gap longer than the hold moves the fuser to post_match …
    hold = app.vision.lifecycle.match_context_hold_seconds
    gap_end = 22.0 + hold + 1.0
    _scan(map_ctx, [Frame(t, roster=True) for t in _ts(26.5, gap_end)])
    assert map_ctx.match_phase == "post_match"
    # … and the next read returns it to in_match.
    _scan(map_ctx, _match(gap_end + 0.5, gap_end + 3.0))
    assert map_ctx.match_phase == "in_match"

    window = _window(map_ctx)
    assert map_ctx.calibration is None
    assert window.t0 == pytest.approx(15.0)
    assert (window.close_reason, window.closed_at) == ("expired", 18.5)
    assert window.rejections["outside_window:expired"].count > 0


# --- timer qualification -------------------------------------------------------


def test_below_floor_non_opening_read_cannot_open_window(tmp_path: Path) -> None:
    """A 0.40 confidence 4:59 ticks the Ready gate but is not an A1 entry."""
    app, map_ctx = _context(tmp_path)
    assert app.vision.timer.min_usable_confidence == pytest.approx(0.50)
    _scan(map_ctx, _pre_match(roster=False))
    _scan(map_ctx, _match(15.0, 16.5, timer_conf=0.40))
    assert map_ctx.opening_clock_ticked is True
    assert map_ctx.match_phase == "opening_countdown"
    assert _window(map_ctx).t0 is None
    assert map_ctx.calibration is None
    assert _window(map_ctx).rejections["phase_ineligible:opening_countdown"].count == 4

    # A later qualifying read opens the window; the earlier tick does not end it.
    _scan(map_ctx, _match(17.0, 18.0))
    assert _window(map_ctx).t0 == pytest.approx(17.0)
    assert map_ctx.calibration is not None
    assert map_ctx.calibration.accepted_frame_times == (17.0, 17.5, 18.0)


def test_only_raw_timer_detector_results_qualify() -> None:
    """Synthesized/fused TimerReadings under other names never qualify."""
    synthesized = DetectorResult(
        id="s",
        detector_name="timer_fused",
        detector_version="test",
        confidence=1.0,
        reading=TimerReading(display="4:00", seconds_remaining=240.0),
    )
    assert qualifying_timer_seconds([synthesized], 0.50) is None
    low = synthesized.model_copy(update={"detector_name": "timer", "confidence": 0.49})
    assert qualifying_timer_seconds([low], 0.50) is None
    ok = low.model_copy(update={"confidence": 0.50})
    assert qualifying_timer_seconds([ok, low], 0.50) == pytest.approx(240.0)


def test_smoothed_held_and_post_scan_phase_cannot_open_window(tmp_path: Path) -> None:
    """Post-scan smoothing turns 300/180 into ~260 (in_match); the scan does not."""
    app, map_ctx = _context(tmp_path)
    frames = _pre_match() + [
        Frame(t, timer=180.0 if int(t * 2) % 2 else 300.0, roster=True)
        for t in _ts(15.0, 18.0)
    ]
    results = _scan(map_ctx, frames)
    snapshots = fuse_game_state(
        results,
        app.vision.timer,
        app.vision.state_fusion,
        lifecycle_config=app.vision.lifecycle,
    )
    assert any(s.match_phase == "in_match" for s in snapshots)
    assert any(s.quality in {"smoothed", "held"} for s in snapshots)
    assert map_ctx.match_phase == "opening_countdown"
    assert _window(map_ctx).t0 is None
    assert map_ctx.calibration is None


# --- fail-closed, immutability, provenance -----------------------------------


def test_no_latch_in_window_keeps_map_ink_fail_closed(tmp_path: Path) -> None:
    """Expired without latch: no calibration, no map-ink observations."""
    _, map_ctx = _context(tmp_path)
    _scan(map_ctx, _pre_match(roster=False))
    _scan(map_ctx, _match(15.0, 18.5, roster=False) + _match(19.0, 22.0, overlay=True))
    assert map_ctx.calibration is None
    assert map_ctx.observations == []
    persist_map_artifacts(map_ctx, tmp_path)
    payload = json.loads((tmp_path / "match_identity.json").read_text("utf-8"))
    assert payload["map_ink_enabled"] is False
    assert payload["team_color_calibration"] is None
    assert payload["team_color_calibration_window"]["close_reason"] == "expired"


def test_successful_calibration_is_immutable(tmp_path: Path) -> None:
    """After the latch, different hues in later frames change nothing."""
    _, map_ctx = _context(tmp_path)
    _scan(map_ctx, _pre_match(roster=False))
    _scan(map_ctx, _match(15.0, 16.0))
    first = map_ctx.calibration
    assert first is not None
    _scan(map_ctx, _match(16.5, 18.0, image=_OTHER))
    assert map_ctx.calibration is first
    assert map_ctx.calibrator is not None and map_ctx.calibrator.result is first
    assert abs(first.ally_h - 11.0) <= 2.0


def test_window_provenance_is_persisted(tmp_path: Path) -> None:
    """t0, bounds, close reason and both rejection kinds reach match_identity."""
    _, map_ctx = _context(tmp_path)
    _scan(map_ctx, _pre_match())
    _scan(map_ctx, _match(15.0, 16.0))
    persist_map_artifacts(map_ctx, tmp_path)
    payload = json.loads((tmp_path / "match_identity.json").read_text("utf-8"))
    window = payload["team_color_calibration_window"]
    assert window["t0"] == pytest.approx(15.0)
    assert window["start"] == pytest.approx(15.0)
    assert window["end"] == pytest.approx(18.0)
    assert window["duration_seconds"] == pytest.approx(3.0)
    assert (window["close_reason"], window["closed_at"]) == ("latched", 16.0)
    assert window["rejections"] == {
        "phase_ineligible:opening_countdown": {
            "count": 6,
            "first_at": 12.0,
            "last_at": 14.5,
        }
    }
    calibration = payload["team_color_calibration"]
    assert calibration["accepted_match_phases"] == ["in_match"] * 3

    _, expired_ctx = _context(tmp_path)
    _scan(expired_ctx, _pre_match(roster=False))
    _scan(expired_ctx, _match(15.0, 18.0, roster=False) + _match(18.5, 19.0))
    persist_map_artifacts(expired_ctx, tmp_path)
    payload = json.loads((tmp_path / "match_identity.json").read_text("utf-8"))
    window = payload["team_color_calibration_window"]
    assert (window["close_reason"], window["closed_at"]) == ("expired", 18.5)
    assert window["rejections"]["outside_window:expired"] == {
        "count": 2,
        "first_at": 18.5,
        "last_at": 19.0,
    }


# --- local window unit (not reachability evidence) ----------------------------


def test_window_unit_open_once_close_once() -> None:
    """Local unit only: open on first in_match, inclusive end, no reopen."""
    window = CalibrationWindow()
    window.advance(1.0, "opening_countdown")
    assert window.admit(1.0, "opening_countdown") == "phase_ineligible:opening_countdown"
    window.advance(2.0, "in_match")
    assert window.admit(2.0, "in_match") is None
    window.advance(5.0, "in_match")
    assert window.admit(5.0, "in_match") is None
    window.advance(5.5, "in_match")
    assert window.close_reason == "expired"
    window.advance(9.0, "in_match")
    assert (window.t0, window.closed_at) == (2.0, 5.5)
    assert window.admit(9.0, "in_match") == "outside_window:expired"
