"""PostgreSQL store for published coaching reports and R2 asset inventory.

Ownership:
- ``published_reports`` owns the customer-visible result projection, publication
  and cleanup state, and the pointer to the active R2 ``bundle.json``.
- ``published_assets`` owns per-object keys, hashes, and deletion state.
- R2 ``bundle.json`` remains the immutable run inventory; this store does not
  re-define inventory contents.
- Existing tables (``coaching_history``, payments, acquisitions, telemetry)
  keep their responsibilities; this module does not duplicate them.

Upload objects live under generation-specific prefixes
``reports/<id>/<run_id>/attempt-<generation>/`` so a stale worker cannot
overwrite a newer generation. PostgreSQL ``attempt_generation`` fencing is the
authoritative distributed concurrency control; local flock is only a
same-machine optimization.

Presigned URLs are never persisted here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

MIGRATION = Path(__file__).parent / "migrations" / "003_published_report_assets.sql"
_SCHEMA_LOCK = 8_273_341_908

PublicationState = Literal[
    "pending",
    "publishing",
    "verified_r2",
    "verified",
    "blocked",
    "delete_pending",
    "deleted",
]
SourceCleanupState = Literal[
    "pending", "pending_settle", "completed", "not_applicable"
]
LocalCleanupState = Literal["pending", "completed"]
AssetType = Literal[
    "clip",
    "frame",
    "thumbnail",
    "visual_asset",
    "coaching_artifact",
    "bundle_manifest",
]
AssetPublicationState = Literal["verified", "superseded"]
DeletionState = Literal["retained", "delete_pending", "deleted"]
Outcome = Literal["coached", "no_deaths"]

_TERMINAL = frozenset({"delete_pending", "deleted"})
_LEASEABLE = frozenset({"pending", "publishing", "verified_r2", "blocked", "verified"})


class StaleAttemptError(RuntimeError):
    """The caller's fencing token is no longer the active publication attempt."""


@dataclass(frozen=True)
class LeaseGrant:
    """One fenced publication attempt. ``generation`` is monotonic per submission."""

    attempt_run_id: str
    generation: int
    lease_until: datetime


@dataclass(frozen=True)
class PublishedAsset:
    """One verified (or superseded) object in R2. Never carries a URL."""

    asset_id: str
    submission_id: str
    run_id: str
    attempt_generation: int
    asset_type: AssetType
    moment_index: int | None
    safe_id: str | None
    storage_provider: str
    bucket: str
    object_key: str
    content_type: str
    size_bytes: int
    sha256: str
    publication_state: AssetPublicationState
    deletion_state: DeletionState
    created_at: datetime
    expires_at: datetime | None
    deleted_at: datetime | None


@dataclass(frozen=True)
class PublishedReport:
    """Authoritative publication row for one submission."""

    submission_id: str
    active_run_id: str | None
    attempt_run_id: str | None
    attempt_lease_until: datetime | None
    attempt_generation: int
    storage_provider: str
    bucket: str
    bundle_key: str | None
    bundle_sha256: str | None
    outcome: Outcome | None
    battle_mode_id: str | None
    stage_id: str | None
    moment_count: int | None
    result_json: dict[str, Any] | None
    localized_cache: dict[str, Any]
    publication_state: PublicationState
    source_object_key: str | None
    source_upload_expires_at: datetime | None
    source_cleanup_state: SourceCleanupState
    local_cleanup_state: LocalCleanupState
    published_at: datetime | None
    expires_at: datetime | None
    cleanup_completed_at: datetime | None
    delete_empty_at: datetime | None
    updated_at: datetime


@dataclass(frozen=True)
class HistoryFields:
    """Match identity fields for ``/api/me/submissions`` without local disk."""

    battle_mode_id: str | None
    stage_id: str | None
    moment_count: int | None


