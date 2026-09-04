"""Tests for death-UI detection, fusion evidence, and DEATH events."""

import numpy as np
import pytest

from splatoon3_ai_coach.config.models import (
    DeathDetectorConfig,
    EventFusionConfig,
    StateFusionConfig,
    TimerDetectorConfig,
)
from splatoon3_ai_coach.vision.death import DeathDetector
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.models import (
    DeathReading,
    DetectorResult,
    GameEventType,
    GameStateSnapshot,
    TimerReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.state import fuse_game_state


def _timer_config() -> TimerDetectorConfig:
    return TimerDetectorConfig(
        roi=(0.0, 0.0, 1.0, 1.0),
        template_dir=".",
        min_usable_confidence=0.5,
    )


def _blank_1080() -> np.ndarray:
    return np.full((1080, 1920, 3), 180, dtype=np.uint8)


def _death_1080() -> np.ndarray:
    """Synthetic death HUD: white Ouch glyphs + dark bottom banner."""
    image = _blank_1080()
    image[880:940, 20:200] = (40, 40, 40)
    # Enough white ink to clear ouch_white_ratio without real font metrics.
    image[895:925, 35:175] = (255, 255, 255)
    image[980:1050, 660:1260] = (18, 18, 18)
    return image


def _yellow_ink_1080() -> np.ndarray:
    """Alive frame where yellow ink fills the ouch ROI (common FP case)."""
    image = _blank_1080()
    image[880:940, 20:200] = (0, 220, 255)
    image[980:1050, 660:1260] = (18, 18, 18)
    return image


def _result(
    timestamp: float,
    index: int,
    *,
    timer: tuple[str, float, float] | None = None,
    death: DeathReading | None = None,
    death_confidence: float = 0.9,
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
    if death is not None:
        detections.append(
            DetectorResult(
                id=f"death:{index}",
                detector_name="death",
                detector_version="death@test",
                confidence=death_confidence,
                reading=death,
            )
        )
    return VisionFrameResult(
        frame_id=f"frame:{index}",
        timestamp=timestamp,
        source="cadence",
        source_frame_index=index,
        detections=detections,
    )


def test_death_detector_confirms_ouch_and_dark_banner() -> None:
    detector = DeathDetector(DeathDetectorConfig())
    reading, score = detector.detect(_death_1080(), timestamp=1.0)
    assert reading is not None
    assert reading.kind == "death"
    assert reading.ouch_detected
    assert reading.detected
    assert reading.banner_dark_score >= 0.72
    assert score >= 0.5


def test_yellow_ink_in_ouch_roi_is_not_ouch() -> None:
    detector = DeathDetector(DeathDetectorConfig())
    reading, _ = detector.detect(_yellow_ink_1080(), timestamp=1.0)
    assert reading is not None
    assert not reading.ouch_detected
    assert not reading.detected


def test_death_detector_debounces_repeat_positives() -> None:
    detector = DeathDetector(DeathDetectorConfig(debounce_seconds=3.0))
    first, _ = detector.detect(_death_1080(), timestamp=10.0)
    suppressed, _ = detector.detect(_death_1080(), timestamp=11.0)
    later, _ = detector.detect(_death_1080(), timestamp=13.1)
    assert first is not None and first.detected
    assert suppressed is None
    assert later is not None and later.detected


def test_alive_frame_is_not_detected() -> None:
    detector = DeathDetector(DeathDetectorConfig())
    reading, _ = detector.detect(_blank_1080(), timestamp=0.0)
    assert reading is not None
    assert not reading.detected
    assert not reading.ouch_detected


def test_fusion_combines_timer_and_death_evidence() -> None:
    results = [
        _result(
            0.0,
            0,
            timer=("2:10", 130.0, 0.9),
            death=DeathReading(
                detected=False,
                ouch_detected=False,
                banner_detected=False,
            ),
        ),
        _result(
            1.0,
            1,
            timer=("2:09", 129.0, 0.9),
            death=DeathReading(
                detected=True,
                ouch_detected=True,
                banner_detected=False,
                banner_dark_score=0.85,
            ),
        ),
    ]
    snapshots = fuse_game_state(
        results,
        _timer_config(),
        StateFusionConfig(),
        DeathDetectorConfig(),
    )
    # Non-detection must not assert alive.
    assert snapshots[0].player_alive is None
    assert snapshots[1].player_alive is False
    assert snapshots[1].evidence_ids == ["timer:1", "death:1"]


def test_fusion_preserves_dead_without_forcing_alive() -> None:
    results = [
        _result(
            0.0,
            0,
            death=DeathReading(
                detected=True,
                ouch_detected=True,
                banner_dark_score=0.85,
            ),
        ),
        _result(
            1.0,
            1,
            death=DeathReading(detected=False, ouch_detected=False),
            death_confidence=0.9,
        ),
        _result(
            2.0,
            2,
            death=DeathReading(detected=False, ouch_detected=True),
            death_confidence=0.9,
        ),
    ]
    snapshots = fuse_game_state(
        results,
        _timer_config(),
        StateFusionConfig(max_hold_duration=0.5),
        DeathDetectorConfig(),
    )
    assert [s.player_alive for s in snapshots] == [False, False, False]


def test_infer_events_emits_death_from_unknown_to_dead() -> None:
    snapshots = [
        GameStateSnapshot(timestamp=1.0, player_alive=None, evidence_ids=[]),
        GameStateSnapshot(
            timestamp=2.0,
            player_alive=False,
            evidence_ids=["death:1"],
        ),
    ]
    events = infer_events(snapshots, EventFusionConfig(debounce_ms=300))
    assert len(events) == 1
    assert events[0].event_type == GameEventType.DEATH
    assert events[0].start_time == pytest.approx(2.0)


def test_infer_events_emits_death_on_alive_to_dead() -> None:
    snapshots = [
        GameStateSnapshot(
            timestamp=1.0,
            player_alive=True,
            evidence_ids=["death:0"],
        ),
        GameStateSnapshot(
            timestamp=2.0,
            player_alive=False,
            evidence_ids=["death:1", "timer:1"],
        ),
    ]
    events = infer_events(snapshots, EventFusionConfig(debounce_ms=300))
    assert len(events) == 1
    assert events[0].event_type == GameEventType.DEATH
    assert events[0].start_time == pytest.approx(2.0)
    assert events[0].evidence_ids == ["death:1", "timer:1"]


def test_infer_events_does_not_repeat_while_held_dead() -> None:
    snapshots = [
        GameStateSnapshot(timestamp=0.0, player_alive=True, evidence_ids=["a"]),
        GameStateSnapshot(timestamp=1.0, player_alive=False, evidence_ids=["b"]),
        GameStateSnapshot(timestamp=1.2, player_alive=False, evidence_ids=["b"]),
    ]
    events = infer_events(snapshots, EventFusionConfig(debounce_ms=300))
    assert len(events) == 1
