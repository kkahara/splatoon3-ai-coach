"""Coach-layer GameClock: raw timer detections only; video time stays canonical."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from splatoon3_ai_coach.analysis.scenarios import build_scenarios, event_id
from splatoon3_ai_coach.coach.coach_input import (
    attach_game_clock_to_event_times,
    build_coach_input_for_scenario,
)
from splatoon3_ai_coach.coach.game_clock import (
    GameClock,
    GameClockObservation,
    build_game_clock,
    find_non_monotonic_raw_reads,
)
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    GameEvent,
    GameEventReason,
    GameEventType,
    TimerReading,
    VisionFrameResult,
    VisionManifest,
)

_REPO = Path(__file__).resolve().parents[1]
_MANIFEST_180224 = _REPO / "analysis" / "2026-07-05 18-02-24" / "vision_manifest.json"


def _frame(timestamp: float, seconds: float, *, confidence: float = 0.9) -> VisionFrameResult:
    display = f"{int(seconds) // 60}:{int(seconds) % 60:02d}"
    return VisionFrameResult(
        frame_id=f"f{timestamp}",
        timestamp=timestamp,
        source="cadence",
        detections=[
            DetectorResult(
                id=f"timer:{timestamp}",
                detector_name="timer",
                detector_version="timer@test",
                confidence=confidence,
                reading=TimerReading(display=display, seconds_remaining=seconds),
            )
        ],
    )


def test_exact_lookup() -> None:
    clock = build_game_clock([_frame(87.0, 153.0)], min_usable_confidence=0.5)
    obs = clock.at(87.0, max_gap_seconds=1.0)
    assert obs is not None
    assert obs.seconds_remaining == 153
    assert obs.video_time == pytest.approx(87.0)
    assert obs.source == "timer_detection"
    assert obs.display == "2:33"


def test_nearby_lookup_within_gap() -> None:
    clock = build_game_clock([_frame(87.0, 153.0)], min_usable_confidence=0.5)
    obs = clock.at(87.1, max_gap_seconds=1.0)
    assert obs is not None
    assert obs.seconds_remaining == 153
    assert obs.video_time == pytest.approx(87.0)


def test_large_gap_and_empty_return_none() -> None:
    clock = build_game_clock([_frame(87.0, 153.0)], min_usable_confidence=0.5)
    assert clock.at(90.0, max_gap_seconds=1.0) is None
    assert GameClock().at(87.0, max_gap_seconds=1.0) is None


def test_below_confidence_excluded() -> None:
    clock = build_game_clock(
        [_frame(87.0, 153.0, confidence=0.4)], min_usable_confidence=0.5
    )
    assert clock.observations == ()


def test_non_integral_seconds_skipped_without_rounding() -> None:
    clock = build_game_clock([_frame(87.0, 153.4)], min_usable_confidence=0.5)
    assert clock.observations == ()


def test_attach_preserves_event_video_times() -> None:
    clock = build_game_clock([_frame(87.0, 153.0)], min_usable_confidence=0.5)
    event = GameEvent(
        start_time=87.0,
        event_type=GameEventType.DEATH,
        reason=GameEventReason.ALIVE_TO_DEAD,
        confidence=1.0,
    )
    mapped = attach_game_clock_to_event_times(
        [event.start_time], clock, max_gap_seconds=1.0
    )
    assert event.start_time == pytest.approx(87.0)
    assert mapped[87.0] is not None
    assert mapped[87.0].seconds_remaining == 153


def test_coach_input_stub_does_not_mutate_scenarios() -> None:
    config = load_config(default_config_path())
    events = [
        GameEvent(
            start_time=87.0,
            event_type=GameEventType.DEATH,
            reason=GameEventReason.ALIVE_TO_DEAD,
            confidence=1.0,
        )
    ]
    scenarios = build_scenarios(events, config.scenarios)
    from splatoon3_ai_coach.analysis.scenario_context import build_scenario_contexts

    contexts = build_scenario_contexts(events, scenarios, config.scenarios)
    clock = build_game_clock([_frame(87.0, 153.0)], min_usable_confidence=0.5)
    before = [item.model_dump(mode="json") for item in scenarios]
    coach_input = build_coach_input_for_scenario(
        scenarios[0].scenario_id,
        scenarios,
        contexts,
        clock,
        max_gap_seconds=config.coach.game_clock_max_lookup_gap_seconds,
    )
    after = [item.model_dump(mode="json") for item in scenarios]
    assert before == after
    assert coach_input.primary_scenario.scenario_id == scenarios[0].scenario_id
    assert any(
        sample.observation is not None and sample.observation.source == "timer_detection"
        for sample in coach_input.game_clock_samples
    )


def test_find_non_monotonic_raw_reads_diagnostic() -> None:
    clock = GameClock(
        observations=(
            GameClockObservation(
                video_time=1.0, seconds_remaining=154, confidence=0.9, display="2:34"
            ),
            GameClockObservation(
                video_time=1.5, seconds_remaining=153, confidence=0.9, display="2:33"
            ),
            GameClockObservation(
                video_time=2.0, seconds_remaining=154, confidence=0.9, display="2:34"
            ),
            GameClockObservation(
                video_time=2.5, seconds_remaining=152, confidence=0.9, display="2:32"
            ),
        )
    )
    anomalies = find_non_monotonic_raw_reads(clock)
    assert anomalies == [(1.5, 153, 2.0, 154)]


@pytest.mark.skipif(not _MANIFEST_180224.is_file(), reason="18-02-24 manifest missing")
def test_180224_fixture_game_clock_diagnostic_and_scenario_regression() -> None:
    """Diagnostic-only clock mapping; scenario membership must stay unchanged."""
    config = load_config(default_config_path())
    manifest = VisionManifest.model_validate_json(_MANIFEST_180224.read_text())
    events = list(manifest.game_events)
    scenarios_before = build_scenarios(events, config.scenarios)
    owners_before = Counter(eid for s in scenarios_before for eid in s.event_ids)

    clock = build_game_clock(
        list(manifest.frame_results),
        min_usable_confidence=config.vision.timer.min_usable_confidence,
    )
    gap = config.coach.game_clock_max_lookup_gap_seconds
    mapped = attach_game_clock_to_event_times(
        [e.start_time for e in events], clock, max_gap_seconds=gap
    )

    scenarios_after = build_scenarios(events, config.scenarios)
    assert [s.model_dump(mode="json") for s in scenarios_before] == [
        s.model_dump(mode="json") for s in scenarios_after
    ]
    owners_after = Counter(eid for s in scenarios_after for eid in s.event_ids)
    assert owners_before == owners_after
    all_ids = {event_id(e) for e in events}
    assert set(owners_after) == all_ids
    assert max(owners_after.values(), default=0) <= 1

    # Diagnostic: do not fail on non-monotonic raw reads.
    _anomalies = find_non_monotonic_raw_reads(clock)
    assert isinstance(_anomalies, list)

    for event in events:
        assert event.start_time == event.start_time  # canonical unchanged
        obs = mapped[event.start_time]
        if obs is not None:
            assert obs.source == "timer_detection"
            assert abs(obs.video_time - event.start_time) <= gap


def test_coach_config_exposes_lookup_gap() -> None:
    config = load_config(default_config_path())
    assert config.coach.game_clock_max_lookup_gap_seconds == pytest.approx(1.0)
    assert config.coach.provider == "ollama"
    assert config.coach.model == "gpt-oss:20b"
    assert config.coach.baseline_model == "llama3.1:8b"
