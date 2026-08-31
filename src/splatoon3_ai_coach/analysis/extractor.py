"""Selection of meaningful frames from a decoded video stream.

The extractor walks the video once and keeps a frame only when a detector
fires. It never saves every Nth frame: `analysis_fps` controls how often
detectors run, not how often frames are kept.
"""

from collections import deque

from loguru import logger

from splatoon3_ai_coach.analysis.detectors import FrameDetectors
from splatoon3_ai_coach.analysis.models import (
    Detection,
    Event,
    EventType,
    ExtractionResult,
    SelectedFrame,
)
from splatoon3_ai_coach.analysis.signals import grayscale_histogram
from splatoon3_ai_coach.config.models import ExtractionConfig
from splatoon3_ai_coach.io.video import VideoFrame, VideoLoader

RATE_LIMIT_WINDOW_SECONDS = 60.0


class MeaningfulFrameExtractor:
    """Detect candidate events and retain representative evidence frames."""

    def __init__(self, config: ExtractionConfig) -> None:
        self.config = config
        self.detectors = FrameDetectors(config)

    def extract(self, loader: VideoLoader) -> ExtractionResult:
        """Walk the video and return the frames and events worth keeping."""
        result = ExtractionResult()
        previous: VideoFrame | None = None
        previous_histogram = None
        last_event_time = -float("inf")
        recent_timestamps: deque[float] = deque()

        for video_frame in self._analysis_frames(loader):
            histogram = grayscale_histogram(video_frame.image)

            if previous is None:
                self._record(
                    result,
                    video_frame,
                    [Detection(EventType.KEYFRAME, 1.0)],
                    recent_timestamps,
                )
                last_event_time = video_frame.timestamp
            else:
                detections = self.detectors.detect(
                    previous.image,
                    video_frame.image,
                    previous_histogram,
                    histogram,
                )
                gap = video_frame.timestamp - last_event_time
                if detections and gap >= self.config.min_event_gap_seconds:
                    if self._within_rate_limit(video_frame.timestamp, recent_timestamps):
                        self._record(result, video_frame, detections, recent_timestamps)
                        last_event_time = video_frame.timestamp
                    else:
                        logger.debug(
                            "Rate limit reached, skipping frame at {:.3f}s",
                            video_frame.timestamp,
                        )

            previous = video_frame
            previous_histogram = histogram

        logger.info(
            "Selected {} frames from {} events",
            len(result.frames),
            len(result.events),
        )
        return result

    def _analysis_frames(self, loader: VideoLoader):
        """Decimate the decoded stream down to the configured analysis rate."""
        step = self.config.analysis_step_seconds
        last_analyzed = -float("inf")

        for video_frame in loader.frames():
            if video_frame.timestamp - last_analyzed < step:
                continue
            last_analyzed = video_frame.timestamp
            yield video_frame

    def _within_rate_limit(
        self,
        timestamp: float,
        recent_timestamps: deque[float],
    ) -> bool:
        """Return whether another frame fits inside `max_frames_per_minute`."""
        cutoff = timestamp - RATE_LIMIT_WINDOW_SECONDS
        while recent_timestamps and recent_timestamps[0] < cutoff:
            recent_timestamps.popleft()
        return len(recent_timestamps) < self.config.max_frames_per_minute

    def _record(
        self,
        result: ExtractionResult,
        video_frame: VideoFrame,
        detections: list[Detection],
        recent_timestamps: deque[float],
    ) -> None:
        """Append one frame and its event, keyed to the strongest detection."""
        strongest = detections[0]
        result.frames.append(
            SelectedFrame(
                timestamp=video_frame.timestamp,
                image=video_frame.image,
                event_type=strongest.event_type,
                confidence=strongest.confidence,
                source_frame_index=video_frame.frame_index,
            )
        )
        result.events.append(
            Event(
                timestamp=video_frame.timestamp,
                event_type=strongest.event_type,
                confidence=strongest.confidence,
                context_start=max(
                    video_frame.timestamp - self.config.context_before_seconds, 0.0
                ),
                context_end=video_frame.timestamp + self.config.context_after_seconds,
                signals={d.event_type: d.confidence for d in detections},
                frame_indices=[len(result.frames) - 1],
            )
        )
        recent_timestamps.append(video_frame.timestamp)
