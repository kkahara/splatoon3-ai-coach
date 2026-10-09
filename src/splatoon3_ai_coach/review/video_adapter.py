"""Adapt a timeline-focused video into the Phase 1 screenshot contract."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
import yaml
from loguru import logger
from pydantic import BaseModel, Field

from splatoon3_ai_coach.config.models import (
    ReviewTimelineConfig,
    ReviewTimelineVideoConfig,
    TimerDetectorConfig,
)
from splatoon3_ai_coach.config.paths import PROJECT_ROOT
from splatoon3_ai_coach.media.video import VideoFrame, VideoLoader
from splatoon3_ai_coach.media.video_identity import video_identity
from splatoon3_ai_coach.review.clock import (
    ClockSource,
    ReviewClock,
    format_elapsed_clock,
)
from splatoon3_ai_coach.review.cursor import CursorDetection, detect_cursor
from splatoon3_ai_coach.review.label_reader import read_display_text
from splatoon3_ai_coach.review.models import ReviewTimelineDataset, ScrollDirection
from splatoon3_ai_coach.review.timeline_pipeline import import_timeline, plausible_clock
from splatoon3_ai_coach.vision.timer import TimerDetector


class NoUsableTimelineSamples(RuntimeError):
    """Raised when a video contains no clocked timeline frames."""


class VideoSample(BaseModel):
    """Video-side sample metadata before conversion to Phase 1 input."""

    source_video_time: float = Field(ge=0)
    frame_index: int = Field(ge=0)
    source_pts: int | None = None
    source_time_base_num: int | None = None
    source_time_base_den: int | None = None
    timeline_confidence: float = Field(ge=0, le=1)
    cursor_confidence: float = Field(ge=0, le=1)
    clock: ReviewClock | None = None
    status: str
    reason: str | None = None
    image_path: str | None = None
    image_sha256: str | None = None
    scroll_direction: ScrollDirection = ScrollDirection.unknown
    retained: bool = False
    rank_key: list[float] = Field(default_factory=list)


@dataclass(frozen=True)
class AdapterRun:
    """Adapter output summary and Phase 1 dataset."""

    samples: tuple[VideoSample, ...]
    dataset: ReviewTimelineDataset
    video_id: str


ClockReader = Callable[[Any, int, ReviewTimelineVideoConfig], ReviewClock | None]


def adapt_video(
    video_path: Path,
    output_dir: Path,
    *,
    config: ReviewTimelineConfig | None = None,
    source_recording_id: str | None = None,
    gameplay_analysis_dir: Path | None = None,
    ally_row: Literal["top", "bottom"] | None = None,
    battle_mode_id: str | None = None,
) -> AdapterRun:
    """Decode a video sequentially and feed clocked samples to Phase 1."""
    config = config or ReviewTimelineConfig()
    video_id = video_identity(video_path)
    with VideoLoader(video_path) as loader:
        metadata = loader.open()
        samples = adapt_frames(
            loader.frames(),
            output_dir / "timeline_samples",
            timeline_config=config,
            video_id=video_id,
        )
    clocked = [sample for sample in samples if sample.clock is not None]
    _write_adapter_manifest(
        output_dir, video_id, metadata.model_dump(mode="json"), samples
    )
    _write_adapter_report(output_dir, metadata.model_dump(mode="json"), samples)
    if not clocked:
        raise NoUsableTimelineSamples(
            "no usable timeline samples; adapter diagnostics were retained"
        )
    dataset = import_timeline(
        output_dir / "timeline_samples",
        config=config,
        manifest_path=output_dir / "timeline_samples_manifest.yaml",
        output_dir=output_dir,
        source_recording_id=source_recording_id,
        gameplay_analysis_dir=gameplay_analysis_dir,
        ally_row=ally_row,
        battle_mode_id=battle_mode_id,
    )
    return AdapterRun(tuple(samples), dataset, video_id)


def adapt_frames(
    frames: Iterable[VideoFrame],
    image_dir: Path,
    *,
    timeline_config: ReviewTimelineConfig | None = None,
    video_id: str = "unknown",
    clock_reader: ClockReader | None = None,
) -> list[VideoSample]:
    """Decode frames, then keep ranked candidates per clock second and direction.

    ``sample_interval_seconds`` of 0 processes every decoded frame. A positive
    interval still drops frames before the clock is read. Retention happens
    only after a clock value and scroll direction are known.
    """
    timeline_config = timeline_config or ReviewTimelineConfig()
    video_config = timeline_config.video
    image_dir.mkdir(parents=True, exist_ok=True)
    detector = _clock_detector() if clock_reader is None else None
    reader = clock_reader or (
        lambda image, cursor_x, config: _read_clock_from_image(
            image, cursor_x, config, detector
        )
    )
    observed, images = _observe_frames(frames, timeline_config, reader)
    return _retain_buckets(
        observed, images, image_dir, video_config.frames_per_second_bucket
    )


def _layout_confidence(image: Any, cursor: CursorDetection) -> float:
    """Combine cursor and dark timeline-layout evidence."""
    if cursor.x is None:
        return 0.0
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    graph = gray[int(gray.shape[0] * 0.28) : int(gray.shape[0] * 0.62)]
    darkness = 1.0 - min(1.0, float(graph.mean()) / 180.0)
    return float(max(0.0, min(1.0, 0.75 * cursor.confidence + 0.25 * darkness)))


def timeline_structure_score(image: Any, config: ReviewTimelineConfig) -> float:
    """Score cursor-independent timeline structure in ``[0, 1]``.

    Requires both a full-width neutral scrub bar row and the neutral-gray
    event-lane background; a bright vertical edge in gameplay satisfies neither.
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    scrub = _roi(hsv, config.scrub_bar_roi)
    bar = (scrub[..., 1] < 55) & (scrub[..., 2] > 100)
    bar_coverage = float(bar.mean(axis=1).max()) if bar.size else 0.0
    event_lane = _roi(hsv, config.event_lane_roi)
    neutral = (
        (event_lane[..., 1] < 40)
        & (event_lane[..., 2] >= 40)
        & (event_lane[..., 2] < 120)
    )
    neutral_fraction = float(neutral.mean()) if neutral.size else 0.0
    return min(bar_coverage, min(1.0, neutral_fraction / 0.8))


