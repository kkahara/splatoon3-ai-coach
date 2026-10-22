"""Create and update capture packages on disk.

Artifacts are stored byte-identical: copied, hash-verified against the source,
then renamed into place read-only. Nothing here re-encodes, remuxes or trims.
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path

from loguru import logger

from splatoon3_ai_coach.capture.models import (
    ARTIFACTS_DIRNAME,
    CAPTURE_PACKAGE_FILENAME,
    TIMELINE_CAPTURE_V1,
    AcquisitionMethod,
    ArtifactKind,
    ArtifactStatus,
    CaptureArtifact,
    CapturePackage,
    CheckStatus,
    GateSummary,
    ManualAttestation,
    SourceType,
    utc_now,
)
from splatoon3_ai_coach.config.models import VisionLanguage
from splatoon3_ai_coach.exceptions import S3CoachError
from splatoon3_ai_coach.media.video import SUPPORTED_SUFFIXES, probe_video_stream
from splatoon3_ai_coach.media.video_identity import video_identity

_KINDS_BY_SOURCE = {
    SourceType.REVIEW_CODE: {ArtifactKind.TIMELINE, ArtifactKind.TOP_DOWN},
    SourceType.UPLOADED_VIDEO: {ArtifactKind.GAMEPLAY},
}


class CapturePackageError(S3CoachError):
    """Raised when a package operation would break the package contract."""


def package_path(package_dir: Path) -> Path:
    """Path of ``capture_package.json`` inside a package directory."""
    return package_dir / CAPTURE_PACKAGE_FILENAME


def load_package(package_dir: Path) -> CapturePackage:
    """Read and validate a package manifest."""
    path = package_path(package_dir)
    if not path.is_file():
        raise CapturePackageError(f"no {CAPTURE_PACKAGE_FILENAME} in {package_dir}")
    return CapturePackage.model_validate_json(path.read_text(encoding="utf-8"))


def save_package(package_dir: Path, package: CapturePackage) -> Path:
    """Write the manifest atomically."""
    path = package_path(package_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(package.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def create_package(
    package_dir: Path,
    *,
    source_type: SourceType,
    acquisition_method: AcquisitionMethod,
    language: VisionLanguage,
    review_code: str | None = None,
    note: str | None = None,
) -> CapturePackage:
    """Create a new package in an empty or missing directory."""
    if package_dir.exists() and any(package_dir.iterdir()):
        raise CapturePackageError(f"{package_dir} is not empty")
    package = CapturePackage(
        package_id=uuid.uuid4().hex[:12],
        source_type=source_type,
        acquisition_method=acquisition_method,
        review_code=review_code,
        language=language,
        created_at=utc_now(),
        note=note,
    )
    package_dir.mkdir(parents=True, exist_ok=True)
    save_package(package_dir, package)
    return package


def artifact_id_for(kind: ArtifactKind, playback_rate: str | None) -> str:
    """``timeline`` / ``gameplay``, or ``top_down_<rate>`` (one per declared rate)."""
    if kind is not ArtifactKind.TOP_DOWN:
        return kind.value
    if not playback_rate:
        raise CapturePackageError("top_down artifacts need a declared playback rate")
    slug = re.sub(r"[^a-z0-9.]+", "_", playback_rate.lower()).strip("_")
    return f"top_down_{slug}"


def add_artifact(
    package_dir: Path,
    source: Path,
    kind: ArtifactKind,
    *,
    playback_rate: str | None = None,
    note: str | None = None,
) -> CaptureArtifact:
    """Copy one acquired recording into the package, verified byte-identical."""
    package = load_package(package_dir)
    _check_kind(package, kind, playback_rate)
    artifact_id = artifact_id_for(kind, playback_rate)
    if package.artifact(artifact_id) is not None:
        raise CapturePackageError(f"artifact {artifact_id} already stored")
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise CapturePackageError(f"unsupported video suffix {suffix or '<none>'}")
    relative = f"{ARTIFACTS_DIRNAME}/{artifact_id}{suffix}"
    identity = _copy_verified(source, package_dir / relative)
    artifact = CaptureArtifact(
        artifact_id=artifact_id,
        kind=kind,
        status=_status_for(kind),
        contract=TIMELINE_CAPTURE_V1 if kind is ArtifactKind.TIMELINE else None,
        path=relative,
        original_filename=source.name,
        video_identity=identity,
        size_bytes=(package_dir / relative).stat().st_size,
        properties=probe_video_stream(package_dir / relative),
        playback_rate_declared=playback_rate if kind is ArtifactKind.TOP_DOWN else None,
        added_at=utc_now(),
        note=note,
    )
    package.artifacts.append(artifact)
    save_package(package_dir, package)
    return artifact


def verify_artifact(package_dir: Path, artifact: CaptureArtifact) -> bool:
    """Whether the stored file still has the identity recorded at ingest."""
    path = package_dir / artifact.path
    return path.is_file() and video_identity(path) == artifact.video_identity


def record_attestation(
    package_dir: Path,
    *,
    artifact_id: str,
    check_id: str,
    status: CheckStatus,
    note: str,
) -> ManualAttestation:
    """Append an operator attestation (earlier ones are kept, the latest wins)."""
    if status not in (CheckStatus.PASS, CheckStatus.FAIL):
        raise CapturePackageError("an attestation is pass or fail")
    if not note.strip():
        raise CapturePackageError("an attestation needs a note")
    package = load_package(package_dir)
    attestation = ManualAttestation(
        artifact_id=artifact_id,
        check_id=check_id,
        status=status,
        note=note.strip(),
        recorded_at=utc_now(),
    )
    package.attestations.append(attestation)
    save_package(package_dir, package)
    return attestation


def store_gate_summary(package_dir: Path, summary: GateSummary) -> None:
    """Replace the gate summary for one artifact."""
    package = load_package(package_dir)
    package.gates = [g for g in package.gates if g.artifact_id != summary.artifact_id]
    package.gates.append(summary)
    save_package(package_dir, package)


def attestations_for(
    package: CapturePackage, artifact_id: str
) -> list[ManualAttestation]:
    """Attestations recorded for one artifact, oldest first."""
    return [a for a in package.attestations if a.artifact_id == artifact_id]


def _check_kind(
    package: CapturePackage, kind: ArtifactKind, playback_rate: str | None
) -> None:
    if kind not in _KINDS_BY_SOURCE[package.source_type]:
        raise CapturePackageError(
            f"{kind.value} artifacts are not part of {package.source_type.value} packages"
        )
    if kind is not ArtifactKind.TOP_DOWN and playback_rate is not None:
        raise CapturePackageError("a playback rate is declared only for top_down")


def _status_for(kind: ArtifactKind) -> ArtifactStatus:
    if kind is ArtifactKind.TOP_DOWN:
        return ArtifactStatus.EXPERIMENTAL
    return ArtifactStatus.REQUIRED


def _copy_verified(source: Path, destination: Path) -> str:
    """Copy, compare content identities, then rename read-only into place."""
    if not source.is_file():
        raise CapturePackageError(f"not a file: {source}")
    if destination.exists():
        raise CapturePackageError(f"{destination.name} already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    shutil.copyfile(source, partial)
    expected = video_identity(source)
    if video_identity(partial) != expected:
        partial.unlink()
        raise CapturePackageError(f"copy of {source.name} does not match the source")
    os.replace(partial, destination)
    destination.chmod(0o444)
    logger.info("Stored {} as {} ({})", source.name, destination.name, expected[:12])
    return expected
