"""Expiry-deletion settle, per-record isolation, startup retries, bundle PUT lock."""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fake_s3_helpers import fake_s3
from loguru import logger
from public_site import report_publisher as publisher_module
from public_site.report_publications import ReportPublication
from public_site.report_publisher import (
    DELETE_SETTLE_SECONDS,
    ReportPublisher,
)
from public_site.storage import file_sha256
from report_test_helpers import (
    Clock,
    complete_submission,
    drain,
    methods,
    report_client,
    run_keys,
)

FLAGS = {"report_publish": True, "report_expiry_delete": True}


@pytest.fixture
def messages():
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG", format="{message}")
    yield lines
    logger.remove(sink)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _published(tmp_path: Path, fake, clock: Clock, count: int = 1):
    client = report_client(tmp_path, fake, clock=clock, **FLAGS)
    publisher = client.app.state.reports
    submissions = []
    for _ in range(count):
        _token, submission = complete_submission(client, tmp_path)
        submissions.append(submission)
    drain(publisher)
    for submission in submissions:
        assert publisher.publications.get(submission.id).state == "verified"
    return client, publisher, submissions


def _late_put(publisher: ReportPublisher, fake, submission_id: str, tmp_path: Path):
    """A PUT through the real writer, held on ``put_gate`` like an abandoned request."""
    record = publisher.publications.get(submission_id)
    source = tmp_path / "late.json"
    source.write_bytes(b'{"late": true}\n')
    key = f"reports/{submission_id}/{record.run_id}/late.json"
    fake.put_gate = threading.Event()
    puts_before = methods(fake).count("PUT")
    thread = threading.Thread(
        target=publisher.writer.put_file,
        args=(key, source, "application/json", file_sha256(source), 15),
    )
    thread.start()
    deadline = time.monotonic() + 10
    while methods(fake).count("PUT") == puts_before:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    return key, thread


def _release(fake, thread: threading.Thread) -> None:
    fake.put_gate.set()
    thread.join(10)
    assert not thread.is_alive()
    fake.put_gate = None


def test_late_put_is_deleted_again_before_the_record_is_forgotten(
    tmp_path: Path,
) -> None:
    clock = Clock(datetime.now(UTC).replace(microsecond=0))
    with fake_s3() as fake:
        client, publisher, (submission,) = _published(tmp_path, fake, clock)
        sid = submission.id
        # 1. An abandoned PUT is in flight, held on the gate.
        key, thread = _late_put(publisher, fake, sid, tmp_path)
        publisher.submission_expired(sid)
        # 2. Deletion empties the prefix; the record waits with delete_empty_at.
        assert publisher.run_once() is True
        assert run_keys(fake, sid) == {}
        record = publisher.publications.get(sid)
        assert (record.state, record.delete_empty_at) == (
            "delete_pending",
            _stamp(clock.now),
        )
        first_empty = clock.now
        # 3. The delayed PUT completes; the object exists.
        _release(fake, thread)
        assert key in fake.objects
        # 4. The next pass, before the settle time, deletes it again.
        clock.advance(seconds=61)
        assert publisher.run_once() is True
        assert key not in fake.objects
        record = publisher.publications.get(sid)
        assert record.state == "delete_pending"
        assert record.delete_empty_at == _stamp(clock.now) != _stamp(first_empty)
        new_empty = clock.now
        # 5. Less than the settle time later, the record is kept.
        while clock.now < new_empty + timedelta(seconds=DELETE_SETTLE_SECONDS - 61):
            clock.advance(seconds=60)
            drain(publisher)
            assert publisher.publications.get(sid) is not None
        clock.now = new_empty + timedelta(seconds=DELETE_SETTLE_SECONDS - 1)
        drain(publisher)
        assert publisher.publications.get(sid) is not None
        # 6. An empty LIST at least the settle time later removes it.
        clock.now = new_empty + timedelta(seconds=DELETE_SETTLE_SECONDS)
        assert publisher.run_once() is True
        assert publisher.publications.get(sid) is None
        assert run_keys(fake, sid) == {}
        client.close()


