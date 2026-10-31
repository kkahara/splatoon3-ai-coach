"""Scheduling provenance remains available through vision manifest refusion."""

import json
from pathlib import Path

import numpy as np
from vision_manifest_viewer.loader import load_manifest_view

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.media.video import VideoFrame
from splatoon3_ai_coach.media.vision_manifest import (
    load_vision_manifest,
    save_vision_manifest,
)
from splatoon3_ai_coach.vision.clam_basket_fusion import basket_schedule_status
from splatoon3_ai_coach.vision.detector_schedule import DetectorSchedule
from splatoon3_ai_coach.vision.models import (
    AnalysisIdentity,
    ClamBasketStateReading,
    DetectorResult,
    VisionManifest,
)
from splatoon3_ai_coach.vision.pipeline import (
    observe_cadence_stream,
    refuse_vision_manifest,
)


class _NoReading:
    name = "clam_basket_state"
    cadence_fps = 1.0
    run_on_evidence = True

    def detect(self, image: np.ndarray, timestamp: float | None = None):
        return None, 0.0


def test_schedule_roundtrip_legacy_refusion_and_viewer(tmp_path: Path) -> None:
    """A scheduled None result differs from an unscheduled and a legacy frame."""
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    frames = [VideoFrame(timestamp=i * 0.5, frame_index=i, image=image) for i in range(3)]
    results, _ = observe_cadence_stream(
        iter(frames),
        [_NoReading()],
        cadence_fps=2,
        analysis_id="a",
        detector_versions={"clam_basket_state": "test"},
        schedule=DetectorSchedule(intervals={"clam_basket_state": 1.0}, tolerance=0.1),
    )
    assert [f.basket_observation_scheduled for f in results] == [True, False, True]
    assert all(not f.detections for f in results)
    identity = AnalysisIdentity(
        analysis_id="a",
        package_version="test",
        vision_config_sha256="x",
        video_identity="v",
    )
    manifest = VisionManifest(
        analysis=identity, video_identity="v", frame_results=results
    )
    path = save_vision_manifest(manifest, tmp_path)
    loaded = load_vision_manifest(path)
    assert [basket_schedule_status(f) for f in loaded.frame_results] == [
        "scheduled",
        "not_scheduled",
        "scheduled",
    ]
    assert [
        f.basket_observation_scheduled
        for f in refuse_vision_manifest(
            loaded, load_config(default_config_path())
        ).frame_results
    ] == [True, False, True]
    assert load_manifest_view(path) is not None
    raw = json.loads(path.read_text())
    for frame in raw["frame_results"]:
        frame.pop("basket_observation_scheduled")
    legacy = VisionManifest.model_validate(raw)
    assert all(
        basket_schedule_status(f) == "schedule_unknown" for f in legacy.frame_results
    )
    legacy.frame_results[0].detections.append(
        DetectorResult(
            id="b",
            detector_name="clam_basket_state",
            detector_version="test",
            confidence=0.7,
            reading=ClamBasketStateReading(visual_state="open"),
        )
    )
    assert basket_schedule_status(legacy.frame_results[0]) == "scheduled"
    legacy.frame_results[0].basket_observation_scheduled = False
    assert basket_schedule_status(legacy.frame_results[0]) == "schedule_unknown"
