"""Tests for Phase 3+ scaffolding."""

from pathlib import Path

from splatoon3_ai_coach.analysis.session import GameSession
from splatoon3_ai_coach.coach.prompts import load_system_prompt
from splatoon3_ai_coach.config.models import VisionConfig
from splatoon3_ai_coach.vision.models import GameEventType, TimerReading
from splatoon3_ai_coach.vision.registry import build_detectors


def test_timer_detector_is_registered_from_config(vision_config: VisionConfig) -> None:
    detectors = build_detectors(vision_config)
    assert any(detector.name == "timer" for detector in detectors)


def test_death_detector_is_registered_when_enabled(vision_config: VisionConfig) -> None:
    vision_config.enabled_detectors = ["timer", "death"]
    detectors = build_detectors(vision_config)
    assert {detector.name for detector in detectors} == {"timer", "death"}


def test_splat_detector_is_registered_when_enabled(vision_config: VisionConfig) -> None:
    vision_config.enabled_detectors = ["timer", "splat"]
    detectors = build_detectors(vision_config)
    assert {detector.name for detector in detectors} == {"timer", "splat"}


def test_respawn_active_and_map_detectors_register_when_enabled(
    vision_config: VisionConfig,
) -> None:
    vision_config.enabled_detectors = ["respawn", "active_gameplay", "map_overlay"]
    detectors = build_detectors(vision_config)
    assert {detector.name for detector in detectors} == {
        "respawn",
        "active_gameplay",
        "map_overlay",
    }


def test_timer_reading_has_display_and_seconds() -> None:
    reading = TimerReading(display="2:07", seconds_remaining=127.0)
    assert reading.display == "2:07"
    assert reading.seconds_remaining == 127.0


def test_game_event_types_are_distinct_from_trigger_types() -> None:
    assert GameEventType.DEATH.value == "death"
    assert GameEventType.RESPAWN.value == "respawn"
    assert GameEventType.ACTIVE_AGAIN.value == "active_again"
    assert GameEventType.SPLAT.value == "splat"
    assert GameEventType.MAP_OVERLAY.value == "map_overlay"


def test_coach_system_prompt_loads_from_package() -> None:
    prompt = load_system_prompt()
    assert "Splatoon 3 coach" in prompt


def test_game_session_accepts_empty_timeline(tmp_path: Path) -> None:
    session = GameSession(video=tmp_path / "game.mp4", duration_seconds=120.0)
    assert session.timeline == []
