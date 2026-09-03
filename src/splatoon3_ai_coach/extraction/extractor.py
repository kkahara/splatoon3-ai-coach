"""Selection of meaningful frames from a decoded video stream.

The extractor walks the video once and keeps a frame only when a change trigger
fires. It never saves every Nth frame: `analysis_fps` controls how often
triggers run, not how often frames are kept.
"""

from collections import deque

from loguru import logger

from splatoon3_ai_coach.config.models import ExtractionConfig
from splatoon3_ai_coach.extraction.models import (
    ChangeTrigger,
    ExtractionResult,
    SelectedFrame,
    TriggerEvent,
    TriggerType,
)
from splatoon3_ai_coach.extraction.sampler import FrameSampler
from splatoon3_ai_coach.extraction.signals import grayscale_histogram
from splatoon3_ai_coach.extraction.triggers import ChangeTriggerPipeline
from splatoon3_ai_coach.media.video import VideoFrame, VideoLoader

RATE_LIMIT_WINDOW_SECONDS = 60.0


class MeaningfulFrameExtractor:
    """Detect candidate triggers and retain representative evidence frames."""

    def __init__(self, config: ExtractionConfig) -> None:
        self.config = config
        self.triggers = ChangeTriggerPipeline(config)
        self.sampler = FrameSampler(config.analysis_step_seconds)

    def extract(self, loader: VideoLoader) -> ExtractionResult:
        """Walk the video and return the frames and triggers worth keeping."""
        result = ExtractionResult()
        previous: VideoFrame | None = None
        previous_histogram = None
        last_event_time = -float("inf")
        recent_timestamps: deque[float] = deque()

        for video_frame in self.sampler.sample(loader.frames()):
            histogram = grayscale_histogram(video_frame.image)

            if previous is None:
                self._record(
                    result,
                    video_frame,
                    [ChangeTrigger(TriggerType.KEYFRAME, 1.0)],
                    recent_timestamps,
                )
                last_event_time = video_frame.timestamp
            else:
                fired = self.triggers.evaluate(
                    previous.image,
                    video_frame.image,
                    previous_histogram,
                    histogram,
                )
                gap = video_frame.timestamp - last_event_time
                if fired and gap >= self.config.min_event_gap_seconds:
                    if self._within_rate_limit(video_frame.timestamp, recent_timestamps):
                        self._record(result, video_frame, fired, recent_timestamps)
                        last_event_time = video_frame.timestamp
                    else:
                        logger.debug(
                            "Rate limit reached, skipping frame at {:.3f}s",
                            video_frame.timestamp,
                        )

            previous = video_frame
            previous_histogram = histogram

        logger.info(
            "Selected {} frames from {} triggers",
            len(result.frames),
            len(result.trigger_events),
        )
        return result

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
        triggers: list[ChangeTrigger],
        recent_timestamps: deque[float],
    ) -> None:
        """Append one frame and its trigger event."""
        strongest = triggers[0]
        result.frames.append(
            SelectedFrame(
                timestamp=video_frame.timestamp,
                image=video_frame.image,
                trigger_type=strongest.trigger_type,
                confidence=strongest.confidence,
                source_frame_index=video_frame.frame_index,
                source_pts=video_frame.source_pts,
                source_time_base_num=video_frame.source_time_base_num,
                source_time_base_den=video_frame.source_time_base_den,
            )
        )
        result.trigger_events.append(
            TriggerEvent(
                timestamp=video_frame.timestamp,
                trigger_type=strongest.trigger_type,
                confidence=strongest.confidence,
                context_start=max(
                    video_frame.timestamp - self.config.context_before_seconds, 0.0
                ),
                context_end=video_frame.timestamp + self.config.context_after_seconds,
                signals={t.trigger_type: t.confidence for t in triggers},
                frame_indices=[len(result.frames) - 1],
            )
        )
        recent_timestamps.append(video_frame.timestamp)
