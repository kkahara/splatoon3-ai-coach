"""Per-worker analysis capacity: slots, claims, recovery, and the admin control."""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from fastapi.testclient import TestClient
from public_site import settings as settings_module
from public_site.accounts import AccountStore
from public_site.app import SESSION_COOKIE, create_app
from public_site.errors import ANALYSIS_FAILED, COACHING_FAILED, INTERRUPTED
from public_site.media_probe import ProbeFacts
from public_site.models import Submission
from public_site.settings import PublicSettings
from public_site.store import PublicStore, add_days, utc_now
from public_site.worker import PublicWorker
from public_site.worker_capacity import WorkerCapacityStore

from splatoon3_ai_coach.cli.coach_inputs import NO_COACHING_UNITS_EXIT_CODE
from splatoon3_ai_coach.coach.artifacts import coaching_index_path
from splatoon3_ai_coach.config.paths import default_config_path

_PASSWORD = "correct-horse"
_WAIT = 10.0


class Gate:
    """Fake s3-coach. ``analyze`` blocks until its submission is released."""

    def __init__(self, codes: dict[str, int] | None = None, block: bool = True) -> None:
        self.codes = codes or {}
        self.block = block
        self.started: set[str] = set()
        self.release: defaultdict[str, threading.Event] = defaultdict(threading.Event)
        self._lock = threading.Lock()

    def __call__(self, argv: list[str]) -> int:
        if "analyze" in argv:
            submission_id = Path(argv[argv.index("--out") + 1]).name
            with self._lock:
                self.started.add(submission_id)
            if self.block:
                assert self.release[submission_id].wait(_WAIT)
            return self.codes.get("analyze", 0)
        if "coach-inputs" in argv:
            return self.codes.get("coach-inputs", NO_COACHING_UNITS_EXIT_CODE)
        return self.codes.get("coach-prototype", 0)

    def release_all(self) -> None:
        for submission_id in list(self.started):
            self.release[submission_id].set()


def _settings(tmp_path: Path, **overrides) -> PublicSettings:
    root = tmp_path / "public"
    root.mkdir(parents=True, exist_ok=True)
    values = {
        "root": root,
        "config_path": default_config_path(),
        "static_dir": tmp_path / "no-dist",
        "dev_mode": True,
        "worker_id": "worker-test",
        "public_base_url": "http://coach.example",
        "max_video_bytes": 1000,
        "submissions_per_hour": 100,
        "guest_submissions_per_hour": 100,
        "max_queued": 100,
    }
    values.update(overrides)
    return PublicSettings(**values)


def _client(
    tmp_path: Path, run_command: Callable[[list[str]], int], **kwargs
) -> TestClient:
    accounts = kwargs.pop("accounts", None)
    capacities = kwargs.pop("capacities", None)
    settings = _settings(tmp_path, **kwargs)
    app = create_app(
        settings,
        store=PublicStore(settings.root),
        verifier=lambda token: token == "ok",
        probe=lambda _path: ProbeFacts(12.0, 1920, 1080, "mp4"),
        run_command=run_command,
        start_worker=False,
        accounts=accounts,
        capacities=capacities,
    )
    client = TestClient(app)
    client.app = app
    return client


def _queue(client: TestClient, count: int) -> list[str]:
    """Queue ``count`` uploads and return their ids, oldest first."""
    store = client.app.state.store
    before = {item.id for item in store.list_submissions()}
    for _ in range(count):
        created = client.post(
            "/api/submissions",
            json={
                "turnstile_token": "ok",
                "filename": "match.mp4",
                "size_bytes": 8,
                "content_type": "video/mp4",
                "language": "en",
            },
        ).json()
        upload = created["upload"]
        client.put(upload["url"], content=b"12345678", headers=upload["headers"])
        client.post(f"/api/submissions/{created['token']}/uploaded")
    ids = sorted(item.id for item in store.list_submissions() if item.id not in before)
    for index, submission_id in enumerate(ids):
        stamp = f"2026-01-01T00:00:{index:02d}Z"

        def order(item: Submission, stamp: str = stamp) -> None:
            item.created_at = stamp

        store.update(submission_id, order)
    assert all(store.get(submission_id).status == "queued" for submission_id in ids)
    return ids


def _worker(
    client: TestClient, gate: Callable[[list[str]], int], capacity: Callable[[], int]
) -> PublicWorker:
    app = client.app
    return PublicWorker(
        app.state.settings,
        app.state.store,
        app.state.service,
        run_command=gate,
        start_thread=False,
        capacity=capacity,
    )


