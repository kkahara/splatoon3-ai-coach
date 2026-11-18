"""Portable stage manifests and a small temporary workspace helper.

Scope is intentionally narrow: materialize ``ArtifactRef`` inputs from object
storage, run one stage against a temporary directory, record outputs, and clean
up. This is not a distributed orchestrator and does not touch ``vision/`` or
``coach/`` internals.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from public_site.storage import file_sha256

MANIFEST_FORMAT = "s3coach.stage_manifest.v1"
ArtifactType = Literal[
    "source_video",
    "vision_manifest",
    "map_observations",
    "coach_inputs_bundle",
    "coach_prototype_bundle",
    "moment_clip",
    "moment_frame",
    "report_bundle",
]
StageName = Literal[
    "analyze",
    "coach_inputs",
    "coach_prototype",
    "media_extract",
    "publish",
]


class ArtifactRef(BaseModel):
    """Pointer to one immutable object used as a stage input or output."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    artifact_type: ArtifactType
    storage_provider: Literal["r2", "local"]
    bucket: str
    object_key: str
    content_type: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)


class StageManifest(BaseModel):
    """Versioned boundary record for one stage execution."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["s3coach.stage_manifest.v1"] = MANIFEST_FORMAT
    submission_id: str
    stage_run_id: str
    stage: StageName
    pipeline_version: str
    git_sha: str | None = None
    evidence_version: str | None = None
    inputs: list[ArtifactRef] = Field(default_factory=list)
    outputs: list[ArtifactRef] = Field(default_factory=list)
    created_at: datetime


class ObjectStore(Protocol):
    """Minimal materialize/upload surface used by ``StageWorkspace``."""

    def local_file(self, key: str, dest: Path) -> tuple[Path, bool]: ...

    def put_file(
        self,
        key: str,
        source: Path,
        content_type: str,
        sha256: str | None = None,
        size_bytes: int | None = None,
    ): ...


class StageWorkspace:
    """Temporary directory for one stage: materialize, publish outputs, cleanup."""

    def __init__(
        self,
        root: Path,
        storage: ObjectStore,
        *,
        submission_id: str,
        stage_run_id: str,
        stage: StageName,
        storage_provider: Literal["r2", "local"] = "r2",
    ) -> None:
        self.root = root
        self.storage = storage
        self.submission_id = submission_id
        self.stage_run_id = stage_run_id
        self.stage = stage
        self.storage_provider = storage_provider
        self.path = root / submission_id / stage_run_id / stage
        self.path.mkdir(parents=True, exist_ok=True)

    def materialize(self, ref: ArtifactRef, relative: str) -> Path:
        """Download ``ref`` into this workspace at ``relative`` and return the path.

        Raises:
            ValueError: Relative path escapes the workspace.
            OSError: Storage download failed.
        """
        dest = self._resolve(relative)
        dest.parent.mkdir(parents=True, exist_ok=True)
        path, temporary = self.storage.local_file(ref.object_key, dest)
        if temporary:
            return path
        if path != dest:
            shutil.copy2(path, dest)
        return dest

    def publish_output(
        self,
        relative: str,
        *,
        artifact_id: str,
        artifact_type: ArtifactType,
        object_key: str,
        content_type: str,
    ) -> ArtifactRef:
        """Upload a local workspace file and return its ``ArtifactRef``."""
        source = self._resolve(relative)
        digest = file_sha256(source)
        size = source.stat().st_size
        self.storage.put_file(object_key, source, content_type, digest, size)
        return ArtifactRef(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            storage_provider=self.storage_provider,
            bucket=str(getattr(self.storage, "bucket", "local")),
            object_key=object_key,
            content_type=content_type,
            size_bytes=size,
            sha256=digest,
        )

    def build_manifest(
        self,
        *,
        inputs: list[ArtifactRef],
        outputs: list[ArtifactRef],
        pipeline_version: str,
        git_sha: str | None = None,
        evidence_version: str | None = None,
        created_at: datetime | None = None,
    ) -> StageManifest:
        """Build an in-memory stage manifest for the current workspace."""
        return StageManifest(
            submission_id=self.submission_id,
            stage_run_id=self.stage_run_id,
            stage=self.stage,
            pipeline_version=pipeline_version,
            git_sha=git_sha,
            evidence_version=evidence_version,
            inputs=list(inputs),
            outputs=list(outputs),
            created_at=created_at or datetime.now(UTC),
        )

    def write_manifest(self, manifest: StageManifest, relative: str = "stage.json") -> Path:
        """Persist ``manifest`` as JSON inside the workspace."""
        path = self._resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        return path

    def cleanup(self) -> None:
        """Remove this stage directory. Missing paths are success."""
        if self.path.exists():
            shutil.rmtree(self.path)

    def _resolve(self, relative: str) -> Path:
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("workspace path escapes stage directory")
        dest = (self.path / relative).resolve()
        root = self.path.resolve()
        if dest != root and root not in dest.parents:
            raise ValueError("workspace path escapes stage directory")
        return dest
