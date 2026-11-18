"""Stage manifest models and StageWorkspace boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from public_site.stage_artifacts import ArtifactRef, StageManifest, StageWorkspace
from public_site.storage import LocalStorage, file_sha256


def test_stage_manifest_round_trip(tmp_path: Path) -> None:
    ref = ArtifactRef(
        artifact_id="a1",
        artifact_type="source_video",
        storage_provider="local",
        bucket="local",
        object_key="submissions/s1/source.mp4",
        content_type="video/mp4",
        size_bytes=4,
        sha256="a" * 64,
    )
    manifest = StageManifest(
        submission_id="s1",
        stage_run_id="run-1",
        stage="analyze",
        pipeline_version="test",
        inputs=[ref],
        outputs=[],
        created_at=__import__("datetime").datetime.now(
            __import__("datetime").UTC
        ),
    )
    assert manifest.format == "s3coach.stage_manifest.v1"
    restored = StageManifest.model_validate(manifest.model_dump(mode="json"))
    assert restored.inputs[0].object_key == ref.object_key


def test_stage_workspace_materialize_publish_and_cleanup(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "store")
    source = tmp_path / "in.mp4"
    source.write_bytes(b"abcd")
    key = "submissions/s1/source.mp4"
    stored = storage.put_file(key, source, "video/mp4")
    workspace = StageWorkspace(
        tmp_path / "workspaces",
        storage,
        submission_id="s1",
        stage_run_id="run-1",
        stage="analyze",
        storage_provider="local",
    )
    local = workspace.materialize(
        ArtifactRef(
            artifact_id="src",
            artifact_type="source_video",
            storage_provider="local",
            bucket="local",
            object_key=key,
            content_type="video/mp4",
            size_bytes=stored.size_bytes,
            sha256=stored.sha256 or file_sha256(source),
        ),
        "source.mp4",
    )
    assert local.read_bytes() == b"abcd"
    out = workspace.path / "vision_manifest.json"
    out.write_text("{}\n", encoding="utf-8")
    published = workspace.publish_output(
        "vision_manifest.json",
        artifact_id="vm",
        artifact_type="vision_manifest",
        object_key="processing/s1/run-1/vision_manifest.json",
        content_type="application/json",
    )
    assert published.size_bytes == out.stat().st_size
    assert storage.head(published.object_key) is not None
    manifest = workspace.build_manifest(
        inputs=[],
        outputs=[published],
        pipeline_version="test",
    )
    path = workspace.write_manifest(manifest)
    assert path.is_file()
    workspace.cleanup()
    assert not workspace.path.exists()


def test_stage_workspace_rejects_path_escape(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "store")
    workspace = StageWorkspace(
        tmp_path / "workspaces",
        storage,
        submission_id="s1",
        stage_run_id="run-1",
        stage="publish",
        storage_provider="local",
    )
    with pytest.raises(ValueError, match="escapes"):
        workspace.materialize(
            ArtifactRef(
                artifact_id="x",
                artifact_type="report_bundle",
                storage_provider="local",
                bucket="local",
                object_key="reports/s1/r/bundle.json",
                content_type="application/json",
                size_bytes=2,
                sha256="b" * 64,
            ),
            "../outside.json",
        )
