"""Configuration schema for the whole application.

Every tunable value the pipeline reads is declared here, so `configs/*.yaml`
is validated in one place instead of being unpacked at the point of use.
"""

from pathlib import Path

from pydantic import BaseModel, Field, field_validator

NormalizedBox = tuple[float, float, float, float]


class VideoConfig(BaseModel):
    """Limits applied to decoded frames before analysis."""

    max_width: int = Field(gt=0)
    max_height: int = Field(gt=0)


class PathsConfig(BaseModel):
    """Default output locations, used when the CLI is not given explicit ones."""

    frame_output: Path
    manifest_output: Path


class HudRegions(BaseModel):
    """Normalized HUD regions, each as x1, y1, x2, y2 in [0, 1]."""

    killfeed: NormalizedBox
    special_gauge: NormalizedBox
    objective_timer: NormalizedBox
    death_text: NormalizedBox

    @field_validator("*")
    @classmethod
    def _check_box(cls, box: NormalizedBox) -> NormalizedBox:
        x1, y1, x2, y2 = box
        if not all(0.0 <= value <= 1.0 for value in box):
            raise ValueError(f"region coordinates must be in [0, 1]: {box}")
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"region must have positive area: {box}")
        return box


class ExtractionConfig(BaseModel):
    """Settings controlling meaningful-frame extraction."""

    analysis_fps: float = Field(gt=0)
    scene_threshold: float = Field(gt=0)
    ssim_threshold: float = Field(gt=0, le=1)
    motion_threshold: float = Field(gt=0)
    hud_threshold: float = Field(gt=0)
    min_event_gap_seconds: float = Field(ge=0)
    context_before_seconds: float = Field(ge=0)
    context_after_seconds: float = Field(ge=0)
    max_frames_per_minute: int = Field(gt=0)
    save_jpeg_quality: int = Field(ge=1, le=100)
    hud: HudRegions

    @property
    def analysis_step_seconds(self) -> float:
        """Minimum spacing between frames handed to the detectors."""
        return 1.0 / self.analysis_fps


class AppConfig(BaseModel):
    """Top-level application configuration."""

    video: VideoConfig
    paths: PathsConfig
    extraction: ExtractionConfig
