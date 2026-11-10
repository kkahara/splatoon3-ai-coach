"""Object storage: key rules, R2 operations against a fake S3, local parity."""

from __future__ import annotations

import hashlib
import urllib.request
from pathlib import Path

import pytest
from fake_s3_helpers import FakeObject, FakeS3, fake_s3
from public_site import storage as storage_module
from public_site.app import open_storage
from public_site.settings import PublicSettings
from public_site.storage import (
    OBJECT_PREFIXES,
    LocalStorage,
    R2Storage,
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
    original = storage_module.shutil.copyfileobj

    def spy(source, dest, length=0):
        sizes.append(length)
        return original(source, dest, length)

    monkeypatch.setattr(storage_module.shutil, "copyfileobj", spy)
    with fake_s3() as state:
        state.objects[f"submissions/{ID}/source.mp4"] = FakeObject(body)
        written = _r2(state).download(f"submissions/{ID}/source.mp4", tmp_path / "v.mp4")
    assert written == len(body)
    assert sizes == [1024 * 1024]


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
