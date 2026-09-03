"""Decimate a decoded video stream to a fixed analysis cadence."""

from collections.abc import Iterator

from splatoon3_ai_coach.media.video import VideoFrame


class FrameSampler:
    """Yield frames spaced at least `step_seconds` apart.

    Shared by extraction (event-triggered) and vision (fixed-cadence HUD reads)
    so both stages consume the same decimated stream.
    """

    def __init__(self, step_seconds: float) -> None:
        if step_seconds <= 0:
            raise ValueError("step_seconds must be positive")
        self.step_seconds = step_seconds

    def sample(self, frames: Iterator[VideoFrame]) -> Iterator[VideoFrame]:
        """Yield frames at the configured analysis cadence."""
        last_sampled = -float("inf")
        for video_frame in frames:
            if video_frame.timestamp - last_sampled < self.step_seconds:
                continue
            last_sampled = video_frame.timestamp
            yield video_frame
