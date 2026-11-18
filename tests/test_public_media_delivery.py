"""Increment 6: authorized 302 media redirects and Range on the presigned R2 URL.

Only the in-process fake S3 is used; no real R2 is ever contacted.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

import psycopg
import pytest
from fake_s3_helpers import fake_s3
from fastapi.testclient import TestClient
from public_site.accounts import AccountStore
from public_site.report_store import ReportStore
from public_site.storage import LocalStorage, file_sha256
from report_test_helpers import Clock, complete_submission, report_client
from tests.test_public_accounts import _PASSWORD, _mail_link, _token_from
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
def db_url() -> str:
    return _database_url()


def _bind_report_store(client: TestClient, db_url: str) -> ReportStore:
    store = ReportStore(db_url)
    store.ensure_schema()
    store.clear()
    client.app.state.report_store = store
    client.app.state.service.report_store = store
    if client.app.state.reports is not None:
        client.app.state.reports.report_store = store
    return store


def _published_client(tmp_path: Path, fake, db_url: str, **flags) -> TestClient:
    flags.setdefault("media_url_seconds", 60)
    client = report_client(
        tmp_path,
        fake,
        database_url=db_url,
        report_publish=True,
        report_expiry_delete=True,
        **flags,
    )
    _bind_report_store(client, db_url)
    payments = getattr(client.app.state, "payments", None)
    if payments is not None:
        payments.set_price(0)
    return client


def _r2_get(url: str, *, range_header: str | None = None) -> tuple[int, dict, bytes]:
    """GET the presigned R2 URL directly (not FastAPI)."""
    headers = {}
    if range_header:
        headers["Range"] = range_header
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=5) as response:
        return response.status, dict(response.headers), response.read()


def test_guest_clip_redirects_to_presigned_r2_without_proxying(
    tmp_path: Path, db_url: str
) -> None:
    with fake_s3() as fake:
        client = _published_client(tmp_path, fake, db_url)
        token, submission = complete_submission(client, tmp_path)
        assert submission.status == "complete"
        assert client.app.state.report_store.get(submission.id).publication_state == (
            "verified"
        )

        response = client.get(
            f"/api/submissions/{token}/moments/0/clip", follow_redirects=False
        )
        assert response.status_code == 302
        assert response.headers["cache-control"] == "private, no-store"
        assert "accept" in response.headers["vary"].lower()
        assert response.content == b""  # FastAPI must not proxy media bytes
        location = response.headers["location"]
        assert location.startswith(fake.endpoint)

        status, headers, body = _r2_get(location)
        assert status == 200
        assert body
        assert "video/mp4" in (headers.get("Content-Type") or "")

        # Range is issued to the presigned R2 URL, never to FastAPI.
        status, headers, body = _r2_get(location, range_header="bytes=0-127")
        assert status == 206
        assert headers.get("Accept-Ranges") == "bytes"
        assert (headers.get("Content-Range") or "").startswith("bytes 0-127/")
        assert len(body) == 128

        api_range = client.get(
            f"/api/submissions/{token}/moments/0/clip",
            headers={"Range": "bytes=0-127"},
            follow_redirects=False,
        )
        assert api_range.status_code == 302
        assert api_range.content == b""


def test_guest_frame_and_json_accept(tmp_path: Path, db_url: str) -> None:
    with fake_s3() as fake:
        client = _published_client(tmp_path, fake, db_url)
        token, submission = complete_submission(client, tmp_path)
        frame = client.get(
            f"/api/submissions/{token}/moments/0/frame", follow_redirects=False
        )
        assert frame.status_code == 302
        assert frame.content == b""
        status, headers, body = _r2_get(frame.headers["location"])
        assert status == 200
        assert body

        meta = client.get(
            f"/api/submissions/{token}/moments/0/clip",
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        assert meta.status_code == 200
        payload = meta.json()
        assert payload["url"].startswith(fake.endpoint)
        assert payload["expires_at"]
        assert payload["content_type"] == "video/mp4"
        published = client.app.state.report_store.get(submission.id)
        dumped = str(published.result_json)
        assert "X-Amz-Signature" not in dumped
        assert fake.endpoint not in dumped


def test_owned_auth_conventions(tmp_path: Path, db_url: str) -> None:
    accounts = AccountStore(db_url)
    accounts.ensure_schema()
    accounts.clear()
    with fake_s3() as fake:
        client = report_client(
            tmp_path,
            fake,
            database_url=db_url,
            report_publish=True,
            report_expiry_delete=True,
        )
        store = _bind_report_store(client, db_url)
        client.app.state.accounts = accounts
        client.app.state.service.accounts = accounts
        if client.app.state.payments is not None:
            client.app.state.payments.set_price(0)

        token, submission = complete_submission(client, tmp_path)
        assert store.get(submission.id).publication_state == "verified"

        logged_out = client.get(
            f"/api/me/submissions/{submission.id}/moments/0/clip",
            follow_redirects=False,
        )
        assert logged_out.status_code == 401

        assert (
            client.post(
                "/api/auth/register",
                json={
                    "name": "Owner",
                    "email": "owner@media.test",
                    "password": _PASSWORD,
                },
            ).status_code
            == 200
        )
        verify = client.post(
            "/api/auth/verify",
            json={"token": _token_from(_mail_link(client.app.state.settings.root, "verify"))},
        )
        assert verify.status_code == 200, verify.text
        me = client.get("/api/me").json()
        user_id = accounts.user_from_session(
            client.cookies.get("s3_session", ""), datetime.now(UTC)
        )
        assert user_id is not None
        accounts.record_history(
            user_id.id,
            submission.id,
            submission.created_at,
            submission.expires_at,
        )
        submission.user_id = user_id.id
        client.app.state.store.save_submission(submission)

        owned = client.get(
            f"/api/me/submissions/{submission.id}/moments/0/clip",
            follow_redirects=False,
        )
        assert owned.status_code == 302
        assert owned.content == b""

        other = client.get(
            f"/api/me/submissions/{'f' * 32}/moments/0/clip",
            follow_redirects=False,
        )
        assert other.status_code == 404


def test_fallback_states_and_terminal_refuse_local(
    tmp_path: Path, db_url: str
) -> None:
    now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    with fake_s3() as fake:
        client = _published_client(tmp_path, fake, db_url)
        token, submission = complete_submission(client, tmp_path)
        reports: ReportStore = client.app.state.report_store
        assert reports.get(submission.id).publication_state == "verified"

        missing = client.get(
            f"/api/submissions/{token}/moments/99/clip", follow_redirects=False
        )
        assert missing.status_code == 404

        # Recreate a local analysis stub after cleanup so fallback would be
        # tempting if terminal states were mishandled.
        analysis = Path(client.app.state.settings.root) / "analysis" / submission.id
        analysis.mkdir(parents=True, exist_ok=True)
        submission.analysis_dir = str(analysis)
        client.app.state.store.save_submission(submission)

        reports.mark_delete_pending(submission.id, now)
        denied = client.get(
            f"/api/submissions/{token}/moments/0/clip", follow_redirects=False
        )
        assert denied.status_code == 404

        reports.clear()
        reports.upsert_pending(
            submission.id,
            bucket=fake.bucket,
            source_object_key=None,
            source_upload_expires_at=None,
            expires_at=None,
            now=now,
        )
        reports.mark_blocked(submission.id, generation=None, now=now)
        blocked = client.get(
            f"/api/submissions/{token}/moments/0/clip", follow_redirects=False
        )
        assert blocked.status_code == 404


def test_expired_presign_refresh_issues_new_url(tmp_path: Path, db_url: str) -> None:
    # Align fake clock with wall time so X-Amz-Date from presign_get expires.
    clock = Clock(datetime.now(UTC))
    with fake_s3() as fake:
        fake.check_presign_expiry = True
        fake.clock = clock
        client = _published_client(tmp_path, fake, db_url, media_url_seconds=30)
        token, _submission = complete_submission(client, tmp_path)
        clock.now = datetime.now(UTC)
        first = client.get(
            f"/api/submissions/{token}/moments/0/clip", follow_redirects=False
        )
        assert first.status_code == 302
        location = first.headers["location"]
        clock.advance(seconds=31)
        with pytest.raises(HTTPError) as raised:
            _r2_get(location)
        assert raised.value.code == 403
        # Realign before issuing a fresh URL so the new TTL starts now.
        clock.now = datetime.now(UTC)
        refreshed = client.get(
            f"/api/submissions/{token}/moments/0/clip?refresh=1",
            follow_redirects=False,
        )
        assert refreshed.status_code == 302
        status, _headers, body = _r2_get(refreshed.headers["location"])
        assert status == 200
        assert body


def test_invalid_guest_token_is_404(tmp_path: Path, db_url: str) -> None:
    with fake_s3() as fake:
        client = _published_client(tmp_path, fake, db_url)
        complete_submission(client, tmp_path)
        response = client.get(
            "/api/submissions/not-a-real-token/moments/0/clip",
            follow_redirects=False,
        )
        assert response.status_code == 404


def test_dev_media_grant_supports_range(tmp_path: Path) -> None:
    root = tmp_path / "public"
    root.mkdir()
    local = LocalStorage(root, media_url_seconds=60)
    body = b"abcdefghijklmnopqrstuvwxyz0123456789"
    path = _file(tmp_path, "still.jpg", body)
    digest = file_sha256(path)
    key = "reports/dev/run/attempt-1/public_frames/x.jpg"
    local.put_file(key, path, "image/jpeg", digest, len(body))
    download = local.presign_get(key, response_content_type="image/jpeg")
    grant_id = download.url.rsplit("/", 1)[-1]

    client = report_client(tmp_path, None, storage="local", dev_mode=True)
    client.app.state.service.storage = local
    response = client.get(
        f"/api/dev-media/{grant_id}",
        headers={"Range": "bytes=0-9"},
    )
    assert response.status_code == 206
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-range"].startswith("bytes 0-9/")
    assert response.content == body[:10]
