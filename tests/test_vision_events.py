"""Authoritative post-fusion event timeline."""

from __future__ import annotations

from splatoon3_ai_coach.config.models import EventFusionConfig
from splatoon3_ai_coach.vision.events import infer_events
from splatoon3_ai_coach.vision.models import (
    GameEventReason,
    GameEventSource,
    GameEventType,
    GameStateSnapshot,
    SplatBannerInstance,
    SourceFrameReference,
)


def _snap(
    timestamp: float,
    *,
    alive: bool | None = None,
    life: str = "unknown",
    map_present: bool | None = None,
    latch: bool | None = None,
    instances: list[SplatBannerInstance] | None = None,
    evidence: list[str] | None = None,
    frame_id: str | None = None,
) -> GameStateSnapshot:
    source = None
    if frame_id is not None:
        source = SourceFrameReference(
            frame_id=frame_id, timestamp=timestamp, source_frame_index=int(timestamp * 2)
        )
    return GameStateSnapshot(
        timestamp=timestamp,
        player_alive=alive,
        player_lifecycle=life,  # type: ignore[arg-type]
        active_gameplay=life == "alive",
        map_overlay_present=map_present,
        countdown_confirmed_this_death_episode=latch,
        splat_instances=instances or [],
        evidence_ids=evidence or [],
        source_frame=source,
        quality="observed",
    )


def _banner(fingerprint: str) -> SplatBannerInstance:
    return SplatBannerInstance(fingerprint=fingerprint, slot_y=0.5)


def test_lifecycle_events_carry_source_reason_and_frame() -> None:
    snaps = [
        _snap(1.0, alive=True, life="alive", frame_id="f:1", evidence=["hud:1"]),
        _snap(2.0, alive=False, life="dead", frame_id="f:2", evidence=["death:2"]),
        _snap(3.0, alive=False, life="countdown", latch=True, evidence=["rs:3"]),
        _snap(4.0, alive=False, life="respawned", latch=True, evidence=["rs:4"]),
        _snap(4.5, alive=False, life="awaiting_control", latch=True),
        _snap(5.0, alive=True, life="alive", frame_id="f:5", evidence=["ag:5"]),
    ]
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    types = [e.event_type for e in events]
    assert types == [
        GameEventType.DEATH,
        GameEventType.RESPAWN,
        GameEventType.ACTIVE_AGAIN,
    ]
    death, respawn, active = events
    assert death.source is GameEventSource.LIFECYCLE
    assert death.reason is GameEventReason.ALIVE_TO_DEAD
    assert death.from_lifecycle == "alive"
    assert death.to_lifecycle == "dead"
    assert death.evidence_ids == ["death:2"]
    assert death.source_frames[0].frame_id == "f:2"
    assert respawn.reason is GameEventReason.COUNTDOWN_PLATE_ENDED
    assert respawn.from_lifecycle == "countdown"
    assert active.reason is GameEventReason.AWAITING_CONTROL_TO_ALIVE
    assert active.source_frames[0].frame_id == "f:5"


def test_skip_countdown_respawn_reason() -> None:
    snaps = [
        _snap(1.0, alive=True, life="alive"),
        _snap(2.0, alive=False, life="dead", latch=False),
        _snap(8.5, alive=False, life="respawned", latch=False, evidence=["ag:8"]),
        _snap(9.0, alive=False, life="awaiting_control"),
        _snap(9.5, alive=True, life="alive"),
    ]
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    respawn = next(e for e in events if e.event_type is GameEventType.RESPAWN)
    assert respawn.reason is GameEventReason.SKIP_COUNTDOWN_CONTROL
    assert respawn.to_lifecycle == "respawned"


def test_splat_episode_is_fingerprint_not_debounce_window() -> None:
    """Two stacked banners stay two SPLAT events even if debounce is 5s."""
    snaps = [
        _snap(
            1.0,
            instances=[
                _banner("aa" * 8),
                SplatBannerInstance(fingerprint="bb" * 8, slot_y=0.85),
            ],
            evidence=["splat:1"],
        ),
        _snap(
            1.2,
            instances=[
                _banner("aa" * 8),
                SplatBannerInstance(fingerprint="bb" * 8, slot_y=0.85),
            ],
            evidence=["splat:2"],
        ),
    ]
    events = infer_events(snaps, EventFusionConfig(debounce_ms=5000))
    splats = [e for e in events if e.event_type is GameEventType.SPLAT]
    assert [e.start_time for e in splats] == [1.0, 1.0]
    assert splats[0].source is GameEventSource.SPLAT_EPISODE
    assert splats[0].reason is GameEventReason.SPLAT_INSTANCE_OPENED
    assert {s.splat_fingerprint for s in splats} == {"aa" * 8, "bb" * 8}


def test_same_banner_fingerprint_is_one_splat_episode() -> None:
    snaps = [
        _snap(1.0, instances=[_banner("aa" * 8)]),
        _snap(1.2, instances=[_banner("aa" * 8)]),
        _snap(1.4, instances=[_banner("aa" * 8)]),
    ]
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    splats = [e for e in events if e.event_type is GameEventType.SPLAT]
    assert [e.start_time for e in splats] == [1.0]


def test_map_overlay_interval_from_fused_state() -> None:
    snaps = [
        _snap(1.0, map_present=False),
        _snap(2.0, map_present=True, evidence=["map:2"], frame_id="f:2"),
        _snap(2.5, map_present=True),
        _snap(3.0, map_present=False),
        _snap(4.0, map_present=True, evidence=["map:4"]),
        _snap(4.5, map_present=True),
    ]
    events = infer_events(snaps, EventFusionConfig(debounce_ms=0))
    maps = [e for e in events if e.event_type is GameEventType.MAP_OVERLAY]
    assert len(maps) == 2
    assert maps[0].source is GameEventSource.STATE
    assert maps[0].reason is GameEventReason.MAP_OVERLAY_PRESENT
    assert maps[0].start_time == 2.0
    assert maps[0].end_time == 3.0
    assert maps[0].source_frames[0].frame_id == "f:2"
    assert maps[1].start_time == 4.0
    assert maps[1].end_time == 4.5
