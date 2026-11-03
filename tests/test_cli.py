"""End-to-end tests that exercise the CLI wiring, config included."""

import json
from pathlib import Path

import pytest
from game_language_helpers import injected_outcome
from typer.testing import CliRunner

from splatoon3_ai_coach.cli import app
from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.media.manifest import load_manifest
from splatoon3_ai_coach.vision import pipeline as vision_pipeline

runner = CliRunner()


def test_inspect_prints_metadata(sample_video: Path) -> None:
    result = runner.invoke(app, ["inspect", str(sample_video)])

    assert result.exit_code == 0
    assert "Video" in result.stdout


def test_extract_writes_frames_and_manifest(sample_video: Path, tmp_path: Path) -> None:
    out = tmp_path / "frames"
    result = runner.invoke(
        app,
        [
            "extract",
            str(sample_video),
            "--out",
            str(out),
            "--config",
            str(default_config_path()),
        ],
    )

    assert result.exit_code == 0, result.stdout

    manifest = load_manifest(out)
    assert manifest.schema_version == 1
    assert manifest.frames, "expected at least the initial keyframe"
    assert len(list(out.glob("*.jpg"))) == len(manifest.frames)

    for frame in manifest.frames:
        assert Path(frame.path).exists()
        assert 0.0 <= frame.confidence <= 1.0


def _detect(monkeypatch: pytest.MonkeyPatch, language: str) -> None:
    """Stand in for the probe: the synthetic sample video has no language cues."""
    outcome = injected_outcome(language, load_config(default_config_path()).vision)
    monkeypatch.setattr(
        vision_pipeline, "probe_game_language", lambda *_a, **_k: outcome
    )


def test_analyze_does_not_require_extraction(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _detect(monkeypatch, "en")
    out = tmp_path / "analysis"
    result = runner.invoke(
        app,
        [
            "analyze",
            str(sample_video),
            "--out",
            str(out),
            "--config",
            str(default_config_path()),
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert (out / "vision_manifest.json").exists()
    assert (out / "scenarios.json").exists()
    assert (out / "scenarios.txt").exists()
    assert (out / "scenario_contexts.json").exists()
    assert not (out / "frames").exists()
    assert "reuse-extraction" not in result.stdout


@pytest.mark.parametrize("detected", ["en", "ja"])
def test_analyze_proceeds_with_the_detected_language(
    sample_video: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, detected: str
) -> None:
    _detect(monkeypatch, detected)
    out = tmp_path / "analysis"
    result = runner.invoke(
        app,
        [
            "analyze",
            str(sample_video),
            "--out",
            str(out),
            "--config",
            str(default_config_path()),
        ],
    )
    assert result.exit_code == 0, result.stdout
    manifest = json.loads((out / "vision_manifest.json").read_text(encoding="utf-8"))
    assert manifest["analysis"]["language"] == detected
    assert manifest["game_language"]["language"] == detected
    assert "supplied_language" not in manifest["game_language"]
    assert "supplied_agreement" not in manifest["game_language"]
    assert (out / "scenarios.json").exists()
    assert (out / "scenario_contexts.json").exists()


def test_extract_uses_default_config_when_none_given(
    sample_video: Path,
    tmp_path: Path,
) -> None:
    out = tmp_path / "frames"
    result = runner.invoke(app, ["extract", str(sample_video), "--out", str(out)])

    assert result.exit_code == 0, result.stdout
    manifest = load_manifest(out)
    assert manifest.trigger_events


def test_extract_reports_a_missing_config(sample_video: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["extract", str(sample_video), "--config", str(tmp_path / "nope.yaml")],
    )

    assert result.exit_code != 0
    assert "Configuration error" in result.stdout
