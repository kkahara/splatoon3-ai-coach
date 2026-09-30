"""Local abuse and failure checks for the public submission site."""

from __future__ import annotations

import json
import os
import smtplib
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from public_site.app import create_app
from public_site.errors import (
    ACCOUNT_RATE_LIMIT,
    BAD_SIZE,
    CAPACITY,
    EXPIRED,
    INTERRUPTED,
    RATE_LIMIT,
    VERIFY,
    RequestRejected,
)
from public_site.media_probe import ProbeFacts
from public_site.s3sign import presign
from public_site.settings import PublicSettings
from public_site.storage import R2Storage

from splatoon3_ai_coach.config.paths import PROJECT_ROOT, default_config_path

_MIB = 1024 * 1024
_CAP = 500 * _MIB


def _settings(tmp_path: Path, **overrides) -> PublicSettings:
    root = tmp_path / "public"
    root.mkdir(parents=True, exist_ok=True)
    values = {
        "root": root,
        "config_path": default_config_path(),
        "static_dir": tmp_path / "no-dist",
        "dev_mode": True,
        "worker_id": "abuse-worker",
        "public_base_url": "http://coach.example",
        "max_video_bytes": _CAP,
    }
    values.update(overrides)
    return PublicSettings(**values)


def _probe_ok(_path: Path) -> ProbeFacts:
    return ProbeFacts(12.0, 1920, 1080, "mp4")


def _client(tmp_path: Path, **kwargs) -> TestClient:
    probe = kwargs.pop("probe", _probe_ok)
    run_command = kwargs.pop("run_command", lambda _argv: 0)
    settings = _settings(tmp_path, **kwargs)
    app = create_app(
        settings,
        probe=probe,
        run_command=run_command,
        start_worker=False,
    )
    client = TestClient(app)
    client.app = app
    return client


def _body(**extra) -> dict:
    payload = {
        "turnstile_token": "dev",
        "filename": "match.mp4",
        "size_bytes": 8,
        "content_type": "video/mp4",
        "language": "en",
    }
    payload.update(extra)
    return payload


def _create(client: TestClient, **extra) -> dict:
    response = client.post("/api/submissions", json=_body(**extra))
    assert response.status_code == 200, response.text
    return response.json()


def _upload(client: TestClient, created: dict) -> str:
    upload = created["upload"]
    response = client.put(upload["url"], content=b"12345678", headers=upload["headers"])
    assert response.status_code == 200, response.text
    confirmed = client.post(f"/api/submissions/{created['token']}/uploaded")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "queued"
    return created["token"]


def _submission_files(client: TestClient) -> list[Path]:
    root = client.app.state.settings.root / "submissions"
    return sorted(root.glob("*.json")) if root.is_dir() else []


def test_rapid_valid_videos_hit_the_hourly_limit(tmp_path: Path) -> None:
    client = _client(tmp_path, submissions_per_hour=2)
    first = _upload(client, _create(client))
    second = _upload(client, _create(client))
    assert first != second
    blocked = client.post("/api/submissions", json=_body(filename="third.mp4"))
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == RATE_LIMIT
    assert len(_submission_files(client)) == 2
    rate = (client.app.state.settings.root / "rate_limits.json").read_text()
    assert "127.0.0.1" not in rate
    assert "testclient" not in rate


def _service_create(client: TestClient, ip: str, user_id: str | None) -> None:
    client.app.state.service.create(
        turnstile_token="dev",
        filename="match.mp4",
        size_bytes=8,
        content_type="video/mp4",
        language="en",
        email=None,
        notify=False,
        display_name=None,
        ip=ip,
        verifier=lambda _token: True,
        user_id=user_id,
    )


def _rejected_detail(client: TestClient, ip: str, user_id: str | None) -> str:
    with pytest.raises(RequestRejected) as excinfo:
        _service_create(client, ip, user_id)
    assert excinfo.value.status_code == 429
    return excinfo.value.detail


def test_accounts_and_guests_share_the_network_cap(tmp_path: Path) -> None:
    client = _client(tmp_path)
    ip = "203.0.113.7"
    for _ in range(2):
        _service_create(client, ip, "account-a")
    assert _rejected_detail(client, ip, "account-a") == ACCOUNT_RATE_LIMIT
    for _ in range(2):
        _service_create(client, ip, "account-b")
    for _ in range(2):
        _service_create(client, ip, None)
    assert _rejected_detail(client, ip, None) == RATE_LIMIT
    assert _rejected_detail(client, ip, "account-c") == RATE_LIMIT
    _service_create(client, "198.51.100.9", "account-c")
    assert len(_submission_files(client)) == 7
    rate = (client.app.state.settings.root / "rate_limits.json").read_text()
    assert ip not in rate
    assert "account-a" not in rate


