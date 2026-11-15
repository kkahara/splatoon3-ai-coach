"""Publication cleanup and projection helpers that span R2 and PostgreSQL.

R2 and PostgreSQL are not one atomic transaction. These helpers encode the
explicit, idempotent recovery rules for generation-fenced publication,
full-inventory verification, and orthogonal source/local cleanup.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol

from loguru import logger

from public_site.models import CoachingMoment, MomentEvidence, PublicSubmissionResult
from public_site.report_bundle import (
    PreparedBundle,
    PreparedFile,
    attempt_key,
    attempt_prefix,
)
from public_site.report_store import (
    Outcome,
    PublishedAsset,
    ReportStore,
    new_asset_id,
)

SourceCleanupState = Literal[
    "pending", "pending_settle", "completed", "not_applicable"
]

PIPELINE_READY_NAME = ".pipeline_ready.json"
UPLOAD_SETTLE_GRACE_SECONDS = 60
ATTEMPT_LEASE_SECONDS = 900


class SourceObjectStore(Protocol):
    """DELETE/HEAD surface for submission source objects."""

    def delete(self, key: str) -> None: ...

    def head(self, key: str): ...


class ReportObjectStore(Protocol):
    """HEAD / read-back / PUT surface used during inventory verification."""

    def head(self, key: str): ...

    def read_sha256(self, key: str) -> tuple[int, str]: ...

    def put_file(
        self, key: str, source: Path, content_type: str, sha256: str, size_bytes: int
    ): ...


@dataclass(frozen=True)
class SourceCleanupResult:
    """Outcome of one idempotent source-object cleanup attempt."""

    state: SourceCleanupState
    object_gone: bool
    reason: str


@dataclass(frozen=True)
class InventoryObject:
    """One required object under a generation prefix."""

    relative_path: str
    object_key: str
    size_bytes: int
    sha256: str
    content_type: str
    source: Path | None = None


def upload_window_open(
    *,
    now: datetime,
    source_upload_expires_at: datetime | None,
    attempt_lease_until: datetime | None = None,
    grace_seconds: int = UPLOAD_SETTLE_GRACE_SECONDS,
) -> bool:
    """True while any authorized upload may still land under the source key."""
    grace = timedelta(seconds=grace_seconds)
    if source_upload_expires_at is not None and now < source_upload_expires_at + grace:
        return True
    if attempt_lease_until is not None and now < attempt_lease_until:
        return True
    return False


def cleanup_source_object(
    storage: SourceObjectStore,
    object_key: str | None,
    *,
    now: datetime,
    source_upload_expires_at: datetime | None,
    attempt_lease_until: datetime | None = None,
    grace_seconds: int = UPLOAD_SETTLE_GRACE_SECONDS,
) -> SourceCleanupResult:
    """Delete the source object; settle while a PUT URL may still be live.

    Publication availability does not depend on this: ``publication_state`` can
    be ``verified`` while ``source_cleanup_state`` is still ``pending_settle``.
    """
    if not object_key:
        return SourceCleanupResult("not_applicable", True, "no_source_key")
    window_open = upload_window_open(
        now=now,
        source_upload_expires_at=source_upload_expires_at,
        attempt_lease_until=attempt_lease_until,
        grace_seconds=grace_seconds,
    )
    try:
        storage.delete(object_key)
        gone = storage.head(object_key) is None
    except OSError as exc:
        logger.warning(
            "source cleanup storage error ({})",
            type(exc).__name__,
        )
        return SourceCleanupResult("pending", False, "storage_error")
    if not gone:
        return SourceCleanupResult("pending", False, "still_present")
    if window_open:
        return SourceCleanupResult("pending_settle", True, "upload_window_open")
    return SourceCleanupResult("completed", True, "deleted")


def clear_local_analysis(path: Path | None) -> bool:
    """Remove a temporary analysis directory. Missing paths are success."""
    if path is None or not path.exists():
        return True
    try:
        shutil.rmtree(path)
    except OSError:
        return False
    return not path.exists()


def pipeline_ready_path(analysis_dir: Path) -> Path:
    """Hidden receipt that local pipeline stages finished; never uploaded to R2."""
    return analysis_dir / PIPELINE_READY_NAME


def write_pipeline_ready(
    analysis_dir: Path, *, outcome: Outcome, game_language: str
) -> Path:
    """Persist the pipeline-ready receipt before synchronous publication."""
    path = pipeline_ready_path(analysis_dir)
    path.write_text(
        json.dumps(
            {"outcome": outcome, "game_language": game_language},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def read_pipeline_ready(analysis_dir: Path | None) -> dict[str, str] | None:
    """Load the pipeline-ready receipt, or None when absent/unreadable."""
    if analysis_dir is None:
        return None
    path = pipeline_ready_path(Path(analysis_dir))
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    outcome = payload.get("outcome")
    language = payload.get("game_language")
    if outcome not in {"coached", "no_deaths"} or not isinstance(language, str):
        return None
    return {"outcome": outcome, "game_language": language}


def asset_type_for_path(relative: str) -> str:
    """Map a bundle-relative path to a ``published_assets.asset_type`` value."""
    if relative == "bundle.json":
        return "bundle_manifest"
    if relative.startswith("public_clips/"):
        return "clip"
    if relative.startswith("public_frames/"):
        return "frame"
    if relative.startswith("thumbnails/"):
        return "thumbnail"
    if relative.startswith("other_visual_assets/"):
        return "visual_asset"
    return "coaching_artifact"


def inventory_for_attempt(
    prepared: PreparedBundle,
    *,
    submission_id: str,
    run_id: str,
    generation: int,
) -> list[InventoryObject]:
    """Required objects under one generation prefix, including ``bundle.json``."""
    items = [
        InventoryObject(
            relative_path=item.path,
            object_key=attempt_key(submission_id, run_id, generation, item.path),
            size_bytes=item.published_size,
            sha256=item.published_sha256,
            content_type=item.content_type,
            source=item.source,
        )
        for item in prepared.files
    ]
    items.append(
        InventoryObject(
            relative_path="bundle.json",
            object_key=attempt_key(submission_id, run_id, generation, "bundle.json"),
            size_bytes=len(prepared.bundle_bytes),
            sha256=prepared.bundle_sha256,
            content_type="application/json",
            source=prepared.bundle_source,
        )
    )
    return items


def verify_inventory_object(storage: ReportObjectStore, item: InventoryObject) -> bool:
    """True when the object exists with matching size and SHA-256."""
    try:
        found = storage.head(item.object_key)
        if found is None:
            return False
        if found.size_bytes != item.size_bytes:
            return False
        if found.sha256 is not None and found.sha256 != item.sha256:
            return False
        size, digest = storage.read_sha256(item.object_key)
    except (FileNotFoundError, OSError):
        return False
    return size == item.size_bytes and digest == item.sha256


def verify_full_inventory(
    storage: ReportObjectStore, items: list[InventoryObject]
) -> list[InventoryObject]:
    """Return the subset of inventory objects that fail verification."""
    return [item for item in items if not verify_inventory_object(storage, item)]


def repair_inventory_object(storage: ReportObjectStore, item: InventoryObject) -> None:
    """PUT one missing/corrupt object from its local source and read it back."""
    if item.source is None or not item.source.is_file():
        raise OSError("storage put failed (local_missing)")
    storage.put_file(
        item.object_key,
        item.source,
        item.content_type,
        item.sha256,
        item.size_bytes,
    )
    if not verify_inventory_object(storage, item):
        raise OSError("storage put failed (checksum)")


def assets_from_prepared(
    prepared: PreparedBundle,
    *,
    submission_id: str,
    run_id: str,
    generation: int,
    bucket: str,
    created_at: datetime,
    expires_at: datetime | None,
    moment_safe_ids: list[str] | None = None,
) -> list[PublishedAsset]:
    """Build asset rows for one verified generation prefix."""
    index_by_safe = {
        safe_id: index for index, safe_id in enumerate(moment_safe_ids or ())
    }
    assets: list[PublishedAsset] = []
    for item in prepared.files:
        assets.append(
            _asset_from_file(
                item,
                submission_id=submission_id,
                run_id=run_id,
                generation=generation,
                bucket=bucket,
                created_at=created_at,
                expires_at=expires_at,
                index_by_safe=index_by_safe,
            )
        )
    assets.append(
        PublishedAsset(
            asset_id=new_asset_id(),
            submission_id=submission_id,
            run_id=run_id,
            attempt_generation=generation,
            asset_type="bundle_manifest",
            moment_index=None,
            safe_id=None,
            storage_provider="r2",
            bucket=bucket,
            object_key=attempt_key(submission_id, run_id, generation, "bundle.json"),
            content_type="application/json",
            size_bytes=len(prepared.bundle_bytes),
            sha256=prepared.bundle_sha256,
            publication_state="verified",
            deletion_state="retained",
            created_at=created_at,
            expires_at=expires_at,
            deleted_at=None,
        )
    )
    return assets


def apply_verified_media_flags(
    result: PublicSubmissionResult, assets: list[PublishedAsset]
) -> PublicSubmissionResult:
    """Overwrite moment clip/frame booleans from verified published assets only."""
    clips = {
        asset.moment_index
        for asset in assets
        if asset.asset_type == "clip" and asset.moment_index is not None
    }
    frames = {
        asset.moment_index
        for asset in assets
        if asset.asset_type == "frame" and asset.moment_index is not None
    }
    moments = [
        moment.model_copy(
            update={"clip": index in clips, "frame": index in frames}
        )
        for index, moment in enumerate(result.moments)
    ]
    return result.model_copy(update={"moments": moments})


def serialize_result_projection(result: PublicSubmissionResult) -> dict[str, Any]:
    """Persist ``PublicSubmissionResult`` including internal ``MomentEvidence``."""
    payload = result.model_dump(mode="json")
    moments: list[dict[str, Any]] = []
    for moment, raw in zip(result.moments, payload["moments"], strict=True):
        entry = dict(raw)
        if moment.evidence is not None:
            entry["evidence"] = moment.evidence.model_dump(mode="json")
        moments.append(entry)
    payload["moments"] = moments
    return payload


def deserialize_result_projection(
    payload: Mapping[str, Any] | None,
) -> PublicSubmissionResult | None:
    """Rebuild a result with ``MomentEvidence`` re-attached for localization."""
    if payload is None:
        return None
    data = dict(payload)
    moments_raw = data.get("moments")
    if not isinstance(moments_raw, list):
        return PublicSubmissionResult.model_validate(data)
    moments: list[CoachingMoment] = []
    for entry in moments_raw:
        if not isinstance(entry, dict):
            continue
        body = dict(entry)
        evidence_raw = body.pop("evidence", None)
        moment = CoachingMoment.model_validate(body)
        if isinstance(evidence_raw, dict):
            moment = moment.model_copy(
                update={"evidence": MomentEvidence.model_validate(evidence_raw)}
            )
        moments.append(moment)
    data["moments"] = []
    result = PublicSubmissionResult.model_validate(data)
    return result.model_copy(update={"moments": moments})


def commit_verified_report(
    reports: ReportStore,
    *,
    submission_id: str,
    run_id: str,
    generation: int,
    bucket: str,
    prepared: PreparedBundle,
    outcome: Outcome,
    battle_mode_id: str | None,
    stage_id: str | None,
    moment_count: int,
    result_json: dict[str, Any] | None,
    moment_safe_ids: list[str] | None,
    published_at: datetime,
    expires_at: datetime | None,
    now: datetime,
) -> None:
    """Mark a fully verified generation inventory into PostgreSQL."""
    bundle_key = attempt_key(submission_id, run_id, generation, "bundle.json")
    if not reports.mark_verified_r2(
        submission_id,
        attempt_run_id=run_id,
        generation=generation,
        bundle_key=bundle_key,
        bundle_sha256=prepared.bundle_sha256,
        now=now,
    ):
        from public_site.report_store import StaleAttemptError

        raise StaleAttemptError("mark_verified_r2 rejected fencing token")
    assets = assets_from_prepared(
        prepared,
        submission_id=submission_id,
        run_id=run_id,
        generation=generation,
        bucket=bucket,
        created_at=published_at,
        expires_at=expires_at,
        moment_safe_ids=moment_safe_ids,
    )
    reports.commit_verified(
        submission_id,
        run_id=run_id,
        generation=generation,
        bucket=bucket,
        bundle_key=bundle_key,
        bundle_sha256=prepared.bundle_sha256,
        outcome=outcome,
        battle_mode_id=battle_mode_id,
        stage_id=stage_id,
        moment_count=moment_count,
        result_json=result_json,
        assets=assets,
        published_at=published_at,
        expires_at=expires_at,
        now=now,
    )


def abandoned_attempt_prefix(
    submission_id: str, run_id: str, generation: int
) -> str:
    """Prefix for a fenced generation that is no longer authoritative."""
    return attempt_prefix(submission_id, run_id, generation)


@dataclass(frozen=True)
class CleanupReconcileResult:
    """Outcome of one orthogonal cleanup pass after a verified publication."""

    source_cleanup_state: SourceCleanupState
    local_cleanup_state: Literal["pending", "completed"]
    cleanup_completed_at: datetime | None


def reconcile_verified_cleanup(
    reports: ReportStore,
    *,
    submission_id: str,
    generation: int,
    source_storage: SourceObjectStore | None,
    analysis_dir: Path | None,
    clear_analysis_dir: bool,
    now: datetime,
) -> CleanupReconcileResult | None:
    """Advance source/local cleanup independently after ``publication_state=verified``.

    Fenced: only mutates rows that are still ``verified`` at the caller's
    generation. A stale worker that lost its lease cannot rewrite cleanup.
    """
    report = reports.get(submission_id)
    if report is None or report.publication_state != "verified":
        return None
    if reports.has_active_lease(submission_id, now):
        # A replacement publication is in flight; do not clear local workspace yet.
        if report.active_run_id is not None and report.attempt_run_id is not None:
            return None

    source_state = report.source_cleanup_state
    if source_storage is not None and source_state in {"pending", "pending_settle"}:
        result = cleanup_source_object(
            source_storage,
            report.source_object_key,
            now=now,
            source_upload_expires_at=report.source_upload_expires_at,
            attempt_lease_until=None,
        )
        source_state = result.state

    local_state = report.local_cleanup_state
    if local_state == "pending" and clear_analysis_dir:
        if clear_local_analysis(analysis_dir):
            local_state = "completed"

    completed_at = report.cleanup_completed_at
    if (
        local_state == "completed"
        and source_state in {"completed", "not_applicable"}
        and completed_at is None
    ):
        completed_at = now

    updated = reports.set_cleanup_states(
        submission_id,
        generation=generation,
        source_cleanup_state=source_state,
        local_cleanup_state=local_state,
        cleanup_completed_at=completed_at,
        source_object_key=(
            None if source_state in {"completed", "not_applicable"} else ...
        ),
        now=now,
    )
    if updated is None:
        return None
    return CleanupReconcileResult(
        source_cleanup_state=updated.source_cleanup_state,
        local_cleanup_state=updated.local_cleanup_state,
        cleanup_completed_at=updated.cleanup_completed_at,
    )


def generation_prefix_empty(
    storage: SourceObjectStore,
    submission_id: str,
    run_id: str,
    generation: int,
) -> bool:
    """True when LIST under the abandoned generation prefix returns nothing.

    ``SourceObjectStore`` is too narrow for LIST; callers that need settle pass
    a storage that also exposes ``list_objects`` via duck typing.
    """
    list_objects = getattr(storage, "list_objects", None)
    if list_objects is None:
        head = getattr(storage, "head", None)
        if head is None:
            return False
        # Without LIST, treat missing bundle as empty (best-effort tests only).
        key = attempt_key(submission_id, run_id, generation, "bundle.json")
        return head(key) is None
    prefix = attempt_prefix(submission_id, run_id, generation)
    return not list_objects(prefix)


def _asset_from_file(
    item: PreparedFile,
    *,
    submission_id: str,
    run_id: str,
    generation: int,
    bucket: str,
    created_at: datetime,
    expires_at: datetime | None,
    index_by_safe: dict[str, int],
) -> PublishedAsset:
    asset_type = asset_type_for_path(item.path)
    safe_id = (
        Path(item.path).stem if asset_type in {"clip", "frame", "thumbnail"} else None
    )
    moment_index = index_by_safe.get(safe_id) if safe_id else None
    return PublishedAsset(
        asset_id=new_asset_id(),
        submission_id=submission_id,
        run_id=run_id,
        attempt_generation=generation,
        asset_type=asset_type,  # type: ignore[arg-type]
        moment_index=moment_index,
        safe_id=safe_id,
        storage_provider="r2",
        bucket=bucket,
        object_key=attempt_key(submission_id, run_id, generation, item.path),
        content_type=item.content_type,
        size_bytes=item.published_size,
        sha256=item.published_sha256,
        publication_state="verified",
        deletion_state="retained",
        created_at=created_at,
        expires_at=expires_at,
        deleted_at=None,
    )
