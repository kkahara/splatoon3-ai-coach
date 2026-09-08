"""Video ingestion through PyAV/FFmpeg.

This is the only module that talks to PyAV. Everything downstream consumes the
`VideoFrame` iterator, which keeps decoding concerns out of the analysis code.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import av
import cv2
import numpy as np
from pydantic import BaseModel, Field

from splatoon3_ai_coach.exceptions import VideoLoadError

SUPPORTED_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".avi", ".klv"})


class VideoMetadata(BaseModel):
    """Metadata describing the primary video stream."""

    path: Path
    format_name: str
    fps: float = Field(gt=0)
    duration_seconds: float = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    video_stream_index: int
    metadata_stream_indices: list[int] = Field(default_factory=list)


@dataclass(frozen=True)
class VideoFrame:
    """A decoded BGR frame and its presentation timestamp in seconds."""

    timestamp: float
    frame_index: int
    image: np.ndarray
    source_pts: int | None = None
    source_time_base_num: int | None = None
    source_time_base_den: int | None = None


class VideoLoader:
    """Decode a video file into timestamped BGR frames.

    ``decode_seconds`` is the ingest cost the cadence pipeline actually pays:
    PyAV frame retrieval, ``to_ndarray(format="bgr24")``, and optional
    downscale. It is not FFmpeg-only decode, and it excludes detector work
    after each yield.
    """

    def __init__(
        self,
        path: Path,
        max_width: int | None = None,
        max_height: int | None = None,
    ) -> None:
        self.path = path
        self.max_width = max_width
        self.max_height = max_height
        self.decode_seconds = 0.0
        self.decoded_frame_count = 0
        self._container: av.container.InputContainer | None = None
        self._video_stream: av.video.stream.VideoStream | None = None
        self._metadata: VideoMetadata | None = None

    def open(self) -> VideoMetadata:
        """Open the container and return validated video metadata."""
        if self._metadata is not None:
            return self._metadata

        if not self.path.exists():
            raise VideoLoadError(f"Video not found: {self.path}")

        if self.path.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise VideoLoadError(
                f"Unsupported video format: {self.path.suffix or '<none>'}"
            )

        self._container = av.open(str(self.path))
        video_streams = self._container.streams.video
        if not video_streams:
            raise VideoLoadError(f"No video stream found in {self.path}")

        self._video_stream = video_streams[0]
        self._metadata = VideoMetadata(
            path=self.path,
            format_name=self._container.format.name or "unknown",
            fps=self._read_fps(),
            duration_seconds=self._read_duration_seconds(),
            width=self._video_stream.width,
            height=self._video_stream.height,
            video_stream_index=self._video_stream.index,
            metadata_stream_indices=[
                stream.index
                for stream in self._container.streams
                if stream.type == "data"
            ],
        )
        return self._metadata

    def frames(self) -> Iterator[VideoFrame]:
        """Yield decoded BGR frames, downscaled to the configured limits.

        ``decode_seconds`` accumulates wall time inside this loop, including
        PyAV ``decode()``, BGR conversion, and optional ``cv2.resize``.
        Time spent by the caller after each ``yield`` is not included.
        """
        if self._container is None or self._video_stream is None:
            self.open()

        assert self._container is not None
        assert self._video_stream is not None
        self.decode_seconds = 0.0
        self.decoded_frame_count = 0
        fallback_rate = float(self._video_stream.average_rate or 1.0)
        decode_iter = enumerate(self._container.decode(self._video_stream))
        while True:
            started = perf_counter()
            try:
                index, av_frame = next(decode_iter)
            except StopIteration:
                break
            video_frame = self._to_video_frame(index, av_frame, fallback_rate)
            self.decode_seconds += perf_counter() - started
            self.decoded_frame_count += 1
            yield video_frame

    def close(self) -> None:
        """Close the underlying media container."""
        if self._container is not None:
            self._container.close()
        self._container = None
        self._video_stream = None
        self._metadata = None

    def __enter__(self) -> "VideoLoader":
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _to_video_frame(
        self,
        index: int,
        av_frame: av.VideoFrame,
        fallback_rate: float,
    ) -> VideoFrame:
        """Convert one decoded PyAV frame into a BGR ``VideoFrame``.

        Called inside the ``decode_seconds`` window: BGR ``to_ndarray`` and
        optional downscale are part of ingest cost, not detector time.
        """
        if av_frame.pts is not None and av_frame.time_base is not None:
            timestamp = float(av_frame.pts * av_frame.time_base)
            pts = int(av_frame.pts)
            tb_num = int(av_frame.time_base.numerator)
            tb_den = int(av_frame.time_base.denominator)
        else:
            timestamp = index / fallback_rate
            pts = None
            tb_num = None
            tb_den = None
        return VideoFrame(
            timestamp=max(timestamp, 0.0),
            frame_index=index,
            image=self._downscale(av_frame.to_ndarray(format="bgr24")),
            source_pts=pts,
            source_time_base_num=tb_num,
            source_time_base_den=tb_den,
        )

    def _read_fps(self) -> float:
        assert self._video_stream is not None
        for rate in (self._video_stream.average_rate, self._video_stream.guessed_rate):
            if rate and float(rate) > 0:
                return float(rate)
        raise VideoLoadError(f"Could not determine FPS for {self.path}")

    def _read_duration_seconds(self) -> float:
        assert self._container is not None
        assert self._video_stream is not None

        stream = self._video_stream
        if stream.duration is not None and stream.time_base is not None:
            return max(float(stream.duration * stream.time_base), 0.0)
        if self._container.duration is not None:
            return max(float(self._container.duration / av.time_base), 0.0)
        return 0.0

    def _downscale(self, image: np.ndarray) -> np.ndarray:
        if self.max_width is None or self.max_height is None:
            return image

        height, width = image.shape[:2]
        scale = min(self.max_width / width, self.max_height / height, 1.0)
        if scale >= 1.0:
            return image

        size = (max(int(width * scale), 1), max(int(height * scale), 1))
        return cv2.resize(image, size, interpolation=cv2.INTER_AREA)