def _count(store: PublicStore, status: str) -> int:
    return store.count_status(status)


def _wait_started(gate: Gate, count: int) -> None:
    deadline = time.monotonic() + _WAIT
    while len(gate.started) < count:
        assert time.monotonic() < deadline, f"only {len(gate.started)} analyses started"
        time.sleep(0.01)


def _wait_active(worker: PublicWorker, count: int) -> None:
    deadline = time.monotonic() + _WAIT
    while worker.active_jobs != count:
        assert time.monotonic() < deadline, f"active_jobs stayed {worker.active_jobs}"
        time.sleep(0.01)


def _release_one(
    gate: Gate, worker: PublicWorker, submission_id: str, active_after: int
) -> None:
    gate.release[submission_id].set()
    _wait_active(worker, active_after)


def test_capacity_two_runs_two_videos_at_once_and_leaves_the_rest_queued(
    tmp_path: Path,
) -> None:
    gate = Gate()
    client = _client(tmp_path, gate)
    _queue(client, 3)
    worker = _worker(client, gate, lambda: 2)
    assert worker.pump() is True
    _wait_started(gate, 2)
    store = client.app.state.store
    assert worker.active_jobs == 2
    assert _count(store, "processing") == 2
    assert _count(store, "queued") == 1
    assert worker.pump() is False
    gate.release_all()
    worker.join()
    assert worker.active_jobs == 0


def test_capacity_one_keeps_the_second_video_queued(tmp_path: Path) -> None:
    gate = Gate()
    client = _client(tmp_path, gate)
    _queue(client, 2)
    worker = _worker(client, gate, lambda: 1)
    assert worker.pump() is True
    _wait_started(gate, 1)
    assert worker.pump() is False
    assert _count(client.app.state.store, "queued") == 1
    gate.release_all()
    worker.join()


def test_raising_capacity_claims_extra_slots_on_the_next_pass(tmp_path: Path) -> None:
    gate = Gate()
    client = _client(tmp_path, gate)
    _queue(client, 6)
    capacity = [2]
    worker = _worker(client, gate, lambda: capacity[0])
    worker.pump()
    _wait_started(gate, 2)
    store = client.app.state.store
    assert _count(store, "processing") == 2
    capacity[0] = 4
    worker.wake()
    assert worker.pump() is True
    _wait_started(gate, 4)
    assert worker.active_jobs == 4
    assert _count(store, "processing") == 4
    assert _count(store, "queued") == 2
    gate.release_all()
    worker.join()


def test_lowering_capacity_lets_running_jobs_finish_before_claiming(
    tmp_path: Path,
) -> None:
    gate = Gate()
    client = _client(tmp_path, gate)
    order = _queue(client, 6)
    capacity = [4]
    worker = _worker(client, gate, lambda: capacity[0])
    worker.pump()
    _wait_started(gate, 4)
    running = order[:4]
    capacity[0] = 2
    store = client.app.state.store
    assert worker.pump() is False
    assert _count(store, "processing") == 4
    _release_one(gate, worker, running[0], 3)
    assert worker.pump() is False
    _release_one(gate, worker, running[1], 2)
    assert worker.pump() is False
    assert _count(store, "queued") == 2
    _release_one(gate, worker, running[2], 1)
    assert worker.pump() is True
    _wait_started(gate, 5)
    assert worker.active_jobs == 2
    assert _count(store, "queued") == 1
    gate.block = False
    gate.release_all()
    worker.join()


def _write_index(argv: list[str]) -> None:
    analysis = Path(argv[argv.index("coach-prototype") + 1])
    path = coaching_index_path(analysis)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]", encoding="utf-8")


def _frames_fail(*_args, **_kwargs) -> None:
    raise RuntimeError("frames failed")


def _raise(_argv: list[str]) -> int:
    raise RuntimeError("unexpected")


