"""Configuration schema for the whole application."""

from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from splatoon3_ai_coach.types import NormalizedBox


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
        """Minimum spacing between frames handed to the change triggers."""
        return 1.0 / self.analysis_fps


class TimerDetectorConfig(BaseModel):
    """Settings for calibrated timer template matching."""

    roi: NormalizedBox
    template_dir: Path
    match_threshold: float = Field(default=0.55, ge=0, le=1)
    min_usable_confidence: float = Field(default=0.50, ge=0, le=1)


class StateFusionConfig(BaseModel):
    """Settings for temporal state fusion."""

    smoothing_window: int = Field(default=5, ge=1)
    max_hold_duration: float = Field(default=2.0, gt=0)
    dedupe_tolerance_seconds: float = Field(default=0.05, ge=0)


class EventFusionConfig(BaseModel):
    """Settings for state-to-event transitions."""

    debounce_ms: int = Field(default=300, ge=0)


class VisionConfig(BaseModel):
    """Settings for the semantic vision layer (Phase 3)."""

    enabled_detectors: list[str] = Field(default_factory=lambda: ["timer"])
    timer: TimerDetectorConfig
    hud_cadence_fps: float = Field(default=2.0, gt=0)
    state_fusion: StateFusionConfig = Field(default_factory=StateFusionConfig)
    events: EventFusionConfig = Field(default_factory=EventFusionConfig)


class CoachConfig(BaseModel):
    """Settings for the LLM coaching layer (Phase 5)."""

    provider: str = "openai"
    model: str = "gpt-4o-mini"


class AppConfig(BaseModel):
    """Top-level application configuration."""

    video: VideoConfig
    paths: PathsConfig
    extraction: ExtractionConfig
    vision: VisionConfig
    coach: CoachConfig = Field(default_factory=CoachConfig)