def test_without_the_settle_step_the_late_object_survives(
    tmp_path: Path, monkeypatch
) -> None:
    """Negative control: forgetting the record at the first empty LIST."""
    monkeypatch.setattr(ReportPublisher, "_settled", lambda _self, _r, _d: True)
    clock = Clock(datetime.now(UTC).replace(microsecond=0))
    with fake_s3() as fake:
        client, publisher, (submission,) = _published(tmp_path, fake, clock)
        key, thread = _late_put(publisher, fake, submission.id, tmp_path)
        publisher.submission_expired(submission.id)
        assert publisher.run_once() is True
        assert publisher.publications.get(submission.id) is None
        _release(fake, thread)
        clock.advance(seconds=DELETE_SETTLE_SECONDS + 1)
        drain(publisher)
        assert key in fake.objects
        client.close()


def test_deletion_failure_clears_delete_empty_at(tmp_path: Path) -> None:
    clock = Clock(datetime.now(UTC).replace(microsecond=0))
    with fake_s3() as fake:
        client, publisher, (submission,) = _published(tmp_path, fake, clock)
        publisher.submission_expired(submission.id)
        assert publisher.run_once() is True
        assert publisher.publications.get(submission.id).delete_empty_at
        clock.advance(seconds=61)
        fake.fail_always = 503
        assert publisher.run_once() is True
        record = publisher.publications.get(submission.id)
        assert (record.delete_empty_at, record.last_error) == (None, "status_5xx")
        fake.fail_always = None
        client.close()


# Per-record isolation.


def _symlink_barrier(publisher: ReportPublisher, submission_id: str, tmp_path: Path):
    writers = publisher.publications._writers
    writers.mkdir(parents=True, exist_ok=True)
    path = writers / f"{submission_id}.lock"
    if path.exists():
        path.unlink()
    path.symlink_to(tmp_path / "elsewhere.lock")


def _set_due(publisher, submission_id: str, moment: datetime, state: str) -> None:
    def change(record: ReportPublication | None) -> ReportPublication | None:
        record.state = state
        record.next_attempt_at = _stamp(moment)
        return record

    publisher.publications.update(submission_id, change)


def _two_pending(tmp_path: Path, fake, clock: Clock):
    client = report_client(tmp_path, fake, clock=clock, **FLAGS)
    publisher = client.app.state.reports
    _t1, a = complete_submission(client, tmp_path)
    _t2, b = complete_submission(client, tmp_path)
    _set_due(publisher, a.id, clock.now - timedelta(minutes=5), "pending")
    _set_due(publisher, b.id, clock.now - timedelta(minutes=1), "pending")
    _symlink_barrier(publisher, a.id, tmp_path)
    return client, publisher, a, b


def _assert_backed_off(publisher, a_id: str, clock: Clock, messages, tmp_path) -> None:
    record = publisher.publications.get(a_id)
    assert record.next_attempt_at == _stamp(clock.now + timedelta(seconds=60))
    assert record.last_error == "local_changed"
    raised = [m for m in messages if "raised OSError" in m]
    assert raised and all(str(tmp_path) not in m and ".lock" not in m for m in raised)


def test_failing_record_is_backed_off_and_the_next_publishes(
    tmp_path: Path, messages
) -> None:
    clock = Clock(datetime.now(UTC).replace(microsecond=0))
    with fake_s3() as fake:
        client, publisher, a, b = _two_pending(tmp_path, fake, clock)
        assert publisher.run_once() is True
        _assert_backed_off(publisher, a.id, clock, messages, tmp_path)
        assert publisher.publications.get(a.id).attempts == 1
        assert publisher.publications.get(b.id).state == "verified"
        assert run_keys(fake, a.id) == {}
        assert publisher.run_once() is False
        client.close()


def test_failing_record_is_backed_off_and_the_next_is_deleted(
    tmp_path: Path, messages
) -> None:
    clock = Clock(datetime.now(UTC).replace(microsecond=0))
    with fake_s3() as fake:
        client, publisher, (a, b) = _published(tmp_path, fake, clock, count=2)
        publisher.submission_expired(a.id)
        publisher.submission_expired(b.id)
        _set_due(publisher, a.id, clock.now - timedelta(minutes=5), "delete_pending")
        _set_due(publisher, b.id, clock.now - timedelta(minutes=1), "delete_pending")
        _symlink_barrier(publisher, a.id, tmp_path)
        assert publisher.run_once() is True
        _assert_backed_off(publisher, a.id, clock, messages, tmp_path)
        assert publisher.publications.get(a.id).delete_attempts == 1
        assert run_keys(fake, a.id) != {}
        assert run_keys(fake, b.id) == {}
        assert publisher.publications.get(b.id).delete_empty_at == _stamp(clock.now)
        client.close()


