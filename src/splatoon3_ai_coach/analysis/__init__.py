"""Frame analysis: signals, detectors, and meaningful-frame extraction."""

from splatoon3_ai_coach.analysis.extractor import MeaningfulFrameExtractor
from splatoon3_ai_coach.analysis.manifest import save_manifest
from splatoon3_ai_coach.analysis.models import (
    Event,
    EventType,
    ExtractionManifest,
    ExtractionResult,
    ManifestFrame,
    SelectedFrame,
)

__all__ = [
    "Event",
    "EventType",
    "ExtractionManifest",
    "ExtractionResult",
    "ManifestFrame",
    "MeaningfulFrameExtractor",
    "SelectedFrame",
    "save_manifest",
]
