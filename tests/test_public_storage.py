"""Object storage: key rules, R2 operations against a fake S3, local parity."""

from __future__ import annotations

import hashlib
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fake_s3_helpers import FakeObject, FakeS3, fake_s3
from public_site import storage as storage_module
from public_site.app import open_storage
from public_site.settings import PublicSettings
from public_site.storage import (
    OBJECT_PREFIXES,
    HashingReader,
    LocalStorage,
    R2Storage,
    ReportEraser,
    ReportLister,
    ReportWriter,
    valid_key,
    valid_prefix,
)

ID = "a" * 32


def _r2(state: FakeS3, *, secret: str | None = None) -> R2Storage:
    return R2Storage(
        account_id="account",
        access_key_id=state.access_key,
        secret_access_key=secret or state.secret,
        bucket=state.bucket,
        endpoint=state.endpoint,
    )


def _file(tmp_path: Path, name: str, body: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(body)
    return path


@pytest.mark.parametrize(
    "key",
    [
        f"submissions/{ID}/source.mp4",
        f"processing/{ID}/attempt-1/recording.mp4",
        f"reports/{ID}/run_1/bundle.json",
        f"exports/{ID}/export.zip",
    ],
)
def test_valid_keys_cover_the_four_prefixes(key: str) -> None:
    assert valid_key(key)


@pytest.mark.parametrize(
    "key",
    [
        "other/x.mp4",
        "submissions/",
        "submissions",
        f"/submissions/{ID}/source.mp4",
        f"submissions/{ID}/../x.mp4",
        f"submissions/{ID}//x.mp4",
        f"submissions/{ID}/.hidden",
        f"submissions/{ID}/a b.mp4",
        f"submissions/{ID}/x\\y.mp4",
        f"reports/{ID}/caf\u00e9.json",
    ],
)
def test_invalid_keys_are_rejected(key: str, tmp_path: Path) -> None:
    assert not valid_key(key)
    with pytest.raises(ValueError):
        R2Storage(
            account_id="a", access_key_id="k", secret_access_key="s", bucket="b"
        ).presign_put(key, "video/mp4", 1)
    assert LocalStorage(tmp_path).head(key) is None


def test_prefixes() -> None:
    assert all(valid_prefix(prefix) for prefix in OBJECT_PREFIXES)
    assert valid_prefix(f"reports/{ID}/")
    assert not valid_prefix("")
    assert not valid_prefix("reports")
    assert not valid_prefix(f"reports/{ID}")
    assert not valid_prefix("other/")


def test_r2_put_head_download_round_trip(tmp_path: Path) -> None:
    body = bytes(range(256)) * 12_289
    source = _file(tmp_path, "clip.mp4", body)
    digest = hashlib.sha256(body).hexdigest()
    key = f"reports/{ID}/run/clips/m1.mp4"
    with fake_s3() as state:
        r2 = _r2(state)
        stored = r2.put_file(key, source, "video/mp4")
        assert (stored.size_bytes, stored.sha256) == (len(body), digest)
        assert state.objects[key].metadata["x-amz-meta-sha256"] == digest
        head = r2.head(key)
        assert (head.size_bytes, head.sha256) == (len(body), digest)
        dest = tmp_path / "out" / "clip.mp4"
        path, temporary = r2.local_file(key, dest)
        assert (path, temporary) == (dest, True)
        assert dest.read_bytes() == body
        assert [p.name for p in dest.parent.iterdir()] == ["clip.mp4"]


def test_r2_download_streams_in_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = b"x" * (3 * 1024 * 1024 + 7)
    sizes: list[int] = []
    original = storage_module._copy_stream

    def spy(response, out, *, deadline=None):
        real_read = response.read

        def read(n=-1):
            sizes.append(n)
            return real_read(n)

        response.read = read  # type: ignore[method-assign]
        return original(response, out, deadline=deadline)

    monkeypatch.setattr(storage_module, "_copy_stream", spy)
    with fake_s3() as state:
        state.objects[f"submissions/{ID}/source.mp4"] = FakeObject(body)
        written = _r2(state).download(f"submissions/{ID}/source.mp4", tmp_path / "v.mp4")
    assert written == len(body)
    # Four data reads (3 MiB + 7) plus the terminal empty read, each sized to _CHUNK.
    assert sizes == [1024 * 1024] * 5


def test_r2_download_failures_leave_no_file(tmp_path: Path) -> None:
    key = f"submissions/{ID}/source.mp4"
    dest = tmp_path / "v.mp4"
    with fake_s3() as state:
        r2 = _r2(state)
        with pytest.raises(FileNotFoundError):
            r2.local_file(key, dest)
        state.objects[key] = FakeObject(b"y" * 4096)
        state.fail_next.append(500)
        with pytest.raises(OSError, match=r"^storage request failed \(500\)$"):
            r2.download(key, dest)
        state.truncate_next_get = True
        with pytest.raises(OSError, match="storage get failed"):
            r2.download(key, dest)
    assert list(tmp_path.iterdir()) == []


def test_r2_put_rejects_a_body_that_does_not_match_its_digest(tmp_path: Path) -> None:
    source = _file(tmp_path, "a.json", b"{}")
    with fake_s3() as state:
        with pytest.raises(OSError, match=r"storage put failed \(400\)"):
            _r2(state).put_file(
                f"reports/{ID}/r/a.json", source, "application/json", "0" * 64
            )
        assert state.objects == {}


def test_r2_wrong_secret_is_refused_by_signature_check(tmp_path: Path) -> None:
    with fake_s3() as state:
        with pytest.raises(OSError, match=r"storage head failed \(403\)"):
            _r2(state, secret="wrong").head(f"submissions/{ID}/source.mp4")


def test_r2_list_pages_and_skips_folder_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage_module, "_LIST_PAGE", 2)
    with fake_s3() as state:
        state.objects["reports/"] = FakeObject(b"")
        for index in range(5):
            state.objects[f"reports/{ID}/run/f{index}.json"] = FakeObject(b"x" * index)
        state.objects[f"reports/{'b' * 32}/run/other.json"] = FakeObject(b"z")
        state.objects[f"submissions/{ID}/source.mp4"] = FakeObject(b"v")
        r2 = _r2(state)
        mine = r2.list_objects(f"reports/{ID}/")
        everything = r2.list_objects("reports/")
        with pytest.raises(ValueError):
            r2.list_objects("")
        lists = [path for method, path in state.requests if path == f"/{state.bucket}"]
    assert [(item.key, item.size_bytes) for item in mine] == [
        (f"reports/{ID}/run/f{index}.json", index) for index in range(5)
    ]
    assert "reports/" not in [item.key for item in everything]
    assert len(everything) == 6
    assert len(lists) == 3 + 4


