"""Data models for meaningful-frame extraction.

Trigger types describe why a frame was kept. They are pixel-change evidence,
not semantic game facts. Semantic events live in `vision.models.GameEvent`.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field


class TriggerType(StrEnum):
    """Reasons a frame can be considered meaningful during extraction."""

    SCENE_CHANGE = "scene_change"
    KILLFEED_CHANGE = "killfeed_change"
    SPECIAL_CHANGE = "special_change"
    DEATH_UI_CHANGE = "death_ui_change"
    OBJECTIVE_CHANGE = "objective_change"
    MOTION = "motion"
    KEYFRAME = "keyframe"


@dataclass(frozen=True)
class ChangeTrigger:
    """A single change trigger firing, with confidence in [0, 1]."""

    trigger_type: TriggerType
    confidence: float


@dataclass(frozen=True)
class SelectedFrame:
    """A meaningful frame kept in memory, still holding its pixels."""

    timestamp: float
    image: np.ndarray
    trigger_type: TriggerType
    confidence: float
    source_frame_index: int
    source_pts: int | None = None
    source_time_base_num: int | None = None
    source_time_base_den: int | None = None


class TriggerEvent(BaseModel):
    """An extraction trigger and the evidence window around it.

    `signals` records every trigger that fired at this timestamp, not just the
    strongest one, so weaker corroborating evidence is not lost.
    """

    timestamp: float = Field(ge=0)
    trigger_type: TriggerType
    confidence: float = Field(ge=0, le=1)
    context_start: float = Field(ge=0)
    context_end: float = Field(ge=0)
    signals: dict[TriggerType, float] = Field(default_factory=dict)
    frame_indices: list[int] = Field(default_factory=list)


class ManifestFrame(BaseModel):
    """Serializable representation of a saved frame."""

    timestamp: float
    trigger_type: TriggerType
    confidence: float
    source_frame_index: int
    source_pts: int | None = None
    source_time_base_num: int | None = None
    source_time_base_den: int | None = None
    path: Path


class ExtractionManifest(BaseModel):
    """JSON-serializable output of an extraction run."""

    schema_version: int = 1
    video: Path
    frames: list[ManifestFrame] = Field(default_factory=list)
    trigger_events: list[TriggerEvent] = Field(default_factory=list)


@dataclass
class ExtractionResult:
    """In-memory result of an extraction run, before anything is written."""

    frames: list[SelectedFrame] = field(default_factory=list)
    trigger_events: list[TriggerEvent] = field(default_factory=list)
