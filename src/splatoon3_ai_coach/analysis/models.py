"""Data models produced by the analysis pipeline.

`SelectedFrame` holds a decoded image and stays in memory; `ManifestFrame` is
its serializable counterpart, written to disk alongside the JPEG.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import numpy as np
from pydantic import BaseModel, Field


class EventType(StrEnum):
    """Reasons a frame can be considered meaningful."""

    SCENE_CHANGE = "scene_change"
    KILLFEED_CHANGE = "killfeed_change"
    SPECIAL_CHANGE = "special_change"
    DEATH = "death"
    OBJECTIVE_CHANGE = "objective_change"
    MOTION = "motion"
    KEYFRAME = "keyframe"


@dataclass(frozen=True)
class Detection:
    """A single detector firing, with confidence in [0, 1]."""

    event_type: EventType
    confidence: float


@dataclass(frozen=True)
class SelectedFrame:
    """A meaningful frame kept in memory, still holding its pixels."""

    timestamp: float
    image: np.ndarray
    event_type: EventType
    confidence: float
    source_frame_index: int


class Event(BaseModel):
    """A detected gameplay event and the evidence window around it.

    `signals` records every detector that fired at this timestamp, not just the
    strongest one, so weaker corroborating evidence is not lost.
    """

    timestamp: float = Field(ge=0)
    event_type: EventType
    confidence: float = Field(ge=0, le=1)
    context_start: float = Field(ge=0)
    context_end: float = Field(ge=0)
    signals: dict[EventType, float] = Field(default_factory=dict)
    frame_indices: list[int] = Field(default_factory=list)


class ManifestFrame(BaseModel):
    """Serializable representation of a saved frame."""

    timestamp: float
    event_type: EventType
    confidence: float
    source_frame_index: int
    path: Path


class ExtractionManifest(BaseModel):
    """JSON-serializable output of an extraction run."""

    video: Path
    frames: list[ManifestFrame] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)


@dataclass
class ExtractionResult:
    """In-memory result of an extraction run, before anything is written."""

    frames: list[SelectedFrame] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
