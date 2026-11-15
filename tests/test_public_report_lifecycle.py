"""Publication cleanup races: source DELETE vs outstanding presigned PUT."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fake_s3_helpers import fake_s3
from public_site.report_lifecycle import (
    UPLOAD_SETTLE_GRACE_SECONDS,
    cleanup_source_object,
    upload_window_open,
)
from public_site.storage import R2Storage
from tests.test_public_storage import _file, _r2


def test_upload_window_stays_open_through_grace() -> None:
    now = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    expires = now - timedelta(seconds=30)
    assert upload_window_open(
        now=now,
        source_upload_expires_at=expires,
        attempt_lease_until=None,
        grace_seconds=UPLOAD_SETTLE_GRACE_SECONDS,
    )
    assert not upload_window_open(
        now=now + timedelta(seconds=UPLOAD_SETTLE_GRACE_SECONDS),
        source_upload_expires_at=expires,
        attempt_lease_until=None,
        grace_seconds=UPLOAD_SETTLE_GRACE_SECONDS,
    )


def test_source_cleanup_pending_settle_while_put_url_live(tmp_path: Path) -> None:
    key = "submissions/abc/source.mp4"
    now = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    with fake_s3() as state:
        r2 = _r2(state)
        r2.put_file(key, _file(tmp_path, "source.mp4", b"video-bytes"), "video/mp4")
        result = cleanup_source_object(
            r2,
            key,
            now=now,
            source_upload_expires_at=now + timedelta(minutes=5),
        )
    assert result.state == "pending_settle"
    assert result.object_gone
    assert state.objects.get(key) is None


def test_outstanding_presigned_put_can_recreate_after_delete(tmp_path: Path) -> None:
    key = "submissions/abc/source.mp4"
    body = b"original-source"
    now = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
    clock = {"t": now}

    def advance(seconds: int) -> datetime:
        clock["t"] = clock["t"] + timedelta(seconds=seconds)
        return clock["t"]

    with fake_s3() as state:
        state.check_presign_expiry = True
        state.clock = lambda: clock["t"]
        r2 = R2Storage(
            account_id="account",
            access_key_id=state.access_key,
            secret_access_key=state.secret,
            bucket=state.bucket,
            endpoint=state.endpoint,
            url_seconds=900,
        )
        r2.put_file(key, _file(tmp_path, "source.mp4", body), "video/mp4")
        upload = r2.presign_put(key, "video/mp4", len(body), issued_at=now)
        first = cleanup_source_object(
            r2,
            key,
            now=clock["t"],
            source_upload_expires_at=now + timedelta(seconds=900),
        )
        assert first.state == "pending_settle"
        assert r2.head(key) is None

        # Outstanding PUT still authorized: recreates the object (replay).
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            upload.url,
            data=body,
            method="PUT",
            headers=upload.headers,
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
        assert r2.head(key) is not None

        # Still inside the PUT window → settle, do not declare completed.
        mid = cleanup_source_object(
            r2,
            key,
            now=advance(10),
            source_upload_expires_at=now + timedelta(seconds=900),
        )
        assert mid.state == "pending_settle"

        # After expiry + grace, DELETE sticks and cleanup completes.
        done = cleanup_source_object(
            r2,
            key,
            now=advance(900 + UPLOAD_SETTLE_GRACE_SECONDS),
            source_upload_expires_at=now + timedelta(seconds=900),
        )
        assert done.state == "completed"
        assert done.object_gone
        assert r2.head(key) is None

        # Expired PUT must not revive the object.
        with pytest.raises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=10)
        assert raised.value.code == 403
        assert r2.head(key) is None