def test_r2_copy_keeps_recorded_digest(tmp_path: Path) -> None:
    source = _file(tmp_path, "rec.mp4", b"recording")
    with fake_s3() as state:
        r2 = _r2(state)
        r2.put_file(f"processing/{ID}/a1/recording.mp4", source, "video/mp4")
        copied = r2.copy(
            f"processing/{ID}/a1/recording.mp4", f"submissions/{ID}/source.mp4"
        )
        assert copied.sha256 == hashlib.sha256(b"recording").hexdigest()
        assert state.objects[f"submissions/{ID}/source.mp4"].body == b"recording"
        with pytest.raises(FileNotFoundError):
            r2.copy(f"processing/{ID}/a1/missing.mp4", f"submissions/{ID}/other.mp4")


def test_r2_delete_and_head_of_missing_objects(tmp_path: Path) -> None:
    with fake_s3() as state:
        r2 = _r2(state)
        state.objects[f"submissions/{ID}/source.mp4"] = FakeObject(b"v")
        r2.delete(f"submissions/{ID}/source.mp4")
        r2.delete(f"submissions/{ID}/source.mp4")
        assert r2.head(f"submissions/{ID}/source.mp4") is None
        state.fail_next.append(503)
        with pytest.raises(OSError, match=r"storage delete failed \(503\)"):
            r2.delete(f"submissions/{ID}/source.mp4")


