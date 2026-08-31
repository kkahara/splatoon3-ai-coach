"""End-to-end tests that exercise the CLI wiring, config included."""

import json
from pathlib import Path

from typer.testing import CliRunner

from splatoon3_ai_coach.cli import app

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
            "configs/default.yaml",
        ],
    )

    assert result.exit_code == 0, result.stdout

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["frames"], "expected at least the initial keyframe"
    assert len(list(out.glob("*.jpg"))) == len(manifest["frames"])

    for frame in manifest["frames"]:
        assert Path(frame["path"]).exists()
        assert 0.0 <= frame["confidence"] <= 1.0


def test_extract_reports_a_missing_config(sample_video: Path, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["extract", str(sample_video), "--config", str(tmp_path / "nope.yaml")],
    )

    assert result.exit_code != 0
