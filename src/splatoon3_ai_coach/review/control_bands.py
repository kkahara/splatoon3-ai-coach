"""Static Review control-band extraction from multiple cursor positions."""

from __future__ import annotations

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ReviewTimelineConfig

from .models import (
    AxisCalibrationEvidence,
    ControlSegment,
    ReviewTimelineSample,
    TeamIdentity,
)


def extract_control_segments(
    images: dict[str, np.ndarray],
    samples: list[ReviewTimelineSample],
    axis: AxisCalibrationEvidence | None,
    identity: TeamIdentity,
    config: ReviewTimelineConfig,
) -> list[ControlSegment]:
    """Aggregate static per-column evidence and run-length encode it."""
    if (
        axis is None
        or identity.top_color is None
        or identity.bottom_color is None
    ):
        return []
    evidence = [
        _frame_evidence(images[sample.sample_id], sample, identity, config)
        for sample in samples
        if sample.sample_id in images and sample.cursor.x is not None
    ]
    if not evidence:
        return []
    stacked = np.stack(evidence)
    with np.errstate(invalid="ignore"):
        scores = np.nanmedian(stacked, axis=0)
    states, confidence = _classify_columns(scores, config)
    states = _bridge_unknown(states, config.band_unknown_bridge_columns)
    return _segments(states, confidence, axis, config.graph_roi)


def _frame_evidence(
    image: np.ndarray,
    sample: ReviewTimelineSample,
    identity: TeamIdentity,
    config: ReviewTimelineConfig,
) -> np.ndarray:
    height, width = image.shape[:2]
    x1 = int(width * config.graph_roi[0])
    x2 = int(width * config.graph_roi[2])
    y1 = int(height * config.graph_roi[1])
    y2 = int(height * config.graph_roi[3])
    hsv = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    hue, saturation, value = (hsv[..., index] for index in range(3))
    color_gate = (
        (saturation >= config.band_color_saturation_min)
        & (value >= config.band_color_value_min)
    )
    top = color_gate & (
        _hue_distance(hue, float(identity.top_color)) <= config.band_hue_tolerance
    )
    bottom = color_gate & (
        _hue_distance(hue, float(identity.bottom_color))
        <= config.band_hue_tolerance
    )
    neutral = (
        (saturation <= config.band_neutral_saturation_max)
        & (value >= config.band_neutral_value_min)
        & (value <= config.band_neutral_value_max)
    )
    result = np.stack(
        (
            np.mean(top, axis=0),
            np.mean(bottom, axis=0),
            np.mean(neutral, axis=0),
        ),
        axis=1,
    ).astype(np.float64)
    cursor = int(round(float(sample.cursor.x))) - x1
    radius = max(1, int(round(width * config.band_cursor_mask_fraction)))
    result[max(0, cursor - radius) : min(result.shape[0], cursor + radius + 1)] = np.nan
    return result


def _classify_columns(
    scores: np.ndarray, config: ReviewTimelineConfig
) -> tuple[list[str], np.ndarray]:
    states: list[str] = []
    confidence = np.zeros(scores.shape[0], dtype=np.float64)
    names = ("top", "bottom", "neutral")
    for index, row in enumerate(scores):
        if not np.all(np.isfinite(row)):
            states.append("unknown")
            continue
        order = np.argsort(row)
        best, runner_up = int(order[-1]), int(order[-2])
        if (
            row[best] < config.band_score_floor
            or row[best] - row[runner_up] < config.band_score_margin
        ):
            states.append("unknown")
            continue
        states.append(names[best])
        confidence[index] = min(1.0, float(row[best] - row[runner_up]))
    return states, confidence


def _bridge_unknown(states: list[str], limit: int) -> list[str]:
    result = list(states)
    index = 0
    while index < len(result):
        if result[index] != "unknown":
            index += 1
            continue
        stop = index
        while stop < len(result) and result[stop] == "unknown":
            stop += 1
        if (
            stop - index <= limit
            and index > 0
            and stop < len(result)
            and result[index - 1] == result[stop]
        ):
            result[index:stop] = [result[index - 1]] * (stop - index)
        index = stop
    return result


def _segments(
    states: list[str],
    confidence: np.ndarray,
    axis: AxisCalibrationEvidence,
    graph_roi: tuple[float, float, float, float],
) -> list[ControlSegment]:
    if not states:
        return []
    segments: list[ControlSegment] = []
    start = 0
    for index in range(1, len(states) + 1):
        if index < len(states) and states[index] == states[start]:
            continue
        left_x = _column_x_norm(start, len(states), graph_roi)
        right_x = _column_x_norm(index, len(states), graph_roi)
        elapsed = sorted((axis.elapsed_at(left_x), axis.elapsed_at(right_x)))
        segment_confidence = float(np.mean(confidence[start:index]))
        segments.append(
            ControlSegment(
                start_elapsed=elapsed[0],
                end_elapsed=elapsed[1],
                controller=states[start],  # type: ignore[arg-type]
                confidence=segment_confidence,
            )
        )
        start = index
    return segments


def _column_x_norm(
    index: int,
    column_count: int,
    graph_roi: tuple[float, float, float, float],
) -> float:
    return graph_roi[0] + (index / column_count) * (graph_roi[2] - graph_roi[0])


def _hue_distance(values: np.ndarray, center: float) -> np.ndarray:
    distance = np.abs(values.astype(np.float64) - center) % 180.0
    return np.minimum(distance, 180.0 - distance)
