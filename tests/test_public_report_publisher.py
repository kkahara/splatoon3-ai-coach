"""Report publishing: settings, capabilities, integrity, retry and serving parity.

Only the in-process fake S3 is used; no real R2 is ever contacted.
"""

from __future__ import annotations

import io
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fake_s3_helpers import fake_s3
from loguru import logger
from public_site.report_publications import PublicationStore, ReportPublication
from public_site.report_publisher import publish_backoff, storage_error_code
from public_site.settings import PublicSettings
from report_bundle_fixture import folder_digest
from report_test_helpers import (
    Clock,
    complete_submission,
    drain,
    expire,
    methods,
    report_client,
    report_settings,
    run_keys,
    settle,
)

FLAGS = ("PUBLIC_REPORT_PUBLISH", "PUBLIC_REPORT_EXPIRY_DELETE", "PUBLIC_REPORT_SWEEP")


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in (
        *FLAGS,
        "PUBLIC_R2_ACCOUNT_ID",
        "PUBLIC_R2_ACCESS_KEY_ID",
        "PUBLIC_R2_SECRET_ACCESS_KEY",
        "PUBLIC_R2_BUCKET",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_report_settings_default_off(clean_env: pytest.MonkeyPatch) -> None:
    settings = PublicSettings.from_env()
    assert (
        settings.report_publish,
        settings.report_expiry_delete,
        settings.report_sweep,
    ) == (
        False,
        False,
        False,
    )
    assert settings.reports_enabled is False
    assert settings.report_problem() is None


@pytest.mark.parametrize("value", ["true", "yes", "2", "on", " 1x"])
def test_report_settings_reject_anything_but_0_or_1(
    clean_env: pytest.MonkeyPatch, value: str
) -> None:
    for name in FLAGS:
        clean_env.setenv(name, value)
        problem = PublicSettings.from_env().report_problem()
        assert problem == f"{name} must be 0 or 1"
        assert value.strip() not in problem.replace(name, "")
        clean_env.delenv(name)


def test_report_setting_on_requires_credentials(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("PUBLIC_REPORT_PUBLISH", "1")
    problem = PublicSettings.from_env().report_problem()
    assert problem == "R2 credentials are required when a PUBLIC_REPORT_* setting is 1"


def test_create_app_refuses_invalid_report_settings(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="R2 credentials are required"):
        report_client(tmp_path, None, report_publish=True)
    with pytest.raises(ValueError, match="must be 0 or 1"):
        report_client(tmp_path, None, report_sweep=None)


def test_all_settings_off_sends_zero_requests_even_with_credentials(
    tmp_path: Path,
) -> None:
    with fake_s3() as fake:
        client = report_client(tmp_path, fake)
        assert client.app.state.reports is None
        _token, submission = complete_submission(client, tmp_path)
        assert submission.status == "complete"
        expire(client.app.state.store, submission.id)
        client.app.state.service.maintain(datetime.now(UTC))
        assert fake.requests == []
    root = tmp_path / "public"
    assert not (root / "report_publications").exists()
    assert not (root / "report_staging").exists()


def test_publish_only_never_deletes(tmp_path: Path) -> None:
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, report_publish=True)
        publisher = client.app.state.reports
        assert publisher.deleter is None and publisher.sweeper is None
        assert not hasattr(publisher.writer, "delete")
        _token, submission = complete_submission(client, tmp_path)
        drain(publisher)
        expire(client.app.state.store, submission.id)
        client.app.state.service.maintain(datetime.now(UTC))
        drain(publisher)
        record = publisher.publications.get(submission.id)
        assert record.state == "delete_pending"
        assert "DELETE" not in methods(fake)
        assert len(run_keys(fake, submission.id)) == 1


def test_publish_verifies_every_object_and_commits_bundle_last(tmp_path: Path) -> None:
    with fake_s3() as fake:
        client = report_client(
            tmp_path, fake, report_publish=True, report_expiry_delete=True
        )
        publisher = client.app.state.reports
        _token, submission = complete_submission(client, tmp_path)
        analysis = Path(submission.analysis_dir)
        before = folder_digest(analysis)
        drain(publisher)
        record = publisher.publications.get(submission.id)
        assert record.state == "verified"
        runs = run_keys(fake, submission.id)
        assert list(runs) == [record.run_id]
        prefix = f"reports/{submission.id}/{record.run_id}/"
        puts = [path for method, path in fake.requests if method == "PUT"]
        assert puts[-1].endswith(f"{prefix}bundle.json")
        bundle = json.loads(fake.objects[prefix + "bundle.json"].body)
        assert {item["path"] for item in bundle["files"]} | {"bundle.json"} == set(
            runs[record.run_id]
        )
        for item in bundle["files"]:
            stored = fake.objects[prefix + item["path"]]
            assert len(stored.body) == item["published_size"]
            assert stored.metadata["x-amz-meta-sha256"] == item["published_sha256"]
        gets = [
            path for method, path in fake.requests if method == "GET" and prefix in path
        ]
        assert len(gets) == len(bundle["files"]) + 1
        assert record.object_count == len(bundle["files"]) + 1
        assert folder_digest(analysis) == before


def test_transient_failure_backs_off_and_resumes_the_same_run(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
    with fake_s3() as fake:
        client = report_client(
            tmp_path, fake, clock=clock, report_publish=True, report_expiry_delete=True
        )
        publisher = client.app.state.reports
        _token, submission = complete_submission(client, tmp_path)
        _make_due(publisher, submission.id, clock)
        original_put = publisher.writer.put_file
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("storage put failed (503)")
            return original_put(*args, **kwargs)

        publisher.writer.put_file = flaky
        assert publisher.run_once() is True
        record = publisher.publications.get(submission.id)
        assert (record.state, record.last_error, record.attempts) == (
            "pending",
            "status_5xx",
            1,
        )
        assert record.next_attempt_at == "2026-11-09T12:01:00Z"
        run_id = record.attempt_run_id
        assert publisher.run_once() is False
        clock.advance(seconds=61)
        drain(publisher)
        record = publisher.publications.get(submission.id)
        assert record.state == "verified" and record.run_id == run_id
        assert list(run_keys(fake, submission.id)) == [run_id]


def test_read_back_mismatch_is_checksum_and_starts_a_new_run(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, clock=clock, report_publish=True)
        publisher = client.app.state.reports
        _token, submission = complete_submission(client, tmp_path)
        _make_due(publisher, submission.id, clock)
        original_put = publisher.writer.put_file
        corrupted: list[str] = []

        def corrupt_first(key, *args, **kwargs):
            result = original_put(key, *args, **kwargs)
            if not corrupted:
                fake.objects[key].body = fake.objects[key].body[:-1] + b"\x00"
                corrupted.append(key)
            return result

        publisher.writer.put_file = corrupt_first
        publisher.run_once()
        record = publisher.publications.get(submission.id)
        assert (record.last_error, record.attempt_run_id) == ("checksum", None)
        first_runs = set(run_keys(fake, submission.id))
        assert len(first_runs) == 1
        clock.advance(seconds=61)
        drain(publisher)
        record = publisher.publications.get(submission.id)
        assert record.state == "verified"
        assert record.run_id not in first_runs


def test_truncated_read_back_is_a_network_failure_on_the_same_run(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, clock=clock, report_publish=True)
        publisher = client.app.state.reports
        _token, submission = complete_submission(client, tmp_path)
        _make_due(publisher, submission.id, clock)
        fake.truncate_next_get = True
        publisher.run_once()
        record = publisher.publications.get(submission.id)
        assert record.last_error == "network" and record.attempt_run_id is not None
        clock.advance(seconds=61)
        drain(publisher)
        final = publisher.publications.get(submission.id)
        assert final.state == "verified" and final.run_id == record.attempt_run_id


def test_existing_object_with_other_bytes_is_a_conflict_never_overwritten(
    tmp_path: Path,
) -> None:
    clock = Clock(datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, clock=clock, report_publish=True)
        publisher = client.app.state.reports
        _token, submission = complete_submission(client, tmp_path)
        _make_due(publisher, submission.id, clock)
        original_put = publisher.writer.put_file
        calls = {"n": 0}

        def fail_second(key, *args, **kwargs):
            calls["n"] += 1
            result = original_put(key, *args, **kwargs)
            if calls["n"] == 1:
                fake.objects[key].body = b"tampered"
                raise OSError("storage put failed (500)")
            return result

        publisher.writer.put_file = fail_second
        publisher.run_once()
        tampered_run = publisher.publications.get(submission.id).attempt_run_id
        clock.advance(seconds=61)
        publisher.run_once()
        record = publisher.publications.get(submission.id)
        assert record.last_error == "conflict" and record.attempt_run_id is None
        tampered = [k for k in fake.objects if tampered_run in k]
        assert any(fake.objects[k].body == b"tampered" for k in tampered)
        clock.advance(seconds=301)
        drain(publisher)
        record = publisher.publications.get(submission.id)
        assert record.state == "verified" and record.run_id != tampered_run


def test_local_change_before_commit_is_local_changed(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, clock=clock, report_publish=True)
        publisher = client.app.state.reports
        _token, submission = complete_submission(client, tmp_path)
        _make_due(publisher, submission.id, clock)
        target = Path(submission.analysis_dir) / "match_identity.json"
        original_put = publisher.writer.put_file

        def change_local(key, *args, **kwargs):
            result = original_put(key, *args, **kwargs)
            if key.endswith("vision_manifest.json"):
                target.write_text('{"stage_id": "changed"}', encoding="utf-8")
            return result

        publisher.writer.put_file = change_local
        publisher.run_once()
        record = publisher.publications.get(submission.id)
        assert record.last_error == "local_changed" and record.state == "pending"
        assert not any(k.endswith("bundle.json") for k in fake.objects)
        publisher.writer.put_file = original_put
        clock.advance(seconds=61)
        drain(publisher)
        assert publisher.publications.get(submission.id).state == "verified"


def test_content_policy_blocks_before_any_put_and_retry_blocked(tmp_path: Path) -> None:
    from public_site.report_ops import retry_blocked

    with fake_s3() as fake:
        client = report_client(tmp_path, fake, report_publish=True)
        publisher = client.app.state.reports
        _token, submission = complete_submission(client, tmp_path)
        llm_view = next(
            Path(submission.analysis_dir).glob("coach_inputs/*.llm_view.json")
        )
        llm_view.write_text(
            json.dumps({"note": "see /Users/someone/file"}), encoding="utf-8"
        )
        drain(publisher)
        record = publisher.publications.get(submission.id)
        assert (record.state, record.blocked_rule) == ("blocked", "absolute_path")
        assert "PUT" not in methods(fake)
        assert not (tmp_path / "public" / "report_staging" / submission.id).exists()
        llm_view.write_text(json.dumps({"note": "clean"}), encoding="utf-8")
        assert retry_blocked(client.app.state.settings, submission.id) is True
        drain(publisher)
        assert publisher.publications.get(submission.id).state == "verified"


def test_expiry_during_upload_aborts_the_commit(tmp_path: Path) -> None:
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, report_publish=True)
        publisher = client.app.state.reports
        store = client.app.state.store
        _token, submission = complete_submission(client, tmp_path)
        original_put = publisher.writer.put_file

        def expire_midway(key, *args, **kwargs):
            result = original_put(key, *args, **kwargs)
            if key.endswith("vision_manifest.json"):
                expire(store, submission.id)
                client.app.state.service.maintain(datetime.now(UTC))
            return result

        publisher.writer.put_file = expire_midway
        publisher.run_once()
        record = publisher.publications.get(submission.id)
        assert record.state == "delete_pending"
        assert not any(k.endswith("bundle.json") for k in fake.objects)


def test_expiry_deletion_removes_prefix_and_record(tmp_path: Path) -> None:
    with fake_s3() as fake:
        client = report_client(
            tmp_path, fake, report_publish=True, report_expiry_delete=True
        )
        publisher = client.app.state.reports
        _token, submission = complete_submission(client, tmp_path)
        drain(publisher)
        expire(client.app.state.store, submission.id)
        client.app.state.service.maintain(datetime.now(UTC))
        fake.requests.clear()
        drain(publisher)
        assert run_keys(fake, submission.id) == {}
        assert publisher.publications.get(submission.id).delete_empty_at
        settle(publisher)
        assert publisher.publications.get(submission.id) is None
        assert {m for m in methods(fake)} <= {"GET", "DELETE"}
        assert all(
            f"/reports/{submission.id}/" in p
            or "list-type" in p
            or p.endswith(fake.bucket)
            for _m, p in fake.requests
        )


def test_expiry_deletion_failure_backs_off(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
    with fake_s3() as fake:
        client = report_client(
            tmp_path, fake, clock=clock, report_publish=True, report_expiry_delete=True
        )
        publisher = client.app.state.reports
        _token, submission = complete_submission(client, tmp_path)
        _make_due(publisher, submission.id, clock)
        drain(publisher)
        publisher.submission_expired(submission.id)
        fake.fail_always = 503
        publisher.run_once()
        record = publisher.publications.get(submission.id)
        assert (record.state, record.delete_attempts) == ("delete_pending", 1)
        assert record.next_attempt_at == "2026-11-09T12:01:00Z"
        fake.fail_always = None
        clock.advance(seconds=61)
        drain(publisher)
        settle(publisher)
        assert publisher.publications.get(submission.id) is None


def test_startup_reconciles_records_of_expired_submissions(tmp_path: Path) -> None:
    with fake_s3() as fake:
        client = report_client(
            tmp_path, fake, report_publish=True, report_expiry_delete=True
        )
        publisher = client.app.state.reports
        store = client.app.state.store
        _token, submission = complete_submission(client, tmp_path)
        drain(publisher)
        expired = store.get(submission.id)
        expired.status = "expired"
        store.save_submission(expired)
        publisher.startup()
        assert publisher.publications.get(submission.id).state == "delete_pending"
        drain(publisher)
        assert run_keys(fake, submission.id) == {}


def test_restart_with_interrupted_run_starts_a_new_run(tmp_path: Path) -> None:
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, report_publish=True)
        publisher = client.app.state.reports
        _token, submission = complete_submission(client, tmp_path)
        store = PublicationStore(tmp_path / "public")
        record = store.get(submission.id)
        record.attempt_run_id = "20261101T000000Z-deadbeef"
        store.save(record)
        drain(publisher)
        record = store.get(submission.id)
        assert record.state == "verified"
        assert record.run_id != "20261101T000000Z-deadbeef"


def test_backoff_schedule_and_error_vocabulary() -> None:
    assert [publish_backoff(n) for n in range(1, 9)] == [
        60,
        300,
        900,
        3600,
        21600,
        86400,
        86400,
        86400,
    ]
    assert storage_error_code(OSError("storage put failed (checksum)")) == "checksum"
    assert storage_error_code(OSError("storage head failed (403)")) == "status_4xx"
    assert storage_error_code(OSError("storage get failed (502)")) == "status_5xx"
    assert (
        storage_error_code(OSError("storage put failed (network: URLError)")) == "network"
    )


def test_sixth_failure_logs_one_error_per_day(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 11, 9, 12, 0, tzinfo=UTC))
    sink = io.StringIO()
    handle = logger.add(sink, level="WARNING", format="{level} {message}")
    try:
        with fake_s3() as fake:
            client = report_client(tmp_path, fake, clock=clock, report_publish=True)
            publisher = client.app.state.reports
            _token, submission = complete_submission(client, tmp_path)
            _make_due(publisher, submission.id, clock)
            fake.fail_always = 503
            for _ in range(8):
                publisher.run_once()
                clock.advance(seconds=3600)
                _make_due(publisher, submission.id, clock)
    finally:
        logger.remove(handle)
    errors = [line for line in sink.getvalue().splitlines() if line.startswith("ERROR")]
    assert len(errors) == 1
    assert "attempts=6" in errors[0]