@pytest.mark.parametrize(
    ("path", "expected_status", "expected_error"),
    [
        ("success", "complete", None),
        ("analyze_nonzero", "failed", ANALYSIS_FAILED),
        ("coach_inputs_nonzero", "failed", COACHING_FAILED),
        ("prototype_nonzero_without_index", "failed", COACHING_FAILED),
        ("missing_source", "failed", ANALYSIS_FAILED),
        ("frames_exception", "complete", None),
        ("command_raises", "failed", ANALYSIS_FAILED),
    ],
)
def test_every_terminal_path_releases_the_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    expected_status: str,
    expected_error: str | None,
) -> None:
    run = _command_for(path)
    if path == "frames_exception":
        monkeypatch.setattr("public_site.worker.extract_frames", _frames_fail)
    client = _client(tmp_path, run)
    first, second = _queue(client, 2)
    store = client.app.state.store
    if path == "missing_source":
        for submission_id in (first, second):
            video = store.get_video(submission_id)
            video.object_key = ""
            store.save_video(video)
    worker = _worker(client, run, lambda: 1)
    assert worker.pump() is True
    worker.join()
    assert worker.active_jobs == 0
    done = store.get(first)
    assert done.status == expected_status
    assert done.error == expected_error
    assert store.get(second).status == "queued"
    assert worker.pump() is True
    worker.join()
    assert worker.active_jobs == 0
    assert store.get(second).status == expected_status


def _command_for(path: str) -> Callable[[list[str]], int]:
    if path == "command_raises":
        return _raise
    codes = {
        "success": {},
        "analyze_nonzero": {"analyze": 1},
        "coach_inputs_nonzero": {"coach-inputs": 1},
        "prototype_nonzero_without_index": {"coach-inputs": 0, "coach-prototype": 1},
        "missing_source": {},
    }.get(path)
    if codes is not None:
        return Gate(codes, block=False)

    def with_index(argv: list[str]) -> int:
        if "coach-inputs" in argv:
            return 0
        if "coach-prototype" in argv:
            _write_index(argv)
        return 0

    return with_index


def _record(status: str, worker_id: str | None = None) -> Submission:
    now = utc_now()
    return Submission(
        id=uuid.uuid4().hex,
        access_token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        status=status,  # type: ignore[arg-type]
        created_at=now,
        updated_at=now,
        expires_at=add_days(now, 30),
        worker_id=worker_id,
        processing_started_at=now if status == "processing" else None,
    )


def test_startup_recovery_assumes_single_worker_and_fails_all_in_flight(
    tmp_path: Path,
) -> None:
    """Startup fails every in-flight record, whichever worker stamped it.

    Correct only while this deployment has exactly one worker. Before a
    second worker exists this must be replaced by lease or heartbeat based
    recovery; this test pins today's single-worker rule, not distributed safety.
    """
    settings = _settings(tmp_path)
    store = PublicStore(settings.root)
    mine = _record("processing", worker_id=settings.worker_id)
    other = _record("processing", worker_id="another-worker")
    unowned = _record("processing")
    validating = _record("validating")
    queued = _record("queued")
    for item in (mine, other, unowned, validating, queued):
        store.save_submission(item)
    calls: list[list[str]] = []
    create_app(
        settings,
        store=store,
        verifier=lambda _token: False,
        start_worker=False,
        run_command=lambda argv: calls.append(argv) or 0,
    )
    for item in (mine, other, unowned, validating):
        recovered = store.get(item.id)
        assert recovered.status == "failed"
        assert recovered.error == INTERRUPTED
    assert store.get(queued.id).status == "queued"
    assert calls == []


