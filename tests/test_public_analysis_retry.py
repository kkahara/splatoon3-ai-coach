"""Retry analysis: a verified review-code recording whose analysis failed is run again."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from public_site import worker as worker_module
from public_site.errors import ANALYSIS_FAILED, INTERRUPTED, REVIEW_CODE_INTERRUPTED
from public_site.models import Submission
from public_site.notices import REVIEW_CODE_FAILED_HELP
from public_site.recording_service import RETRY_RUNNING
from public_site.storage import ObjectStat
from public_site.telemetry import Diagnostic, ErrorCode, classify_error
from test_public_recording_intake import (  # noqa: F401
    CODE,
    _assert_handed_off,
    _claim,
    _count_deliveries,
    _endpoint,
    _events,
    _gate,
    _job,
    _run_worker,
    _verified,
    site,
)
from test_public_recordings import _view

NOW = "2026-11-12T00:00:00Z"


def _outbox(site, created: dict, kind: str) -> str:  # noqa: F811
    root = site.app.state.settings.root
    return (root / "outbox" / f"{created['submission_id']}.{kind}.txt").read_text()


def _analysis(site, created: dict) -> Path:  # noqa: F811
    return site.app.state.settings.root / "analysis" / created["submission_id"]


def _stale(site, created: dict) -> Path:  # noqa: F811
    """A previous run's partial output: nested files a new run must never read."""
    analysis = _analysis(site, created)
    (analysis / "vision").mkdir(parents=True)
    (analysis / "vision" / "vision_manifest.json").write_text("{}")
    (analysis / "stale.txt").write_text("old run")
    return analysis


def test_recovery_sends_a_review_code_customer_to_the_administrator(site) -> None:  # noqa: F811
    created = _verified(site)
    assert _claim(site) is not None
    site.app.state.worker.recover()
    failed = site.app.state.service.store.get(created["submission_id"])
    assert failed.status == "failed" and failed.error == REVIEW_CODE_INTERRUPTED
    assert _view(site, created)["error"] == REVIEW_CODE_INTERRUPTED
    body = _outbox(site, created, "failed")
    assert REVIEW_CODE_FAILED_HELP in body
    assert "Submit the video again" not in body


def test_recovery_keeps_the_upload_wording_for_an_uploaded_video(site) -> None:  # noqa: F811
    created = _verified(site)
    svc = site.app.state.service
    item = svc.store.get(created["submission_id"])
    video = item.model_copy(
        update={
            "id": "f" * 32,
            "input_type": "video",
            "source": "upload",
            "access_token_hash": "e" * 64,
            "status": "processing",
        }
    )
    svc.store.save_submission(video)
    site.app.state.worker.recover()
    assert svc.store.get(video.id).error == INTERRUPTED


def test_both_interruption_wordings_map_to_the_same_closed_code() -> None:
    expected = (ErrorCode.INTERRUPTED, Diagnostic.INTERRUPTED)
    assert classify_error(INTERRUPTED) == expected
    assert classify_error(REVIEW_CODE_INTERRUPTED) == expected


def test_a_run_without_an_analysis_directory_starts_clean(site) -> None:  # noqa: F811
    created = _verified(site)
    assert not _analysis(site, created).exists()
    done = _run_worker(site, created)
    assert done.status == "complete"
    assert _analysis(site, created).is_dir()


def test_a_claimed_run_removes_the_previous_partial_analysis(site) -> None:  # noqa: F811
    created = _verified(site)
    analysis = _stale(site, created)
    done = _run_worker(site, created)
    assert done.status == "complete"
    assert analysis.is_dir() and not analysis.is_symlink()
    assert not (analysis / "stale.txt").exists()
    assert not (analysis / "vision" / "vision_manifest.json").exists()


