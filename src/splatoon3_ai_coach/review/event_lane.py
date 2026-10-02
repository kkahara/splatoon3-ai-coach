"""Static-layout event-lane extraction and cursor-axis calibration."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from splatoon3_ai_coach.config.models import ReviewTimelineConfig

from .models import ReviewTimelineSample, TimelineEventMarker


@dataclass(frozen=True)
class AxisCalibration:
    """Linear graph-position to elapsed-clock calibration."""

    x_min: float
    x_max: float
    elapsed_min: int
    elapsed_max: int
    mean_absolute_error: float

    def elapsed_at(self, x_norm: float) -> float:
        """Estimate elapsed time for a normalized graph x position."""
        span = self.x_max - self.x_min
        if span == 0:
            return float(self.elapsed_min)
        fraction = (x_norm - self.x_min) / span
        return self.elapsed_min + fraction * (
            self.elapsed_max - self.elapsed_min
        )


def calibrate_axis(samples: list[ReviewTimelineSample]) -> AxisCalibration | None:
    """Fit the graph axis from valid cursor x and elapsed observations."""
    points = [
        (sample.cursor.x, sample.elapsed_seconds)
        for sample in samples
        if sample.cursor.x is not None and sample.elapsed_seconds is not None
    ]
    if len(points) < 2:
        return None
    xs = [float(point[0]) for point in points]
    clocks = [int(point[1]) for point in points]
    return AxisCalibration(
        x_min=min(xs),
        x_max=max(xs),
        elapsed_min=min(clocks),
        elapsed_max=max(clocks),
        mean_absolute_error=0.0,
    )


def select_static_samples(
    samples: list[ReviewTimelineSample], limit: int
) -> list[ReviewTimelineSample]:
    """Select high-confidence, cursor-diverse samples deterministically."""
    valid = [
        sample for sample in samples
        if sample.cursor.x is not None and sample.elapsed_seconds is not None
    ]
    ranked = sorted(
        valid,
        key=lambda sample: (
            -sample.cursor.confidence,
            -sample.clock_confidence,
            sample.capture_index,
        ),
    )
    selected: list[ReviewTimelineSample] = []
    for sample in ranked:
        if not selected or all(
            abs((sample.cursor.x or 0) - (item.cursor.x or 0)) > 20
            for item in selected
        ):
            selected.append(sample)
        if len(selected) >= limit:
            break
    return sorted(selected, key=lambda sample: sample.capture_index)


def extract_static_markers(
    images: dict[str, np.ndarray],
    selected: list[ReviewTimelineSample],
    config: ReviewTimelineConfig,
) -> list[TimelineEventMarker]:
    """Extract conservative team-row blobs from selected lane frames."""
    markers: dict[tuple[str, int], TimelineEventMarker] = {}
    for sample in selected:
        image = images.get(sample.sample_id)
        if image is None:
            continue
        lane = _crop(image, config.event_lane_roi)
        for row, row_image in _split_rows(lane):
            for x, confidence in _colored_components(row_image):
                key = (row, x // 12)
                marker = markers.get(key)
                if marker is None or confidence > marker.confidence:
                    markers[key] = TimelineEventMarker(
                        marker_id=f"marker-{row}-{x // 12}",
                        team_row=row,  # type: ignore[arg-type]
                        kind="unknown",
                        x_norm=x / max(row_image.shape[1] - 1, 1),
                        confidence=confidence,
                        supporting_sample_ids=[sample.sample_id],
                    )
                elif sample.sample_id not in marker.supporting_sample_ids:
                    marker.supporting_sample_ids.append(sample.sample_id)
    return sorted(markers.values(), key=lambda marker: marker.marker_id)


def _crop(image: np.ndarray, box: tuple[float, float, float, float]) -> np.ndarray:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = box
    return image[int(y1 * height) : int(y2 * height), int(x1 * width) : int(x2 * width)]


def _split_rows(lane: np.ndarray) -> list[tuple[str, np.ndarray]]:
    midpoint = lane.shape[0] // 2
    return [("top", lane[:midpoint]), ("bottom", lane[midpoint:])]


def _colored_components(region: np.ndarray) -> list[tuple[int, float]]:
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    mask = ((hsv[..., 1] > 90) & (hsv[..., 2] > 80)).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    result: list[tuple[int, float]] = []
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if 12 <= area <= 3000:
            result.append((int(stats[label, cv2.CC_STAT_LEFT]), min(1.0, area / 500)))
    return result

