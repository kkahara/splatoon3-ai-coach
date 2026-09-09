"""Player-count fusion: X-marker readings → snapshot alive counts."""

from __future__ import annotations

import pytest

from splatoon3_ai_coach.config.models import (
    PlayerCountDetectorConfig,
    StateFusionConfig,
    TimerDetectorConfig,
)
from splatoon3_ai_coach.vision.models import (
    DetectorResult,
    PlayerCountReading,
    VisionFrameResult,
)
from splatoon3_ai_coach.vision.player_count import alive_counts_from_reading
from splatoon3_ai_coach.vision.state import fuse_game_state


def _timer_cfg() -> TimerDetectorConfig:
    return TimerDetectorConfig(
        roi=(0.47, 0.045, 0.53, 0.095),
        template_dir=".",
        match_threshold=0.55,
        min_usable_confidence=0.5,
    )


def _frame_with_reading(
    timestamp: float,
    reading: PlayerCountReading | None,
    *,
    confidence: float = 0.9,
) -> VisionFrameResult:
    detections: list[DetectorResult] = []
    if reading is not None:
        detections.append(
            DetectorResult(
                id=f"player_count:{timestamp}",
                detector_name="player_count",
                detector_version="player_count@test",
                confidence=confidence,
                reading=reading,
            )
        )
    return VisionFrameResult(
        frame_id=f"f{timestamp}",
        timestamp=timestamp,
        source="cadence",
        detections=detections,
    )


def test_no_usable_reading_leaves_counts_none() -> None:
    snapshots = fuse_game_state(
        [_frame_with_reading(1.0, None)],
        _timer_cfg(),
        StateFusionConfig(),
        player_count_config=PlayerCountDetectorConfig(min_usable_confidence=0.5),
    )
    assert snapshots[0].ally_alive_count is None
    assert snapshots[0].opponent_alive_count is None


def test_below_confidence_leaves_counts_none() -> None:
    reading = PlayerCountReading(ally_dead_slots=(), opponent_dead_slots=())
    snapshots = fuse_game_state(
        [_frame_with_reading(1.0, reading, confidence=0.2)],
        _timer_cfg(),
        StateFusionConfig(),
        player_count_config=PlayerCountDetectorConfig(min_usable_confidence=0.5),
    )
    assert snapshots[0].ally_alive_count is None
    assert snapshots[0].opponent_alive_count is None


def test_zero_dead_slots_fuses_to_four_four() -> None:
    reading = PlayerCountReading(ally_dead_slots=(), opponent_dead_slots=())
    snapshots = fuse_game_state(
        [_frame_with_reading(1.0, reading)],
        _timer_cfg(),
        StateFusionConfig(),
        player_count_config=PlayerCountDetectorConfig(),
    )
    assert snapshots[0].ally_alive_count == 4
    assert snapshots[0].opponent_alive_count == 4
    assert snapshots[0].player_count_confidence == pytest.approx(0.9)


def test_one_ally_x_fuses_to_three_four() -> None:
    reading = PlayerCountReading(ally_dead_slots=(1,), opponent_dead_slots=())
    snapshots = fuse_game_state(
        [_frame_with_reading(1.0, reading)],
        _timer_cfg(),
        StateFusionConfig(),
        player_count_config=PlayerCountDetectorConfig(),
    )
    assert snapshots[0].ally_alive_count == 3
    assert snapshots[0].opponent_alive_count == 4


def test_two_ally_one_opponent() -> None:
    reading = PlayerCountReading(ally_dead_slots=(1, 3), opponent_dead_slots=(2,))
    snapshots = fuse_game_state(
        [_frame_with_reading(1.0, reading)],
        _timer_cfg(),
        StateFusionConfig(),
        player_count_config=PlayerCountDetectorConfig(),
    )
    assert snapshots[0].ally_alive_count == 2
    assert snapshots[0].opponent_alive_count == 3


def test_all_dead() -> None:
    reading = PlayerCountReading(
        ally_dead_slots=(1, 2, 3, 4),
        opponent_dead_slots=(1, 2, 3, 4),
    )
    snapshots = fuse_game_state(
        [_frame_with_reading(1.0, reading)],
        _timer_cfg(),
        StateFusionConfig(),
        player_count_config=PlayerCountDetectorConfig(),
    )
    assert snapshots[0].ally_alive_count == 0
    assert snapshots[0].opponent_alive_count == 0


def test_invalid_slot_indexes_ignored() -> None:
    reading = PlayerCountReading(
        ally_dead_slots=(0, 1, 5, 1),
        opponent_dead_slots=(9, 2),
    )
    assert alive_counts_from_reading(reading) == (3, 3)
    snapshots = fuse_game_state(
        [_frame_with_reading(1.0, reading)],
        _timer_cfg(),
        StateFusionConfig(),
        player_count_config=PlayerCountDetectorConfig(),
    )
    assert snapshots[0].ally_alive_count == 3
    assert snapshots[0].opponent_alive_count == 3
