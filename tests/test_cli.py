"""End-to-end tests that exercise the CLI wiring, config included."""

from pathlib import Path

from typer.testing import CliRunner

from splatoon3_ai_coach.cli import app
from splatoon3_ai_coach.config import default_config_path
from splatoon3_ai_coach.media.manifest import load_manifest

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


def test_analyze_does_not_require_extraction(sample_video: Path, tmp_path: Path) -> None:
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


def test_analyze_language_flag_persists_in_manifest(
    sample_video: Path, tmp_path: Path
) -> None:
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
            "--language",
            "ja",
        ],
    )
    assert result.exit_code == 0, result.stdout
    import json

    manifest = json.loads((out / "vision_manifest.json").read_text(encoding="utf-8"))
    assert manifest["analysis"]["language"] == "ja"


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