def test_logs_and_records_carry_no_secrets_paths_or_fingerprints(tmp_path: Path) -> None:
    sink = io.StringIO()
    handle = logger.add(sink, level="DEBUG", format="{message}")
    try:
        with fake_s3() as fake:
            client = report_client(
                tmp_path, fake, report_publish=True, report_expiry_delete=True
            )
            publisher = client.app.state.reports
            _token, submission = complete_submission(client, tmp_path)
            manifest = json.loads(
                (Path(submission.analysis_dir) / "vision_manifest.json").read_text()
            )
            fake.fail_next = [500]
            publisher.run_once()
            publisher.publications.update(submission.id, _due_now)
            drain(publisher)
            record_text = (
                tmp_path / "public" / "report_publications" / f"{submission.id}.json"
            ).read_text()
    finally:
        logger.remove(handle)
    text = sink.getvalue() + record_text
    for secret in (
        fake.secret,
        fake.endpoint,
        str(tmp_path),
        "turnstile-secret-value",
        manifest["video_identity"],
        manifest["analysis"]["analysis_id"],
        "ranked-replay-2026",
    ):
        assert secret not in text


def test_failing_r2_leaves_serving_identical_to_settings_off(tmp_path: Path) -> None:
    def serve(root: Path, **flags) -> dict:
        with fake_s3() as fake:
            fake.fail_always = 503
            client = report_client(root, fake, **flags)
            token, submission = complete_submission(client, root)
            if client.app.state.reports is not None:
                drain(client.app.state.reports)
            body = client.get(f"/api/submissions/{token}").json()
            frame = client.get(f"/api/submissions/{token}/moments/0/frame")
            clip = client.get(f"/api/submissions/{token}/moments/0/clip")
            return {
                "status": body["status"],
                "result": body["result"],
                "frame": (frame.status_code, frame.content),
                "clip": (clip.status_code, clip.content),
                "notices": submission.notices_sent,
            }

    off = serve(tmp_path / "off")
    on = serve(
        tmp_path / "on", report_publish=True, report_expiry_delete=True, report_sweep=True
    )
    assert on == off
    assert off["frame"][0] == 200


