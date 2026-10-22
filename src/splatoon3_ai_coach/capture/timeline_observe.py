"""Decode a timeline capture once and collect what the gate needs.

Frames are tapped on their way into the existing ``review.video_adapter``:
the intro detector sees ~2 fps samples, frame timing is recorded, and the
adapter classifies every frame exactly as ``review timeline-video-import``
does. Nothing here changes how either component reads a frame.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from loguru import logger

from splatoon3_ai_coach.config.models import (
    MatchIntroDetectorConfig,
    ReviewTimelineConfig,
)
from splatoon3_ai_coach.media.video import (
    VideoFrame,
    VideoLoader,
    VideoStreamProperties,
    probe_video_stream,
)
from splatoon3_ai_coach.review.video_adapter import VideoSample, adapt_frames
from splatoon3_ai_coach.vision.match_intro import MatchIdentityTracker, MatchIntroDetector

INTRO_SAMPLE_INTERVAL_SECONDS = 0.5


@dataclass
class IntroObservation:
    """Latched intro identity plus the best raw scores, even below threshold."""

    battle_mode_id: str | None = None
    stage_id: str | None = None
    resolved_at: float | None = None
    best_battle_mode_score: float = 0.0
    best_battle_mode_time: float | None = None
    best_stage_score: float = 0.0
    best_stage_time: float | None = None
    frames_checked: int = 0
    match_threshold: float = 0.0


@dataclass
class FrameTiming:
    """Decoded frame timestamps; ``repeats`` marks pixel-identical repeats.

    Static content also decodes identically, so repeats indicate duplicated
    source frames only where the picture is known to move (cursor sweeps).
    """

    timestamps: list[float] = field(default_factory=list)
    repeats: list[bool] = field(default_factory=list)


@dataclass(frozen=True)
class TimelineObservation:
    """Everything one decode of a timeline capture produced."""

    properties: VideoStreamProperties
    timing: FrameTiming
    intro: IntroObservation
    samples: tuple[VideoSample, ...]
    decode_error: str | None = None


class _IntroProbe:
    """Run the intro detector on sampled frames until identity latches."""

    def __init__(self, detector: MatchIntroDetector, config: MatchIntroDetectorConfig):
        self._detector = detector
        self._tracker = MatchIdentityTracker(
            intro_deadline_seconds=config.intro_deadline_seconds
        )
        self._next_time = 0.0
        self.observation = IntroObservation(match_threshold=config.match_threshold)

    def update(self, frame: VideoFrame) -> None:
        if frame.timestamp + 1e-9 < self._next_time:
            return
        if not self._tracker.should_run_intro_detector(frame.timestamp):
            return
        self._next_time = frame.timestamp + INTRO_SAMPLE_INTERVAL_SECONDS
        reading, _ = self._detector.detect(frame.image, frame.timestamp)
        self._tracker.update(reading, video_time=frame.timestamp)
        self._record(
            reading.battle_mode_template_score, reading.stage_template_score, frame
        )

    def _record(self, mode_score: float, stage_score: float, frame: VideoFrame) -> None:
        obs = self.observation
        obs.frames_checked += 1
        if mode_score > obs.best_battle_mode_score:
            obs.best_battle_mode_score, obs.best_battle_mode_time = (
                mode_score,
                frame.timestamp,
            )
        if stage_score > obs.best_stage_score:
            obs.best_stage_score, obs.best_stage_time = stage_score, frame.timestamp
        identity = self._tracker.identity
        obs.battle_mode_id = identity.battle_mode_id
        obs.stage_id = identity.stage_id
        obs.resolved_at = identity.resolved_at


def observe_timeline_video(
    path: Path,
    *,
    timeline_config: ReviewTimelineConfig,
    intro_config: MatchIntroDetectorConfig,
    language: str,
    samples_dir: Path,
) -> TimelineObservation:
    """Decode once; a decode failure is recorded, never raised."""
    properties = probe_video_stream(path)
    intro = _IntroProbe(MatchIntroDetector(intro_config, language=language), intro_config)
    timing = FrameTiming()
    samples: list[VideoSample] = []
    error: str | None = None
    try:
        with VideoLoader(path) as loader:
            tapped = _tap(loader.frames(), intro, timing)
            samples = adapt_frames(tapped, samples_dir, timeline_config=timeline_config)
    except Exception as exc:  # noqa: BLE001 — any decode failure is a gate result
        logger.warning("Timeline capture decode failed: {}", exc)
        error = f"{type(exc).__name__}: {exc}"
    return TimelineObservation(
        properties, timing, intro.observation, tuple(samples), error
    )


def _tap(
    frames: Iterable[VideoFrame], intro: _IntroProbe, timing: FrameTiming
) -> Iterator[VideoFrame]:
    """Yield frames unchanged after recording timing, repeats and intro readings."""
    previous: np.ndarray | None = None
    for frame in frames:
        timing.timestamps.append(frame.timestamp)
        timing.repeats.append(
            previous is not None and np.array_equal(previous, frame.image)
        )
        previous = frame.image
        intro.update(frame)
        yield frame
