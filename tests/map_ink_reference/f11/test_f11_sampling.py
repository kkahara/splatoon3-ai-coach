"""Synthetic tests for F11 sampling and exact replay."""

from __future__ import annotations

from map_ink_reference.f11.sampling import (
    draw_control_points,
    draw_frame_index,
    draw_target_indices,
    new_salt,
)


def test_adjacent_targets_are_both_drawn() -> None:
    """D7-a: no target-to-target spacing, so adjacent pixels stay eligible."""
    chosen = draw_target_indices("salt", "video", 10, [4, 5])
    assert sorted(chosen) == [4, 5]


def test_draws_replay_exactly_from_the_salt() -> None:
    salt = "ab" * 32
    frames = [
        draw_frame_index(salt, "video", 100, 5),
        draw_frame_index(salt, "video", 100, 5),
    ]
    targets = [
        draw_target_indices(salt, "video", 100, list(range(20))),
        draw_target_indices(salt, "video", 100, list(range(20))),
    ]
    candidates = [(0, index * 40) for index in range(8)]
    controls = [
        draw_control_points(salt, "video", 100, candidates, []),
        draw_control_points(salt, "video", 100, candidates, []),
    ]
    assert frames[0] == frames[1]
    assert targets[0] == targets[1]
    assert len(targets[0]) == 6
    assert controls[0] == controls[1]
    assert len(controls[0]) == 3


def test_controls_keep_32px_separation() -> None:
    chosen = draw_control_points("salt", "video", 1, [(0, 0), (0, 10), (0, 40)], [(0, 0)])
    assert (0, 0) not in chosen
    assert all(abs(point[1] - 0) >= 32 or point != (0, 0) for point in chosen)
    assert (0, 10) not in chosen


def test_salt_is_32_os_random_bytes() -> None:
    assert len(bytes.fromhex(new_salt())) == 32
    assert new_salt() != new_salt()