def _roi(image: Any, box: tuple[float, float, float, float]) -> Any:
    """Crop a normalized ``(x1, y1, x2, y2)`` box."""
    height, width = image.shape[:2]
    x1, y1, x2, y2 = box
    return image[int(height * y1) : int(height * y2), int(width * x1) : int(width * x2)]


def _clock_detector() -> TimerDetector:
    """Build the one glyph matcher used for an adapter run."""
    return TimerDetector(
        TimerDetectorConfig(
            roi=(0.0, 0.0, 1.0, 1.0),
            template_dir=PROJECT_ROOT / "calibration" / "templates",
        )
    )


def _observe_frames(
    frames: Iterable[VideoFrame],
    timeline_config: ReviewTimelineConfig,
    reader: ClockReader,
) -> tuple[list[VideoSample], dict[int, Any]]:
    """Read every selected frame without writing images."""
    video_config = timeline_config.video
    next_sample_time = 0.0
    results: list[VideoSample] = []
    images: dict[int, Any] = {}
    previous_elapsed: int | None = None
    for frame in frames:
        if _before_sample_time(frame, next_sample_time, video_config):
            continue
        if video_config.sample_interval_seconds > 0:
            next_sample_time = frame.timestamp + video_config.sample_interval_seconds
        sample, elapsed = _observe_frame(frame, timeline_config, reader, previous_elapsed)
        results.append(sample)
        if sample.clock is not None:
            images[id(sample)] = frame.image
        if elapsed is not None:
            previous_elapsed = elapsed
    return results, images


def _before_sample_time(
    frame: VideoFrame, next_sample_time: float, config: ReviewTimelineVideoConfig
) -> bool:
    """Return whether a positive interval skips this frame before clock reading."""
    if config.sample_interval_seconds <= 0:
        return False
    return frame.timestamp + 1e-9 < next_sample_time


