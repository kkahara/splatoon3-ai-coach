"""Upload confirmation racing the maintenance pass over the same R2 source.

The request's ``confirm`` and ``maintain`` (``_abandon_one`` on a free site,
``_recover_ready`` on a paid one) may probe one submission at the same time.
Each probe must use its own download, and the second must not probe again.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from fake_s3_helpers import FakeObject, FakeS3, fake_s3
from fastapi.testclient import TestClient
from public_site.accounts import AccountStore
from public_site.app import SESSION_COOKIE, create_app
from public_site.errors import PROBE_CONTAINER
from public_site.keyed_locks import KeyedLocks
from public_site.media_probe import ProbeFacts, ProbeRejected
from public_site.payments import PaymentStore
from public_site.settings import PublicSettings
from public_site.storage import R2Storage
from public_site.tokens import hash_token

from splatoon3_ai_coach.config.paths import default_config_path

ID = "b" * 32
BODY = b"12345678"


def _r2(state: FakeS3) -> R2Storage:
    return R2Storage(
        account_id="account",
        access_key_id=state.access_key,
        secret_access_key=state.secret,
        bucket=state.bucket,
        endpoint=state.endpoint,
    )


def _wait_for(condition: Callable[[], bool], seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.01)


class _Run(threading.Thread):
    """Run ``target`` in a thread and keep its exception for the test."""

    def __init__(self, target: Callable, *args: object) -> None:
        super().__init__(daemon=True)
        self._call = lambda: target(*args)
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self._call()
        except BaseException as exc:  # noqa: BLE001 — reported by the test
            self.error = exc


class GatedProbe:
    """The first probe waits for ``release``. Every probe needs its file to exist."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls: list[Path] = []
        self._lock = threading.Lock()

    def __call__(self, path: Path) -> ProbeFacts:
        with self._lock:
            self.calls.append(path)
            first = len(self.calls) == 1
        if first:
            self.entered.set()
            self.release.wait(10)
        if not path.is_file():
            raise ProbeRejected(PROBE_CONTAINER)
        return ProbeFacts(12.0, 1920, 1080, "mp4")


def _client(
    tmp_path: Path,
    state: FakeS3,
    probe: GatedProbe,
    *,
    payments: PaymentStore | None = None,
    accounts: AccountStore | None = None,
) -> TestClient:
    root = tmp_path / "public"
    root.mkdir(parents=True, exist_ok=True)
    settings = PublicSettings(
        root=root,
        config_path=default_config_path(),
        static_dir=tmp_path / "no-dist",
        dev_mode=True,
        worker_id="worker-test",
        public_base_url="http://coach.example",
        max_video_bytes=1000,
    )
    app = create_app(
        settings,
        storage=_r2(state),
        verifier=lambda token: token == "ok",
        probe=probe,
        run_command=lambda _argv: 0,
        start_worker=False,
        payments=payments,
        accounts=accounts,
    )
    client = TestClient(app)
    client.app = app
    return client


def _uploaded(client: TestClient, state: FakeS3) -> tuple[str, str, str]:
    """Create a submission and store its source in the fake bucket."""
    response = client.post(
        "/api/submissions",
        json={
            "turnstile_token": "ok",
            "filename": "match.mov",
            "size_bytes": len(BODY),
            "content_type": "video/quicktime",
        },
    )
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    store = client.app.state.store
    submission = store.get_by_hash(hash_token(token))
    key = store.get_video(submission.id).object_key
    state.objects[key] = FakeObject(BODY)
    return token, submission.id, key


def _race(
    probe: GatedProbe, confirm: Callable[[], object], sweep: Callable[[], object]
) -> None:
    """Hold the request's probe, run the sweep against it, then let both finish."""
    first = _Run(confirm)
    first.start()
    assert probe.entered.wait(10)
    second = _Run(sweep)
    second.start()
    second.join(1.0)
    probe.release.set()
    first.join(10)
    second.join(10)
    assert not first.is_alive() and not second.is_alive()
    assert first.error is None, repr(first.error)
    assert second.error is None, repr(second.error)


