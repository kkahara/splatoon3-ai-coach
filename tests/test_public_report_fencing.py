"""Generation-prefix fencing: stale workers cannot publish or overwrite newer gens."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from fake_s3_helpers import fake_s3

from public_site.report_bundle import attempt_key, attempt_prefix
from public_site.report_lifecycle import (
    UPLOAD_SETTLE_GRACE_SECONDS,
    cleanup_source_object,
    inventory_for_attempt,
    verify_full_inventory,
)
from public_site.report_content import KnownValues
from public_site.report_store import (
    PublishedAsset,
    ReportStore,
    StaleAttemptError,
    new_asset_id,
)
from public_site.storage import ReportWriter, file_sha256
from report_bundle_fixture import write_report_fixture
from report_test_helpers import Clock, r2_for
from tests.test_public_storage import _file


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


def test_stale_worker_put_lands_only_in_abandoned_generation(
    reports: ReportStore, tmp_path: Path
) -> None:
    """A late PUT after lease loss cannot overwrite the newer generation prefix."""
    now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    submission_id = "a" * 32
    reports.upsert_pending(
        submission_id,
        bucket="s3-coaching-dev",
        source_object_key=None,
        source_upload_expires_at=None,
        expires_at=None,
        now=now,
    )
    lease_a = reports.lease_attempt(
        submission_id, "run-old", now + timedelta(seconds=1), now
    )
    assert lease_a is not None and lease_a.generation == 1
    later = now + timedelta(seconds=2)
    lease_b = reports.lease_attempt(
        submission_id, "run-new", later + timedelta(minutes=10), later
    )
    assert lease_b is not None and lease_b.generation == 2

    body = b"stale-generation-bytes"
    with fake_s3() as fake:
        r2 = r2_for(fake)
        writer = ReportWriter(r2)
        stale_key = attempt_key(
            submission_id, "run-old", lease_a.generation, "public_clips/x.mp4"
        )
        fresh_key = attempt_key(
            submission_id, "run-new", lease_b.generation, "public_clips/x.mp4"
        )
        from public_site.storage import file_sha256

        fresh = _file(tmp_path, "fresh.mp4", b"authoritative")
        writer.put_file(
            fresh_key,
            fresh,
            "video/mp4",
            file_sha256(fresh),
            fresh.stat().st_size,
        )
        # Stale worker finishes a late PUT into its own abandoned prefix only.
        stale = _file(tmp_path, "stale.mp4", body)
        writer.put_file(
            stale_key,
            stale,
            "video/mp4",
            file_sha256(stale),
            stale.stat().st_size,
        )
        assert fake.objects[stale_key].body == body
        assert fake.objects[fresh_key].body == b"authoritative"
        with pytest.raises(StaleAttemptError):
            reports.validate_fence(
                submission_id, "run-old", lease_a.generation, later
            )


def test_resume_same_generation_while_lease_valid(
    reports: ReportStore,
) -> None:
    now = datetime(2026, 5, 2, 12, 0, tzinfo=UTC)
    submission_id = "b" * 32
    reports.upsert_pending(
        submission_id,
        bucket="s3-coaching-dev",
        source_object_key=None,
        source_upload_expires_at=None,
        expires_at=None,
        now=now,
    )
    first = reports.lease_attempt(
        submission_id, "run-a", now + timedelta(minutes=10), now
    )
    assert first is not None and first.generation == 1
    renewed = reports.lease_attempt(
        submission_id,
        "run-a",
        now + timedelta(minutes=20),
        now + timedelta(minutes=1),
    )
    assert renewed is not None
    assert renewed.generation == 1
    assert renewed.attempt_run_id == "run-a"


def test_full_inventory_repair_targets_only_corrupt_object(
    tmp_path: Path,
) -> None:
    analysis = tmp_path / "analysis"
    write_report_fixture(analysis, death_times=(0.5,))
    from report_bundle_fixture import add_media

    add_media(analysis)
    from public_site.report_bundle import prepare_bundle
    from public_site.report_staging import StagingArea

    staging = StagingArea(tmp_path / "root")
    run_id = "20260503T120000Z-aaaaaaaa"
    run = staging.open_run("c" * 32, run_id)
    prepared = prepare_bundle(
        analysis,
        submission_id="c" * 32,
        run_id=run_id,
        outcome="coached",
        created_at="2026-05-03T12:00:00Z",
        known=KnownValues(),
        staging=run,
    )
    inventory = inventory_for_attempt(
        prepared, submission_id="c" * 32, run_id=run_id, generation=1
    )
    with fake_s3() as fake:
        r2 = r2_for(fake)
        writer = ReportWriter(r2)
        puts = {"n": 0}
        original = writer.put_file

        def counting(key, *args, **kwargs):
            puts["n"] += 1
            return original(key, *args, **kwargs)

        writer.put_file = counting  # type: ignore[method-assign]
        for item in inventory:
            assert item.source is not None
            original(
                item.object_key,
                item.source,
                item.content_type,
                item.sha256,
                item.size_bytes,
            )
        puts["n"] = 0
        # Corrupt one non-bundle object.
        target = next(item for item in inventory if item.relative_path != "bundle.json")
        fake.objects[target.object_key].body = b"x"
        missing = verify_full_inventory(writer, inventory)
        assert [item.object_key for item in missing] == [target.object_key]
        from public_site.report_lifecycle import repair_inventory_object

        repair_inventory_object(writer, missing[0])
        assert puts["n"] == 1
        assert verify_full_inventory(writer, inventory) == []
    run.close()


def test_delete_pending_fences_commit_and_refuses_resurrection(
    reports: ReportStore,
) -> None:
    now = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    submission_id = "d" * 32
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
    assert reports.mark_delete_pending(submission_id, now)
    with pytest.raises(StaleAttemptError):
        reports.commit_verified(
            submission_id,
            run_id="run-a",
            generation=lease.generation,
            bucket="s3-coaching-dev",
            bundle_key=attempt_key(
                submission_id, "run-a", lease.generation, "bundle.json"
            ),
            bundle_sha256="e" * 64,
            outcome="coached",
            battle_mode_id=None,
            stage_id=None,
            moment_count=0,
            result_json={},
            assets=[],
            published_at=now,
            expires_at=None,
            now=now,
        )
    with pytest.raises(StaleAttemptError):
        reports.upsert_pending(
            submission_id,
            bucket="s3-coaching-dev",
            source_object_key=None,
            source_upload_expires_at=None,
            expires_at=now,
            now=now,
        )


def test_source_cleanup_settle_after_late_put_replay(tmp_path: Path) -> None:
    key = "submissions/fff/source.mp4"
    body = b"source-video"
    now = datetime(2026, 5, 5, 12, 0, tzinfo=UTC)
    clock = Clock(now)
    with fake_s3() as fake:
        fake.check_presign_expiry = True
        fake.clock = clock
        from public_site.storage import R2Storage

        r2 = R2Storage(
            account_id="account",
            access_key_id=fake.access_key,
            secret_access_key=fake.secret,
            bucket=fake.bucket,
            endpoint=fake.endpoint,
            url_seconds=900,
        )
        r2.put_file(key, _file(tmp_path, "source.mp4", body), "video/mp4")
        upload = r2.presign_put(key, "video/mp4", len(body), issued_at=now)
        first = cleanup_source_object(
            r2,
            key,
            now=clock.now,
            source_upload_expires_at=now + timedelta(seconds=900),
        )
        assert first.state == "pending_settle"
        import urllib.request

        request = urllib.request.Request(
            upload.url, data=body, method="PUT", headers=upload.headers
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
        assert r2.head(key) is not None
        clock.advance(seconds=900 + UPLOAD_SETTLE_GRACE_SECONDS)
        done = cleanup_source_object(
            r2,
            key,
            now=clock.now,
            source_upload_expires_at=now + timedelta(seconds=900),
        )
        assert done.state == "completed"
        assert r2.head(key) is None


def test_abandoned_prefix_helper_matches_attempt_layout() -> None:
    prefix = attempt_prefix("e" * 32, "run-z", 3)
    assert prefix == f"reports/{'e' * 32}/run-z/attempt-3/"
    assert attempt_key("e" * 32, "run-z", 3, "bundle.json") == prefix + "bundle.json"


def test_abandoned_generation_cleanup_survives_late_put(
    reports: ReportStore, tmp_path: Path
) -> None:
    """Scenario 1: delete abandoned gen, late PUT recreates, re-delete, settle."""
    from public_site.report_publisher import (
        DELETE_SETTLE_SECONDS,
        ReportDeleter,
        ReportPublisher,
    )
    from public_site.report_publications import PublicationStore
    from public_site.report_staging import StagingArea
    from public_site.storage import ReportEraser, file_sha256
    from public_site.store import PublicStore

    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    clock = Clock(now)
    submission_id = "1" * 32
    reports.upsert_pending(
        submission_id,
        bucket="s3-coaching-dev",
        source_object_key=None,
        source_upload_expires_at=None,
        expires_at=None,
        now=now,
    )
    old = reports.lease_attempt(
        submission_id, "20260601T120000Z-old00001", now + timedelta(seconds=1), now
    )
    assert old is not None and old.generation == 1
    later = now + timedelta(seconds=2)
    new = reports.lease_attempt(
        submission_id,
        "20260601T120000Z-new00002",
        later + timedelta(minutes=10),
        later,
    )
    assert new is not None and new.generation == 2

    with fake_s3() as fake:
        r2 = r2_for(fake)
        writer = ReportWriter(r2)
        stale_key = attempt_key(
            submission_id, old.attempt_run_id, old.generation, "public_clips/x.mp4"
        )
        fresh_key = attempt_key(
            submission_id, new.attempt_run_id, new.generation, "public_clips/x.mp4"
        )
        body = b"abandoned-bytes"
        path = _file(tmp_path, "x.mp4", body)
        digest = file_sha256(path)
        writer.put_file(stale_key, path, "video/mp4", digest, len(body))
        writer.put_file(fresh_key, path, "video/mp4", digest, len(body))

        fresh_asset = PublishedAsset(
            asset_id=new_asset_id(),
            submission_id=submission_id,
            run_id=new.attempt_run_id,
            attempt_generation=new.generation,
            asset_type="clip",
            moment_index=0,
            safe_id="x",
            storage_provider="r2",
            bucket=fake.bucket,
            object_key=fresh_key,
            content_type="video/mp4",
            size_bytes=len(body),
            sha256=digest,
            publication_state="verified",
            deletion_state="retained",
            created_at=later,
            expires_at=None,
            deleted_at=None,
        )
        reports.commit_verified(
            submission_id,
            run_id=new.attempt_run_id,
            generation=new.generation,
            bucket=fake.bucket,
            bundle_key=attempt_key(
                submission_id, new.attempt_run_id, new.generation, "bundle.json"
            ),
            bundle_sha256="a" * 64,
            outcome="coached",
            battle_mode_id=None,
            stage_id=None,
            moment_count=0,
            result_json={"status": "complete", "moments": []},
            assets=[fresh_asset],
            published_at=later,
            expires_at=None,
            now=later,
        )
        # Plant a delete_pending row for the abandoned generation.
        with reports._conn() as conn:  # noqa: SLF001
            conn.execute(
                """
                INSERT INTO published_assets (
                    asset_id, submission_id, run_id, attempt_generation,
                    asset_type, moment_index, safe_id, storage_provider, bucket,
                    object_key, content_type, size_bytes, sha256,
                    publication_state, deletion_state, created_at
                ) VALUES (
                    %s, %s, %s, %s, 'clip', 0, 'x', 'r2', %s, %s, 'video/mp4',
                    %s, %s, 'superseded', 'delete_pending', %s
                )
                """,
                (
                    new_asset_id(),
                    submission_id,
                    old.attempt_run_id,
                    old.generation,
                    fake.bucket,
                    stale_key,
                    len(body),
                    digest,
                    later,
                ),
            )

        root = tmp_path / "public"
        root.mkdir()
        publisher = ReportPublisher(
            store=PublicStore(root),
            publications=PublicationStore(root),
            staging=StagingArea(root),
            known=lambda _s: None,  # type: ignore[arg-type, return-value]
            writer=writer,
            deleter=ReportDeleter(ReportEraser(r2)),
            clock=clock,
            report_store=reports,
            bucket=fake.bucket,
        )
        # First pass: DELETE abandoned objects, start settle clock.
        publisher._settle_abandoned_generations()  # noqa: SLF001
        assert r2.head(stale_key) is None
        assert r2.head(fresh_key) is not None
        assets = reports.list_assets_for_generation(
            submission_id, old.attempt_run_id, old.generation
        )
        assert assets and assets[0].deletion_state == "delete_pending"

        # Outstanding late PUT recreates the abandoned object.
        writer.put_file(stale_key, path, "video/mp4", digest, len(body))
        assert r2.head(stale_key) is not None
        publisher._settle_abandoned_generations()  # noqa: SLF001
        assert r2.head(stale_key) is None  # re-deleted
        assert publisher._abandoned_empty_at.get(  # noqa: SLF001
            (submission_id, old.attempt_run_id, old.generation)
        ) is not None

        # Before settle window: still delete_pending.
        clock.advance(seconds=DELETE_SETTLE_SECONDS - 1)
        publisher._settle_abandoned_generations()  # noqa: SLF001
        assets = reports.list_assets_for_generation(
            submission_id, old.attempt_run_id, old.generation
        )
        assert assets[0].deletion_state == "delete_pending"

        # After settle + empty LIST: marked deleted. Newer gen untouched.
        clock.advance(seconds=2)
        publisher._settle_abandoned_generations()  # noqa: SLF001
        assets = reports.list_assets_for_generation(
            submission_id, old.attempt_run_id, old.generation
        )
        assert assets[0].deletion_state == "deleted"
        assert r2.head(fresh_key) is not None
        assert fake.bucket != "s3-coaching"


def test_restart_serves_result_from_postgres_after_local_cleanup(
    reports: ReportStore, tmp_path: Path
) -> None:
    """Scenario 2: after PG commit + local cleanup, project() reads PostgreSQL."""
    from public_site.models import Submission
    from public_site.notices import NoticeSender
    from public_site.report_lifecycle import (
        clear_local_analysis,
        deserialize_result_projection,
        serialize_result_projection,
    )
    from public_site.results import load_result
    from public_site.service import SubmissionService
    from public_site.settings import PublicSettings
    from public_site.storage import LocalStorage
    from public_site.store import PublicStore
    from report_bundle_fixture import add_media, write_report_fixture
    from splatoon3_ai_coach.config.paths import default_config_path

    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    submission_id = "2" * 32
    analysis = tmp_path / "analysis" / submission_id
    write_report_fixture(analysis, death_times=(0.5,))
    add_media(analysis)
    result = load_result(analysis)
    payload = serialize_result_projection(result)

    reports.upsert_pending(
        submission_id,
        bucket="s3-coaching-dev",
        source_object_key=None,
        source_upload_expires_at=None,
        expires_at=now + timedelta(days=7),
        now=now,
    )
    lease = reports.lease_attempt(
        submission_id, "20260602T120000Z-aaaa0001", now + timedelta(minutes=10), now
    )
    assert lease is not None
    reports.commit_verified(
        submission_id,
        run_id=lease.attempt_run_id,
        generation=lease.generation,
        bucket="s3-coaching-dev",
        bundle_key=attempt_key(
            submission_id, lease.attempt_run_id, lease.generation, "bundle.json"
        ),
        bundle_sha256="b" * 64,
        outcome="coached",
        battle_mode_id="splat_zones",
        stage_id="synthetic_stage",
        moment_count=len(result.moments),
        result_json=payload,
        assets=[],
        published_at=now,
        expires_at=now + timedelta(days=7),
        now=now,
    )
    assert clear_local_analysis(analysis)
    assert not analysis.exists()

    root = tmp_path / "public"
    root.mkdir()
    store = PublicStore(root)
    submission = Submission(
        id=submission_id,
        access_token_hash="c" * 64,
        created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        updated_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=(now + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        status="complete",
        step="ready",
        outcome="coached",
        analysis_dir=None,
    )
    store.save_submission(submission)

    settings = PublicSettings(
        root=root,
        config_path=default_config_path(),
        static_dir=tmp_path / "no-dist",
        max_video_bytes=10_000_000,
        retention_days=7,
        r2_account_id="acct",
        r2_access_key_id="key",
        r2_secret_access_key="secret",
        r2_bucket="s3-coaching-dev",
        database_url=reports.database_url,
        dev_mode=True,
    )
    service = SubmissionService(
        settings, store, LocalStorage(root / "objects"), NoticeSender(settings)
    )
    service.report_store = reports
    projected = service.project(submission, "en")
    assert projected.result is not None
    assert projected.result.moments
    assert len(projected.result.moments) == len(result.moments)
    reloaded = deserialize_result_projection(reports.get(submission_id).result_json)  # type: ignore[union-attr]
    assert reloaded is not None
    assert len(reloaded.moments) == len(result.moments)


def test_two_publishers_stale_generation_cannot_commit(
    reports: ReportStore, tmp_path: Path
) -> None:
    """Scenario 3: separate PublicationStore roots; stale fence loses to newer gen."""
    from public_site.report_publications import PublicationStore, ReportPublication

    now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)
    submission_id = "3" * 32
    reports.upsert_pending(
        submission_id,
        bucket="s3-coaching-dev",
        source_object_key=None,
        source_upload_expires_at=None,
        expires_at=None,
        now=now,
    )

    root_a = tmp_path / "pub-a"
    root_b = tmp_path / "pub-b"
    root_a.mkdir()
    root_b.mkdir()
    pubs_a = PublicationStore(root_a)
    pubs_b = PublicationStore(root_b)
    # No shared flock: each root has its own locks directory.
    assert pubs_a.root != pubs_b.root

    lease_a = reports.lease_attempt(
        submission_id, "20260603T120000Z-aaaa0001", now + timedelta(seconds=1), now
    )
    assert lease_a is not None and lease_a.generation == 1
    pubs_a.save(
        ReportPublication(
            submission_id=submission_id,
            state="pending",
            attempt_run_id=lease_a.attempt_run_id,
            attempt_generation=lease_a.generation,
        )
    )

    later = now + timedelta(seconds=2)
    lease_b = reports.lease_attempt(
        submission_id,
        "20260603T120000Z-bbbb0002",
        later + timedelta(minutes=10),
        later,
    )
    assert lease_b is not None and lease_b.generation == 2
    pubs_b.save(
        ReportPublication(
            submission_id=submission_id,
            state="pending",
            attempt_run_id=lease_b.attempt_run_id,
            attempt_generation=lease_b.generation,
        )
    )

    # Stale publisher A is fenced for validate, upload, and commit.
    with pytest.raises(StaleAttemptError):
        reports.validate_fence(
            submission_id, lease_a.attempt_run_id, lease_a.generation, later
        )
    with pytest.raises(StaleAttemptError):
        reports.commit_verified(
            submission_id,
            run_id=lease_a.attempt_run_id,
            generation=lease_a.generation,
            bucket="s3-coaching-dev",
            bundle_key=attempt_key(
                submission_id, lease_a.attempt_run_id, lease_a.generation, "bundle.json"
            ),
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

    # Publisher B commits the newer generation successfully.
    committed = reports.commit_verified(
        submission_id,
        run_id=lease_b.attempt_run_id,
        generation=lease_b.generation,
        bucket="s3-coaching-dev",
        bundle_key=attempt_key(
            submission_id, lease_b.attempt_run_id, lease_b.generation, "bundle.json"
        ),
        bundle_sha256="e" * 64,
        outcome="coached",
        battle_mode_id=None,
        stage_id=None,
        moment_count=0,
        result_json={"from": "publisher-b"},
        assets=[],
        published_at=later,
        expires_at=None,
        now=later,
    )
    assert committed.publication_state == "verified"
    assert committed.active_run_id == lease_b.attempt_run_id
    assert committed.attempt_generation == lease_b.generation
    assert reports.get(submission_id).result_json == {"from": "publisher-b"}  # type: ignore[union-attr]
    # Local records remain independent; flock was never shared.
    assert pubs_a.get(submission_id).attempt_generation == 1  # type: ignore[union-attr]
    assert pubs_b.get(submission_id).attempt_generation == 2  # type: ignore[union-attr]