def test_two_stores_on_one_root_cannot_claim_the_same_submission(
    tmp_path: Path,
) -> None:
    root = tmp_path / "public"
    first = PublicStore(root)
    second = PublicStore(root)
    queued = _record("queued")
    first.save_submission(queued)
    barrier = threading.Barrier(2)
    results: list[Submission | None] = []
    lock = threading.Lock()

    def claim(store: PublicStore, worker_id: str) -> None:
        barrier.wait()
        found = store.claim_oldest_queued(worker_id, utc_now())
        with lock:
            results.append(found)

    threads = [
        threading.Thread(target=claim, args=(first, "worker-a")),
        threading.Thread(target=claim, args=(second, "worker-b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    won = [item for item in results if item is not None]
    assert len(won) == 1
    assert first.get(queued.id).status == "processing"
    assert first.get(queued.id).worker_id == won[0].worker_id


def _clear_capacity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_module, "_load_env", lambda: None)
    for name in (
        "PUBLIC_WORKER_CAPACITY",
        "PUBLIC_MAX_CONCURRENT",
        "PUBLIC_WORKER_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_worker_capacity_env_is_canonical(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_capacity_env(monkeypatch)
    monkeypatch.setenv("PUBLIC_WORKER_CAPACITY", "4")
    monkeypatch.setenv("PUBLIC_MAX_CONCURRENT", "2")
    settings = PublicSettings.from_env()
    assert settings.max_concurrent_jobs == 4
    assert settings.worker_key == "default"


def test_legacy_max_concurrent_still_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_capacity_env(monkeypatch)
    monkeypatch.setenv("PUBLIC_MAX_CONCURRENT", "3")
    monkeypatch.setenv("PUBLIC_WORKER_KEY", "analysis-primary")
    settings = PublicSettings.from_env()
    assert settings.max_concurrent_jobs == 3
    assert settings.worker_key == "analysis-primary"


def test_worker_capacity_defaults_to_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_capacity_env(monkeypatch)
    assert PublicSettings.from_env().max_concurrent_jobs == 1


def _database_url() -> str:
    url = os.environ.get("PUBLIC_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("PUBLIC_DATABASE_URL is not set")
    parts = urlsplit(url)
    admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, ""))
    test_url = urlunsplit((parts.scheme, parts.netloc, "/coach_test", parts.query, ""))
    with psycopg.connect(admin, autocommit=True) as conn:
        found = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", ("coach_test",)
        ).fetchone()
        if found is None:
            conn.execute("CREATE DATABASE coach_test")
    return test_url


@pytest.fixture
def capacities() -> WorkerCapacityStore:
    store = WorkerCapacityStore(_database_url())
    store.ensure_schema()
    store.clear()
    yield store
    store.clear()


@pytest.fixture
def accounts(capacities: WorkerCapacityStore) -> AccountStore:
    store = AccountStore(capacities.database_url)
    store.ensure_schema()
    store.clear()
    yield store
    store.clear()


def _session(accounts: AccountStore, email: str, admin: bool) -> str:
    now = datetime.now(UTC)
    verify = accounts.register(name="Ada", email=email, password=_PASSWORD, now=now)
    _account, session = accounts.verify(verify, now)
    if admin:
        accounts.set_role(email, "admin")
    return session


def test_admin_saves_this_workers_capacity_only(
    tmp_path: Path, capacities: WorkerCapacityStore, accounts: AccountStore
) -> None:
    capacities.set_capacity("another-worker", 8)
    client = _client(
        tmp_path,
        Gate(),
        accounts=accounts,
        capacities=capacities,
        worker_key="analysis-primary",
    )
    client.cookies.set(
        SESSION_COOKIE, _session(accounts, "user@example.com", admin=False)
    )
    assert client.get("/api/admin/worker-capacity").status_code == 403
    assert (
        client.put(
            "/api/admin/worker-capacity", json={"max_concurrent_jobs": 4}
        ).status_code
        == 403
    )
    client.cookies.set(
        SESSION_COOKIE, _session(accounts, "ada@example.com", admin=True)
    )
    current = client.get("/api/admin/worker-capacity").json()
    assert current["worker_key"] == "analysis-primary"
    assert current["max_concurrent_jobs"] == 1
    assert current["active_jobs"] == 0
    for value in (0, 17, 4.5, 4.0, "4", "nope", True):
        bad = client.put(
            "/api/admin/worker-capacity", json={"max_concurrent_jobs": value}
        )
        assert bad.status_code == 422
    saved = client.put("/api/admin/worker-capacity", json={"max_concurrent_jobs": 4})
    assert saved.status_code == 200
    assert saved.json()["max_concurrent_jobs"] == 4
    again = client.get("/api/admin/worker-capacity").json()
    assert again == {
        "worker_key": "analysis-primary",
        "max_concurrent_jobs": 4,
        "active_jobs": 0,
        "min": 1,
        "max": 16,
    }
    assert capacities.capacity("analysis-primary") == 4
    assert capacities.capacity("another-worker") == 8


def test_admin_raise_applies_to_the_running_worker(
    tmp_path: Path, capacities: WorkerCapacityStore, accounts: AccountStore
) -> None:
    gate = Gate()
    client = _client(tmp_path, gate, accounts=accounts, capacities=capacities)
    capacities.set_capacity("default", 2)
    _queue(client, 6)
    worker = client.app.state.worker
    worker.pump()
    _wait_started(gate, 2)
    client.cookies.set(
        SESSION_COOKIE, _session(accounts, "ada@example.com", admin=True)
    )
    saved = client.put("/api/admin/worker-capacity", json={"max_concurrent_jobs": 4})
    assert saved.status_code == 200
    assert saved.json()["active_jobs"] == 2
    assert worker.pump() is True
    _wait_started(gate, 4)
    store = client.app.state.store
    assert _count(store, "processing") == 4
    assert _count(store, "queued") == 2
    gate.block = False
    gate.release_all()
    worker.join()
