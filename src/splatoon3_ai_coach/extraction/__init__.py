"""Meaningful-frame extraction: signals, triggers, sampling, selection."""

from splatoon3_ai_coach.extraction.extractor import MeaningfulFrameExtractor
from splatoon3_ai_coach.extraction.models import (
    ExtractionManifest,
    ExtractionResult,
    ManifestFrame,
    SelectedFrame,
    TriggerEvent,
    TriggerType,
)
from splatoon3_ai_coach.extraction.pipeline import run_extraction
from splatoon3_ai_coach.extraction.sampler import FrameSampler

__all__ = [
    "ExtractionManifest",
    "ExtractionResult",
    "FrameSampler",
    "ManifestFrame",
    "MeaningfulFrameExtractor",
    "SelectedFrame",
    "TriggerEvent",
    "TriggerType",
    "run_extraction",
]
