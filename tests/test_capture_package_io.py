"""Capture package creation, byte-identical artifact storage and probing."""

import hashlib
from pathlib import Path

import pytest

from splatoon3_ai_coach.capture.models import (
    AcquisitionMethod,
    ArtifactKind,
    ArtifactStatus,
    CheckStatus,
    SourceType,
)
from splatoon3_ai_coach.capture.package_io import (
    CapturePackageError,
    add_artifact,
    create_package,
    load_package,
    record_attestation,
    verify_artifact,
)
from splatoon3_ai_coach.exceptions import VideoLoadError
from splatoon3_ai_coach.media.video import probe_video_stream


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _review_package(path: Path) -> Path:
    create_package(
        path,
        source_type=SourceType.REVIEW_CODE,
        acquisition_method=AcquisitionMethod.MANUAL,
        language="en",
        review_code="ABCD-EFGH-1234-5678",
    )
    return path


def test_probe_reads_declared_stream_properties(sample_video: Path) -> None:
    props = probe_video_stream(sample_video)
    assert (props.width, props.height) == (320, 180)
    assert props.avg_fps == pytest.approx(30.0)
    assert props.r_frame_rate == "30/1"
    assert props.suffix == ".mp4"
    assert props.declared_frame_count == 60


def test_probe_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(VideoLoadError):
        probe_video_stream(tmp_path / "missing.mov")


def test_new_refuses_a_non_empty_directory(tmp_path: Path) -> None:
    (tmp_path / "existing.txt").write_text("x")
    with pytest.raises(CapturePackageError):
        _review_package(tmp_path)


def test_add_stores_a_byte_identical_read_only_copy(
    tmp_path: Path, sample_video: Path
) -> None:
    package_dir = _review_package(tmp_path / "pkg")
    before = _sha(sample_video)
    artifact = add_artifact(package_dir, sample_video, ArtifactKind.TIMELINE)
    stored = package_dir / artifact.path
    assert artifact.path == "artifacts/timeline.mp4"
    assert artifact.contract == "timeline-capture-v1"
    assert artifact.original_filename == "sample.mp4"
    assert _sha(stored) == before == _sha(sample_video)
    assert stored.stat().st_mode & 0o222 == 0
    assert not list(stored.parent.glob("*.part"))
    assert verify_artifact(package_dir, artifact)
    assert load_package(package_dir).artifact("timeline") == artifact


def test_tampered_artifact_fails_verification(tmp_path: Path, sample_video: Path) -> None:
    package_dir = _review_package(tmp_path / "pkg")
    artifact = add_artifact(package_dir, sample_video, ArtifactKind.TIMELINE)
    stored = package_dir / artifact.path
    stored.chmod(0o644)
    stored.write_bytes(stored.read_bytes()[:-1] + b"\x00")
    assert not verify_artifact(package_dir, artifact)


def test_duplicate_artifact_is_refused(tmp_path: Path, sample_video: Path) -> None:
    package_dir = _review_package(tmp_path / "pkg")
    add_artifact(package_dir, sample_video, ArtifactKind.TIMELINE)
    with pytest.raises(CapturePackageError):
        add_artifact(package_dir, sample_video, ArtifactKind.TIMELINE)


def test_top_down_needs_a_declared_rate_and_stays_experimental(
    tmp_path: Path, sample_video: Path
) -> None:
    package_dir = _review_package(tmp_path / "pkg")
    with pytest.raises(CapturePackageError):
        add_artifact(package_dir, sample_video, ArtifactKind.TOP_DOWN)
    artifact = add_artifact(
        package_dir, sample_video, ArtifactKind.TOP_DOWN, playback_rate="1x"
    )
    assert artifact.artifact_id == "top_down_1x"
    assert artifact.status is ArtifactStatus.EXPERIMENTAL
    assert artifact.contract is None
    assert artifact.playback_rate_declared == "1x"
    assert artifact.effective_playback_rate is None


def test_kind_must_belong_to_the_source_type(tmp_path: Path, sample_video: Path) -> None:
    package_dir = _review_package(tmp_path / "pkg")
    with pytest.raises(CapturePackageError):
        add_artifact(package_dir, sample_video, ArtifactKind.GAMEPLAY)
    upload_dir = tmp_path / "upload"
    create_package(
        upload_dir,
        source_type=SourceType.UPLOADED_VIDEO,
        acquisition_method=AcquisitionMethod.UPLOAD,
        language="ja",
    )
    with pytest.raises(CapturePackageError):
        add_artifact(upload_dir, sample_video, ArtifactKind.TIMELINE)
    assert add_artifact(upload_dir, sample_video, ArtifactKind.GAMEPLAY).contract is None


def test_attestation_needs_a_note_and_a_decision(tmp_path: Path) -> None:
    package_dir = _review_package(tmp_path / "pkg")
    with pytest.raises(CapturePackageError):
        record_attestation(
            package_dir,
            artifact_id="timeline",
            check_id="x",
            status=CheckStatus.PASS,
            note=" ",
        )
    with pytest.raises(CapturePackageError):
        record_attestation(
            package_dir,
            artifact_id="timeline",
            check_id="x",
            status=CheckStatus.UNKNOWN,
            note="n",
        )