def test_single_record_pass_leaves_the_next_record_untouched(
    tmp_path: Path, monkeypatch
) -> None:
    """Negative control: with one record per pass B is not processed."""
    monkeypatch.setattr(publisher_module, "MAX_RECORDS_PER_PASS", 1)
    clock = Clock(datetime.now(UTC).replace(microsecond=0))
    with fake_s3() as fake:
        client, publisher, a, b = _two_pending(tmp_path, fake, clock)
        assert publisher.run_once() is True
        assert publisher.publications.get(b.id).state == "pending"
        assert "PUT" not in methods(fake)
        client.close()


def test_each_record_is_tried_at_most_once_per_pass(tmp_path: Path) -> None:
    clock = Clock(datetime.now(UTC).replace(microsecond=0))
    with fake_s3() as fake:
        client, publisher, a, b = _two_pending(tmp_path, fake, clock)
        _symlink_barrier(publisher, b.id, tmp_path)
        tried: list[str] = []
        original = publisher._publish

        def publish(submission_id: str) -> None:
            tried.append(submission_id)
            original(submission_id)

        publisher._publish = publish
        assert publisher.run_once() is True
        assert tried == [a.id, b.id]
        assert publisher.run_once() is False
        client.close()


def test_backoff_write_failure_reaches_the_loop_pause(
    tmp_path: Path, monkeypatch
) -> None:
    clock = Clock(datetime.now(UTC).replace(microsecond=0))
    with fake_s3() as fake:
        client, publisher, a, _b = _two_pending(tmp_path, fake, clock)

        def broken(*_args, **_kwargs):
            raise OSError("record store unavailable")

        monkeypatch.setattr(publisher.publications, "update", broken)
        with pytest.raises(OSError):
            publisher.run_once()
        client.close()


# Startup inside the loop's error handling.


def test_startup_is_retried_and_no_record_is_processed_before_it_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(publisher_module, "ERROR_PAUSE_SECONDS", 0.05)
    monkeypatch.setattr(publisher_module, "IDLE_SECONDS", 0.05)
    clock = Clock(datetime.now(UTC).replace(microsecond=0))
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, clock=clock, report_publish=True)
        publisher = client.app.state.reports
        _token, submission = complete_submission(client, tmp_path)
        events: list[str] = []
        original_startup, original_run = publisher.startup, publisher.run_once

        def startup() -> None:
            events.append("startup")
            if events.count("startup") < 3:
                raise OSError("staging root unavailable")
            original_startup()
            events.append("started")

        def run_once() -> bool:
            events.append("run_once")
            return original_run()

        publisher.startup, publisher.run_once = startup, run_once
        publisher.start()
        deadline = time.monotonic() + 10
        while publisher.publications.get(submission.id).state != "verified":
            assert time.monotonic() < deadline
            time.sleep(0.02)
        publisher.stop()
        assert events[:4] == ["startup", "startup", "startup", "started"]
        assert events.index("run_once") > events.index("started")
        client.close()


# The bundle PUT runs outside the record lock.


def test_record_lock_is_free_during_the_bundle_put(tmp_path: Path) -> None:
    clock = Clock(datetime.now(UTC).replace(microsecond=0))
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, clock=clock, **FLAGS)
        publisher = client.app.state.reports
        _token, submission = complete_submission(client, tmp_path)
        entered, release = threading.Event(), threading.Event()
        original = publisher._put_bundle

        def put_bundle(submission_id, active):
            entered.set()
            assert release.wait(10)
            original(submission_id, active)

        publisher._put_bundle = put_bundle
        worker = threading.Thread(target=publisher.run_once)
        worker.start()
        assert entered.wait(30)
        expired = threading.Thread(
            target=publisher.submission_expired, args=(submission.id,)
        )
        expired.start()
        expired.join(5)
        assert not expired.is_alive(), "record lock held during the bundle PUT"
        release.set()
        worker.join(30)
        record = publisher.publications.get(submission.id)
        assert record.state == "delete_pending" and record.run_id is None
        bundle = [k for k in fake.objects if k.endswith("bundle.json")]
        assert len(bundle) == 1
        assert os.path.basename(bundle[0]) == "bundle.json"
        client.close()
