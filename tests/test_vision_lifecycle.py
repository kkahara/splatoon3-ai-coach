"""Tests for respawn lifecycle fusion, countdown, and active-gameplay cues."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import (
    ActiveGameplayDetectorConfig,
    RespawnDetectorConfig,
    DeathDetectorConfig,
    EventFusionConfig,
    LifecycleFusionConfig,
    StateFusionConfig,
    TimerDetectorConfig,
)
from splatoon3_ai_coach.vision.active_gameplay import ActiveGameplayDetector
from splatoon3_ai_coach.vision.respawn import RespawnDetector
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.models import (
    ActiveGameplayReading,
    RespawnReading,
    DeathReading,
    DetectorResult,
    GameEventType,
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


def _life_cfg(**overrides: int | float | bool) -> LifecycleFusionConfig:
    base = dict(
        countdown_present_min_observations=2,
        countdown_absent_min_observations=2,
        active_again_min_observations=3,
        max_respawn_observation_seconds=30.0,
        stale_to_unknown=True,
        death_requires_match_context=False,
    )
    base.update(overrides)
    return LifecycleFusionConfig(**base)  # type: ignore[arg-type]


def _frame(
    timestamp: float,
    index: int,
    *,
    death: DeathReading | None = None,
    respawn: RespawnReading | None = None,
    active: ActiveGameplayReading | None = None,
) -> VisionFrameResult:
    detections: list[DetectorResult] = [
        DetectorResult(
            id=f"timer:{index}",
            detector_name="timer",
            detector_version="timer@test",
            confidence=0.9,
            reading=TimerReading(display="3:00", seconds_remaining=180.0),
        )
    ]
    if death is not None:
        detections.append(
            DetectorResult(
                id=f"death:{index}",
                detector_name="death",
                detector_version="death@test",
                confidence=0.9,
                reading=death,
            )
        )
    if respawn is not None:
        detections.append(
            DetectorResult(
                id=f"respawn:{index}",
                detector_name="respawn",
                detector_version="respawn@test",
                confidence=0.9,
                reading=respawn,
            )
        )
    if active is not None:
        detections.append(
            DetectorResult(
                id=f"active:{index}",
                detector_name="active_gameplay",
                detector_version="active@test",
                confidence=0.9 if active.detected else 0.55,
                reading=active,
            )
        )
    return VisionFrameResult(
        frame_id=f"frame:{index}",
        timestamp=timestamp,
        source="cadence",
        source_frame_index=index,
        detections=detections,
    )


def _present() -> RespawnReading:
    return RespawnReading(
        detected=True,
        confidence=0.9,
        presence_score=0.7,
        dark_frac=0.5,
        bright_frac=0.2,
        p95=0.9,
        evidence_type="template",
        template_score=0.85,
    )


def _absent() -> RespawnReading:
    return RespawnReading(detected=False, confidence=0.9, presence_score=0.1)


def _active(on: bool = True) -> ActiveGameplayReading:
    return ActiveGameplayReading(
        detected=on,
        score=0.8 if on else 0.1,
        weapon_edge_frac=0.1 if on else 0.0,
        weapon_luma_std=0.2 if on else 0.0,
        hud_edge_frac=0.05 if on else 0.0,
    )


def _fuse(frames: list[VisionFrameResult], life: LifecycleFusionConfig | None = None):
    return fuse_game_state(
        frames,
        _timer_config(),
        StateFusionConfig(),
        DeathDetectorConfig(),
        None,
        RespawnDetectorConfig(),
        ActiveGameplayDetectorConfig(),
        life or _life_cfg(),
    )


def test_death_enters_dead_and_starts_episode() -> None:
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(1.5, 1, respawn=_absent()),
    ]
    snaps = _fuse(frames)
    assert snaps[0].player_lifecycle == "dead"
    assert snaps[0].player_alive is False
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    assert [e.event_type for e in events] == [GameEventType.DEATH]


def test_subthreshold_countdown_then_absent_does_not_respawn() -> None:
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(1.1, 1, respawn=_present()),  # only 1 < present_min=2
        _frame(1.2, 2, respawn=_absent()),
        _frame(1.3, 3, respawn=_absent()),
        _frame(1.4, 4, respawn=_absent()),
    ]
    snaps = _fuse(frames)
    assert all(s.player_lifecycle == "dead" for s in snaps)
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    assert GameEventType.RESPAWN not in {e.event_type for e in events}


def test_present_sustained_enters_countdown_without_respawn() -> None:
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(1.1, 1, respawn=_present()),
        _frame(1.2, 2, respawn=_present()),
        _frame(1.3, 3, respawn=_present()),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle == "countdown"
    assert snaps[-1].player_alive is False
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    assert GameEventType.RESPAWN not in {e.event_type for e in events}


def test_present_then_absent_emits_respawn_still_dead() -> None:
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(1.1, 1, respawn=_present()),
        _frame(1.2, 2, respawn=_present()),
        _frame(1.3, 3, respawn=_absent()),
        _frame(1.4, 4, respawn=_absent()),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle == "respawned"
    assert snaps[-1].player_alive is False
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    assert [e.event_type for e in events] == [
        GameEventType.DEATH,
        GameEventType.RESPAWN,
    ]


def test_absent_without_latch_never_respawns() -> None:
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(1.1, 1, respawn=_absent()),
        _frame(1.2, 2, respawn=_absent()),
        _frame(1.3, 3, respawn=_absent()),
    ]
    snaps = _fuse(frames)
    assert all(s.player_lifecycle == "dead" for s in snaps)
    assert GameEventType.RESPAWN not in {
        e.event_type for e in infer_events(snaps, EventFusionConfig(debounce_ms=0))
    }


def test_positive_active_from_respawned_emits_active_again() -> None:
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(1.1, 1, respawn=_present()),
        _frame(1.2, 2, respawn=_present()),
        _frame(1.3, 3, respawn=_absent()),
        _frame(1.4, 4, respawn=_absent()),
        _frame(1.5, 5, active=_active(True), respawn=_absent()),
        _frame(1.6, 6, active=_active(True), respawn=_absent()),
        _frame(1.7, 7, active=_active(True), respawn=_absent()),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle == "alive"
    assert snaps[-1].player_alive is True
    types = [e.event_type for e in infer_events(snaps, EventFusionConfig(debounce_ms=0))]
    assert types == [
        GameEventType.DEATH,
        GameEventType.RESPAWN,
        GameEventType.ACTIVE_AGAIN,
    ]


def test_pure_absence_from_respawned_is_not_active() -> None:
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(1.1, 1, respawn=_present()),
        _frame(1.2, 2, respawn=_present()),
        _frame(1.3, 3, respawn=_absent()),
        _frame(1.4, 4, respawn=_absent()),
        _frame(1.5, 5, respawn=_absent(), active=_active(False)),
        _frame(1.6, 6, respawn=_absent(), active=_active(False)),
        _frame(1.7, 7, respawn=_absent(), active=_active(False)),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle == "respawned"
    assert snaps[-1].player_alive is False
    assert GameEventType.ACTIVE_AGAIN not in {
        e.event_type for e in infer_events(snaps, EventFusionConfig(debounce_ms=0))
    }


def test_active_conflicted_by_countdown_does_not_count() -> None:
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(1.1, 1, respawn=_present()),
        _frame(1.2, 2, respawn=_present()),
        _frame(1.3, 3, respawn=_absent()),
        _frame(1.4, 4, respawn=_absent()),
        # Positive active but countdown present again — conflict; stay respawned.
        _frame(1.5, 5, respawn=_present(), active=_active(True)),
        _frame(1.6, 6, respawn=_present(), active=_active(True)),
        _frame(1.7, 7, respawn=_present(), active=_active(True)),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle == "respawned"
    assert snaps[-1].player_alive is False
    assert GameEventType.ACTIVE_AGAIN not in {
        e.event_type for e in infer_events(snaps, EventFusionConfig(debounce_ms=0))
    }


def test_death_reassertion_from_respawned_resets_episode() -> None:
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(1.1, 1, respawn=_present()),
        _frame(1.2, 2, respawn=_present()),
        _frame(1.3, 3, respawn=_absent()),
        _frame(1.4, 4, respawn=_absent()),
        _frame(2.0, 5, death=DeathReading(detected=True)),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle == "dead"
    assert snaps[-1].player_alive is False


def test_max_elapsed_since_death_goes_unknown_without_respawn() -> None:
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(40.0, 1, respawn=_absent()),
    ]
    snaps = _fuse(frames, _life_cfg(max_respawn_observation_seconds=30.0))
    assert snaps[-1].player_lifecycle == "unknown"
    assert snaps[-1].player_alive is None
    types = {e.event_type for e in infer_events(snaps, EventFusionConfig(debounce_ms=0))}
    assert GameEventType.RESPAWN not in types
    assert GameEventType.ACTIVE_AGAIN not in types


def test_second_death_after_active_again() -> None:
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(1.1, 1, respawn=_present()),
        _frame(1.2, 2, respawn=_present()),
        _frame(1.3, 3, respawn=_absent()),
        _frame(1.4, 4, respawn=_absent()),
        _frame(1.5, 5, active=_active(True), respawn=_absent()),
        _frame(1.6, 6, active=_active(True), respawn=_absent()),
        _frame(1.7, 7, active=_active(True), respawn=_absent()),
        _frame(5.0, 8, death=DeathReading(detected=True)),
    ]
    snaps = _fuse(frames)
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    deaths = [e for e in events if e.event_type == GameEventType.DEATH]
    assert len(deaths) == 2
    assert snaps[-1].player_lifecycle == "dead"


def test_respawn_detector_heuristic_fallback_without_templates() -> None:
    image = np.full((1080, 1920, 3), 40, dtype=np.uint8)
    # Yellow ink only in BR ROI — must not trip present.
    image[972:1074, 1498:1910] = (0, 220, 255)
    detector = RespawnDetector(RespawnDetectorConfig(template_dir=None))
    reading, _ = detector.detect(image, timestamp=1.0)
    assert reading is not None
    assert not reading.detected

    # Dark plate + bright glyphs.
    image[972:1074, 1498:1910] = (20, 20, 20)
    image[990:1040, 1550:1880] = (255, 255, 255)
    reading, _ = detector.detect(image, timestamp=2.0)
    assert reading is not None
    assert reading.detected
    assert reading.evidence_type == "heuristic"


def test_active_gameplay_detector_needs_weapon_and_hud_structure() -> None:
    blank = np.full((1080, 1920, 3), 180, dtype=np.uint8)
    detector = ActiveGameplayDetector(ActiveGameplayDetectorConfig())
    reading, _ = detector.detect(blank, timestamp=1.0)
    assert reading is not None
    assert not reading.detected

    image = blank.copy()
    # Noisy weapon band + edged HUD chrome.
    rng = np.random.default_rng(0)
    wy1, wy2 = int(0.48 * 1080), int(0.90 * 1080)
    wx1, wx2 = int(0.32 * 1920), int(0.68 * 1920)
    noise = rng.integers(0, 255, size=(wy2 - wy1, wx2 - wx1, 3), dtype=np.uint8)
    image[wy1:wy2, wx1:wx2] = noise
    hy1, hy2 = int(0.78 * 1080), int(0.98 * 1080)
    hx1, hx2 = int(0.01 * 1920), int(0.22 * 1920)
    image[hy1:hy2, hx1:hx2] = 30
    cv2.rectangle(image, (hx1 + 10, hy1 + 10), (hx2 - 10, hy2 - 10), (240, 240, 240), 2)
    reading, _ = detector.detect(image, timestamp=2.0)
    assert reading is not None
    assert reading.detected


def test_match_context_gate_blocks_death_outside_hold() -> None:
    """After a timer has been seen, stale match context rejects new deaths."""
    from splatoon3_ai_coach.vision.lifecycle import (
        LifecycleFuser,
        LifecycleObservation,
    )

    fuser = LifecycleFuser(
        LifecycleFusionConfig(
            death_requires_match_context=True,
            match_context_hold_seconds=5.0,
        )
    )
    # Establish match context.
    fuser.step(
        1.0,
        LifecycleObservation(
            death_detected=False,
            countdown_present=None,
            active_detected=True,
            match_context=True,
        ),
    )
    # Death while still in hold → accepted.
    mid = fuser.step(
        3.0,
        LifecycleObservation(
            death_detected=True,
            countdown_present=None,
            active_detected=None,
            match_context=None,
        ),
    )
    assert mid.player_lifecycle == "dead"

    # Recover to alive via unknown path would need active; reset with new fuser
    # and prove out-of-hold rejection.
    fuser2 = LifecycleFuser(
        LifecycleFusionConfig(
            death_requires_match_context=True,
            match_context_hold_seconds=5.0,
        )
    )
    fuser2.step(
        10.0,
        LifecycleObservation(
            death_detected=False,
            countdown_present=None,
            active_detected=None,
            match_context=True,
        ),
    )
    blocked = fuser2.step(
        20.0,
        LifecycleObservation(
            death_detected=True,
            countdown_present=None,
            active_detected=None,
            match_context=None,
        ),
    )
    assert blocked.player_lifecycle == "unknown"
    assert blocked.player_alive is None


def test_respawn_label_fixtures_prefer_detected_over_active() -> None:
    labels = Path("analysis/respawn_roi_review/labels")
    present = list((labels / "countdown_present").glob("*_full.jpg"))
    active = list((labels / "active_gameplay").glob("*_full.jpg"))
    if not present or not active:
        return
    detector = RespawnDetector(RespawnDetectorConfig(template_dir=None))
    present_hits = 0
    for path in present[:5]:
        image = cv2.imread(str(path))
        if image is None:
            continue
        reading, _ = detector.detect(image)
        if reading is not None and reading.detected:
            present_hits += 1
    active_hits = 0
    for path in active[:5]:
        image = cv2.imread(str(path))
        if image is None:
            continue
        reading, _ = detector.detect(image)
        if reading is not None and reading.detected:
            active_hits += 1
    assert present_hits >= 1
    assert active_hits == 0
