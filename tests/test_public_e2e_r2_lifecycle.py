"""End-to-end R2 publication lifecycle with Increment 6 media delivery.

Only the in-process fake S3 and local coach_test PostgreSQL are used.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

import psycopg
import pytest
from fake_s3_helpers import fake_s3
from fastapi.testclient import TestClient
from public_site.report_bundle import attempt_key
from public_site.report_store import ReportStore
from public_site.storage import file_sha256
from public_site.store import PublicStore
from report_bundle_fixture import write_report_fixture, add_media
from report_test_helpers import (
    Clock,
    complete_submission,
    drain,
    expire,
    report_client,
    r2_for,
    settle,
)
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


def _bind_report_store(client: TestClient, db_url: str) -> ReportStore:
    store = ReportStore(db_url)
    store.ensure_schema()
    store.clear()
    client.app.state.report_store = store
    client.app.state.service.report_store = store
    if client.app.state.reports is not None:
        client.app.state.reports.report_store = store
    return store


def _r2_get(url: str, *, range_header: str | None = None) -> tuple[int, bytes]:
    headers = {}
    if range_header:
        headers["Range"] = range_header
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=5) as response:
        return response.status, response.read()


def test_full_lifecycle_restart_and_expiry(tmp_path: Path) -> None:
    db_url = _database_url()
    with fake_s3() as fake:
        client = report_client(
            tmp_path,
            fake,
            database_url=db_url,
            report_publish=True,
            report_expiry_delete=True,
            media_url_seconds=120,
        )
        store = _bind_report_store(client, db_url)
        if client.app.state.payments is not None:
            client.app.state.payments.set_price(0)
        token, submission = complete_submission(client, tmp_path)
        published = store.get(submission.id)
        assert published is not None
        assert published.publication_state == "verified"
        assert published.result_json
        assert store.media_asset(submission.id, "clip", 0) is not None

        view = client.get(f"/api/submissions/{token}")
        assert view.status_code == 200
        assert view.json()["status"] == "complete"
        assert view.json()["result"]["moments"]

        clip = client.get(
            f"/api/submissions/{token}/moments/0/clip", follow_redirects=False
        )
        assert clip.status_code == 302
        assert clip.content == b""
        status, body = _r2_get(clip.headers["location"], range_header="bytes=0-63")
        assert status == 206
        assert len(body) == 64

        # Application restart over the same PG + fake S3, empty analysis.
        root = client.app.state.settings.root
        analysis = root / "analysis"
        if analysis.exists():
            for child in analysis.iterdir():
                if child.is_dir():
                    for path in child.rglob("*"):
                        if path.is_file():
                            path.unlink()
        restarted = report_client(
            tmp_path,
            fake,
            database_url=db_url,
            report_publish=True,
            report_expiry_delete=True,
        )
        restarted.app.state.report_store = store
        restarted.app.state.service.report_store = store
        if restarted.app.state.reports is not None:
            restarted.app.state.reports.report_store = store
        again = restarted.get(f"/api/submissions/{token}")
        assert again.status_code == 200
        assert again.json()["result"]["moments"]
        clip2 = restarted.get(
            f"/api/submissions/{token}/moments/0/clip", follow_redirects=False
        )
        assert clip2.status_code == 302
        status, body = _r2_get(clip2.headers["location"])
        assert status == 200 and body

        # Expiry → deleted → media 404 (no local fallback)
        expire(restarted.app.state.store, submission.id)
        restarted.app.state.service.maintain(datetime.now(UTC))
        if restarted.app.state.reports is not None:
            settle(restarted.app.state.reports)
            settle(restarted.app.state.reports)
        published = store.get(submission.id)
        assert published is not None
        assert published.publication_state == "deleted"
        gone = restarted.get(
            f"/api/submissions/{token}/moments/0/clip", follow_redirects=False
        )
        assert gone.status_code == 404
        restarted.close()
        client.close()


def test_replacement_continuity_keeps_old_assets_readable(
    tmp_path: Path,
) -> None:
    """Observable before / during / after replacement (validates mark_verified_r2)."""
    db_url = _database_url()
    now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    with fake_s3() as fake:
        client = report_client(
            tmp_path,
            fake,
            database_url=db_url,
            report_publish=True,
            report_expiry_delete=True,
            media_url_seconds=300,
        )
        store = _bind_report_store(client, db_url)
        if client.app.state.payments is not None:
            client.app.state.payments.set_price(0)
        token, submission = complete_submission(client, tmp_path)
        first = store.get(submission.id)
        assert first is not None and first.publication_state == "verified"
        old_run = first.active_run_id
        old_asset = store.media_asset(submission.id, "clip", 0)
        assert old_asset is not None and old_run

        before = client.get(
            f"/api/submissions/{token}/moments/0/clip", follow_redirects=False
        )
        assert before.status_code == 302
        assert old_run in before.headers["location"]
        status, body = _r2_get(before.headers["location"])
        assert status == 200 and body

        # Begin a replacement attempt while keeping verified readable.
        later = now + timedelta(seconds=2)
        lease = store.lease_attempt(
            submission.id,
            "20260801T120000Z-repl0001",
            later + timedelta(minutes=10),
            later,
        )
        assert lease is not None and lease.generation >= 2
        # Plant a new-generation object and mark_verified_r2 (PG pending).
        new_key = attempt_key(
            submission.id, lease.attempt_run_id, lease.generation, "public_clips/x.mp4"
        )
        path = _file(tmp_path, "repl.mp4", b"replacement-clip-bytes-xxxxx")
        digest = file_sha256(path)
        r2_for(fake).put_file(new_key, path, "video/mp4", digest, path.stat().st_size)
        assert store.mark_verified_r2(
            submission.id,
            attempt_run_id=lease.attempt_run_id,
            generation=lease.generation,
            bundle_key=attempt_key(
                submission.id, lease.attempt_run_id, lease.generation, "bundle.json"
            ),
            bundle_sha256="c" * 64,
            now=later,
        )
        mid = store.get(submission.id)
        assert mid is not None
        assert mid.publication_state == "verified"  # continuity fix
        assert mid.active_run_id == old_run

        during = client.get(
            f"/api/submissions/{token}/moments/0/clip", follow_redirects=False
        )
        assert during.status_code == 302
        assert old_run in during.headers["location"]
        assert lease.attempt_run_id not in during.headers["location"]
        status, body = _r2_get(during.headers["location"])
        assert status == 200 and body

        # Commit replacement → media switches to new generation.
        from public_site.report_store import PublishedAsset, new_asset_id

        committed = store.commit_verified(
            submission.id,
            run_id=lease.attempt_run_id,
            generation=lease.generation,
            bucket=fake.bucket,
            bundle_key=attempt_key(
                submission.id, lease.attempt_run_id, lease.generation, "bundle.json"
            ),
            bundle_sha256="c" * 64,
            outcome="coached",
            battle_mode_id=None,
            stage_id=None,
            moment_count=1,
            result_json=first.result_json,
            assets=[
                PublishedAsset(
                    asset_id=new_asset_id(),
                    submission_id=submission.id,
                    run_id=lease.attempt_run_id,
                    attempt_generation=lease.generation,
                    asset_type="clip",
                    moment_index=0,
                    safe_id="x",
                    storage_provider="r2",
                    bucket=fake.bucket,
                    object_key=new_key,
                    content_type="video/mp4",
                    size_bytes=path.stat().st_size,
                    sha256=digest,
                    publication_state="verified",
                    deletion_state="retained",
                    created_at=later,
                    expires_at=None,
                    deleted_at=None,
                )
            ],
            published_at=later,
            expires_at=None,
            now=later,
        )
        assert committed.active_run_id == lease.attempt_run_id

        after = client.get(
            f"/api/submissions/{token}/moments/0/clip", follow_redirects=False
        )
        assert after.status_code == 302
        assert lease.attempt_run_id in after.headers["location"]
        status, body = _r2_get(after.headers["location"])
        assert status == 200
        assert body == b"replacement-clip-bytes-xxxxx"
        client.close()
