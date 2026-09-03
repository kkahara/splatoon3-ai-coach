"""Tests for the extraction pipeline service layer."""

from splatoon3_ai_coach.config import default_config_path, load_config
from splatoon3_ai_coach.extraction.pipeline import run_extraction


def test_run_extraction_writes_manifest(sample_video, tmp_path) -> None:
    config = load_config(default_config_path())
    manifest = run_extraction(sample_video, config, output_dir=tmp_path / "frames")

    assert manifest.schema_version == 1
    assert manifest.frames
    assert (tmp_path / "frames" / "manifest.json").exists()
