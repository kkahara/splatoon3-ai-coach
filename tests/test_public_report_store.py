"""Published report / asset PostgreSQL store with generation fencing."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest

from public_site.report_store import (
    PublishedAsset,
    ReportStore,
    StaleAttemptError,
    new_asset_id,
)


def _database_url() -> str:
    url = os.environ.get("PUBLIC_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("PUBLIC_DATABASE_URL is not set")
    parts = urlsplit(url)
    admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, ""))
    test_url = urlunsplit((parts.scheme, parts.netloc, "/coach_test", parts.query, ""))
    with psycopg.connect(admin, autocommit=True) as conn:
        found = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            ("coach_test",),
        ).fetchone()
        if found is None:
            conn.execute("CREATE DATABASE coach_test")
    return test_url


@pytest.fixture
def reports() -> ReportStore:
    store = ReportStore(_database_url())
    store.ensure_schema()
    store.clear()
    yield store
    store.clear()


def _asset(
    *,
    submission_id: str,
    run_id: str,
    generation: int,
    asset_type: str,
    moment_index: int | None,
    object_key: str,
    sha256: str,
    now: datetime,
) -> PublishedAsset:
    return PublishedAsset(
        asset_id=new_asset_id(),
        submission_id=submission_id,
        run_id=run_id,
        attempt_generation=generation,
        asset_type=asset_type,  # type: ignore[arg-type]
        moment_index=moment_index,
        safe_id="death_episode_1.0" if moment_index is not None else None,
        storage_provider="r2",
        bucket="s3-coaching-dev",
        object_key=object_key,
        content_type="video/mp4" if asset_type == "clip" else "image/jpeg",
        size_bytes=12,
        sha256=sha256,
        publication_state="verified",
        deletion_state="retained",
        created_at=now,
        expires_at=now + timedelta(days=30),
        deleted_at=None,
    )


def test_ensure_schema_is_idempotent(reports: ReportStore) -> None:
    reports.ensure_schema()
    reports.ensure_schema()


def test_lease_increments_generation_and_renews_same_lease(
    reports: ReportStore,
) -> None:
    now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    reports.upsert_pending(
        "sub-fence-1",
        bucket="s3-coaching-dev",
        source_object_key="submissions/sub-fence-1/source.mp4",
        source_upload_expires_at=now + timedelta(minutes=15),
        expires_at=now + timedelta(days=30),
        now=now,
    )
    first = reports.lease_attempt(
        "sub-fence-1", "run-a", now + timedelta(minutes=10), now
    )
    assert first is not None
    assert first.generation == 1
    renewed = reports.lease_attempt(
        "sub-fence-1", "run-a", now + timedelta(minutes=20), now + timedelta(minutes=1)
    )
    assert renewed is not None
    assert renewed.generation == 1
    reports.release_lease("sub-fence-1", "run-a", 1, now + timedelta(minutes=2))
    second = reports.lease_attempt(
        "sub-fence-1", "run-b", now + timedelta(minutes=30), now + timedelta(minutes=3)
    )
    assert second is not None
    assert second.generation == 2


def test_commit_verified_is_fenced_and_serves_media(reports: ReportStore) -> None:
    now = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    submission_id = "sub-report-1"
    reports.upsert_pending(
        submission_id,
        bucket="s3-coaching-dev",
        source_object_key=f"submissions/{submission_id}/source.mp4",
        source_upload_expires_at=now + timedelta(minutes=15),
        expires_at=now + timedelta(days=30),
        now=now,
    )
    lease = reports.lease_attempt(
        submission_id, "run-a", now + timedelta(minutes=10), now
    )
    assert lease is not None
    clip = _asset(
        submission_id=submission_id,
        run_id="run-a",
        generation=lease.generation,
        asset_type="clip",
        moment_index=0,
        object_key=(
            f"reports/{submission_id}/run-a/attempt-{lease.generation}/"
            "public_clips/death_episode_1.0.mp4"
        ),
        sha256="a" * 64,
        now=now,
    )
    frame = _asset(
        submission_id=submission_id,
        run_id="run-a",
        generation=lease.generation,
        asset_type="frame",
        moment_index=0,
        object_key=(
            f"reports/{submission_id}/run-a/attempt-{lease.generation}/"
            "public_frames/death_episode_1.0.jpg"
        ),
        sha256="b" * 64,
        now=now,
    )
    committed = reports.commit_verified(
        submission_id,
        run_id="run-a",
        generation=lease.generation,
        bucket="s3-coaching-dev",
        bundle_key=(
            f"reports/{submission_id}/run-a/attempt-{lease.generation}/bundle.json"
        ),
        bundle_sha256="c" * 64,
        outcome="coached",
        battle_mode_id="splat_zones",
        stage_id="mahi_mahi",
        moment_count=1,
        result_json={"status": "complete", "moments": [{"index": 0}]},
        assets=[clip, frame],
        published_at=now,
        expires_at=now + timedelta(days=30),
        now=now,
    )
    assert committed.publication_state == "verified"
    assert committed.active_run_id == "run-a"
    assert committed.attempt_run_id is None
    media = reports.media_asset(submission_id, "clip", 0)
    assert media is not None
    assert media.attempt_generation == lease.generation
    history = reports.history_fields([submission_id, "missing"])
    assert history[submission_id].stage_id == "mahi_mahi"
    assert "missing" not in history


def test_stale_generation_cannot_commit(reports: ReportStore) -> None:
    now = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)
    submission_id = "sub-report-stale"
    reports.upsert_pending(
        submission_id,
        bucket="s3-coaching-dev",
        source_object_key=None,
        source_upload_expires_at=None,
        expires_at=None,
        now=now,
    )
    first = reports.lease_attempt(
        submission_id, "run-old", now + timedelta(seconds=1), now
    )
    assert first is not None
    later = now + timedelta(seconds=2)
    second = reports.lease_attempt(
        submission_id, "run-new", later + timedelta(minutes=10), later
    )
    assert second is not None
    assert second.generation == first.generation + 1
    with pytest.raises(StaleAttemptError):
        reports.commit_verified(
            submission_id,
            run_id="run-old",
            generation=first.generation,
            bucket="s3-coaching-dev",
            bundle_key="reports/x/y/attempt-1/bundle.json",
            bundle_sha256="d" * 64,
            outcome="coached",
            battle_mode_id=None,
            stage_id=None,
            moment_count=0,
            result_json={},
            assets=[],
            published_at=later,
            expires_at=None,
            now=later,
        )


def test_delete_pending_is_terminal_barrier(reports: ReportStore) -> None:
    now = datetime(2026, 3, 3, 12, 0, tzinfo=UTC)
    submission_id = "sub-report-expire"
    reports.upsert_pending(
        submission_id,
        bucket="s3-coaching-dev",
        source_object_key=None,
        source_upload_expires_at=None,
        expires_at=now,
        now=now,
    )
    lease = reports.lease_attempt(
        submission_id, "run-a", now + timedelta(minutes=10), now
    )
    assert lease is not None
    assert reports.mark_delete_pending(submission_id, now)
    assert reports.lease_attempt(
        submission_id, "run-b", now + timedelta(minutes=20), now
    ) is None
    with pytest.raises(StaleAttemptError):
        reports.validate_fence(submission_id, "run-a", lease.generation, now)
    with pytest.raises(StaleAttemptError):
        reports.upsert_pending(
            submission_id,
            bucket="s3-coaching-dev",
            source_object_key=None,
            source_upload_expires_at=None,
            expires_at=now,
            now=now,
        )


def test_mark_delete_pending_hides_media(reports: ReportStore) -> None:
    now = datetime(2026, 3, 3, 12, 0, tzinfo=UTC)
    submission_id = "sub-report-3"
    reports.upsert_pending(
        submission_id,
        bucket="s3-coaching-dev",
        source_object_key=None,
        source_upload_expires_at=None,
        expires_at=now,
        now=now,
    )
    lease = reports.lease_attempt(
        submission_id, "run-a", now + timedelta(minutes=5), now
    )
    assert lease is not None
    asset = _asset(
        submission_id=submission_id,
        run_id="run-a",
        generation=lease.generation,
        asset_type="clip",
        moment_index=0,
        object_key=(
            f"reports/{submission_id}/run-a/attempt-{lease.generation}/"
            "public_clips/x.mp4"
        ),
        sha256="h" * 64,
        now=now,
    )
    reports.commit_verified(
        submission_id,
        run_id="run-a",
        generation=lease.generation,
        bucket="s3-coaching-dev",
        bundle_key=(
            f"reports/{submission_id}/run-a/attempt-{lease.generation}/bundle.json"
        ),
        bundle_sha256="i" * 64,
        outcome="coached",
        battle_mode_id=None,
        stage_id=None,
        moment_count=1,
        result_json={},
        assets=[asset],
        published_at=now,
        expires_at=now,
        now=now,
    )
    assert reports.mark_delete_pending(submission_id, now)
    assert reports.media_asset(submission_id, "clip", 0) is None