def _assert_probed_once(
    client: TestClient, probe: GatedProbe, state: FakeS3, sid: str, key: str
) -> None:
    assert len(probe.calls) == 1
    assert client.app.state.store.get(sid).status == "queued"
    assert state.objects[key].body == BODY
    probe_dir = client.app.state.settings.root / "probe"
    assert list(probe_dir.iterdir()) == []
    assert len(client.app.state.service._probe_locks) == 0


def test_keyed_locks_serialize_one_key_only_and_forget_released_keys() -> None:
    locks = KeyedLocks()
    inside = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def holder() -> None:
        with locks.hold("a"):
            inside.set()
            release.wait(10)
            order.append("first")

    def waiter() -> None:
        with locks.hold("a"):
            order.append("second")

    first = _Run(holder)
    first.start()
    assert inside.wait(10)
    second = _Run(waiter)
    second.start()
    with locks.hold("b"):
        order.append("other key")
    second.join(0.2)
    assert second.is_alive()
    release.set()
    first.join(10)
    second.join(10)
    assert order == ["other key", "first", "second"]
    with locks.hold("a"), locks.hold("a"):
        assert len(locks) == 1
    assert len(locks) == 0


def test_concurrent_downloads_to_one_destination_do_not_collide(tmp_path: Path) -> None:
    body = bytes(range(256)) * 1024
    key = f"submissions/{ID}/source.mov"
    dest = tmp_path / "probe" / f"{ID}.mov"
    with fake_s3() as state:
        state.objects[key] = FakeObject(body)
        state.get_gate = threading.Event()
        state.get_gate_count = 1
        r2 = _r2(state)
        first = _Run(r2.download, key, dest)
        first.start()
        _wait_for(lambda: dest.parent.is_dir() and any(dest.parent.glob(".*.part")))
        assert r2.download(key, dest) == len(body)
        state.get_gate.set()
        first.join(10)
    assert first.error is None, repr(first.error)
    assert dest.read_bytes() == body
    assert [path.name for path in dest.parent.iterdir()] == [dest.name]


def test_confirm_and_maintain_probe_a_free_upload_once(tmp_path: Path) -> None:
    probe = GatedProbe()
    with fake_s3() as state:
        client = _client(tmp_path, state, probe)
        service = client.app.state.service
        token, sid, key = _uploaded(client, state)
        later = datetime.now(UTC) + timedelta(hours=1)
        _race(probe, lambda: service.confirm(token), lambda: service.maintain(later))
        _assert_probed_once(client, probe, state, sid, key)


def _database_url() -> str:
    url = os.environ.get("PUBLIC_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("PUBLIC_DATABASE_URL is not set")
    parts = urlsplit(url)
    admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, ""))
    with psycopg.connect(admin, autocommit=True) as conn:
        found = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", ("coach_test",)
        ).fetchone()
        if found is None:
            conn.execute("CREATE DATABASE coach_test")
    return urlunsplit((parts.scheme, parts.netloc, "/coach_test", parts.query, ""))


@pytest.fixture
def stores() -> tuple[PaymentStore, AccountStore]:
    payments = PaymentStore(_database_url())
    payments.ensure_schema()
    payments.clear()
    accounts = AccountStore(payments.database_url)
    accounts.ensure_schema()
    accounts.clear()
    yield payments, accounts
    accounts.clear()
    payments.clear()


def test_confirm_and_recover_ready_probe_a_paid_site_upload_once(
    tmp_path: Path, stores: tuple[PaymentStore, AccountStore]
) -> None:
    payments, accounts = stores
    probe = GatedProbe()
    now = datetime.now(UTC)
    verify = accounts.register(
        name="Ada", email="ada@example.com", password="correct-horse", now=now
    )
    _account, session = accounts.verify(verify, now)
    accounts.set_role("ada@example.com", "admin")
    with fake_s3() as state:
        client = _client(tmp_path, state, probe, payments=payments, accounts=accounts)
        client.cookies.set(SESSION_COOKIE, session)
        service = client.app.state.service
        token, sid, key = _uploaded(client, state)
        assert payments.get(sid).payment_state == "not_required"
        _race(
            probe,
            lambda: service.confirm(token),
            lambda: service.maintain(datetime.now(UTC)),
        )
        _assert_probed_once(client, probe, state, sid, key)
