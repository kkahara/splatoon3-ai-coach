"""Tests for cadence-only vision analysis."""

from __future__ import annotations

from pathlib import Path
from time import sleep

import numpy as np
import pytest

from splatoon3_ai_coach.analysis.pipeline import run_analysis
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.media.video import VideoFrame, VideoLoader
from splatoon3_ai_coach.media.vision_manifest import VISION_MANIFEST_FILENAME
from splatoon3_ai_coach.vision.models import (
    ActiveGameplayReading,
    DeathReading,
    MapOverlayReading,
    RespawnReading,
    SplatReading,
    TimerReading,
    VisionTimingMetrics,
)
from splatoon3_ai_coach.vision.pipeline import observe_cadence_stream, run_vision

SIX_DETECTORS = (
    "timer",
    "death",
    "splat",
    "respawn",
    "active_gameplay",
    "map_overlay",
)

_READINGS = {
    "timer": TimerReading(display="3:00", seconds_remaining=180.0),
    "death": DeathReading(),
    "splat": SplatReading(),
    "respawn": RespawnReading(confidence=0.9),
    "active_gameplay": ActiveGameplayReading(),
    "map_overlay": MapOverlayReading(),
}


class _RecordingDetector:
    """Records the exact image object passed to detect()."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.seen_images: list[np.ndarray] = []
        self.decode_calls = 0

    def detect(
        self,
        image: np.ndarray,
        timestamp: float | None = None,
    ) -> tuple[object, float]:
        _ = timestamp
        self.seen_images.append(image)
        return _READINGS[self.name], 0.9


def _solid_frame(index: int, timestamp: float, image: np.ndarray) -> VideoFrame:
    return VideoFrame(timestamp=timestamp, frame_index=index, image=image)


def test_cadence_frame_is_shared_by_all_six_detectors() -> None:
    image = np.zeros((180, 320, 3), dtype=np.uint8)
    detectors = [_RecordingDetector(name) for name in SIX_DETECTORS]
    results, stats = observe_cadence_stream(
        iter([_solid_frame(0, 0.0, image)]),
        detectors,
        cadence_fps=2.0,
        analysis_id="test",
        detector_versions={name: f"{name}@test" for name in SIX_DETECTORS},
    )

    assert stats.decoded_frame_count == 1
    assert stats.cadence_frame_count == 1
    assert len(results) == 1
    assert results[0].frame_path is None
    assert results[0].source == "cadence"
    names = {item.detector_name for item in results[0].detections}
    assert names == set(SIX_DETECTORS)
    assert stats.decode_seconds == 0.0
    for detector in detectors:
        assert len(detector.seen_images) == 1
        assert detector.seen_images[0] is image
        assert stats.detector_invocations[detector.name] == 1


def test_cadence_sampler_decodes_skipped_frames_once() -> None:
    image = np.zeros((180, 320, 3), dtype=np.uint8)
    frames = [
        _solid_frame(0, 0.0, image),
        _solid_frame(1, 0.1, image),
        _solid_frame(2, 0.5, image),
    ]
    detectors = [_RecordingDetector(name) for name in SIX_DETECTORS]
    _results, stats = observe_cadence_stream(
        iter(frames),
        detectors,
        cadence_fps=2.0,
        analysis_id="test",
        detector_versions={name: f"{name}@test" for name in SIX_DETECTORS},
    )

    assert stats.decoded_frame_count == 3
    assert stats.cadence_frame_count == 2
    for detector in detectors:
        assert len(detector.seen_images) == 2
        assert detector.seen_images[0] is image
        assert detector.seen_images[1] is image
        assert stats.detector_invocations[detector.name] == 2


def test_debug_snapshots_are_optional(tmp_path: Path) -> None:
    image = np.zeros((180, 320, 3), dtype=np.uint8)
    detectors = [_RecordingDetector("timer")]
    debug_dir = tmp_path / "debug_snapshots"
    results, _stats = observe_cadence_stream(
        iter([_solid_frame(0, 0.0, image)]),
        detectors,
        cadence_fps=2.0,
        analysis_id="test",
        detector_versions={"timer": "timer@test"},
        debug_dir=debug_dir,
    )

    assert results[0].frame_path is not None
    assert list(debug_dir.glob("*.jpg"))
    assert detectors[0].seen_images[0] is image


def test_run_analysis_does_not_require_extraction(
    sample_video: Path,
    tmp_path: Path,
) -> None:
    config = load_config(default_config_path())
    config.vision.enabled_detectors = list(SIX_DETECTORS)
    output_dir = tmp_path / "analysis"
    manifest = run_analysis(sample_video, config, output_dir)

    assert not (output_dir / "frames").exists()
    assert not (output_dir / "debug_snapshots").exists()
    assert (output_dir / VISION_MANIFEST_FILENAME).exists()
    assert (output_dir / "scenarios.json").exists()
    assert (output_dir / "scenarios.txt").exists()
    assert (output_dir / "scenario_contexts.json").exists()
    assert manifest.extraction_manifest_path is None
    assert manifest.frame_results
    assert all(frame.source == "cadence" for frame in manifest.frame_results)
    assert all(frame.frame_path is None for frame in manifest.frame_results)
    assert manifest.timing is not None
    assert manifest.timing.cadence_frame_count == len(manifest.frame_results)
    assert manifest.timing.decoded_frame_count >= manifest.timing.cadence_frame_count
    assert manifest.timing.decode_seconds > 0
    assert manifest.timing.decode_seconds < manifest.timing.total_seconds
    assert manifest.timing.total_seconds > 0
    assert manifest.state_snapshots
    cadence = manifest.timing.cadence_frame_count
    assert manifest.timing.death_detector_invocations == cadence
    assert manifest.timing.splat_detector_invocations == cadence
    assert manifest.timing.respawn_detector_invocations == cadence
    assert manifest.timing.active_gameplay_detector_invocations == cadence
    assert manifest.timing.map_overlay_detector_invocations == cadence
    always_present = {
        "death",
        "splat",
        "respawn",
        "active_gameplay",
        "map_overlay",
    }
    for frame in manifest.frame_results:
        names = {item.detector_name for item in frame.detections}
        assert always_present <= names


def test_run_vision_opens_video_once(
    sample_video: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opens: list[int] = []

    class _CountingLoader(VideoLoader):
        def __init__(self, *args, **kwargs) -> None:
            opens.append(1)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(
        "splatoon3_ai_coach.vision.pipeline.VideoLoader",
        _CountingLoader,
    )
    config = load_config(default_config_path())
    config.vision.enabled_detectors = list(SIX_DETECTORS)
    run_vision(sample_video, config, tmp_path / "analysis")
    assert len(opens) == 1


def test_debug_persist_writes_observed_cadence_frames(
    sample_video: Path,
    tmp_path: Path,
) -> None:
    config = load_config(default_config_path())
    config.vision.enabled_detectors = ["timer"]
    output_dir = tmp_path / "analysis"
    manifest = run_analysis(
        sample_video,
        config,
        output_dir,
        debug_persist_cadence_frames=True,
    )
    snapshots = list((output_dir / "debug_snapshots").glob("*.jpg"))
    assert snapshots
    assert all(frame.frame_path for frame in manifest.frame_results)


def test_pipeline_decode_seconds_exclude_detector_time(
    sample_video: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _SlowTimer:
        name = "timer"

        def detect(
            self,
            image: np.ndarray,
            timestamp: float | None = None,
        ) -> tuple[TimerReading, float]:
            _ = image, timestamp
            sleep(0.02)
            return TimerReading(display="3:00", seconds_remaining=180.0), 0.9

    monkeypatch.setattr(
        "splatoon3_ai_coach.vision.pipeline.build_detectors",
        lambda _config: [_SlowTimer()],
    )
    config = load_config(default_config_path())
    manifest = run_vision(sample_video, config, tmp_path / "analysis")
    timing = manifest.timing
    assert timing is not None
    assert timing.timer_detector_invocations == timing.cadence_frame_count
    assert timing.timer_detector_seconds >= 0.015 * timing.cadence_frame_count
    assert timing.decode_seconds < timing.timer_detector_seconds


def test_realtime_factor_is_duration_over_total_time() -> None:
    timing = VisionTimingMetrics(
        video_duration_seconds=10.0,
        decoded_frame_count=10,
        cadence_frame_count=5,
        decode_seconds=1.0,
        temporal_seconds=0.5,
        total_seconds=2.0,
    )
    assert timing.realtime_factor == 5.0