def test_a_symlink_at_the_analysis_path_is_unlinked_and_never_followed(
    site, tmp_path: Path  # noqa: F811
) -> None:
    created = _verified(site)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("not ours")
    analysis = _analysis(site, created)
    analysis.parent.mkdir(parents=True, exist_ok=True)
    analysis.symlink_to(outside, target_is_directory=True)
    done = _run_worker(site, created)
    assert done.status == "complete"
    assert analysis.is_dir() and not analysis.is_symlink()
    assert (outside / "keep.txt").read_text() == "not ours"
    assert sorted(path.name for path in outside.iterdir()) == ["keep.txt"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_permission_failure_fails_the_run_before_any_stage(site) -> None:  # noqa: F811
    created = _verified(site)
    analysis = _stale(site, created)
    locked = analysis / "locked"
    locked.mkdir()
    (locked / "partial.json").write_text("{}")
    locked.chmod(0o500)
    try:
        done = _run_worker(site, created)
    finally:
        locked.chmod(0o700)
    assert done.status == "failed" and done.error == ANALYSIS_FAILED
    assert site.pipeline.calls == []
    assert (locked / "partial.json").exists()


def test_a_cleanup_error_fails_the_run_and_keeps_stale_files_unused(
    site, monkeypatch  # noqa: F811
) -> None:
    created = _verified(site)
    analysis = _stale(site, created)

    def refuse(_path: Path) -> None:
        raise OSError("cleanup path remains after deletion")

    monkeypatch.setattr(worker_module, "_remove_tree", refuse)
    done = _run_worker(site, created)
    assert done.status == "failed" and done.error == ANALYSIS_FAILED
    assert site.pipeline.calls == []
    assert done.outcome is None
    assert (analysis / "stale.txt").read_text() == "old run"


def test_requeue_changes_only_a_failed_record_and_keeps_notices(site) -> None:  # noqa: F811
    created = _verified(site)
    store = site.app.state.service.store
    claimed = _claim(site)
    assert claimed is not None
    assert store.requeue_failed(created["submission_id"], NOW) is None
    site.app.state.worker.recover()
    failed = store.get(created["submission_id"])
    assert failed.status == "failed" and "failed" in failed.notices_sent
    queued = store.requeue_failed(created["submission_id"], NOW)
    assert queued is not None
    assert (queued.status, queued.step) == ("queued", "queued")
    assert queued.error is None and queued.outcome is None
    assert queued.analysis_dir is None and queued.worker_id is None
    assert queued.processing_started_at is None and queued.updated_at == NOW
    assert queued.notices_sent == failed.notices_sent
    assert store.requeue_failed(created["submission_id"], NOW) is None


def test_is_running_tracks_the_job_thread_until_its_cleanup_ends(
    site, monkeypatch  # noqa: F811
) -> None:
    created = _verified(site)
    worker = site.app.state.worker
    entered, release = threading.Event(), threading.Event()
    execute = worker._execute

    def held(submission) -> None:
        entered.set()
        release.wait(timeout=30)
        execute(submission)

    monkeypatch.setattr(worker, "_execute", held)
    assert not worker.is_running(created["submission_id"])
    assert worker.tick()
    assert entered.wait(timeout=30)
    assert worker.is_running(created["submission_id"])
    release.set()
    worker.join(timeout=30)
    assert not worker.is_running(created["submission_id"])


def _retry(site, created: dict, version: int | None = None, **changes):  # noqa: F811
    if version is None:
        version = _job(site, created)["version"]
    body = {
        "action": "retry_analysis",
        "expected_version": version,
        "note": "Analysis was interrupted by a site restart.",
        **changes,
    }
    return site.post(_endpoint(created) + "/actions", json=body)


def _failed_run(site) -> tuple[dict, Submission]:  # noqa: F811
    """A verified recording whose analysis failed, leaving partial output behind."""
    site.pipeline.analyze_code = 1
    created = _verified(site)
    failed = _run_worker(site, created)
    assert failed.status == "failed" and failed.processing_started_at
    (_analysis(site, created) / "stale.txt").write_text("old run")
    site.pipeline.analyze_code = 0
    site.pipeline.calls.clear()
    return created, failed


def _retry_events(site, created: dict) -> list[dict]:  # noqa: F811
    detail = site.app.state.service.recordings.database.detail(created["submission_id"])
    return [event for event in detail["events"] if event["kind"] == "retry_analysis"]


def test_retry_runs_the_verified_recording_once_without_a_new_email(
    site, monkeypatch  # noqa: F811
) -> None:
    site.pipeline.coached = True
    created, failed = _failed_run(site)
    svc = site.app.state.service
    assert _outbox(site, created, "failed")
    gate = _gate(site, created)
    grants = _events(site, created, "recording_upload_granted")
    version = _job(site, created)["version"]
    reports: list[str] = []
    monkeypatch.setattr(svc, "note_report_completed", reports.append)
    log: list[str] = []
    _count_deliveries(site, monkeypatch, log)

    response = _retry(site, created)
    assert response.status_code == 200, response.text
    assert response.json()["job"]["version"] == version + 1
    assert response.json()["job"]["state"] == "uploading"
    assert response.json()["analysis"]["status"] == "queued"
    queued = svc.store.get(created["submission_id"])
    assert (queued.status, queued.step, queued.error) == ("queued", "queued", None)
    assert queued.notices_sent == failed.notices_sent
    assert _view(site, created)["status"] == "queued"

    done = _run_worker(site, created)
    assert done.status == "complete" and done.outcome == "coached"
    assert not (_analysis(site, created) / "stale.txt").exists()
    _assert_handed_off(site, created, done)
    assert reports == [created["submission_id"]]
    assert log == []
    outbox = svc.settings.root / "outbox"
    assert not (outbox / f"{created['submission_id']}.ready.txt").exists()
    after = _gate(site, created)
    assert after.payment_state == gate.payment_state
    assert after.voucher_state == gate.voucher_state
    assert after.payment_intent_id == gate.payment_intent_id
    assert _events(site, created, "recording_upload_granted") == grants
    assert len(_retry_events(site, created)) == 1
    assert _view(site, created)["status"] == "complete"
    svc.maintain(datetime.now(UTC))
    assert _events(site, created, "recording_handoff_verified") == 1
    assert reports == [created["submission_id"]]


def test_retry_audit_keeps_the_failed_run_without_raw_error_text(site) -> None:  # noqa: F811
    created, failed = _failed_run(site)
    store = site.app.state.service.store
    leaked = "/private/tmp/s3local/analysis/x player@example.com R2_SECRET=abc"

    def taint(item: Submission) -> None:
        item.error = leaked
        item.worker_id = "worker-a1"

    store.update(created["submission_id"], taint)
    assert _retry(site, created).status_code == 200
    [event] = _retry_events(site, created)
    details = event["details"]
    assert details["failed_error_code"] == "unknown"
    assert details["failed_step"] == failed.step
    assert details["processing_started_at"] == failed.processing_started_at
    assert details["worker_id"] == "worker-a1"
    assert details["note"] == "Analysis was interrupted by a site restart."
    text = json.dumps(details)
    for fragment in ("/private", "s3local", "@example.com", "R2_SECRET", CODE):
        assert fragment not in text
    assert "failed_error" not in details


def test_retry_audit_names_the_closed_error_code_and_drops_odd_worker_ids(
    site,  # noqa: F811
) -> None:
    created, _failed = _failed_run(site)

    def odd(item: Submission) -> None:
        item.worker_id = "host/../../etc passwd"

    site.app.state.service.store.update(created["submission_id"], odd)
    assert _retry(site, created).status_code == 200
    [event] = _retry_events(site, created)
    assert event["details"]["failed_error_code"] == "analysis_failed"
    assert "worker_id" not in event["details"]


def _unchanged(site, created: dict, version: int, status: str = "failed") -> None:  # noqa: F811
    assert _job(site, created)["version"] == version
    assert site.app.state.service.store.get(created["submission_id"]).status == status
    assert _retry_events(site, created) == []


def test_retry_is_refused_unless_the_analysis_failed(site) -> None:  # noqa: F811
    created = _verified(site)
    version = _job(site, created)["version"]
    assert _retry(site, created).status_code == 409
    _unchanged(site, created, version, "queued")
    done = _run_worker(site, created)
    assert done.status == "complete"
    assert _retry(site, created).status_code == 409


def test_retry_is_refused_for_a_failed_recording_exception(site) -> None:  # noqa: F811
    created, _failed = _failed_run(site)
    flagged = site.post(
        _endpoint(created) + "/actions",
        json={
            "action": "recording_failed",
            "expected_version": _job(site, created)["version"],
            "note": "Wrong match recorded.",
        },
    )
    assert flagged.status_code == 200
    version = _job(site, created)["version"]
    assert _retry(site, created).status_code == 409
    _unchanged(site, created, version)


def test_retry_is_refused_while_the_failed_run_still_owns_the_job(
    site, monkeypatch  # noqa: F811
) -> None:
    created, _failed = _failed_run(site)
    recordings = site.app.state.service.recordings
    monkeypatch.setattr(recordings, "_is_running", lambda _id: True)
    version = _job(site, created)["version"]
    response = _retry(site, created)
    assert response.status_code == 409
    assert response.json()["detail"] == RETRY_RUNNING
    _unchanged(site, created, version)


@pytest.mark.parametrize("problem", ["missing", "size", "attempt"])
def test_retry_is_refused_when_storage_no_longer_holds_the_verified_file(
    site, monkeypatch, problem: str  # noqa: F811
) -> None:
    created, _failed = _failed_run(site)
    storage = site.app.state.service.storage
    video = site.app.state.service.store.get_video(created["submission_id"])
    key, size = video.object_key, video.size_bytes
    stat = storage.head(key)
    replaced = {
        "missing": None,
        "size": ObjectStat(key, size + 1, attempt=stat.attempt),
        "attempt": ObjectStat(key, size, attempt="other-attempt"),
    }[problem]
    monkeypatch.setattr(storage, "head", lambda _key: replaced)
    version = _job(site, created)["version"]
    assert _retry(site, created).status_code == 409
    _unchanged(site, created, version)


def test_retry_needs_a_note_and_the_current_version(site) -> None:  # noqa: F811
    created, _failed = _failed_run(site)
    version = _job(site, created)["version"]
    assert _retry(site, created, note="   ").status_code == 400
    assert _retry(site, created, version=version - 1).status_code == 409
    _unchanged(site, created, version)


def test_retry_is_refused_for_anything_but_a_review_code_run(site) -> None:  # noqa: F811
    created, _failed = _failed_run(site)
    store = site.app.state.service.store
    version = _job(site, created)["version"]

    def as_video(item: Submission) -> None:
        item.input_type = "video"

    store.update(created["submission_id"], as_video)
    assert _retry(site, created).status_code == 409
    _unchanged(site, created, version)

    def never_ran(item: Submission) -> None:
        item.input_type = "review_code"
        item.processing_started_at = None

    store.update(created["submission_id"], never_ran)
    assert _retry(site, created).status_code == 409
    _unchanged(site, created, version)


def _requeue_fails_once(site, monkeypatch) -> None:  # noqa: F811
    store = site.app.state.service.store
    real = store.requeue_failed

    def broken(submission_id: str, now: str):
        monkeypatch.setattr(store, "requeue_failed", real)
        raise OSError("disk full")

    monkeypatch.setattr(store, "requeue_failed", broken)


def test_a_repeated_retry_completes_the_requeue_without_a_second_event(
    site, monkeypatch  # noqa: F811
) -> None:
    created, _failed = _failed_run(site)
    version = _job(site, created)["version"]
    _requeue_fails_once(site, monkeypatch)
    with pytest.raises(OSError):
        _retry(site, created, version=version)
    assert _job(site, created)["version"] == version + 1
    assert site.app.state.service.store.get(created["submission_id"]).status == "failed"
    assert _retry(site, created, version=version).status_code == 200
    assert _job(site, created)["version"] == version + 1
    assert len(_retry_events(site, created)) == 1
    assert site.app.state.service.store.get(created["submission_id"]).status == "queued"
    assert _run_worker(site, created).status == "complete"


def test_reconcile_completes_a_committed_retry_after_a_failed_requeue(
    site, monkeypatch  # noqa: F811
) -> None:
    created, _failed = _failed_run(site)
    _requeue_fails_once(site, monkeypatch)
    with pytest.raises(OSError):
        _retry(site, created)
    svc = site.app.state.service
    svc.recordings._published.clear()
    svc.maintain(datetime.now(UTC))
    assert svc.store.get(created["submission_id"]).status == "queued"
    assert len(_retry_events(site, created)) == 1
    assert _run_worker(site, created).status == "complete"


def test_reconcile_never_requeues_a_failure_no_retry_named(site) -> None:  # noqa: F811
    created, _failed = _failed_run(site)
    svc = site.app.state.service
    svc.recordings._published.clear()
    svc.maintain(datetime.now(UTC))
    assert svc.store.get(created["submission_id"]).status == "failed"


def test_detail_shows_the_failed_analysis_without_paths(site) -> None:  # noqa: F811
    created, _failed = _failed_run(site)
    analysis = site.get(_endpoint(created)).json()["analysis"]
    assert analysis == {
        "status": "failed",
        "step": analysis["step"],
        "error": ANALYSIS_FAILED,
        "input_type": "review_code",
    }
