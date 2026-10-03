"""Cursor-centered roster slot geometry and classification."""

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ReviewTimelineConfig
from splatoon3_ai_coach.review.roster import extract_cursor_slots

WIDTH, HEIGHT = 1920, 1080


def test_cursor_centered_rows_classify_alive_and_dead() -> None:
    config = ReviewTimelineConfig()
    image = _timeline_with_roster(1571, config, dead={"bottom_3", "bottom_4"})
    states = _states(image, 1571, config)
    assert states == {
        "top_1": "alive",
        "top_2": "alive",
        "top_3": "alive",
        "top_4": "alive",
        "bottom_1": "alive",
        "bottom_2": "alive",
        "bottom_3": "dead",
        "bottom_4": "dead",
    }


def test_banner_x_above_the_roster_does_not_affect_top_row() -> None:
    config = ReviewTimelineConfig()
    image = _timeline_with_roster(1571, config, dead=set())
    cv2.line(image, (1400, 0), (1750, 60), (120, 120, 120), 12)
    cv2.line(image, (1400, 60), (1750, 0), (120, 120, 120), 12)
    states = _states(image, 1571, config)
    assert all(states[f"top_{index}"] == "alive" for index in range(1, 5))


def test_right_edge_cursor_keeps_four_centered_slots() -> None:
    config = ReviewTimelineConfig()
    image = _timeline_with_roster(1740, config, dead={"top_1", "bottom_1"})
    states = _states(image, 1740, config)
    assert states["top_1"] == "dead"
    assert states["bottom_1"] == "dead"
    assert [states[f"top_{index}"] for index in range(2, 5)] == ["alive"] * 3


def test_saturated_icon_pixels_are_not_an_x_marker() -> None:
    config = ReviewTimelineConfig()
    image = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    for (x1, y1, x2, y2), _row, _index in _slot_boxes(1000, config):
        image[y1:y2, x1:x2] = (40, 90, 230)
        cv2.line(image, (x1, y1), (x2, y2), (0, 0, 0), 2)
    states = _states(image, 1000, config)
    assert "dead" not in states.values()


def _states(image: np.ndarray, cursor_x: int, config: ReviewTimelineConfig) -> dict:
    return {
        slot.slot_id: slot.state.value
        for slot in extract_cursor_slots(
            image, cursor_x, config=config, sample_id="s", elapsed_seconds=0
        )
    }


def _slot_boxes(cursor_x: int, config: ReviewTimelineConfig) -> list:
    slot_w = WIDTH * config.roster_row_width_fraction / 4
    left = cursor_x - 2 * slot_w
    boxes = []
    for row, (top, bottom) in (
        ("top", config.roster_top_band),
        ("bottom", config.roster_bottom_band),
    ):
        for index in range(4):
            x1 = int(round(left + index * slot_w))
            x2 = int(round(left + (index + 1) * slot_w))
            boxes.append(((x1, int(HEIGHT * top), x2, int(HEIGHT * bottom)), row, index))
    return boxes


def _timeline_with_roster(
    cursor_x: int, config: ReviewTimelineConfig, *, dead: set[str]
) -> np.ndarray:
    image = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
    image[int(HEIGHT * 0.28) : int(HEIGHT * 0.62), cursor_x - 4 : cursor_x + 4] = 255
    for (x1, y1, x2, y2), row, index in _slot_boxes(cursor_x, config):
        center = ((x1 + x2) // 2, (y1 + y2) // 2)
        if f"{row}_{index + 1}" in dead:
            cv2.circle(image, center, 22, (25, 25, 25), -1)
            cv2.line(image, (x1, y1), (x2 - 1, y2 - 1), (120, 120, 120), 9)
            cv2.line(image, (x1, y2 - 1), (x2 - 1, y1), (120, 120, 120), 9)
            continue
        color = (0, 120, 255) if row == "top" else (255, 80, 0)
        cv2.circle(image, center, 22, color, -1)
        cx, cy = center
        cv2.rectangle(image, (cx - 8, cy - 8), (cx + 8, cy + 8), (0, 0, 0), 2)
    return image