class ReportStore:
    """Create and query published report / asset rows under advisory locks."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def ensure_schema(self) -> None:
        """Install the published report tables when they are missing."""
        with self._conn() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK,))
            conn.execute(MIGRATION.read_text())

    def clear(self) -> None:
        """Delete every published report and asset. Tests only."""
        with self._conn() as conn:
            conn.execute("TRUNCATE published_reports CASCADE")

    def get(self, submission_id: str) -> PublishedReport | None:
        """Load one report row, or None when missing."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM published_reports WHERE submission_id = %s",
                (submission_id,),
            ).fetchone()
        return None if row is None else _report(row)

    def upsert_pending(
        self,
        submission_id: str,
        *,
        bucket: str,
        source_object_key: str | None,
        source_upload_expires_at: datetime | None,
        expires_at: datetime | None,
        now: datetime,
    ) -> PublishedReport:
        """Create or refresh a pending report shell before publication starts.

        Rejected once ``delete_pending`` / ``deleted`` (terminal expiration barrier).
        """
        with self._conn() as conn:
            row = conn.execute(
                """
                INSERT INTO published_reports (
                    submission_id, storage_provider, bucket, publication_state,
                    source_object_key, source_upload_expires_at, expires_at,
                    attempt_generation, updated_at
                ) VALUES (
                    %s, 'r2', %s, 'pending', %s, %s, %s, 0, %s
                )
                ON CONFLICT (submission_id) DO UPDATE SET
                    bucket = EXCLUDED.bucket,
                    source_object_key = COALESCE(
                        EXCLUDED.source_object_key,
                        published_reports.source_object_key
                    ),
                    source_upload_expires_at = COALESCE(
                        EXCLUDED.source_upload_expires_at,
                        published_reports.source_upload_expires_at
                    ),
                    expires_at = COALESCE(
                        EXCLUDED.expires_at, published_reports.expires_at
                    ),
                    updated_at = EXCLUDED.updated_at
                WHERE published_reports.publication_state NOT IN (
                    'delete_pending', 'deleted'
                )
                RETURNING *
                """,
                (
                    submission_id,
                    bucket,
                    source_object_key,
                    source_upload_expires_at,
                    expires_at,
                    now,
                ),
            ).fetchone()
        if row is None:
            found = self.get(submission_id)
            if found is not None and found.publication_state in _TERMINAL:
                raise StaleAttemptError("publication is delete_pending or deleted")
            raise RuntimeError("published report upsert did not create a row")
        return _report(row)

    def lease_attempt(
        self,
        submission_id: str,
        attempt_run_id: str,
        lease_until: datetime,
        now: datetime,
    ) -> LeaseGrant | None:
        """Acquire a new fenced attempt, or renew the same unexpired lease.

        A new generation is allocated only when no unexpired lease is held.
        Ordinary transient retries pass the same ``attempt_run_id`` while the
        lease is still valid and keep the same generation (and R2 prefix).
        """
        with self._conn() as conn:
            current = conn.execute(
                """
                SELECT * FROM published_reports
                WHERE submission_id = %s
                FOR UPDATE
                """,
                (submission_id,),
            ).fetchone()
            if current is None:
                return None
            if current["publication_state"] in _TERMINAL:
                return None
            if current["publication_state"] not in _LEASEABLE:
                return None
            same = (
                current["attempt_run_id"] == attempt_run_id
                and current["attempt_lease_until"] is not None
                and current["attempt_lease_until"] > now
            )
            if same:
                row = conn.execute(
                    """
                    UPDATE published_reports
                    SET attempt_lease_until = %s,
                        publication_state = CASE
                            WHEN publication_state IN ('pending', 'blocked')
                                THEN 'publishing'
                            ELSE publication_state
                        END,
                        updated_at = %s
                    WHERE submission_id = %s
                    RETURNING attempt_run_id, attempt_generation, attempt_lease_until
                    """,
                    (lease_until, now, submission_id),
                ).fetchone()
            elif (
                current["attempt_lease_until"] is None
                or current["attempt_lease_until"] <= now
            ):
                row = conn.execute(
                    """
                    UPDATE published_reports
                    SET attempt_run_id = %s,
                        attempt_lease_until = %s,
                        attempt_generation = attempt_generation + 1,
                        publication_state = CASE
                            WHEN publication_state IN ('pending', 'blocked')
                                THEN 'publishing'
                            WHEN publication_state = 'verified'
                                THEN 'verified'
                            ELSE 'publishing'
                        END,
                        updated_at = %s
                    WHERE submission_id = %s
                    RETURNING attempt_run_id, attempt_generation, attempt_lease_until
                    """,
                    (attempt_run_id, lease_until, now, submission_id),
                ).fetchone()
            else:
                return None
        if row is None:
            return None
        return LeaseGrant(
            attempt_run_id=row["attempt_run_id"],
            generation=int(row["attempt_generation"]),
            lease_until=row["attempt_lease_until"],
        )

    def renew_lease(
        self,
        submission_id: str,
        attempt_run_id: str,
        generation: int,
        lease_until: datetime,
        now: datetime,
    ) -> bool:
        """Extend the lease when the fencing token still matches."""
        with self._conn() as conn:
            row = conn.execute(
                """
                UPDATE published_reports
                SET attempt_lease_until = %s, updated_at = %s
                WHERE submission_id = %s
                  AND attempt_run_id = %s
                  AND attempt_generation = %s
                  AND attempt_lease_until > %s
                  AND publication_state NOT IN ('delete_pending', 'deleted')
                RETURNING submission_id
                """,
                (lease_until, now, submission_id, attempt_run_id, generation, now),
            ).fetchone()
        return row is not None

    def release_lease(
        self,
        submission_id: str,
        attempt_run_id: str,
        generation: int,
        now: datetime,
    ) -> bool:
        """Clear the active lease when the fencing token still matches."""
        with self._conn() as conn:
            row = conn.execute(
                """
                UPDATE published_reports
                SET attempt_run_id = NULL,
                    attempt_lease_until = NULL,
                    updated_at = %s
                WHERE submission_id = %s
                  AND attempt_run_id = %s
                  AND attempt_generation = %s
                RETURNING submission_id
                """,
                (now, submission_id, attempt_run_id, generation),
            ).fetchone()
        return row is not None

    def validate_fence(
        self,
        submission_id: str,
        attempt_run_id: str,
        generation: int,
        now: datetime,
    ) -> None:
        """Raise ``StaleAttemptError`` when the fencing token is no longer current."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT attempt_run_id, attempt_generation, attempt_lease_until,
                       publication_state
                FROM published_reports
                WHERE submission_id = %s
                """,
                (submission_id,),
            ).fetchone()
        if row is None:
            raise StaleAttemptError("published report missing")
        if row["publication_state"] in _TERMINAL:
            raise StaleAttemptError("publication is delete_pending or deleted")
        if (
            row["attempt_run_id"] != attempt_run_id
            or int(row["attempt_generation"]) != generation
            or row["attempt_lease_until"] is None
            or row["attempt_lease_until"] <= now
        ):
            raise StaleAttemptError("fencing token is stale")

    def mark_verified_r2(
        self,
        submission_id: str,
        *,
        attempt_run_id: str,
        generation: int,
        bundle_key: str,
        bundle_sha256: str,
        now: datetime,
    ) -> bool:
        """Record that the generation prefix is fully verified in R2, PG pending."""
        with self._conn() as conn:
            row = conn.execute(
                """
                UPDATE published_reports
                SET publication_state = 'verified_r2',
                    attempt_run_id = %s,
                    bundle_key = %s,
                    bundle_sha256 = %s,
                    updated_at = %s
                WHERE submission_id = %s
                  AND attempt_run_id = %s
                  AND attempt_generation = %s
                  AND attempt_lease_until > %s
                  AND publication_state IN ('publishing', 'verified_r2', 'verified')
                RETURNING submission_id
                """,
                (
                    attempt_run_id,
                    bundle_key,
                    bundle_sha256,
                    now,
                    submission_id,
                    attempt_run_id,
                    generation,
                    now,
                ),
            ).fetchone()
        return row is not None

    def commit_verified(
        self,
        submission_id: str,
        *,
        run_id: str,
        generation: int,
        bucket: str,
        bundle_key: str,
        bundle_sha256: str,
        outcome: Outcome,
        battle_mode_id: str | None,
        stage_id: str | None,
        moment_count: int,
        result_json: Mapping[str, Any] | None,
        assets: Sequence[PublishedAsset],
        published_at: datetime,
        expires_at: datetime | None,
        now: datetime,
    ) -> PublishedReport:
        """Atomically commit the active run under the caller's fencing token."""
        with self._conn() as conn:
            locked = conn.execute(
                """
                SELECT * FROM published_reports
                WHERE submission_id = %s
                FOR UPDATE
                """,
                (submission_id,),
            ).fetchone()
            if locked is None:
                raise StaleAttemptError("published report missing")
            if locked["publication_state"] in _TERMINAL:
                raise StaleAttemptError("publication is delete_pending or deleted")
            if (
                locked["attempt_run_id"] != run_id
                or int(locked["attempt_generation"]) != generation
                or locked["attempt_lease_until"] is None
                or locked["attempt_lease_until"] <= now
            ):
                raise StaleAttemptError("fencing token is stale")
            conn.execute(
                """
                UPDATE published_reports SET
                    active_run_id = %s,
                    attempt_run_id = NULL,
                    attempt_lease_until = NULL,
                    storage_provider = 'r2',
                    bucket = %s,
                    bundle_key = %s,
                    bundle_sha256 = %s,
                    outcome = %s,
                    battle_mode_id = %s,
                    stage_id = %s,
                    moment_count = %s,
                    result_json = %s,
                    publication_state = 'verified',
                    published_at = %s,
                    expires_at = COALESCE(%s, expires_at),
                    updated_at = %s
                WHERE submission_id = %s
                """,
                (
                    run_id,
                    bucket,
                    bundle_key,
                    bundle_sha256,
                    outcome,
                    battle_mode_id,
                    stage_id,
                    moment_count,
                    None if result_json is None else Jsonb(dict(result_json)),
                    published_at,
                    expires_at,
                    now,
                    submission_id,
                ),
            )
            conn.execute(
                """
                UPDATE published_assets
                SET publication_state = 'superseded',
                    deletion_state = CASE
                        WHEN deletion_state = 'retained' THEN 'delete_pending'
                        ELSE deletion_state
                    END
                WHERE submission_id = %s
                  AND NOT (run_id = %s AND attempt_generation = %s)
                  AND publication_state = 'verified'
                """,
                (submission_id, run_id, generation),
            )
            conn.execute(
                """
                DELETE FROM published_assets
                WHERE submission_id = %s AND run_id = %s AND attempt_generation = %s
                """,
                (submission_id, run_id, generation),
            )
            for asset in assets:
                conn.execute(
                    """
                    INSERT INTO published_assets (
                        asset_id, submission_id, run_id, attempt_generation,
                        asset_type, moment_index, safe_id, storage_provider, bucket,
                        object_key, content_type, size_bytes, sha256,
                        publication_state, deletion_state, created_at, expires_at,
                        deleted_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, 'r2', %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s
                    )
                    """,
                    (
                        asset.asset_id,
                        submission_id,
                        run_id,
                        generation,
                        asset.asset_type,
                        asset.moment_index,
                        asset.safe_id,
                        bucket,
                        asset.object_key,
                        asset.content_type,
                        asset.size_bytes,
                        asset.sha256,
                        asset.publication_state,
                        asset.deletion_state,
                        asset.created_at,
                        asset.expires_at,
                        asset.deleted_at,
                    ),
                )
        found = self.get(submission_id)
        if found is None:
            raise RuntimeError("verified commit left no published report row")
        return found

    def set_cleanup_states(
        self,
        submission_id: str,
        *,
        generation: int | None = None,
        source_cleanup_state: SourceCleanupState | None = None,
        local_cleanup_state: LocalCleanupState | None = None,
        cleanup_completed_at: datetime | None = None,
        source_object_key: str | None | object = ...,
        now: datetime,
    ) -> PublishedReport | None:
        """Update cleanup flags after publication without rewriting the result."""
        assignments = ["updated_at = %s"]
        values: list[Any] = [now]
        if source_cleanup_state is not None:
            assignments.append("source_cleanup_state = %s")
            values.append(source_cleanup_state)
        if local_cleanup_state is not None:
            assignments.append("local_cleanup_state = %s")
            values.append(local_cleanup_state)
        if cleanup_completed_at is not None:
            assignments.append("cleanup_completed_at = %s")
            values.append(cleanup_completed_at)
        if source_object_key is not ...:
            assignments.append("source_object_key = %s")
            values.append(source_object_key)
        values.append(submission_id)
        fence = ""
        if generation is not None:
            fence = " AND attempt_generation = %s AND publication_state = 'verified'"
            values.append(generation)
        with self._conn() as conn:
            row = conn.execute(
                f"""
                UPDATE published_reports
                SET {", ".join(assignments)}
                WHERE submission_id = %s{fence}
                RETURNING *
                """,
                values,
            ).fetchone()
        return None if row is None else _report(row)

    def media_asset(
        self,
        submission_id: str,
        asset_type: AssetType,
        moment_index: int,
    ) -> PublishedAsset | None:
        """Active retained media asset for one moment, or None."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT a.*
                FROM published_assets a
                JOIN published_reports r ON r.submission_id = a.submission_id
                WHERE a.submission_id = %s
                  AND r.active_run_id = a.run_id
                  AND r.publication_state = 'verified'
                  AND a.asset_type = %s
                  AND a.moment_index = %s
                  AND a.publication_state = 'verified'
                  AND a.deletion_state = 'retained'
                """,
                (submission_id, asset_type, moment_index),
            ).fetchone()
        return None if row is None else _asset(row)

    def history_fields(
        self, submission_ids: Sequence[str]
    ) -> dict[str, HistoryFields]:
        """Batch match-identity fields for history listings."""
        if not submission_ids:
            return {}
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT submission_id, battle_mode_id, stage_id, moment_count
                FROM published_reports
                WHERE submission_id = ANY(%s)
                  AND publication_state = 'verified'
                """,
                (list(submission_ids),),
            ).fetchall()
        return {
            row["submission_id"]: HistoryFields(
                battle_mode_id=row["battle_mode_id"],
                stage_id=row["stage_id"],
                moment_count=row["moment_count"],
            )
            for row in rows
        }

    def merge_localized_cache(
        self, submission_id: str, entries: Mapping[str, Any], now: datetime
    ) -> None:
        """Merge translation-cache entries into ``localized_cache``."""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE published_reports
                SET localized_cache = localized_cache || %s::jsonb,
                    updated_at = %s
                WHERE submission_id = %s
                  AND publication_state = 'verified'
                """,
                (Jsonb(dict(entries)), now, submission_id),
            )

    def set_localized_cache(
        self, submission_id: str, localized_cache: Mapping[str, Any], now: datetime
    ) -> None:
        """Replace the language cache blob for one report."""
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE published_reports
                SET localized_cache = %s, updated_at = %s
                WHERE submission_id = %s
                """,
                (Jsonb(dict(localized_cache)), now, submission_id),
            )

    def mark_blocked(
        self,
        submission_id: str,
        *,
        generation: int | None,
        now: datetime,
    ) -> bool:
        """Stop automatic retries after content-policy / transform failure."""
        with self._conn() as conn:
            if generation is None:
                row = conn.execute(
                    """
                    UPDATE published_reports
                    SET publication_state = 'blocked',
                        attempt_run_id = NULL,
                        attempt_lease_until = NULL,
                        updated_at = %s
                    WHERE submission_id = %s
                      AND publication_state IN (
                          'pending', 'publishing', 'verified_r2', 'blocked'
                      )
                    RETURNING submission_id
                    """,
                    (now, submission_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    UPDATE published_reports
                    SET publication_state = 'blocked',
                        attempt_run_id = NULL,
                        attempt_lease_until = NULL,
                        updated_at = %s
                    WHERE submission_id = %s
                      AND attempt_generation = %s
                      AND publication_state IN (
                          'pending', 'publishing', 'verified_r2', 'blocked'
                      )
                    RETURNING submission_id
                    """,
                    (now, submission_id, generation),
                ).fetchone()
        return row is not None

    def mark_delete_pending(self, submission_id: str, now: datetime) -> bool:
        """Terminal expiration barrier: fence out publishers and mark for deletion."""
        with self._conn() as conn:
            row = conn.execute(
                """
                INSERT INTO published_reports (
                    submission_id, storage_provider, bucket, publication_state,
                    attempt_generation, updated_at
                ) VALUES (%s, 'r2', 'unknown', 'delete_pending', 1, %s)
                ON CONFLICT (submission_id) DO UPDATE SET
                    publication_state = 'delete_pending',
                    attempt_generation = published_reports.attempt_generation + 1,
                    updated_at = EXCLUDED.updated_at
                WHERE published_reports.publication_state NOT IN ('deleted')
                RETURNING submission_id
                """,
                (submission_id, now),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                """
                UPDATE published_assets
                SET deletion_state = 'delete_pending'
                WHERE submission_id = %s AND deletion_state = 'retained'
                """,
                (submission_id,),
            )
        return True

    def mark_deleted(
        self, submission_id: str, delete_empty_at: datetime | None, now: datetime
    ) -> bool:
        """Record that the reports prefix is empty after settle."""
        with self._conn() as conn:
            row = conn.execute(
                """
                UPDATE published_reports
                SET publication_state = 'deleted',
                    attempt_run_id = NULL,
                    attempt_lease_until = NULL,
                    delete_empty_at = COALESCE(%s, delete_empty_at),
                    updated_at = %s
                WHERE submission_id = %s
                  AND publication_state = 'delete_pending'
                  AND (attempt_lease_until IS NULL OR attempt_lease_until <= %s)
                RETURNING submission_id
                """,
                (delete_empty_at, now, submission_id, now),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                """
                UPDATE published_assets
                SET deletion_state = 'deleted', deleted_at = %s
                WHERE submission_id = %s AND deletion_state <> 'deleted'
                """,
                (now, submission_id),
            )
        return True

    def mark_run_deleted(
        self, submission_id: str, run_id: str, generation: int | None, now: datetime
    ) -> bool:
        """Mark one superseded run/generation's assets deleted after prefix purge."""
        with self._conn() as conn:
            locked = conn.execute(
                """
                SELECT active_run_id, attempt_run_id, attempt_generation
                FROM published_reports
                WHERE submission_id = %s
                FOR UPDATE
                """,
                (submission_id,),
            ).fetchone()
            if locked is None:
                return False
            if locked["active_run_id"] == run_id:
                return False
            if locked["attempt_run_id"] == run_id:
                return False
            if generation is None:
                conn.execute(
                    """
                    UPDATE published_assets
                    SET deletion_state = 'deleted', deleted_at = %s
                    WHERE submission_id = %s AND run_id = %s
                      AND deletion_state = 'delete_pending'
                    """,
                    (now, submission_id, run_id),
                )
            else:
                if (
                    locked["active_run_id"] == run_id
                    and int(locked["attempt_generation"]) == generation
                ):
                    return False
                conn.execute(
                    """
                    UPDATE published_assets
                    SET deletion_state = 'deleted', deleted_at = %s
                    WHERE submission_id = %s AND run_id = %s
                      AND attempt_generation = %s
                      AND deletion_state = 'delete_pending'
                    """,
                    (now, submission_id, run_id, generation),
                )
        return True

    def has_active_lease(self, submission_id: str, now: datetime) -> bool:
        """True while an unexpired publication lease is held."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM published_reports
                WHERE submission_id = %s
                  AND attempt_lease_until IS NOT NULL
                  AND attempt_lease_until > %s
                """,
                (submission_id, now),
            ).fetchone()
        return row is not None

    def list_incomplete_cleanup(self) -> list[PublishedReport]:
        """Verified reports whose source or local cleanup is unfinished."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM published_reports
                WHERE publication_state = 'verified'
                  AND (
                    source_cleanup_state IN ('pending', 'pending_settle')
                    OR local_cleanup_state = 'pending'
                  )
                ORDER BY submission_id
                """
            ).fetchall()
        return [_report(row) for row in rows]

    def list_verified_r2(self) -> list[PublishedReport]:
        """Reports whose R2 inventory is verified but PG commit is pending."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM published_reports
                WHERE publication_state = 'verified_r2'
                ORDER BY submission_id
                """
            ).fetchall()
        return [_report(row) for row in rows]

    def list_delete_pending_assets(self) -> list[PublishedAsset]:
        """Assets marked for deletion after supersession or expiry."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM published_assets
                WHERE deletion_state = 'delete_pending'
                ORDER BY submission_id, run_id, attempt_generation, object_key
                """
            ).fetchall()
        return [_asset(row) for row in rows]

    def list_assets_for_generation(
        self, submission_id: str, run_id: str, generation: int
    ) -> list[PublishedAsset]:
        """All asset rows for one generation prefix."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM published_assets
                WHERE submission_id = %s AND run_id = %s AND attempt_generation = %s
                ORDER BY object_key
                """,
                (submission_id, run_id, generation),
            ).fetchall()
        return [_asset(row) for row in rows]

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)


