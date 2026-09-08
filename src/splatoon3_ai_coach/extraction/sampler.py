"""Decimate a decoded video stream to a fixed analysis cadence."""

from collections.abc import Iterator

from splatoon3_ai_coach.media.video import VideoFrame


class FrameSampler:
    """Yield frames spaced at least `step_seconds` apart.

    Consumes an already-decoded ``VideoFrame`` iterator. Does not open, seek,
    or decode video. Vision uses this to pick cadence observations from a
    single ``VideoLoader.frames()`` pass.
    """

    def __init__(self, step_seconds: float) -> None:
        if step_seconds <= 0:
            raise ValueError("step_seconds must be positive")
        self.step_seconds = step_seconds

    def sample(self, frames: Iterator[VideoFrame]) -> Iterator[VideoFrame]:
        """Keep frames whose timestamps are at least ``step_seconds`` apart.

        Walks the given iterator in order and either yields a frame or
        discards it. Does not seek, reopen, or decode video.
        """
        last_sampled = -float("inf")
        for video_frame in frames:
            if video_frame.timestamp - last_sampled < self.step_seconds:
                continue
            last_sampled = video_frame.timestamp
            yield video_frame
