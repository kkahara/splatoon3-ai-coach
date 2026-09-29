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


def _reading(ally_dead: int, opponent_dead: int) -> PlayerCountReading:
    return PlayerCountReading(
        ally_dead_slots=tuple(range(1, ally_dead + 1)),
        opponent_dead_slots=tuple(range(1, opponent_dead + 1)),
    )


def _fused_counts(
    readings: list[PlayerCountReading | None],
    config: PlayerCountDetectorConfig | None = None,
    *,
    step: float = 0.5,
) -> list[tuple[int | None, int | None]]:
    frames = [
        _frame_with_reading(round(index * step, 3), reading)
        for index, reading in enumerate(readings)
    ]
    snapshots = fuse_game_state(
        frames,
        _timer_cfg(),
        StateFusionConfig(),
        player_count_config=config or PlayerCountDetectorConfig(),
    )
    return [(s.ally_alive_count, s.opponent_alive_count) for s in snapshots]


def test_one_frame_misread_is_suppressed() -> None:
    counts = _fused_counts([_reading(0, 0), _reading(2, 0), _reading(0, 0)])
    assert counts == [(4, 4), (4, 4), (4, 4)]


def test_confirmed_change_is_accepted_one_reading_late() -> None:
    counts = _fused_counts(
        [_reading(0, 0), _reading(1, 0), _reading(1, 0), _reading(1, 0)]
    )
    assert counts == [(4, 4), (4, 4), (3, 4), (3, 4)]


def test_sides_are_debounced_independently() -> None:
    counts = _fused_counts(
        [_reading(0, 0), _reading(1, 1), _reading(1, 0), _reading(1, 0)]
    )
    assert counts == [(4, 4), (4, 4), (3, 4), (3, 4)]


def test_alternating_misreads_never_replace_the_count() -> None:
    readings = [_reading(0, 0), _reading(1, 0)] * 3
    assert {ally for ally, _ in _fused_counts(readings)} == {4}


def _fuse(frames: list[VisionFrameResult], config: PlayerCountDetectorConfig):
    snapshots = fuse_game_state(
        frames, _timer_cfg(), StateFusionConfig(), player_count_config=config
    )
    return [(s.ally_alive_count, s.opponent_alive_count) for s in snapshots]


def test_unusable_frame_restarts_confirmation_and_is_unknown() -> None:
    frames = [
        _frame_with_reading(0.0, _reading(0, 0)),
        _frame_with_reading(0.5, _reading(1, 0)),
        _frame_with_reading(1.0, _reading(1, 0), confidence=0.1),
        _frame_with_reading(1.5, _reading(1, 0)),
        _frame_with_reading(2.0, _reading(1, 0)),
    ]
    counts = _fuse(frames, PlayerCountDetectorConfig())
    assert counts == [(4, 4), (4, 4), (None, None), (None, 4), (3, 4)]


def test_unscheduled_frames_carry_no_counts_and_keep_confirmation() -> None:
    config = PlayerCountDetectorConfig(sample_fps=1.0, hold_seconds=1.5)
    frames = [
        _frame_with_reading(0.0, _reading(0, 0)),
        _frame_with_reading(0.5, None),
        _frame_with_reading(1.0, _reading(1, 0)),
        _frame_with_reading(1.5, None),
        _frame_with_reading(2.0, _reading(1, 0)),
    ]
    assert _fuse(frames, config) == [(4, 4), (None, None), (4, 4), (None, None), (3, 4)]


def test_hold_must_exceed_the_sampling_interval() -> None:
    with pytest.raises(ValueError, match="must exceed the sampling interval"):
        PlayerCountDetectorConfig(sample_fps=1.0, hold_seconds=1.0)


def test_accepted_count_is_not_held_across_a_long_gap() -> None:
    frames = [
        _frame_with_reading(0.0, _reading(0, 0)),
        _frame_with_reading(10.0, _reading(2, 1)),
        _frame_with_reading(10.5, _reading(2, 1)),
    ]
    snapshots = fuse_game_state(
        frames,
        _timer_cfg(),
        StateFusionConfig(),
        player_count_config=PlayerCountDetectorConfig(),
    )
    counts = [(s.ally_alive_count, s.opponent_alive_count) for s in snapshots]
    assert counts == [(4, 4), (None, None), (2, 3)]


def test_confirm_readings_one_disables_debounce() -> None:
    config = PlayerCountDetectorConfig(confirm_readings=1)
    counts = _fused_counts([_reading(0, 0), _reading(2, 0), _reading(0, 0)], config)
    assert counts == [(4, 4), (2, 4), (4, 4)]


def test_held_count_cites_the_readings_that_support_it() -> None:
    frames = [
        _frame_with_reading(0.0, _reading(0, 0)),
        _frame_with_reading(0.5, _reading(2, 1)),
    ]
    snapshots = fuse_game_state(
        frames,
        _timer_cfg(),
        StateFusionConfig(),
        player_count_config=PlayerCountDetectorConfig(),
    )
    assert "player_count:0.0" in snapshots[1].evidence_ids
    assert "player_count:0.5" not in snapshots[1].evidence_ids


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
