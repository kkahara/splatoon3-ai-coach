"""Adapt a timeline-focused video into the Phase 1 screenshot contract."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
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
from splatoon3_ai_coach.review.models import ReviewTimelineDataset
from splatoon3_ai_coach.review.timeline_pipeline import import_timeline
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
    """Adapt timestamped frames using source-time sampling."""
    timeline_config = timeline_config or ReviewTimelineConfig()
    video_config = timeline_config.video
    image_dir.mkdir(parents=True, exist_ok=True)
    reader = clock_reader or _read_clock_from_image
    next_sample_time = 0.0
    results: list[VideoSample] = []
    emitted = 0
    for frame in frames:
        if frame.timestamp + 1e-9 < next_sample_time:
            continue
        next_sample_time = frame.timestamp + video_config.sample_interval_seconds
        cursor = detect_cursor(frame.image, timeline_config)
        layout_confidence = _layout_confidence(frame.image, cursor)
        if layout_confidence < video_config.layout_confidence_floor:
            results.append(
                _rejected(
                    frame, layout_confidence, cursor, "no timeline layout"
                )
            )
            continue
        structure = timeline_structure_score(frame.image, timeline_config)
        if structure < video_config.structure_confidence_floor:
            results.append(
                _rejected(
                    frame,
                    min(layout_confidence, structure),
                    cursor,
                    "no timeline structure",
                )
            )
            continue
        clock = reader(frame.image, cursor.x or 0, video_config)
        if clock is None or clock.confidence < video_config.clock_confidence_floor:
            results.append(
                _rejected(frame, layout_confidence, cursor, "unreadable timeline clock",
                          status="unclocked_timeline")
            )
            continue
        emitted += 1
        output_path = image_dir / f"sample_{emitted:06d}.png"
        encoded = cv2.imencode(".png", frame.image)[1]
        output_path.write_bytes(encoded.tobytes())
        results.append(
            VideoSample(
                source_video_time=frame.timestamp,
                frame_index=frame.frame_index,
                source_pts=frame.source_pts,
                source_time_base_num=frame.source_time_base_num,
                source_time_base_den=frame.source_time_base_den,
                timeline_confidence=layout_confidence,
                cursor_confidence=cursor.confidence,
                clock=clock,
                status="clocked_timeline",
                image_path=str(output_path.name),
                image_sha256=hashlib.sha256(encoded.tobytes()).hexdigest(),
            )
        )
    return results


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

    Requires both a full-width neutral scrub bar row and a mostly dark neutral
    graph panel; a bright vertical edge in gameplay satisfies neither.
    """
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    scrub = _roi(hsv, config.scrub_bar_roi)
    bar = (scrub[..., 1] < 55) & (scrub[..., 2] > 100)
    bar_coverage = float(bar.mean(axis=1).max()) if bar.size else 0.0
    graph = _roi(hsv, config.graph_roi)
    neutral = (graph[..., 1] < 60) & (graph[..., 2] < 140)
    graph_neutral = float(neutral.mean()) if neutral.size else 0.0
    return min(bar_coverage, min(1.0, graph_neutral / 0.5))


def _roi(image: Any, box: tuple[float, float, float, float]) -> Any:
    """Crop a normalized ``(x1, y1, x2, y2)`` box."""
    height, width = image.shape[:2]
    x1, y1, x2, y2 = box
    return image[
        int(height * y1) : int(height * y2), int(width * x1) : int(width * x2)
    ]


def _read_clock_from_image(
    image: Any, cursor_x: int, config: ReviewTimelineVideoConfig
) -> ReviewClock | None:
    """Read a positive elapsed label with the existing calibrated glyph matcher."""
    height, width = image.shape[:2]
    half_width = int(width * config.clock_roi_width_fraction / 2)
    x1 = max(0, cursor_x - half_width)
    x2 = min(width, cursor_x + half_width)
    y1 = int(height * config.clock_roi_top_fraction)
    y2 = int(height * config.clock_roi_bottom_fraction)
    crop = image[y1:y2, x1:x2]
    detector = TimerDetector(
        TimerDetectorConfig(
            roi=(0.0, 0.0, 1.0, 1.0),
            template_dir=PROJECT_ROOT / "calibration" / "templates",
        )
    )
    logger.disable("splatoon3_ai_coach.vision.timer")
    try:
        reading, confidence = detector.detect(crop)
    finally:
        logger.enable("splatoon3_ai_coach.vision.timer")
    if reading is None:
        return None
    parsed = read_display_text(reading.display, confidence=confidence)
    if parsed.clock is None:
        return None
    elapsed = parsed.clock.elapsed_seconds
    if _has_leading_minus(crop):
        elapsed = -abs(elapsed)
    return parsed.clock.model_copy(
        update={
            "source": ClockSource.image,
            "confidence": confidence,
            "display": format_elapsed_clock(elapsed),
            "elapsed_seconds": elapsed,
        }
    )


def _has_leading_minus(image: Any) -> bool:
    """Detect the short leading minus glyph in a pre-GO clock label."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = (gray > 180).astype("uint8")
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    for label in range(1, count):
        x, y, width, height, area = stats[label]
        if x < image.shape[1] * 0.38 and width >= 8 and width >= height * 2:
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
        (item for item in samples if item.status == "clocked_timeline"), start=0
    ):
        assert sample.clock is not None
        entries.append(
            {
                "capture_index": index,
                "image_path": sample.image_path,
                "elapsed_clock": sample.clock.display,
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