def new_asset_id() -> str:
    """Fresh UUID string for a ``published_assets`` row."""
    return str(uuid.uuid4())


def _report(row: Mapping[str, Any]) -> PublishedReport:
    return PublishedReport(
        submission_id=row["submission_id"],
        active_run_id=row["active_run_id"],
        attempt_run_id=row["attempt_run_id"],
        attempt_lease_until=row["attempt_lease_until"],
        attempt_generation=int(row.get("attempt_generation") or 0),
        storage_provider=row["storage_provider"],
        bucket=row["bucket"],
        bundle_key=row["bundle_key"],
        bundle_sha256=row["bundle_sha256"],
        outcome=row["outcome"],
        battle_mode_id=row["battle_mode_id"],
        stage_id=row["stage_id"],
        moment_count=row["moment_count"],
        result_json=row["result_json"],
        localized_cache=dict(row["localized_cache"] or {}),
        publication_state=row["publication_state"],
        source_object_key=row["source_object_key"],
        source_upload_expires_at=row["source_upload_expires_at"],
        source_cleanup_state=row["source_cleanup_state"],
        local_cleanup_state=row["local_cleanup_state"],
        published_at=row["published_at"],
        expires_at=row["expires_at"],
        cleanup_completed_at=row["cleanup_completed_at"],
        delete_empty_at=row["delete_empty_at"],
        updated_at=row["updated_at"],
    )


def _asset(row: Mapping[str, Any]) -> PublishedAsset:
    return PublishedAsset(
        asset_id=str(row["asset_id"]),
        submission_id=row["submission_id"],
        run_id=row["run_id"],
        attempt_generation=int(row.get("attempt_generation") or 0),
        asset_type=row["asset_type"],
        moment_index=row["moment_index"],
        safe_id=row["safe_id"],
        storage_provider=row["storage_provider"],
        bucket=row["bucket"],
        object_key=row["object_key"],
        content_type=row["content_type"],
        size_bytes=int(row["size_bytes"]),
        sha256=row["sha256"],
        publication_state=row["publication_state"],
        deletion_state=row["deletion_state"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        deleted_at=row["deleted_at"],
    )
