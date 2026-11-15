"""Unexpected exceptions back off instead of retrying immediately (R3)."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fake_s3_helpers import fake_s3
from loguru import logger
from public_site import report_publisher as publisher_module
from report_test_helpers import Clock, complete_submission, methods, report_client

PRIVATE = "/Users/someone/private/Switch_Capture_secret.mp4"


@pytest.fixture
def messages():
    """Every log line, as rendered (message plus any exception text)."""
    lines: list[str] = []
    sink = logger.add(lambda m: lines.append(str(m)), level="DEBUG", format="{message}")
    yield lines
    logger.remove(sink)


def _queued(tmp_path: Path, fake, clock: Clock, **flags):
    client = report_client(tmp_path, fake, clock=clock, report_publish=True, **flags)
    _token, submission = complete_submission(client, tmp_path)
    return client, client.app.state.reports, submission


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (RuntimeError(f"bug near {PRIVATE}"), "local_changed"),
        (PermissionError(13, "denied", PRIVATE), "local_changed"),
        (FileNotFoundError(2, "gone", PRIVATE), "local_missing"),
    ],
    ids=["unexpected", "filesystem", "missing"],
)
def test_unexpected_preparation_error_backs_off(
    tmp_path: Path, monkeypatch, messages, error: Exception, code: str
) -> None:
    clock = Clock(datetime.now(UTC))
    with fake_s3() as fake:
        client, publisher, submission = _queued(tmp_path, fake, clock)

        def explode(*_args, **_kwargs):
            raise error

        monkeypatch.setattr(publisher_module, "prepare_bundle", explode)
        assert publisher.run_once() is True
        record = publisher.publications.get(submission.id)
        assert (record.state, record.last_error, record.attempts) == ("pending", code, 1)
        assert record.attempt_run_id is None
        assert publisher.run_once() is False
        clock.advance(seconds=61)
        assert publisher.run_once() is True
        assert publisher.publications.get(submission.id).attempts == 2
        assert "PUT" not in methods(fake)
        assert not any(publisher.staging.root.glob(f"{submission.id}/*"))
        text = "\n".join(messages)
        assert type(error).__name__ in text
        assert PRIVATE not in text and "Switch_Capture" not in text
        client.close()


def test_unexpected_upload_error_backs_off(tmp_path: Path, monkeypatch) -> None:
    clock = Clock(datetime.now(UTC))
    with fake_s3() as fake:
        client, publisher, submission = _queued(tmp_path, fake, clock)

        def explode(*_args, **_kwargs):
            raise KeyError("unexpected")

        monkeypatch.setattr(publisher, "_attempt", explode)
        publisher.run_once()
        record = publisher.publications.get(submission.id)
        assert (record.last_error, record.attempts) == ("local_changed", 1)
        assert publisher.run_once() is False
        client.close()


def test_unexpected_deletion_error_backs_off(
    tmp_path: Path, monkeypatch, messages
) -> None:
    clock = Clock(datetime.now(UTC))
    with fake_s3() as fake:
        client, publisher, submission = _queued(
            tmp_path, fake, clock, report_expiry_delete=True
        )
        publisher.submission_expired(submission.id)

        def explode(_submission_id: str) -> bool:
            raise RuntimeError(f"bug near {PRIVATE}")

        monkeypatch.setattr(publisher.deleter, "delete_submission", explode)
        assert publisher.run_once() is True
        record = publisher.publications.get(submission.id)
        assert (record.state, record.last_error, record.delete_attempts) == (
            "delete_pending",
            "network",
            1,
        )
        assert publisher.run_once() is False
        assert "RuntimeError" in "\n".join(messages)
        assert PRIVATE not in "\n".join(messages)
        client.close()


def test_loop_pauses_after_an_error_even_when_woken(
    tmp_path: Path, monkeypatch, messages
) -> None:
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, report_publish=True)
        publisher = client.app.state.reports
        calls: list[float] = []
        failed = threading.Event()

        def broken() -> bool:
            calls.append(time.monotonic())
            failed.set()
            raise RuntimeError(f"bug near {PRIVATE}")

        monkeypatch.setattr(publisher, "run_once", broken)
        publisher.start()
        try:
            assert failed.wait(30)
            for _ in range(20):
                publisher.wake()
                time.sleep(0.01)
            assert len(calls) == 1
        finally:
            publisher.stop()
        assert "report publisher error (RuntimeError)" in "\n".join(messages)
        assert PRIVATE not in "\n".join(messages)
        client.close()