def _observe_frame(
    frame: VideoFrame,
    timeline_config: ReviewTimelineConfig,
    reader: ClockReader,
    previous_elapsed: int | None,
) -> tuple[VideoSample, int | None]:
    """Classify one frame. A plausible elapsed value updates scroll direction.

    An implausible label (e.g. ``95:00``) keeps the frame for provenance but
    neither gets a direction nor becomes the previous clock.
    """
    video_config = timeline_config.video
    cursor = detect_cursor(frame.image, timeline_config)
    layout_confidence = _layout_confidence(frame.image, cursor)
    if layout_confidence < video_config.layout_confidence_floor:
        return _rejected(frame, layout_confidence, cursor, "no timeline layout"), None
    structure = timeline_structure_score(frame.image, timeline_config)
    if structure < video_config.structure_confidence_floor:
        return (
            _rejected(
                frame,
                min(layout_confidence, structure),
                cursor,
                "no timeline structure",
            ),
            None,
        )
    clock = reader(frame.image, cursor.x or 0, video_config)
    if clock is None or clock.confidence < video_config.clock_confidence_floor:
        return (
            _rejected(
                frame,
                layout_confidence,
                cursor,
                "unreadable timeline clock",
                status="unclocked_timeline",
            ),
            None,
        )
    if not plausible_clock(clock, timeline_config):
        sample = _clocked_sample(
            frame, cursor, layout_confidence, structure, clock, ScrollDirection.unknown
        )
        return sample, None
    direction = _scroll_direction(previous_elapsed, clock.elapsed_seconds)
    sample = _clocked_sample(
        frame, cursor, layout_confidence, structure, clock, direction
    )
    return sample, clock.elapsed_seconds


def _scroll_direction(previous: int | None, current: int) -> ScrollDirection:
    """Direction from the Review clock, never from the video timestamp."""
    if previous is None or current == previous:
        return ScrollDirection.unknown
    if current > previous:
        return ScrollDirection.forward
    return ScrollDirection.backward


def _clocked_sample(
    frame: VideoFrame,
    cursor: CursorDetection,
    layout_confidence: float,
    structure: float,
    clock: ReviewClock,
    direction: ScrollDirection,
) -> VideoSample:
    """Build a clocked observation and its deterministic rank key."""
    sharpness = _cursor_sharpness(frame.image, cursor.x or 0)
    valid_clock = 1.0 if clock.confidence > 0 else 0.0
    rank_key = [
        valid_clock,
        cursor.confidence,
        cursor.runner_up_margin,
        layout_confidence,
        structure,
        sharpness,
        -float(frame.frame_index),
    ]
    return VideoSample(
        source_video_time=frame.timestamp,
        frame_index=frame.frame_index,
        source_pts=frame.source_pts,
        source_time_base_num=frame.source_time_base_num,
        source_time_base_den=frame.source_time_base_den,
        timeline_confidence=layout_confidence,
        cursor_confidence=cursor.confidence,
        clock=clock,
        status="clocked_timeline",
        scroll_direction=direction,
        rank_key=rank_key,
    )


def _cursor_sharpness(image: Any, cursor_x: int) -> float:
    """Laplacian variance of the cursor column. Higher is sharper."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    x1 = max(0, cursor_x - 8)
    x2 = min(gray.shape[1], cursor_x + 8)
    column = gray[:, x1:x2]
    if column.size == 0:
        return 0.0
    return float(cv2.Laplacian(column, cv2.CV_64F).var())


def _retain_buckets(
    samples: list[VideoSample],
    images: dict[int, Any],
    image_dir: Path,
    limit: int,
) -> list[VideoSample]:
    """Keep the top-ranked candidates in each clock-second and direction bucket."""
    grouped: dict[tuple[int, str], list[VideoSample]] = {}
    for sample in samples:
        if sample.clock is None:
            continue
        key = (sample.clock.elapsed_seconds, sample.scroll_direction.value)
        grouped.setdefault(key, []).append(sample)
    retained_ids = {
        id(sample) for group in grouped.values() for sample in _ranked(group)[:limit]
    }
    written = 0
    results: list[VideoSample] = []
    for sample in samples:
        if id(sample) not in retained_ids:
            results.append(sample)
            continue
        written += 1
        results.append(_write_retained(sample, images[id(sample)], image_dir, written))
    return results


def _ranked(samples: list[VideoSample]) -> list[VideoSample]:
    """Order candidates by the recorded rank key, best first."""
    return sorted(samples, key=lambda sample: sample.rank_key, reverse=True)


def _write_retained(
    sample: VideoSample, image: Any, image_dir: Path, index: int
) -> VideoSample:
    """Write one retained candidate as JPEG. Rejected frames are not written."""
    output_path = image_dir / f"sample_{index:06d}.jpg"
    encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 92])[1]
    output_path.write_bytes(encoded.tobytes())
    return sample.model_copy(
        update={
            "retained": True,
            "image_path": output_path.name,
            "image_sha256": hashlib.sha256(encoded.tobytes()).hexdigest(),
        }
    )


def _read_clock_from_image(
    image: Any,
    cursor_x: int,
    config: ReviewTimelineVideoConfig,
    detector: TimerDetector | None = None,
) -> ReviewClock | None:
    """Read an elapsed label with the run's calibrated glyph matcher."""
    height, width = image.shape[:2]
    half_width = int(width * config.clock_roi_width_fraction / 2)
    x1 = max(0, cursor_x - half_width)
    x2 = min(width, cursor_x + half_width)
    y1 = int(height * config.clock_roi_top_fraction)
    y2 = int(height * config.clock_roi_bottom_fraction)
    crop = image[y1:y2, x1:x2]
    reader = detector or _clock_detector()
    logger.disable("splatoon3_ai_coach.vision.timer")
    try:
        reading, confidence = reader.detect(crop)
    finally:
        logger.enable("splatoon3_ai_coach.vision.timer")
    if reading is None:
        return None
    parsed = read_display_text(reading.display, confidence=confidence)
    if parsed.clock is None:
        return None
    elapsed = parsed.clock.elapsed_seconds
    overtime = _is_overtime_label(crop)
    if overtime or _has_leading_minus(crop):
        elapsed = -abs(elapsed)
    return parsed.clock.model_copy(
        update={
            "source": ClockSource.image,
            "confidence": confidence,
            "display": format_elapsed_clock(elapsed, overtime=overtime),
            "elapsed_seconds": elapsed,
        }
    )