def _make_due(publisher, submission_id: str, clock: Clock) -> None:
    def change(record: ReportPublication | None) -> ReportPublication | None:
        if record is None:
            return None
        record.next_attempt_at = clock.now.strftime("%Y-%m-%dT%H:%M:%SZ")
        return record

    publisher.publications.update(submission_id, change)


def _due_now(record: ReportPublication | None) -> ReportPublication | None:
    if record is None:
        return None
    record.next_attempt_at = "2000-01-01T00:00:00Z"
    return record


def test_settings_repr_and_report_settings_helper(tmp_path: Path) -> None:
    with fake_s3() as fake:
        settings = report_settings(tmp_path, fake, report_publish=True)
        assert fake.secret not in repr(settings)
        assert settings.report_problem() is None


def test_hooks_never_raise_into_the_worker(tmp_path: Path) -> None:
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, report_publish=True)
        publisher = client.app.state.reports

        def boom(_submission_id: str) -> None:
            raise RuntimeError("disk full")

        publisher.submission_completed = boom
        _token, submission = complete_submission(client, tmp_path)
        assert submission.status == "complete"


def test_publisher_thread_starts_and_stops(tmp_path: Path) -> None:
    with fake_s3() as fake:
        client = report_client(tmp_path, fake, report_publish=True)
        publisher = client.app.state.reports
        _token, submission = complete_submission(client, tmp_path)
        done = threading.Event()
        original = publisher._mark_verified

        def mark(record, active):
            original(record, active)
            done.set()

        publisher._mark_verified = mark
        publisher.start()
        try:
            assert done.wait(20)
        finally:
            publisher.stop()
        assert publisher.publications.get(submission.id).state == "verified"
