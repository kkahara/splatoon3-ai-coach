"""Video acquisition source and evidence observability (run-level metadata).

Source determination is a run-level decision; detector observability is
evidence-specific. ``VideoSource`` does not change GameEvent semantics.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from splatoon3_ai_coach.exceptions import S3CoachError


class VideoSource(StrEnum):
    """How the video was obtained — not what evidence means."""

    SCREEN_CAPTURE = "screen_capture"
    REVIEW = "review"
    HAND_CAPTURE = "hand_capture"


class Observability(StrEnum):
    """Whether a given evidence kind can be asserted from this source."""

    OBSERVABLE = "observable"
    UNOBSERVABLE = "unobservable"
    POTENTIALLY_OBSERVABLE = "potentially_observable"


class AnalysisQuality(StrEnum):
    """Video-level usability for the current pipeline (assessed later)."""

    SUFFICIENT = "sufficient"
    MARGINAL = "marginal"
    INSUFFICIENT = "insufficient"


SourceDetermination = Literal[
    "default",
    "declared",
    "detected_review_icon",
    "declared_with_review_icon",
]


class UnsupportedVideoSourceError(S3CoachError):
    """Raised when declared source conflicts with detected Review chrome."""


class VideoRunMetadata(BaseModel):
    """Persisted acquisition + resolution provenance for one analysis run."""

    source: VideoSource = VideoSource.SCREEN_CAPTURE
    source_declared: VideoSource | None = None
    source_determination: SourceDetermination = "default"
    review_icon_detected: bool = False
    review_icon_video_time: float | None = None
    review_icon_score: float | None = None
    original_width: int = Field(gt=0)
    original_height: int = Field(gt=0)
    analysis_width: int = Field(gt=0)
    analysis_height: int = Field(gt=0)
    analysis_quality: AnalysisQuality | None = None
    map_overlay_observability: Observability = Observability.OBSERVABLE


def analysis_frame_size(
    original_width: int,
    original_height: int,
    *,
    max_width: int,
    max_height: int,
) -> tuple[int, int]:
    """Width/height of frames after optional downscale (no upscale)."""
    scale = min(max_width / original_width, max_height / original_height, 1.0)
    if scale >= 1.0:
        return int(original_width), int(original_height)
    return max(1, int(original_width * scale)), max(1, int(original_height * scale))


def resolve_video_source(
    *,
    declared: VideoSource | None,
    review_icon_detected: bool,
    review_icon_video_time: float | None = None,
    review_icon_score: float | None = None,
) -> tuple[VideoSource, SourceDetermination]:
    """Apply Phase 1 source-determination rules.

    Raises ``UnsupportedVideoSourceError`` when ``hand_capture`` is declared
    and the Review icon is detected.
    """
    if declared is VideoSource.HAND_CAPTURE and review_icon_detected:
        raise UnsupportedVideoSourceError(
            "Review UI detected while source=hand_capture; hand-captured Review "
            "is unsupported. Re-capture via HDMI screen capture or digital Review, "
            "or declare --video-source review only for a digital Review recording."
        )

    if declared is VideoSource.HAND_CAPTURE:
        return VideoSource.HAND_CAPTURE, "declared"

    if declared is VideoSource.REVIEW:
        if review_icon_detected:
            return VideoSource.REVIEW, "declared_with_review_icon"
        return VideoSource.REVIEW, "declared"

    # none or screen_capture
    if review_icon_detected:
        return VideoSource.REVIEW, "detected_review_icon"

    if declared is VideoSource.SCREEN_CAPTURE:
        return VideoSource.SCREEN_CAPTURE, "declared"

    return VideoSource.SCREEN_CAPTURE, "default"


def build_video_run_metadata(
    *,
    declared: VideoSource | None,
    review_icon_detected: bool,
    review_icon_video_time: float | None,
    review_icon_score: float | None,
    original_width: int,
    original_height: int,
    analysis_width: int,
    analysis_height: int,
) -> VideoRunMetadata:
    """Resolve source and assemble persisted video nest."""
    from splatoon3_ai_coach.media.source_capabilities import map_overlay_observability

    source, determination = resolve_video_source(
        declared=declared,
        review_icon_detected=review_icon_detected,
        review_icon_video_time=review_icon_video_time,
        review_icon_score=review_icon_score,
    )
    return VideoRunMetadata(
        source=source,
        source_declared=declared,
        source_determination=determination,
        review_icon_detected=review_icon_detected,
        review_icon_video_time=review_icon_video_time,
        review_icon_score=review_icon_score,
        original_width=original_width,
        original_height=original_height,
        analysis_width=analysis_width,
        analysis_height=analysis_height,
        analysis_quality=None,
        map_overlay_observability=map_overlay_observability(source),
    )