def _is_overtime_label(image: Any) -> bool:
    """Overtime labels (``+M:SS``) are orange; regulation and pre-GO are white."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    bright = hsv[..., 2] > 120
    if int(bright.sum()) < 50:
        return False
    saturation = float(np.median(hsv[..., 1][bright]))
    hue = float(np.median(hsv[..., 0][bright]))
    return saturation >= 100 and 5 <= hue <= 25


def _has_leading_minus(image: Any) -> bool:
    """Detect the short leading minus glyph in a pre-GO clock label."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = (gray > 180).astype("uint8")
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    for label in range(1, count):
        x, y, width, height, area = stats[label]
        if x < image.shape[1] * 0.38 and width >= 8 and width >= height * 1.6:
            if area >= 12:
                return True
    return False


def _rejected(
    frame: VideoFrame,
    layout_confidence: float,
    cursor: CursorDetection,
    reason: str,
    *,
    status: str = "rejected",
) -> VideoSample:
    return VideoSample(
        source_video_time=frame.timestamp,
        frame_index=frame.frame_index,
        source_pts=frame.source_pts,
        source_time_base_num=frame.source_time_base_num,
        source_time_base_den=frame.source_time_base_den,
        timeline_confidence=layout_confidence,
        cursor_confidence=cursor.confidence,
        status=status,
        reason=reason,
    )


def _write_adapter_manifest(
    output_dir: Path,
    video_id: str,
    metadata: dict[str, Any],
    samples: list[VideoSample],
) -> None:
    """Write deterministic adapter provenance and Phase 1 entries."""
    output_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, sample in enumerate(
        (item for item in samples if item.status == "clocked_timeline" and item.retained),
        start=0,
    ):
        assert sample.clock is not None
        entries.append(
            {
                "capture_index": index,
                "image_path": sample.image_path,
                "elapsed_clock": sample.clock.display,
                "clock_confidence": sample.clock.confidence,
                "scroll_direction": sample.scroll_direction.value,
                "source_video_time": sample.source_video_time,
                "frame_index": sample.frame_index,
                "source_pts": sample.source_pts,
                "rank_key": sample.rank_key,
            }
        )
    payload = {
        "source_video_id": video_id,
        "video_metadata": metadata,
        "entries": entries,
        "video_samples": [sample.model_dump(mode="json") for sample in samples],
    }
    (output_dir / "timeline_samples_manifest.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )


def _write_adapter_report(
    output_dir: Path, metadata: dict[str, Any], samples: list[VideoSample]
) -> None:
    """Write a concise adapter diagnostic report."""
    counts: dict[str, int] = {}
    for sample in samples:
        counts[sample.status] = counts.get(sample.status, 0) + 1
    lines = [
        "# Timeline video adapter report",
        "",
        f"- video: {metadata.get('path', 'unknown')}",
        f"- sampled frames: {len(samples)}",
        f"- statuses: {json.dumps(counts, sort_keys=True)}",
        "- source video time is provenance; elapsed_seconds comes from "
        "the rendered clock",
        "",
        "## Rejections",
        "",
    ]
    reasons = sorted({sample.reason for sample in samples if sample.reason})
    lines.extend(f"- {reason}" for reason in reasons)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "video_adapter_report.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
