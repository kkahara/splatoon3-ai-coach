"""Tests for Phase 3 vision models and IDs."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.vision import pipeline as vision_pipeline
from splatoon3_ai_coach.vision import provenance
from splatoon3_ai_coach.vision.ids import (
    compute_analysis_id,
    make_frame_id,
    make_result_id,
    parse_result_id,
    reading_hash,
    result_detector_name,
)
from splatoon3_ai_coach.vision.models import TimerReading, VisionManifest
from splatoon3_ai_coach.vision.provenance import detector_version


def test_reading_hash_is_deterministic() -> None:
    reading = TimerReading(display="2:07", seconds_remaining=127.0)
    assert reading_hash(reading) == reading_hash(reading)


def test_frame_id_uses_source_frame_index() -> None:
    analysis_id = "abc123"
    frame_id = make_frame_id(analysis_id, 42, 1.5)
    assert frame_id == "abc123:frame:42"


def test_result_id_includes_reading_hash() -> None:
    reading = TimerReading(display="1:30", seconds_remaining=90.0)
    result_id = make_result_id("analysis", 10, 5.0, "timer", "timer@0.2.0", reading)
    assert ":timer:timer@0.2.0:" in result_id


def test_analysis_id_is_deterministic() -> None:
    first = compute_analysis_id("video", "extract", "vision")
    second = compute_analysis_id("video", "extract", "vision")
    assert first == second


def test_cadence_only_analysis_id_uses_empty_extraction_hash() -> None:
    first = compute_analysis_id("video", "", "vision")
    second = compute_analysis_id("video", "", "vision")
    assert first == second
    assert first != compute_analysis_id("video", "extract", "vision")


def test_detector_version_never_contains_a_commit() -> None:
    assert detector_version("death", "0.2.0") == "death@0.2.0"
    assert "+" not in detector_version("death")


def test_different_detector_version_changes_the_result_id() -> None:
    reading = TimerReading(display="1:30", seconds_remaining=90.0)
    first = make_result_id("a", 10, 5.0, "timer", "timer@0.2.0", reading)
    second = make_result_id("a", 10, 5.0, "timer", "timer@0.3.0", reading)
    assert first != second


def test_different_frame_changes_frame_and_result_ids() -> None:
    reading = TimerReading(display="1:30", seconds_remaining=90.0)
    assert make_frame_id("a", 10, 5.0) != make_frame_id("a", 11, 5.5)
    first = make_result_id("a", 10, 5.0, "timer", "timer@0.2.0", reading)
    second = make_result_id("a", 11, 5.5, "timer", "timer@0.2.0", reading)
    assert first != second


def test_different_reading_changes_the_result_id() -> None:
    first = TimerReading(display="1:30", seconds_remaining=90.0)
    second = TimerReading(display="1:29", seconds_remaining=89.0)
    assert make_result_id("a", 10, 5.0, "timer", "timer@0.2.0", first) != (
        make_result_id("a", 10, 5.0, "timer", "timer@0.2.0", second)
    )


def test_parse_result_id_reads_fields_from_the_right() -> None:
    reading = TimerReading(display="1:30", seconds_remaining=90.0)
    by_index = make_result_id("a", 10, 5.0, "zone_control", "zone_control@1", reading)
    by_time = make_result_id("a", None, 5.25, "score", "score@1", reading)
    parsed = parse_result_id(by_index)
    assert parsed is not None
    assert (parsed.analysis_id, parsed.frame_key) == ("a", "10")
    assert (parsed.detector_name, parsed.detector_version) == (
        "zone_control",
        "zone_control@1",
    )
    assert result_detector_name(by_time) == "score"
    assert parse_result_id(by_time).frame_key.startswith("ts:")
    assert result_detector_name("death:95.000:ouch:abcd") is None
    assert result_detector_name("zone:1.0:zone_control:read") is None


def _analyze(
    sample_video: Path, out: Path, monkeypatch: pytest.MonkeyPatch, sha: str | None
) -> VisionManifest:
    config = load_config(default_config_path())
    config.vision.enabled_detectors = ["timer", "death", "splat"]
    monkeypatch.setattr(vision_pipeline, "git_short_sha", lambda: sha)
    return vision_pipeline.run_vision(sample_video, config, out)


def _result_ids(manifest: VisionManifest) -> list[str]:
    ids = [result.id for frame in manifest.frame_results for result in frame.detections]
    assert ids, "sample video produced no detector results"
    return ids


def test_ids_are_identical_across_commits_and_sha_stays_in_provenance(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _analyze(sample_video, tmp_path / "a", monkeypatch, "abc1234")
    second = _analyze(sample_video, tmp_path / "b", monkeypatch, "def5678")
    assert first.analysis.analysis_id == second.analysis.analysis_id
    assert _result_ids(first) == _result_ids(second)
    assert all("abc1234" not in rid for rid in _result_ids(first))
    assert first.analysis.git_sha == "abc1234"
    assert second.analysis.git_sha == "def5678"
    assert first.analysis.detector_versions == second.analysis.detector_versions


def test_ids_work_outside_a_git_checkout(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inside = _analyze(sample_video, tmp_path / "a", monkeypatch, "abc1234")
    outside = _analyze(sample_video, tmp_path / "b", monkeypatch, None)
    assert outside.analysis.git_sha is None
    assert _result_ids(outside) == _result_ids(inside)


def test_git_is_resolved_once_per_run(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="abc1234\n", stderr="")

    monkeypatch.setattr(provenance.subprocess, "run", fake_run)
    config = load_config(default_config_path())
    config.vision.enabled_detectors = ["timer", "death", "splat", "respawn"]
    manifest = vision_pipeline.run_vision(sample_video, config, tmp_path / "a")
    assert len(calls) == 1
    assert manifest.analysis.git_sha == "abc1234"


def test_git_lookup_failure_yields_no_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_git(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(provenance.subprocess, "run", missing_git)
    assert provenance.git_short_sha() is None
