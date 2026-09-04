"""Tests for local-kill splat detection, fusion, and SPLAT events."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.models import (
    DeathDetectorConfig,
    EventFusionConfig,
    SplatDetectorConfig,
    StateFusionConfig,
    TimerDetectorConfig,
)
from splatoon3_ai_coach.vision.death import DeathDetector
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    GameEventType,
    GameStateSnapshot,
    SplatReading,
    TimerReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.splat import SplatDetector
from splatoon3_ai_coach.vision.state import fuse_game_state

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATE_DIR = _REPO_ROOT / "calibration" / "templates" / "splat"
_ASSETS = Path(
    "/Users/kenjikahara/.cursor/projects/Users-kenjikahara-splatoon3-ai-coach/assets"
)


def _timer_config() -> TimerDetectorConfig:
    return TimerDetectorConfig(
        roi=(0.0, 0.0, 1.0, 1.0),
        template_dir=".",
        min_usable_confidence=0.5,
    )


def _splat_config(**overrides: object) -> SplatDetectorConfig:
    data: dict[str, object] = {
        "template_dir": _TEMPLATE_DIR,
        "debounce_seconds": 1.75,
    }
    data.update(overrides)
    return SplatDetectorConfig(**data)  # type: ignore[arg-type]


def _blank_1080() -> np.ndarray:
    return np.full((1080, 1920, 3), 180, dtype=np.uint8)


def _load_snap_1080(name: str) -> np.ndarray:
    path = _ASSETS / name
    if not path.exists():
        pytest.skip(f"missing evidence snap: {path}")
    image = cv2.imread(str(path))
    assert image is not None
    return cv2.resize(image, (1920, 1080), interpolation=cv2.INTER_AREA)


def _result(
    timestamp: float,
    index: int,
    *,
    timer: tuple[str, float, float] | None = None,
    splat: SplatReading | None = None,
    splat_confidence: float = 0.9,
) -> VisionFrameResult:
    detections: list[DetectorResult] = []
    if timer is not None:
        display, seconds, confidence = timer
        detections.append(
            DetectorResult(
                id=f"timer:{index}",
                detector_name="timer",
                detector_version="timer@test",
                confidence=confidence,
                reading=TimerReading(display=display, seconds_remaining=seconds),
            )
        )
    if splat is not None:
        detections.append(
            DetectorResult(
                id=f"splat:{index}",
                detector_name="splat",
                detector_version="splat@test",
                confidence=splat_confidence,
                reading=splat,
            )
        )
    return VisionFrameResult(
        frame_id=f"frame:{index}",
        timestamp=timestamp,
        source="cadence",
        source_frame_index=index,
        detections=detections,
    )


def test_splat_detector_confirms_user_snap() -> None:
    detector = SplatDetector(_splat_config())
    image = _load_snap_1080(
        "vlcsnap-2026-09-03-21h18m56s379-b44b62b5-0f8a-487c-9f77-1d0b94fc199a.jpg"
    )
    reading, score = detector.detect(image, timestamp=1.0)
    assert reading is not None
    assert reading.kind == "splat"
    assert reading.detected
    assert reading.skull_score >= 0.55
    assert reading.adjacent_color_score >= 0.20
    assert reading.victim_name is None
    assert reading.victim_name_confidence == 0.0
    assert score >= 0.5


def test_blank_frame_is_not_a_splat() -> None:
    detector = SplatDetector(_splat_config())
    reading, _ = detector.detect(_blank_1080(), timestamp=0.0)
    assert reading is not None
    assert not reading.detected


def test_skull_without_adjacent_color_is_rejected() -> None:
    """White glyph alone in the ROI must not count as a local kill."""
    detector = SplatDetector(_splat_config(adjacent_color_min_ratio=0.20))
    image = _blank_1080()
    # Dark banner + white square (skull-like) with no saturated neighbor.
    image[918:1058, 672:1248] = (20, 20, 20)
    image[980:1012, 740:772] = (255, 255, 255)
    reading, _ = detector.detect(image, timestamp=1.0)
    assert reading is not None
    assert not reading.detected


def test_splat_detector_debounces_repeat_positives() -> None:
    detector = SplatDetector(_splat_config(debounce_seconds=1.75))
    image = _load_snap_1080(
        "vlcsnap-2026-09-03-21h18m56s379-b44b62b5-0f8a-487c-9f77-1d0b94fc199a.jpg"
    )
    first, _ = detector.detect(image, timestamp=10.0)
    suppressed, _ = detector.detect(image, timestamp=10.5)
    later, _ = detector.detect(image, timestamp=12.0)
    assert first is not None and first.detected
    assert suppressed is None
    assert later is not None and later.detected


def test_fusion_sets_transient_player_splatted() -> None:
    results = [
        _result(0.0, 0, splat=SplatReading(detected=False)),
        _result(
            1.0,
            1,
            splat=SplatReading(
                detected=True,
                skull_score=0.9,
                adjacent_color_score=0.5,
            ),
        ),
        _result(1.5, 2, splat=SplatReading(detected=False)),
        _result(4.0, 3, splat=SplatReading(detected=False)),
    ]
    snapshots = fuse_game_state(
        results,
        _timer_config(),
        StateFusionConfig(max_hold_duration=2.0),
        DeathDetectorConfig(),
        _splat_config(),
    )
    assert snapshots[0].player_splatted is None
    assert snapshots[1].player_splatted is True
    assert snapshots[2].player_splatted is True  # hold
    assert snapshots[3].player_splatted is None  # expired
    assert "splat:1" in snapshots[1].evidence_ids


def test_fusion_does_not_touch_player_alive() -> None:
    results = [
        _result(
            1.0,
            1,
            splat=SplatReading(
                detected=True,
                skull_score=0.9,
                adjacent_color_score=0.5,
            ),
        ),
    ]
    snapshots = fuse_game_state(
        results,
        _timer_config(),
        StateFusionConfig(),
        DeathDetectorConfig(),
        _splat_config(),
    )
    assert snapshots[0].player_splatted is True
    assert snapshots[0].player_alive is None


def test_infer_events_emits_splat_on_rising_edge() -> None:
    snapshots = [
        GameStateSnapshot(timestamp=1.0, player_splatted=None),
        GameStateSnapshot(
            timestamp=2.0,
            player_splatted=True,
            evidence_ids=["splat:1"],
        ),
        GameStateSnapshot(timestamp=2.5, player_splatted=True),
        GameStateSnapshot(timestamp=5.0, player_splatted=None),
        GameStateSnapshot(
            timestamp=6.0,
            player_splatted=True,
            evidence_ids=["splat:2"],
        ),
    ]
    events = infer_events(snapshots, EventFusionConfig(debounce_ms=300))
    splat_events = [e for e in events if e.event_type == GameEventType.SPLAT]
    assert len(splat_events) == 2
    assert splat_events[0].start_time == pytest.approx(2.0)
    assert splat_events[1].start_time == pytest.approx(6.0)


def test_death_detector_unchanged_on_blank() -> None:
    """Splat work must not alter DeathDetector behavior on a blank frame."""
    reading, _ = DeathDetector(DeathDetectorConfig()).detect(_blank_1080(), timestamp=0.0)
    assert reading is not None
    assert not reading.detected
