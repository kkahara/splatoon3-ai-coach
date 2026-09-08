"""Tests for respawn lifecycle fusion, countdown, and active-gameplay cues."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from splatoon3_ai_coach.config.models import (
    ActiveGameplayDetectorConfig,
    RespawnDetectorConfig,
    DeathDetectorConfig,
    EventFusionConfig,
    LifecycleFusionConfig,
    MapOverlayDetectorConfig,
    StateFusionConfig,
    TimerDetectorConfig,
)
from splatoon3_ai_coach.vision.active_gameplay import ActiveGameplayDetector
from splatoon3_ai_coach.vision.respawn import RespawnDetector
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.models import (
    ActiveGameplayReading,
    MapOverlayReading,
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
        awaiting_control_absent_min_observations=2,
        active_again_min_observations=3,
        active_again_max_gap_observations=1,
        max_respawn_observation_seconds=30.0,
        stale_to_unknown=True,
        death_requires_match_context=False,
    )
    base.update(overrides)
    return LifecycleFusionConfig(**base)  # type: ignore[arg-type]


def _timer_display(seconds: float) -> str:
    """Format remaining seconds as M:SS for test timer readings."""
    whole = int(round(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def _frame(
    timestamp: float,
    index: int,
    *,
    death: DeathReading | None = None,
    respawn: RespawnReading | None = None,
    active: ActiveGameplayReading | None = None,
    map_overlay: MapOverlayReading | None = None,
    timer_seconds: float | None = 125.0,
) -> VisionFrameResult:
    detections: list[DetectorResult] = []
    if timer_seconds is not None:
        detections.append(
            DetectorResult(
                id=f"timer:{index}",
                detector_name="timer",
                detector_version="timer@test",
                confidence=0.9,
                reading=TimerReading(
                    display=_timer_display(timer_seconds),
                    seconds_remaining=timer_seconds,
                ),
            )
        )
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
    if map_overlay is not None:
        detections.append(
            DetectorResult(
                id=f"map:{index}",
                detector_name="map_overlay",
                detector_version="map@test",
                confidence=0.9 if map_overlay.present else 0.55,
                reading=map_overlay,
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
        center_edge_frac=0.08 if on else 0.0,
        center_control=on,
        return_control=on,
    )


def _soft_return() -> ActiveGameplayReading:
    """Weapon+center ok, HUD weak — soft latch exit without full detected."""
    return ActiveGameplayReading(
        detected=False,
        score=0.55,
        weapon_edge_frac=0.08,
        weapon_luma_std=0.15,
        hud_edge_frac=0.005,
        center_edge_frac=0.08,
        center_control=True,
        return_control=True,
    )


def _hud_only() -> ActiveGameplayReading:
    """HUD chrome without weapon+center — mid-tier cue, not return_control."""
    return ActiveGameplayReading(
        detected=True,
        score=0.8,
        weapon_edge_frac=0.01,
        weapon_luma_std=0.01,
        hud_edge_frac=0.05,
        center_edge_frac=0.01,
        center_control=False,
        return_control=False,
    )


def _prod_life() -> LifecycleFusionConfig:
    """default.yaml latch-exit knobs (min=2, mid-tier delay=2.5s)."""
    return _life_cfg(
        active_again_min_observations=2,
        active_again_max_gap_observations=1,
        active_again_mid_tier_delay_seconds=2.5,
    )


def _map(present: bool = True) -> MapOverlayReading:
    return MapOverlayReading(
        present=present,
        map_edge_frac=0.08 if present else 0.01,
        periphery_blur=0.7 if present else 0.2,
        center_tank_edge_frac=0.02 if present else 0.1,
    )


def _held() -> RespawnReading:
    return RespawnReading(
        detected=True,
        confidence=0.7,
        evidence_type="hold",
        template_score=0.4,
    )


def _to_respawned() -> list[VisionFrameResult]:
    """Death → countdown latch → RESPAWNED sequence."""
    return [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(1.1, 1, respawn=_present()),
        _frame(1.2, 2, respawn=_present()),
        _frame(1.3, 3, respawn=_absent()),
        _frame(1.4, 4, respawn=_absent()),
    ]


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
        MapOverlayDetectorConfig(),
    )


def _snap_life(snaps, timestamp: float) -> str:
    """Lifecycle phase of the snapshot nearest ``timestamp``."""
    snap = min(snaps, key=lambda item: abs(item.timestamp - timestamp))
    return str(snap.player_lifecycle)


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
    assert GameEventType.RESPAWN in {e.event_type for e in events}


def test_hold_after_plate_is_countdown_absent() -> None:
    """Landing hold is still a respawn reading but must not keep countdown."""
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(1.1, 1, respawn=_present()),
        _frame(1.2, 2, respawn=_present()),
        _frame(1.3, 3, respawn=_held()),
        _frame(1.4, 4, respawn=_held()),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle == "respawned"
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    assert GameEventType.RESPAWN in {e.event_type for e in events}
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


def test_early_control_during_death_cam_does_not_skip_countdown() -> None:
    """Death-cam HUD/weapon must not leave DEAD before the plate-skip delay."""
    frames = [
        _frame(10.0, 0, death=DeathReading(detected=True)),
        *[
            _frame(10.0 + 0.5 * step, step, respawn=_absent(), active=_active(True))
            for step in range(1, 13)  # 10.5 .. 16.0, delay is 6.5s
        ],
    ]
    snaps = _fuse(frames)
    assert all(s.player_lifecycle == "dead" for s in snaps)
    assert GameEventType.RESPAWN not in {
        e.event_type for e in infer_events(snaps, EventFusionConfig(debounce_ms=0))
    }


def test_plate_less_death_skips_to_respawn_then_active_again() -> None:
    """Water / wipeout: no Respawn-in-N plate, then control returns."""
    frames = [
        _frame(10.0, 0, death=DeathReading(detected=True)),
        *[
            _frame(10.0 + 0.5 * step, step, respawn=_absent(), active=_active(False))
            for step in range(1, 13)
        ],
        _frame(16.5, 13, respawn=_absent(), active=_active(True)),
        _frame(17.0, 14, respawn=_absent(), active=_active(True)),
        _frame(17.5, 15, respawn=_absent(), active=_active(True)),
        _frame(18.0, 16, respawn=_absent(), active=_active(True)),
        _frame(18.5, 17, respawn=_absent(), active=_active(True)),
        _frame(19.0, 18, respawn=_absent(), active=_active(True)),
        _frame(19.5, 19, respawn=_absent(), active=_active(True)),
    ]
    snaps = _fuse(frames)
    assert snaps[0].player_lifecycle == "dead"
    assert _snap_life(snaps, 16.0) == "dead"
    assert _snap_life(snaps, 17.5) == "respawned"
    assert _snap_life(snaps, 18.0) == "awaiting_control"
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    types = [e.event_type for e in events]
    assert types.count(GameEventType.DEATH) == 1
    assert GameEventType.RESPAWN in types
    assert GameEventType.ACTIVE_AGAIN in types
    respawn_at = next(e.start_time for e in events if e.event_type is GameEventType.RESPAWN)
    active_at = next(
        e.start_time for e in events if e.event_type is GameEventType.ACTIVE_AGAIN
    )
    assert respawn_at == pytest.approx(17.5)
    assert active_at > respawn_at
    assert snaps[-1].player_lifecycle == "alive"
    assert snaps[-1].active_gameplay is True


def test_map_overlay_vetoes_plate_less_skip() -> None:
    """Death-map viewing is not control return, even after the skip delay."""
    frames = [
        _frame(10.0, 0, death=DeathReading(detected=True)),
        _frame(16.5, 1, respawn=_absent(), active=_active(True), map_overlay=_map(True)),
        _frame(17.0, 2, respawn=_absent(), active=_active(True), map_overlay=_map(True)),
        _frame(17.5, 3, respawn=_absent(), active=_active(True), map_overlay=_map(True)),
        _frame(18.0, 4, respawn=_absent(), active=_active(True), map_overlay=_map(False)),
        _frame(18.5, 5, respawn=_absent(), active=_active(True), map_overlay=_map(False)),
        _frame(19.0, 6, respawn=_absent(), active=_active(True), map_overlay=_map(False)),
    ]
    snaps = _fuse(frames)
    assert _snap_life(snaps, 17.5) == "dead"
    assert _snap_life(snaps, 19.0) == "respawned"


def test_countdown_plate_still_wins_over_skip() -> None:
    """A normal Respawn-in-N plate must not be stolen by the skip path."""
    frames = [
        _frame(10.0, 0, death=DeathReading(detected=True), active=_active(True)),
        _frame(12.5, 1, respawn=_present(), active=_active(True)),
        _frame(13.0, 2, respawn=_present(), active=_active(True)),
        _frame(13.5, 3, respawn=_present(), active=_active(True)),
        _frame(14.0, 4, respawn=_absent(), active=_active(True)),
        _frame(14.5, 5, respawn=_absent(), active=_active(True)),
    ]
    snaps = _fuse(frames)
    phases = [s.player_lifecycle for s in snaps]
    assert "countdown" in phases
    assert phases[-1] == "respawned"
    assert "respawned" not in phases[:3]


def test_second_death_after_plate_less_recovery() -> None:
    """Skip recovery must re-arm DEATH so a later splat is not swallowed."""
    frames = [
        _frame(10.0, 0, death=DeathReading(detected=True)),
        _frame(16.5, 1, respawn=_absent(), active=_active(True)),
        _frame(17.0, 2, respawn=_absent(), active=_active(True)),
        _frame(17.5, 3, respawn=_absent(), active=_active(True)),
        _frame(18.0, 4, respawn=_absent(), active=_active(True)),
        _frame(18.5, 5, respawn=_absent(), active=_active(True)),
        _frame(19.0, 6, respawn=_absent(), active=_active(True)),
        _frame(19.5, 7, respawn=_absent(), active=_active(True)),
        _frame(20.0, 8, death=DeathReading(detected=True), active=_active(True)),
    ]
    snaps = _fuse(frames)
    assert snaps[-2].player_lifecycle == "alive"
    assert snaps[-1].player_lifecycle == "dead"
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    deaths = [e.start_time for e in events if e.event_type is GameEventType.DEATH]
    assert deaths == pytest.approx([10.0, 20.0])


def test_persistent_ouch_does_not_bounce_countdown_to_dead() -> None:
    """22:35-09 episode-1 shape: re-fired Ouch must not reset the episode.

    The Ouch glyph stays on screen through the whole death and the detector
    debounce re-asserts it at 61.5 and 64.5 while the respawn plate is up.
    Those are continued death evidence, not new death episodes.
    """
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(1.1, 1),  # detector debounced: no reading at all
        _frame(1.2, 2, respawn=_present()),
        # Ouch re-fires while the plate is still up (the 61.5 frame).
        _frame(1.3, 3, death=DeathReading(detected=True), respawn=_present()),
        _frame(1.4, 4, respawn=_present()),
        # Ouch re-fires again at the end of the plate (the 64.5 frame).
        _frame(1.5, 5, death=DeathReading(detected=True), respawn=_present()),
        _frame(1.6, 6, respawn=_held()),
        _frame(1.7, 7, respawn=_held()),
    ]
    snaps = _fuse(frames)

    phases = [s.player_lifecycle for s in snaps]
    assert "countdown" in phases
    first_countdown = phases.index("countdown")
    # The whole point: no bounce back to DEAD once the episode is counting down.
    assert "dead" not in phases[first_countdown:]
    # The latch survives both re-fires, so RESPAWNED is still reachable.
    assert all(
        s.countdown_confirmed_this_death_episode for s in snaps[first_countdown:]
    )
    assert snaps[-1].player_lifecycle == "respawned"

    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    types = [e.event_type for e in events]
    assert types.count(GameEventType.DEATH) == 1
    assert GameEventType.RESPAWN in types


def test_stale_recovery_emits_no_second_death_until_alive() -> None:
    """Stale recovery may reset lifecycle, but DEATH stays one-shot.

    ``dead -> unknown -> dead`` is a new *lifecycle* episode by definition;
    lifecycle cannot know the evidence is the same death. The one-shot
    guarantee lives in the event layer instead.
    """
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(10.0, 1, death=DeathReading(detected=True)),
        _frame(25.0, 2, death=DeathReading(detected=True)),
        # Past max_respawn_observation_seconds=30: the escape must be reachable
        # even though death evidence never dropped.
        _frame(40.0, 3, death=DeathReading(detected=True)),
        _frame(45.0, 4, death=DeathReading(detected=True)),
    ]
    snaps = _fuse(frames)

    phases = [s.player_lifecycle for s in snaps]
    assert "unknown" in phases[1:], "stale escape never fired"
    assert phases[-1] == "dead", "re-entry from unknown is a new episode"

    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    assert [e.event_type for e in events].count(GameEventType.DEATH) == 1


def test_death_after_observed_alive_emits_a_second_death() -> None:
    """The boundary condition: being seen alive re-arms the DEATH edge."""
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(40.0, 1, death=DeathReading(detected=True)),  # stale → unknown
        _frame(40.1, 2, active=_active(True)),
        _frame(40.2, 3, active=_active(True)),
        _frame(40.3, 4, active=_active(True)),
        _frame(40.4, 5, death=DeathReading(detected=True)),
    ]
    snaps = _fuse(frames)

    assert any(s.player_lifecycle == "alive" for s in snaps)
    assert snaps[-1].player_lifecycle == "dead"
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    assert [e.event_type for e in events].count(GameEventType.DEATH) == 2


def test_awaiting_control_then_control_emits_active_again() -> None:
    """Episode-76 shape: RESPAWNED → AWAITING_CONTROL → ALIVE + ACTIVE_AGAIN."""
    frames = _to_respawned() + [
        _frame(1.5, 5, respawn=_absent(), active=_active(False), map_overlay=_map(False)),
        _frame(1.6, 6, respawn=_absent(), active=_active(False), map_overlay=_map(False)),
        _frame(1.7, 7, respawn=_absent(), active=_active(True), map_overlay=_map(False)),
        _frame(1.8, 8, respawn=_absent(), active=_active(True), map_overlay=_map(False)),
        _frame(1.9, 9, respawn=_absent(), active=_active(True), map_overlay=_map(False)),
    ]
    snaps = _fuse(frames)
    assert any(s.player_lifecycle == "awaiting_control" for s in snaps[5:8])
    assert snaps[-1].player_lifecycle == "alive"
    assert snaps[-1].player_alive is True
    types = [e.event_type for e in infer_events(snaps, EventFusionConfig(debounce_ms=0))]
    assert types == [
        GameEventType.DEATH,
        GameEventType.RESPAWN,
        GameEventType.ACTIVE_AGAIN,
    ]


def test_control_looking_while_respawned_is_not_active_again() -> None:
    """Episode-189 shape: RESPAWNED + false control cue must not ACTIVE_AGAIN."""
    frames = _to_respawned() + [
        _frame(1.5, 5, respawn=_absent(), active=_active(True), map_overlay=_map(True)),
        _frame(1.6, 6, respawn=_absent(), active=_active(True), map_overlay=_map(True)),
        _frame(1.7, 7, respawn=_absent(), active=_active(True), map_overlay=_map(True)),
    ]
    snaps = _fuse(frames)
    # Map evidence is recorded but does not drive lifecycle.
    assert snaps[-1].map_overlay_present is True
    assert snaps[-1].player_alive is False
    assert GameEventType.ACTIVE_AGAIN not in {
        e.event_type for e in infer_events(snaps, EventFusionConfig(debounce_ms=0))
    }


def test_soft_return_control_exits_awaiting_without_full_detected() -> None:
    """Weapon+center (HUD optional) is enough to leave awaiting_control."""
    frames = _to_respawned() + [
        _frame(1.5, 5, respawn=_absent(), active=_active(False)),
        _frame(1.6, 6, respawn=_absent(), active=_active(False)),
        _frame(1.7, 7, respawn=_absent(), active=_soft_return()),
        _frame(1.8, 8, respawn=_absent(), active=_soft_return()),
        _frame(1.9, 9, respawn=_absent(), active=_soft_return()),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle == "alive"
    assert snaps[-1].active_gameplay is True
    assert snaps[-1].match_phase == "in_match"
    assert GameEventType.ACTIVE_AGAIN in {
        e.event_type for e in infer_events(snaps, EventFusionConfig(debounce_ms=0))
    }


def test_one_frame_gap_keeps_return_streak() -> None:
    """A single miss does not clear the present streak (max_gap=1)."""
    frames = _to_respawned() + [
        _frame(1.5, 5, respawn=_absent(), active=_active(False)),
        _frame(1.6, 6, respawn=_absent(), active=_active(False)),
        _frame(1.7, 7, respawn=_absent(), active=_soft_return()),
        _frame(1.8, 8, respawn=_absent(), active=_active(False)),  # one gap
        _frame(1.9, 9, respawn=_absent(), active=_soft_return()),
        _frame(2.0, 10, respawn=_absent(), active=_soft_return()),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle == "alive"


def test_two_frame_gap_resets_return_streak() -> None:
    """Two consecutive misses clear the present streak when max_gap=1."""
    frames = _to_respawned() + [
        _frame(1.5, 5, respawn=_absent(), active=_active(False)),
        _frame(1.6, 6, respawn=_absent(), active=_active(False)),
        _frame(1.7, 7, respawn=_absent(), active=_soft_return()),
        _frame(1.8, 8, respawn=_absent(), active=_active(False)),
        _frame(1.9, 9, respawn=_absent(), active=_active(False)),  # 2nd gap → reset
        _frame(2.0, 10, respawn=_absent(), active=_soft_return()),
        _frame(2.1, 11, respawn=_absent(), active=_soft_return()),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle == "awaiting_control"


def test_map_present_does_not_demote_awaiting_control() -> None:
    """Map flicker must not bounce awaiting_control → respawned (no 2nd RESPAWN)."""
    frames = _to_respawned() + [
        _frame(1.5, 5, respawn=_absent(), active=_active(False)),
        _frame(1.6, 6, respawn=_absent(), active=_active(False)),
        # Map flicker while latched — stay awaiting_control.
        _frame(1.7, 7, respawn=_absent(), active=_active(False), map_overlay=_map(True)),
        _frame(1.8, 8, respawn=_absent(), active=_active(True)),
        _frame(1.9, 9, respawn=_absent(), active=_active(True)),
        _frame(2.0, 10, respawn=_absent(), active=_active(True)),
    ]
    snaps = _fuse(frames)
    assert snaps[7].player_lifecycle == "awaiting_control"
    assert snaps[7].map_overlay_present is True
    assert snaps[-1].player_lifecycle == "alive"
    types = [e.event_type for e in infer_events(snaps, EventFusionConfig(debounce_ms=0))]
    assert types.count(GameEventType.RESPAWN) == 1
    assert types[-1] == GameEventType.ACTIVE_AGAIN


def test_control_looking_after_respawn_still_goes_through_latch() -> None:
    """Control-looking frames do not skip the awaiting_control latch."""
    frames = _to_respawned() + [
        _frame(1.5, 5, respawn=_absent(), active=_active(True)),
        _frame(1.6, 6, respawn=_absent(), active=_active(True)),
        _frame(1.7, 7, respawn=_absent(), active=_active(True)),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle == "awaiting_control"
    assert snaps[-1].player_alive is False
    assert GameEventType.ACTIVE_AGAIN not in {
        e.event_type for e in infer_events(snaps, EventFusionConfig(debounce_ms=0))
    }


def test_missing_active_observation_does_not_return_control() -> None:
    """None ≠ control present; missing frames must not exit the latch."""
    frames = _to_respawned() + [
        _frame(1.5, 5, respawn=_absent()),  # active_detected=None
        _frame(1.6, 6, respawn=_absent()),
        _frame(1.7, 7, respawn=_absent()),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle == "awaiting_control"
    assert snaps[-1].player_alive is False
    assert GameEventType.ACTIVE_AGAIN not in {
        e.event_type for e in infer_events(snaps, EventFusionConfig(debounce_ms=0))
    }


def test_pure_absence_from_respawned_enters_awaiting_control_not_alive() -> None:
    frames = _to_respawned() + [
        _frame(1.5, 5, respawn=_absent(), active=_active(False)),
        _frame(1.6, 6, respawn=_absent(), active=_active(False)),
        _frame(1.7, 7, respawn=_absent(), active=_active(False)),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle == "awaiting_control"
    assert snaps[-1].player_alive is False
    assert snaps[-1].awaiting_control_confirmed_this_death_episode is True
    assert GameEventType.ACTIVE_AGAIN not in {
        e.event_type for e in infer_events(snaps, EventFusionConfig(debounce_ms=0))
    }


def test_mid_tier_hud_does_not_exit_before_delay() -> None:
    """HUD+timer right after the plate must not ACTIVE_AGAIN (135.0 hold)."""
    frames = _to_respawned() + [
        _frame(10.0, 5, respawn=_absent(), active=_hud_only(), timer_seconds=90.0),
        _frame(10.5, 6, respawn=_absent(), active=_hud_only(), timer_seconds=89.0),
        _frame(11.0, 7, respawn=_absent(), active=_hud_only(), timer_seconds=89.0),
        _frame(11.5, 8, respawn=_absent(), active=_hud_only(), timer_seconds=88.0),
        _frame(12.0, 9, respawn=_absent(), active=_hud_only(), timer_seconds=88.0),
    ]
    snaps = _fuse(frames, _prod_life())
    by_ts = {round(s.timestamp, 1): s for s in snaps}
    assert by_ts[11.0].player_lifecycle == "awaiting_control"
    assert by_ts[11.0].active_gameplay is not True
    assert snaps[-1].player_lifecycle == "awaiting_control"
    assert GameEventType.ACTIVE_AGAIN not in {
        e.event_type for e in infer_events(snaps, EventFusionConfig(debounce_ms=0))
    }


def test_mid_tier_exits_after_delay_and_two_observations() -> None:
    """HUD+timer+no veto, two frames after the 2.5s delay → ALIVE."""
    frames = _to_respawned() + [
        _frame(10.0, 5, respawn=_absent(), active=_hud_only(), timer_seconds=90.0),
        _frame(10.5, 6, respawn=_absent(), active=_hud_only(), timer_seconds=89.0),
        _frame(11.0, 7, respawn=_absent(), active=_hud_only(), timer_seconds=89.0),
        _frame(12.5, 8, respawn=_absent(), active=_hud_only(), timer_seconds=87.0),
        _frame(13.0, 9, respawn=_absent(), active=_hud_only(), timer_seconds=87.0),
    ]
    snaps = _fuse(frames, _prod_life())
    by_ts = {round(s.timestamp, 1): s for s in snaps}
    assert by_ts[11.0].player_lifecycle == "awaiting_control"
    assert snaps[-1].player_lifecycle == "alive"
    assert snaps[-1].active_gameplay is True
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    actives = [e.start_time for e in events if e.event_type is GameEventType.ACTIVE_AGAIN]
    assert actives == [13.0]


def test_mid_tier_blocked_by_map_or_missing_timer() -> None:
    """Map-view or missing timer vetoes the HUD shortcut; latch stays."""
    mapped = _to_respawned() + [
        _frame(10.0, 5, respawn=_absent(), active=_hud_only(), timer_seconds=90.0),
        _frame(12.5, 6, respawn=_absent(), active=_hud_only(), timer_seconds=87.0, map_overlay=_map(True)),
        _frame(13.0, 7, respawn=_absent(), active=_hud_only(), timer_seconds=87.0, map_overlay=_map(True)),
    ]
    mapped_snaps = _fuse(mapped, _prod_life())
    assert mapped_snaps[-1].player_lifecycle == "awaiting_control"
    assert mapped_snaps[-1].map_overlay_present is True

    no_timer = _to_respawned() + [
        _frame(10.0, 5, respawn=_absent(), active=_hud_only(), timer_seconds=90.0),
        _frame(12.5, 6, respawn=_absent(), active=_hud_only(), timer_seconds=None),
        _frame(13.0, 7, respawn=_absent(), active=_hud_only(), timer_seconds=None),
    ]
    assert _fuse(no_timer, _prod_life())[-1].player_lifecycle == "awaiting_control"


def test_return_control_still_exits_without_waiting_out_delay() -> None:
    """Weapon+center keeps the fast latch exit used by the 67.5 episode."""
    frames = _to_respawned() + [
        _frame(10.0, 5, respawn=_absent(), active=_active(False), timer_seconds=148.0),
        _frame(10.5, 6, respawn=_absent(), active=_soft_return(), timer_seconds=148.0),
        _frame(11.0, 7, respawn=_absent(), active=_soft_return(), timer_seconds=147.0),
    ]
    snaps = _fuse(frames, _prod_life())
    assert snaps[-1].player_lifecycle == "alive"
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    actives = [e.start_time for e in events if e.event_type is GameEventType.ACTIVE_AGAIN]
    assert actives == [11.0]


def test_active_conflicted_by_countdown_returns_to_countdown() -> None:
    frames = _to_respawned() + [
        # Positive active but countdown present again → back to COUNTDOWN.
        _frame(1.5, 5, respawn=_present(), active=_active(True)),
        _frame(1.6, 6, respawn=_present(), active=_active(True)),
        _frame(1.7, 7, respawn=_present(), active=_active(True)),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle == "countdown"
    assert snaps[-1].player_alive is False
    assert GameEventType.ACTIVE_AGAIN not in {
        e.event_type for e in infer_events(snaps, EventFusionConfig(debounce_ms=0))
    }


def test_death_evidence_from_respawned_does_not_reset_episode() -> None:
    """Late Ouch during recovery is continued evidence, not a new episode."""
    frames = [
        _frame(1.0, 0, death=DeathReading(detected=True)),
        _frame(1.1, 1, respawn=_present()),
        _frame(1.2, 2, respawn=_present()),
        _frame(1.3, 3, respawn=_absent()),
        _frame(1.4, 4, respawn=_absent()),
        _frame(2.0, 5, death=DeathReading(detected=True)),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle != "dead"
    assert snaps[-1].player_alive is False
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    assert [e.event_type for e in events].count(GameEventType.DEATH) == 1


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
    frames = _to_respawned() + [
        _frame(1.5, 5, respawn=_absent(), active=_active(False)),
        _frame(1.6, 6, respawn=_absent(), active=_active(False)),
        _frame(1.7, 7, respawn=_absent(), active=_active(True)),
        _frame(1.8, 8, respawn=_absent(), active=_active(True)),
        _frame(1.9, 9, respawn=_absent(), active=_active(True)),
        _frame(5.0, 10, death=DeathReading(detected=True)),
    ]
    snaps = _fuse(frames)
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    deaths = [e for e in events if e.event_type == GameEventType.DEATH]
    assert len(deaths) == 2
    assert snaps[-1].player_lifecycle == "dead"


def test_respawn_detector_heuristic_fallback_without_templates() -> None:
    image = np.full((1080, 1920, 3), 40, dtype=np.uint8)
    y1, y2 = int(0.825 * 1080), int(0.950 * 1080)
    x1, x2 = int(0.840 * 1920), 1920
    # Yellow ink only in BR ROI — must not trip present.
    image[y1:y2, x1:x2] = (0, 220, 255)
    detector = RespawnDetector(RespawnDetectorConfig(template_dir=None))
    reading, _ = detector.detect(image, timestamp=1.0)
    assert reading is not None
    assert not reading.detected

    # Dark plate + compact bright glyphs (not a fully white ROI).
    image[y1:y2, x1:x2] = (20, 20, 20)
    gy1 = y1 + (y2 - y1) // 3
    gy2 = y1 + 2 * (y2 - y1) // 3
    image[gy1:gy2, x1 + 40 : x2 - 40] = (255, 255, 255)
    reading, _ = detector.detect(image, timestamp=2.0)
    assert reading is not None
    assert reading.detected
    assert reading.evidence_type == "heuristic"


def _hud_chrome_image() -> np.ndarray:
    """Blank frame with bottom-left HUD edges only (squid-swim analogue)."""
    image = np.full((1080, 1920, 3), 180, dtype=np.uint8)
    hy1, hy2 = int(0.78 * 1080), int(0.98 * 1080)
    hx1, hx2 = int(0.01 * 1920), int(0.22 * 1920)
    image[hy1:hy2, hx1:hx2] = 30
    cv2.rectangle(image, (hx1 + 10, hy1 + 10), (hx2 - 10, hy2 - 10), (240, 240, 240), 2)
    return image


def test_active_gameplay_detector_trips_on_hud() -> None:
    blank = np.full((1080, 1920, 3), 180, dtype=np.uint8)
    detector = ActiveGameplayDetector(ActiveGameplayDetectorConfig())
    reading, _ = detector.detect(blank, timestamp=1.0)
    assert reading is not None
    assert not reading.detected

    reading, _ = detector.detect(_hud_chrome_image(), timestamp=2.0)
    assert reading is not None
    assert reading.detected
    assert reading.return_control is False


def test_active_gameplay_full_structure_sets_return_control() -> None:
    image = _hud_chrome_image()
    rng = np.random.default_rng(0)
    wy1, wy2 = int(0.48 * 1080), int(0.90 * 1080)
    wx1, wx2 = int(0.32 * 1920), int(0.68 * 1920)
    image[wy1:wy2, wx1:wx2] = rng.integers(
        0, 255, size=(wy2 - wy1, wx2 - wx1, 3), dtype=np.uint8
    )
    cy1, cy2 = int(0.40 * 1080), int(0.62 * 1080)
    cx1, cx2 = int(0.46 * 1920), int(0.54 * 1920)
    image[cy1:cy2, cx1:cx2] = rng.integers(
        0, 255, size=(cy2 - cy1, cx2 - cx1, 3), dtype=np.uint8
    )
    detector = ActiveGameplayDetector(ActiveGameplayDetectorConfig())
    reading, _ = detector.detect(image, timestamp=2.0)
    assert reading is not None
    assert reading.detected
    assert reading.center_control is True
    assert reading.return_control is True


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
            timer_seconds=125.0,
        ),
    )
    # Death while still in hold → accepted.
    mid = fuser.step(
        3.0,
        LifecycleObservation(
            death_detected=True,
            countdown_present=None,
            active_detected=None,
            timer_seconds=None,
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
            timer_seconds=125.0,
        ),
    )
    blocked = fuser2.step(
        20.0,
        LifecycleObservation(
            death_detected=True,
            countdown_present=None,
            active_detected=None,
            timer_seconds=None,
        ),
    )
    assert blocked.player_lifecycle == "unknown"
    assert blocked.player_alive is None


def test_death_and_hud_evidence_can_coexist() -> None:
    """Detectors are not mutually exclusive; lifecycle owns DEAD vs ACTIVE."""
    frames = [
        _frame(1.0, 0, active=_active(True)),
        _frame(1.5, 1, active=_active(True)),
        _frame(2.0, 2, active=_active(True)),
        _frame(2.5, 3, death=DeathReading(detected=True), active=_active(True)),
    ]
    snaps = _fuse(frames)
    assert snaps[-1].player_lifecycle == "dead"
    assert snaps[-1].active_gameplay is False
    assert snaps[-1].match_phase == "in_match"
    death_frame = frames[-1]
    active_reading = death_frame.detections[-1].reading
    assert getattr(active_reading, "detected", False) is True


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