def test_guest_limit_is_two_per_network(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _upload(client, _create(client))
    _upload(client, _create(client))
    blocked = client.post("/api/submissions", json=_body(filename="third.mp4"))
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == RATE_LIMIT


def test_production_turnstile_rejects_a_bad_token(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        dev_mode=False,
        turnstile_secret="abuse-test-secret-not-a-widget",
    )
    app = create_app(
        settings,
        probe=_probe_ok,
        run_command=lambda _argv: 0,
        start_worker=False,
    )
    client = TestClient(app)
    rejected = client.post(
        "/api/submissions",
        json=_body(turnstile_token="not-a-widget-token"),
    )
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == VERIFY
    dev = client.post("/api/submissions", json=_body(turnstile_token="dev"))
    assert dev.status_code == 400
    assert dev.json()["detail"] == VERIFY
    root = settings.root / "submissions"
    assert not root.is_dir() or list(root.glob("*.json")) == []


def test_presigned_upload_cannot_be_retargeted(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = _create(client, filename="../../outside.mp4")
    upload = created["upload"]
    grant = upload["url"].rsplit("/", 1)[-1]
    missing = client.put("/api/dev-upload/another-grant-id-value", content=b"12345678")
    assert missing.status_code == 404
    query = client.put(
        f"{upload['url']}?object_key=submissions/evil/source.mp4",
        content=b"12345678",
        headers=upload["headers"],
    )
    assert query.status_code == 200
    root = client.app.state.settings.root
    stored = [path for path in (root / "objects").rglob("*") if path.is_file()]
    assert len(stored) == 1
    assert stored[0].name == "source.mp4"
    assert "evil" not in str(stored[0])
    assert "outside" not in str(stored[0])
    video = client.app.state.store.list_submissions()
    record = client.app.state.store.get_video(video[0].id)
    assert record.object_key == f"submissions/{video[0].id}/source.mp4"
    assert record.original_filename == "outside.mp4"
    assert grant not in record.object_key

    original = "submissions/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/source.mp4"
    issued = R2Storage(
        account_id="account",
        access_key_id="key",
        secret_access_key="secret",
        bucket="bucket",
    ).presign_put(original, "video/mp4", 8)
    swapped_key = "submissions/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/source.mp4"
    signed_at = datetime.strptime(
        parse_qs(urlparse(issued.url).query)["X-Amz-Date"][0],
        "%Y%m%dT%H%M%SZ",
    ).replace(tzinfo=UTC)
    headers = {"content-type": "video/mp4", "content-length": "8"}
    original_sig = _signature(original, signed_at, headers)
    swapped_sig = _signature(swapped_key, signed_at, headers)
    assert issued.url.split("?", 1)[0].endswith(original)
    assert original_sig.split("X-Amz-Signature=", 1)[1] in issued.url
    assert swapped_sig.split("X-Amz-Signature=", 1)[1] not in issued.url


def test_changed_token_does_not_open_another_submission(tmp_path: Path) -> None:
    client = _client(tmp_path, submissions_per_hour=5)
    first = _create(client, display_name="Ada")
    second = _create(client, display_name="Bea")
    seen = client.get(f"/api/submissions/{first['token']}")
    assert seen.status_code == 200
    assert seen.json()["display_name"] == "Ada"
    assert "Bea" not in seen.text
    flipped = first["token"][:-1] + ("A" if first["token"][-1] != "A" else "B")
    assert client.get(f"/api/submissions/{flipped}").status_code == 404
    mixed = second["token"][:8] + first["token"][8:]
    assert client.get(f"/api/submissions/{mixed}").status_code == 404
    submission_id = client.app.state.store.list_submissions()[0].id
    assert client.get(f"/api/submissions/{submission_id}").status_code == 404
    other = client.get(f"/api/submissions/{second['token']}")
    assert other.json()["display_name"] == "Bea"
    assert first["token"] not in other.text


def test_twenty_unfinished_submissions_block_the_next_create(tmp_path: Path) -> None:
    client = _client(
        tmp_path, submissions_per_hour=30, guest_submissions_per_hour=30, max_queued=20
    )
    for index in range(20):
        _create(client, filename=f"match-{index}.mp4")
    before = _submission_files(client)
    assert len(before) == 20
    blocked = client.post("/api/submissions", json=_body(filename="match-extra.mp4"))
    assert blocked.status_code == 429
    assert blocked.json()["detail"] == CAPACITY
    assert _submission_files(client) == before
    assert "token" not in blocked.json()


def test_over_500_mib_is_rejected_before_analysis(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/api/submissions", json=_body(size_bytes=_CAP + 1))
    assert response.status_code == 400
    assert response.json()["detail"] == BAD_SIZE
    assert _submission_files(client) == []
    root = client.app.state.settings.root
    stored = [path for path in (root / "objects").rglob("*") if path.is_file()]
    assert stored == []
    assert not (root / "analysis").exists()
    accepted = client.post("/api/submissions", json=_body(size_bytes=_CAP))
    assert accepted.status_code == 200
    assert not (root / "analysis").exists()


def test_expiry_deletes_the_video_and_the_analysis(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = _create(client, notify=True, email="player@example.com", display_name="Rin")
    _upload(client, created)
    stored = client.app.state.store.list_submissions()[0]
    video = client.app.state.store.get_video(stored.id)
    analysis = client.app.state.settings.root / "analysis" / stored.id
    analysis.mkdir(parents=True)
    (analysis / "vision_manifest.json").write_text("{}", encoding="utf-8")
    object_path = client.app.state.settings.root / "objects" / video.object_key
    assert object_path.is_file()
    stored.analysis_dir = str(analysis)
    client.app.state.store.save_submission(stored)
    client.app.state.service.maintain(datetime.now(UTC) + timedelta(days=31))
    assert not object_path.exists()
    assert not analysis.exists()
    expired = client.app.state.store.get(stored.id)
    assert expired.status == "expired"
    assert expired.analysis_dir is None
    assert expired.email is None
    body = client.get(f"/api/submissions/{created['token']}").json()
    assert body["status"] == "expired"
    assert body["error"] == EXPIRED


def test_smtp_notice_has_only_the_private_link(
    tmp_path: Path,
    monkeypatch,
) -> None:
    sink = _SmtpSink()
    monkeypatch.setattr(smtplib.SMTP, "starttls", lambda self, *args, **kwargs: None)
    client = _client(
        tmp_path,
        smtp_host="127.0.0.1",
        smtp_port=sink.port,
        smtp_from="coach@example.com",
    )
    other = _create(client, display_name="Other")
    created = _create(
        client,
        notify=True,
        email="player@example.com",
        display_name="Rin",
    )
    _upload(client, created)
    message = sink.message
    stored = next(
        item
        for item in client.app.state.store.list_submissions()
        if item.display_name == "Rin"
    )
    video = client.app.state.store.get_video(stored.id)
    link = f"http://coach.example/review/{created['token']}"
    assert "View your review" in message
    assert link in message
    assert message.count("http://") == 1
    assert other["token"] not in message
    assert created["token"] not in message.split("Subject:", 1)[0]
    subject = message.split("Subject: ", 1)[1].splitlines()[0]
    assert created["token"] not in subject
    for hidden in (
        stored.id,
        stored.access_token_hash,
        video.object_key,
        stored.worker_id or "abuse-worker",
        "analysis",
        "vision_manifest",
        "coach_inputs",
        "object_key",
    ):
        assert hidden not in message
    assert list((client.app.state.settings.root / "outbox").glob("*.txt")) == []


def test_killed_worker_fails_validating_and_processing(tmp_path: Path) -> None:
    validating = _kill_once(tmp_path / "validating", "validating")
    processing = _kill_once(tmp_path / "processing", "processing")
    assert validating["status"] == "failed"
    assert validating["error"] == INTERRUPTED
    assert processing["status"] == "failed"
    assert processing["error"] == INTERRUPTED


def _kill_once(tmp_path: Path, mode: str) -> dict:
    root = tmp_path / "public"
    root.mkdir(parents=True)
    port = _free_port()
    script = tmp_path / "serve.py"
    script.write_text(_SERVER, encoding="utf-8")
    env = {**_child_env(), "PYTHONPATH": _pythonpath()}
    proc = _spawn(script, mode, root, port, env)
    try:
        _wait_until(lambda: _health(port), 10)
        token = _drive(port)
        if mode == "validating":
            _wait_until(lambda: _status_on_disk(root) == "validating", 10)
        else:
            _wait_until(lambda: _status_on_disk(root) == "processing", 10)
        proc.kill()
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    recover = _spawn(script, "recover", root, port, env)
    try:
        _wait_until(lambda: _health(port), 10)
        assert not (root / "recover-commands.log").exists()
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/submissions/{token}",
            timeout=5,
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
    finally:
        recover.kill()
        recover.wait(timeout=10)
    return body


def _drive(port: int) -> str:
    created = _json_request(
        port,
        "POST",
        "/api/submissions",
        _body(),
    )
    upload = created["upload"]
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{upload['url']}",
        data=b"12345678",
        method="PUT",
        headers=upload["headers"],
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200
    if created["token"]:
        threading.Thread(
            target=_confirm_quietly,
            args=(port, created["token"]),
            daemon=True,
        ).start()
    return created["token"]


def _confirm_quietly(port: int, token: str) -> None:
    try:
        _json_request(port, "POST", f"/api/submissions/{token}/uploaded", None)
    except (urllib.error.URLError, TimeoutError, OSError):
        return


def _json_request(port: int, method: str, path: str, payload: dict | None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data else {}
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def _spawn(script: Path, mode: str, root: Path, port: int, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(script), mode, str(root), str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _health(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=0.5):
            return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _status_on_disk(root: Path) -> str:
    files = list((root / "submissions").glob("*.json"))
    if not files:
        return ""
    return json.loads(files[0].read_text(encoding="utf-8"))["status"]


def _wait_until(ready, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if ready():
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _pythonpath() -> str:
    tools = str(PROJECT_ROOT / "tools")
    current = os.environ.get("PYTHONPATH", "")
    return tools if not current else tools + os.pathsep + current


def _child_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PUBLIC_TURNSTILE_SECRET", None)
    env["PUBLIC_DEV_MODE"] = "1"
    return env


class _SmtpSink(threading.Thread):
    """One-connection SMTP catcher. STARTTLS is skipped by the test."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.message = ""
        self._ready = threading.Event()
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self.port = int(self._sock.getsockname()[1])
        self._sock.listen(1)
        self.start()
        self._ready.wait(timeout=5)

    def run(self) -> None:
        self._ready.set()
        connection, _addr = self._sock.accept()
        with connection, connection.makefile("rwb") as stream:
            stream.write(b"220 local\r\n")
            stream.flush()
            collecting = False
            chunks: list[bytes] = []
            while True:
                line = stream.readline()
                if not line:
                    break
                if collecting:
                    if line == b".\r\n":
                        self.message = b"".join(chunks).decode("utf-8")
                        stream.write(b"250 ok\r\n")
                        stream.flush()
                        collecting = False
                    else:
                        chunks.append(line)
                    continue
                command = line.split()[0].upper() if line.strip() else b""
                if command == b"DATA":
                    stream.write(b"354 go\r\n")
                    collecting = True
                elif command == b"QUIT":
                    stream.write(b"221 bye\r\n")
                    stream.flush()
                    break
                else:
                    stream.write(b"250 local\r\n")
                stream.flush()


_SERVER = """
import sys
import time
from pathlib import Path

import uvicorn
from public_site.app import create_app
from public_site.media_probe import ProbeFacts
from public_site.settings import PublicSettings
from splatoon3_ai_coach.config.paths import default_config_path

mode, root_text, port_text = sys.argv[1:]
root = Path(root_text)

def probe(_path: Path) -> ProbeFacts:
    if mode == "validating":
        time.sleep(30)
    return ProbeFacts(12.0, 1920, 1080, "mp4")

def run(_argv: list[str]) -> int:
    if mode == "recover":
        (root / "recover-commands.log").write_text("called\\n", encoding="utf-8")
    else:
        time.sleep(30)
    return 0

settings = PublicSettings(
    root=root,
    config_path=default_config_path(),
    static_dir=root / "no-dist",
    dev_mode=True,
    worker_id="abuse-worker",
    public_base_url="http://coach.example",
    max_video_bytes=1000,
)
uvicorn.run(
    create_app(settings, probe=probe, run_command=run, start_worker=True),
    host="127.0.0.1",
    port=int(port_text),
    access_log=False,
    log_level="warning",
)
"""


def _signature(key: str, signed_at: datetime, headers: dict[str, str]) -> str:
    return presign(
        method="PUT",
        host="account.r2.cloudflarestorage.com",
        canonical_uri=f"/bucket/{key}",
        access_key="key",
        secret="secret",
        region="auto",
        service="s3",
        now=signed_at,
        expires=900,
        signed_headers=headers,
    )