def test_r2_network_failure_is_an_oserror_without_detail(tmp_path: Path) -> None:
    r2 = R2Storage(
        account_id="a",
        access_key_id="k",
        secret_access_key="s",
        bucket="b",
        endpoint="http://127.0.0.1:9",
    )
    with pytest.raises(OSError, match=r"^storage head failed \(network: \w+\)$"):
        r2.head(f"submissions/{ID}/source.mp4")


def test_presigned_put_is_accepted_by_the_store(tmp_path: Path) -> None:
    key = f"submissions/{ID}/source.mp4"
    with fake_s3() as state:
        upload = _r2(state).presign_put(key, "video/mp4", 8)
        request = urllib.request.Request(
            upload.url, data=b"12345678", method="PUT", headers=upload.headers
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 200
        assert _r2(state).head(key).size_bytes == 8
        tampered = upload.url.replace(key, f"submissions/{'b' * 32}/source.mp4")
        request = urllib.request.Request(
            tampered, data=b"12345678", method="PUT", headers=upload.headers
        )
        with pytest.raises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(request, timeout=10)
        assert rejected.value.code == 403


def _put_status(url: str, headers: dict[str, str], body: bytes) -> int:
    request = urllib.request.Request(url, data=body, method="PUT", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_attempt_put_is_create_only_stamped_and_repeatable(tmp_path: Path) -> None:
    key = f"submissions/{ID}/source.mov"
    issued = datetime(2026, 11, 11, 4, 0, tzinfo=UTC)
    attempt = "0123456789abcdef0123456789abcdef"
    with fake_s3() as state:
        first = _r2(state).presign_put(
            key, "video/quicktime", 8, attempt=attempt, issued_at=issued
        )
        again = _r2(state).presign_put(
            key, "video/quicktime", 8, attempt=attempt, issued_at=issued
        )
        assert first == again
        assert first.headers["If-None-Match"] == "*"
        assert first.headers["x-amz-meta-attempt"] == attempt
        unsigned = {k: v for k, v in first.headers.items() if k != "x-amz-meta-attempt"}
        assert _put_status(first.url, unsigned, b"12345678") == 403
        assert key not in state.objects
        assert _put_status(first.url, first.headers, b"12345678") == 200
        assert _r2(state).head(key).attempt == attempt
        assert _put_status(first.url, first.headers, b"87654321") == 412
        assert state.objects[key].body == b"12345678"


def test_local_attempt_grant_is_create_only_and_stamped(tmp_path: Path) -> None:
    local = LocalStorage(tmp_path / "root")
    key = f"submissions/{ID}/source.mp4"
    attempt = "0123456789abcdef0123456789abcdef"
    upload = local.presign_put(key, "video/mp4", 8, attempt=attempt)
    assert upload.url.endswith(f"/{attempt}")
    assert local.write_grant(attempt, b"12345678") == key
    assert local.head(key).attempt == attempt
    with pytest.raises(storage_module.ObjectExists):
        local.write_grant(attempt, b"87654321")
    assert local.read_sha256(key)[1] == hashlib.sha256(b"12345678").hexdigest()
    local.delete(key)
    local.put_file(key, _file(tmp_path, "station.mp4", b"abcdefgh"), "video/mp4")
    assert local.head(key).attempt is None


def test_local_storage_matches_the_r2_operations(tmp_path: Path) -> None:
    local = LocalStorage(tmp_path / "root")
    source = _file(tmp_path, "bundle.json", b'{"a": 1}')
    digest = hashlib.sha256(b'{"a": 1}').hexdigest()
    stored = local.put_file(f"reports/{ID}/run/bundle.json", source, "application/json")
    assert (stored.size_bytes, stored.sha256) == (8, digest)
    assert local.head(f"reports/{ID}/run/bundle.json").sha256 == digest
    copied = local.copy(f"reports/{ID}/run/bundle.json", f"exports/{ID}/bundle.json")
    assert copied.sha256 == digest
    keys = [item.key for item in local.list_objects("reports/")]
    assert keys == [f"reports/{ID}/run/bundle.json"]
    with pytest.raises(OSError, match="checksum"):
        local.put_file(f"reports/{ID}/run/x.json", source, "application/json", "0" * 64)
    local.delete(f"reports/{ID}/run/bundle.json")
    assert local.head(f"reports/{ID}/run/bundle.json") is None
    assert [item.key for item in local.list_objects("reports/")] == []
    with pytest.raises(FileNotFoundError):
        local.copy(f"reports/{ID}/run/bundle.json", f"exports/{ID}/b.json")


def test_hashing_reader_accounts_for_exactly_the_bytes_read(tmp_path: Path) -> None:
    source = _file(tmp_path, "a.bin", b"abcdef")
    with source.open("rb") as handle:
        reader = HashingReader(handle)
        assert reader.read(4) == b"abcd"
        assert (reader.size_bytes, reader.hexdigest()) == (
            4, hashlib.sha256(b"abcd").hexdigest()
        )
        assert reader.read() == b"ef"
    assert reader.hexdigest() == hashlib.sha256(b"abcdef").hexdigest()


def test_r2_put_fails_checksum_when_the_sent_bytes_differ(tmp_path: Path) -> None:
    source = _file(tmp_path, "a.json", b"{}")
    key = f"reports/{ID}/r/a.json"
    with fake_s3() as state:
        state.verify_payload = False
        with pytest.raises(OSError, match=r"^storage put failed \(checksum\)$"):
            _r2(state).put_file(key, source, "application/json", "0" * 64, 2)
        digest = hashlib.sha256(b"{}").hexdigest()
        stored = _r2(state).put_file(key, source, "application/json", digest, 2)
        assert (stored.size_bytes, stored.sha256) == (2, digest)


def test_read_sha256_streams_the_stored_bytes(tmp_path: Path) -> None:
    body = b"z" * (2 * 1024 * 1024 + 3)
    key = f"reports/{ID}/r/clip.mp4"
    expected = (len(body), hashlib.sha256(body).hexdigest())
    local = LocalStorage(tmp_path / "root")
    local.put_file(key, _file(tmp_path, "clip.mp4", body), "video/mp4")
    assert local.read_sha256(key) == expected
    with pytest.raises(FileNotFoundError):
        local.read_sha256(f"reports/{ID}/r/missing.mp4")
    with fake_s3() as state:
        state.objects[key] = FakeObject(body)
        r2 = _r2(state)
        assert r2.read_sha256(key) == expected
        with pytest.raises(FileNotFoundError):
            r2.read_sha256(f"reports/{ID}/r/missing.mp4")
        state.truncate_next_get = True
        with pytest.raises(OSError, match="storage get failed"):
            r2.read_sha256(key)
    assert not (tmp_path / "root" / "tmp").exists()


def test_listings_carry_last_modified(tmp_path: Path) -> None:
    key = f"reports/{ID}/r/a.json"
    moment = datetime(2026, 11, 1, 12, 30, tzinfo=UTC)
    with fake_s3() as state:
        state.objects[key] = FakeObject(b"{}", {}, moment)
        (item,) = _r2(state).list_objects("reports/")
    assert item.last_modified == moment
    local = LocalStorage(tmp_path / "root")
    local.put_file(key, _file(tmp_path, "a.json", b"{}"), "application/json")
    (local_item,) = local.list_objects("reports/")
    assert local_item.last_modified is not None
    assert abs((local_item.last_modified - datetime.now(UTC)).total_seconds()) < 60


def test_report_views_expose_only_their_capability(tmp_path: Path) -> None:
    local = LocalStorage(tmp_path / "root")
    source = _file(tmp_path, "a.json", b"{}")
    digest = hashlib.sha256(b"{}").hexdigest()
    writer, lister, eraser = ReportWriter(local), ReportLister(local), ReportEraser(local)
    assert not hasattr(writer, "delete") and not hasattr(writer, "copy")
    assert not hasattr(lister, "delete") and not hasattr(lister, "read_sha256")
    assert not hasattr(lister, "put_file") and not hasattr(eraser, "put_file")
    assert not hasattr(eraser, "read_sha256") and not hasattr(eraser, "copy")
    writer.put_file(f"reports/{ID}/r/a.json", source, "application/json", digest, 2)
    for view in (writer, lister, eraser):
        assert [o.key for o in view.list_objects(f"reports/{ID}/")] == [
            f"reports/{ID}/r/a.json"
        ]
        with pytest.raises(ValueError):
            view.list_objects(f"submissions/{ID}/")
    local.put_file(f"submissions/{ID}/source.mp4", source, "video/mp4")
    with pytest.raises(ValueError):
        writer.put_file(f"submissions/{ID}/x.json", source, "application/json", digest, 2)
    with pytest.raises(ValueError):
        writer.read_sha256(f"submissions/{ID}/source.mp4")
    with pytest.raises(ValueError):
        eraser.delete(f"submissions/{ID}/source.mp4")
    with pytest.raises(ValueError):
        eraser.delete(f"reports/{ID}/../submissions/{ID}/source.mp4")
    assert local.head(f"submissions/{ID}/source.mp4") is not None
    eraser.delete(f"reports/{ID}/r/a.json")
    assert lister.list_objects("reports/") == []
    assert writer.bucket == lister.bucket == eraser.bucket == "local"


@pytest.mark.parametrize(
    ("storage", "credentials", "problem"),
    [
        ("local", False, None),
        ("r2", True, None),
        ("r2", False, "R2 credentials are required when PUBLIC_STORAGE=r2"),
        ("R2", True, "PUBLIC_STORAGE must be local or r2"),
        ("s3", True, "PUBLIC_STORAGE must be local or r2"),
    ],
)
def test_storage_settings_are_validated_before_start(
    tmp_path: Path, storage: str, credentials: bool, problem: str | None
) -> None:
    secret = "SECRET-VALUE-0123456789"
    extra = (
        {
            "r2_account_id": "account",
            "r2_access_key_id": "key",
            "r2_secret_access_key": secret,
            "r2_bucket": "bucket",
        }
        if credentials
        else {}
    )
    settings = PublicSettings(
        root=tmp_path,
        config_path=tmp_path / "c.yaml",
        static_dir=tmp_path,
        storage=storage,
        **extra,
    )
    assert settings.storage_problem() == problem
    if problem is None:
        open_storage(settings)
        return
    with pytest.raises(ValueError, match=problem) as raised:
        open_storage(settings)
    assert secret not in str(raised.value)


def test_storage_env_value_is_trimmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PUBLIC_STORAGE", " ")
    assert PublicSettings.from_env().storage == "local"
    monkeypatch.setenv("PUBLIC_STORAGE", "r2 ")
    assert PublicSettings.from_env().storage == "r2"


def test_dev_mode_rejects_the_production_r2_bucket(tmp_path: Path) -> None:
    from public_site.settings import PROD_R2_BUCKET

    settings = PublicSettings(
        root=tmp_path,
        config_path=tmp_path / "c.yaml",
        static_dir=tmp_path,
        storage="r2",
        dev_mode=True,
        r2_account_id="account",
        r2_access_key_id="key",
        r2_secret_access_key="secret",
        r2_bucket=PROD_R2_BUCKET,
    )
    assert settings.storage_problem() == (
        "PUBLIC_R2_BUCKET must not be the production bucket in PUBLIC_DEV_MODE"
    )


def test_production_problem_requires_r2_prod_bucket_and_report_flags(
    tmp_path: Path,
) -> None:
    from public_site.settings import PROD_R2_BUCKET

    base = dict(
        root=tmp_path,
        config_path=tmp_path / "c.yaml",
        static_dir=tmp_path,
        storage="r2",
        dev_mode=False,
        database_url="postgresql://coach@127.0.0.1:5432/coach",
        r2_account_id="account",
        r2_access_key_id="key",
        r2_secret_access_key="secret",
        r2_bucket=PROD_R2_BUCKET,
        report_publish=True,
        report_expiry_delete=True,
        report_sweep=False,
    )
    assert PublicSettings(**base).production_problem() is None
    assert (
        PublicSettings(**{**base, "storage": "local"}).production_problem()
        == "PUBLIC_STORAGE must be r2 when PUBLIC_DEV_MODE is off"
    )
    assert (
        PublicSettings(**{**base, "r2_bucket": "s3-coaching-dev"}).production_problem()
        == f"PUBLIC_R2_BUCKET must be {PROD_R2_BUCKET} when PUBLIC_DEV_MODE is off"
    )
    assert (
        PublicSettings(**{**base, "database_url": ""}).production_problem()
        == "PUBLIC_DATABASE_URL is required when PUBLIC_DEV_MODE is off"
    )
    assert (
        PublicSettings(**{**base, "report_publish": False}).production_problem()
        == "PUBLIC_REPORT_PUBLISH must be 1 when PUBLIC_DEV_MODE is off"
    )
    assert PublicSettings(**{**base, "dev_mode": True}).production_problem() is None


def test_media_url_seconds_defaults_and_validates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PUBLIC_MEDIA_URL_SECONDS", raising=False)
    assert PublicSettings.from_env().media_url_seconds == 3600
    monkeypatch.setenv("PUBLIC_MEDIA_URL_SECONDS", "7200")
    assert PublicSettings.from_env().media_url_seconds == 7200
    monkeypatch.setenv("PUBLIC_MEDIA_URL_SECONDS", "59")
    bad = PublicSettings.from_env()
    assert bad.media_url_seconds is None
    assert bad.media_url_problem() is not None
    assert "PUBLIC_MEDIA_URL_SECONDS" in (bad.media_url_problem() or "")


def test_presigned_get_supports_range_and_content_type(tmp_path: Path) -> None:
    key = f"reports/{ID}/run/public_clips/m1.mp4"
    body = b"0123456789abcdef"
    source = _file(tmp_path, "clip.mp4", body)
    with fake_s3() as state:
        r2 = _r2(state)
        r2.put_file(key, source, "video/mp4")
        download = r2.presign_get(
            key, expires_seconds=600, response_content_type="video/mp4"
        )
        assert download.method == "GET"
        assert "response-content-type=video%2Fmp4" in download.url
        assert "X-Amz-SignedHeaders=host" in download.url
        request = urllib.request.Request(
            download.url, method="GET", headers={"Range": "bytes=0-7"}
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.status == 206
            assert response.headers.get("Content-Range") == "bytes 0-7/16"
            assert response.headers.get("Content-Type") == "video/mp4"
            assert response.read() == body[:8]


def test_local_presign_get_issues_a_dev_media_grant(tmp_path: Path) -> None:
    local = LocalStorage(tmp_path, media_url_seconds=120)
    key = f"reports/{ID}/run/public_frames/m1.jpg"
    local.put_file(key, _file(tmp_path, "still.jpg", b"jpeg-bytes"), "image/jpeg")
    download = local.presign_get(key, response_content_type="image/jpeg")
    assert download.url.startswith("/api/dev-media/")
    grant_id = download.url.rsplit("/", 1)[-1]
    grant = local.media_grant(grant_id)
    assert grant is not None
    assert grant["object_key"] == key
    assert grant["content_type"] == "image/jpeg"


def test_r2_request_deadline_aborts_a_stalled_put(tmp_path: Path) -> None:
    key = f"reports/{ID}/run/public_clips/slow.mp4"
    source = _file(tmp_path, "slow.mp4", b"x" * 64)
    with fake_s3() as state:
        state.put_delay_seconds = 2.0
        r2 = R2Storage(
            account_id="account",
            access_key_id=state.access_key,
            secret_access_key=state.secret,
            bucket=state.bucket,
            endpoint=state.endpoint,
            request_timeout_seconds=0.2,
            socket_timeout_seconds=0.2,
        )
        with pytest.raises(OSError, match=r"storage put failed \(timeout\)"):
            r2.put_file(key, source, "video/mp4")
