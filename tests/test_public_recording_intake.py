"""Operator upload, verification, queueing and worker admission for review codes."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from game_language_helpers import FAKE_DETECTED_LANGUAGE, analyze_detects
from public_site.accounts import AccountStore
from public_site.app import create_app
from public_site.media_probe import ProbeFacts, ProbeRejected
from public_site.models import Submission
from public_site.payments import PaymentStore
from public_site.recording_publish import MISMATCH
from public_site.recording_service import handoff_reference
from public_site.recording_store import report_expires_at
from public_site.settings import PublicSettings
from public_site.worker import NO_COACHING_UNITS_EXIT_CODE
from test_public_payments import FakeCheckout
from test_public_recordings import _create, _login, _paid, _view
from test_public_site import _write_assessment, _write_coaching

from splatoon3_ai_coach.config.paths import default_config_path

CODE = "ABCD-1234-EFGH-5678"
BODY = b"0123456789abcdef"


class Pipeline:
    """Controllable probe and pipeline fakes; records every pipeline call."""

    def __init__(self) -> None:
        self.reject: str | None = None
        self.calls: list[list[str]] = []
        self.coached = False
        self.analyze_code = 0

    def probe(self, _path: Path) -> ProbeFacts:
        if self.reject:
            raise ProbeRejected(self.reject)
        return ProbeFacts(180.0, 1920, 1080, "mov,mp4,m4a,3gp,3g2,mj2")

    def run(self, argv: list[str]) -> int:
        self.calls.append(argv)
        if "analyze" in argv:
            return self.analyze_code
        if "coach-inputs" in argv:
            if not self.coached:
                return NO_COACHING_UNITS_EXIT_CODE
            _write_coaching(Path(argv[argv.index("coach-inputs") + 1]))
        if "coach-prototype" in argv:
            _write_assessment(Path(argv[argv.index("coach-prototype") + 1]))
        return 0


@pytest.fixture
def site(tmp_path: Path):
    """Use an explicitly selected test database; never load repository secrets."""
    url = os.environ.get("PUBLIC_DATABASE_URL", "")
    if "coach_test" not in url:
        pytest.skip("An isolated coach_test database is required")
    accounts, payments = AccountStore(url), PaymentStore(url)
    accounts.ensure_schema()
    accounts.clear()
    payments.ensure_schema()
    payments.clear()
    payments.set_price(500)
    pipeline = Pipeline()
    settings = PublicSettings(
        root=tmp_path / "public",
        static_dir=tmp_path / "no-dist",
        config_path=default_config_path(),
        review_codes_enabled=True,
        dev_mode=True,
        public_base_url="http://testserver",
        stripe_webhook_secret="whsec",
        guest_submissions_per_hour=100,
        account_submissions_per_hour=100,
        submissions_per_hour=100,
        max_video_bytes=1000,
    )
    checkout = FakeCheckout()
    app = create_app(
        settings,
        accounts=accounts,
        payments=payments,
        checkout=checkout,
        verifier=lambda token: token == "ok",
        probe=pipeline.probe,
        start_worker=False,
        run_command=analyze_detects(pipeline.run, language=FAKE_DETECTED_LANGUAGE),
    )
    with TestClient(app) as client:
        client.fake_checkout = checkout
        client.pipeline = pipeline
        yield client
    payments.clear()
    accounts.clear()


def _endpoint(created: dict) -> str:
    return f"/api/admin/recordings/{created['submission_id']}"


def _job(site: TestClient, created: dict) -> dict:
    return site.get(_endpoint(created)).json()["job"]


def _request(site: TestClient, created: dict, **changes):
    body = {
        "expected_version": _job(site, created)["version"],
        "filename": "recording.mp4",
        "content_type": "video/mp4",
        "size_bytes": len(BODY),
        **changes,
    }
    return site.post(_endpoint(created) + "/upload", json=body)


def _granted(site: TestClient, created: dict, **changes) -> dict:
    response = _request(site, created, **changes)
    assert response.status_code == 200, response.text
    return response.json()


def _put(site: TestClient, grant: dict, content: bytes = BODY) -> None:
    assert _put_status(site, grant, content) == 200


def _put_status(site: TestClient, grant: dict, content: bytes = BODY) -> int:
    upload = grant["upload"]
    return site.put(upload["url"], content=content, headers=upload["headers"]).status_code


def _cancel(site: TestClient, created: dict):
    return site.post(
        _endpoint(created) + "/upload/cancel",
        json={"expected_version": _job(site, created)["version"]},
    )


def _age_grants(site: TestClient, created: dict, seconds: int) -> None:
    """Pretend every upload link of this job was issued ``seconds`` ago."""
    issued = (datetime.now(UTC) - timedelta(seconds=seconds)).replace(microsecond=0)
    with site.app.state.service.recordings.database._conn() as conn:
        conn.execute(
            """UPDATE review_code_acquisition_events
               SET created_at = %s,
                   details = details || jsonb_build_object('issued_at', %s::text)
               WHERE submission_id = %s AND kind = 'recording_upload_granted'""",
            (issued, issued.isoformat(), created["submission_id"]),
        )


def _expire_grants(site: TestClient, created: dict) -> None:
    _age_grants(site, created, 2 * 60 * 60)


def _confirm(site: TestClient, created: dict, version: int):
    return site.post(
        _endpoint(created) + "/upload/confirm", json={"expected_version": version}
    )


def _ready_job(site: TestClient) -> dict:
    created = _paid(site)
    _login(site, admin=True)
    return created


def _verified(site: TestClient) -> dict:
    created = _ready_job(site)
    grant = _granted(site, created)
    _put(site, grant)
    assert _confirm(site, created, grant["version"]).status_code == 200
    return created


def _events(site: TestClient, created: dict, kind: str) -> int:
    events = site.app.state.service.recordings.database.detail(created["submission_id"])
    return sum(1 for event in events["events"] if event["kind"] == kind)


def _gate(site: TestClient, created: dict):
    return site.app.state.payments.get(created["submission_id"])


def _claim(site: TestClient):
    worker = site.app.state.worker
    return site.app.state.store.claim_oldest_queued(
        "test-worker", "2026-11-10T00:00:00Z", admit=worker._admit_review_code
    )


def _run_worker(site: TestClient, created: dict) -> Submission:
    worker = site.app.state.worker
    assert worker.tick()
    worker.join(timeout=30)
    return site.app.state.service.store.get(created["submission_id"])


def _row(site: TestClient, created: dict) -> dict:
    return site.app.state.service.recordings.database.get(created["submission_id"])


def _count_deliveries(site: TestClient, monkeypatch, log: list[str]) -> None:
    notices = site.app.state.service.notices
    deliver = notices.deliver

    def counted(
        submission: Submission, kind: str, token: str, reference: str | None = None
    ) -> None:
        log.append(f"notice:{kind}")
        deliver(submission, kind, token, reference)

    monkeypatch.setattr(notices, "deliver", counted)


def _assert_handed_off(site: TestClient, created: dict, done: Submission) -> None:
    row = _row(site, created)
    assert row["state"] == "ready" and row["review_code"] is None
    assert row["handoff_reference"] == (
        f"analysis:{created['submission_id']}:{done.processing_started_at}"
    )
    assert _events(site, created, "recording_handoff_verified") == 1


def test_upload_endpoints_require_an_admin(site) -> None:
    created = _paid(site)
    body = {
        "expected_version": 0,
        "filename": "a.mp4",
        "content_type": "video/mp4",
        "size_bytes": 8,
    }
    cancel = _endpoint(created) + "/upload/cancel"
    assert site.post(_endpoint(created) + "/upload", json=body).status_code == 401
    assert _confirm(site, created, 0).status_code == 401
    assert site.post(cancel, json={"expected_version": 0}).status_code == 401
    _login(site)
    assert site.post(_endpoint(created) + "/upload", json=body).status_code == 403
    assert _confirm(site, created, 0).status_code == 403
    assert site.post(cancel, json={"expected_version": 0}).status_code == 403
    assert site.app.state.service.store.get_video(created["submission_id"]) is None


def test_admin_grant_uses_the_fixed_key_and_never_the_operator_filename(site) -> None:
    created = _ready_job(site)
    grant = _granted(site, created, filename=f"{CODE} replay.MP4")
    video = site.app.state.service.store.get_video(created["submission_id"])
    assert video.object_key == f"submissions/{created['submission_id']}/source.mp4"
    assert video.original_filename == "source.mp4"
    assert video.content_type == "video/mp4" and video.size_bytes == len(BODY)
    assert CODE not in str(grant) and "ABCD" not in grant["upload"]["url"]
    headers = grant["upload"]["headers"]
    assert headers["Content-Type"] == "video/mp4"
    assert headers["If-None-Match"] == "*"
    assert headers["x-amz-meta-attempt"] == grant["attempt"]["attempt"]
    assert _job(site, created)["state"] == "recording"
    assert _gate(site, created).status == "received"


def test_mov_upload_uses_its_own_key_and_queues(site) -> None:
    created = _ready_job(site)
    grant = _granted(
        site, created, filename="Capture 2026.MOV", content_type="video/quicktime"
    )
    video = site.app.state.service.store.get_video(created["submission_id"])
    assert video.object_key == f"submissions/{created['submission_id']}/source.mov"
    assert video.original_filename == "source.mov"
    assert grant["upload"]["headers"]["Content-Type"] == "video/quicktime"
    _put(site, grant)
    assert _confirm(site, created, grant["version"]).status_code == 200
    assert _gate(site, created).status == "queued"
    assert site.app.state.service.store.get(created["submission_id"]).status == "queued"
    done = _run_worker(site, created)
    analyze = next(argv for argv in site.pipeline.calls if "analyze" in argv)
    assert analyze[analyze.index("analyze") + 1].endswith("/source.mov")
    assert done.status == "complete"


def test_upload_needs_a_paid_job_and_not_a_confirmed_target(site) -> None:
    created = _paid(site)
    other = _create(site, review_code="1111 2222 3333 4444", email="other@example.com")
    _login(site, admin=True)
    assert _request(site, created).status_code == 200
    body = {
        "expected_version": 0,
        "filename": "a.mp4",
        "content_type": "video/mp4",
        "size_bytes": 8,
    }
    assert site.post(_endpoint(other) + "/upload", json=body).status_code == 404
    assert site.app.state.service.store.get_video(other["submission_id"]) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"filename": "recording.mp4", "content_type": "video/quicktime"},
        {"filename": "recording.mov", "content_type": "video/mp4"},
        {"filename": "recording.mkv", "content_type": "video/x-matroska"},
        {"filename": "recording.mp4", "content_type": "text/plain"},
        {"size_bytes": 1001},
    ],
)
def test_invalid_declared_files_are_rejected_before_any_grant(site, changes) -> None:
    created = _ready_job(site)
    assert _request(site, created, **changes).status_code == 400
    assert _job(site, created)["state"] == "awaiting_recording"
    assert site.app.state.service.store.get_video(created["submission_id"]) is None


def test_repeated_grant_returns_the_same_attempt_without_new_events(site) -> None:
    created = _ready_job(site)
    first = _granted(site, created)
    again = _granted(site, created, expected_version=first["version"] - 1)
    assert again["upload"] == first["upload"]
    assert again["attempt"] == first["attempt"] and again["version"] == first["version"]
    for changes in (
        {"size_bytes": len(BODY) + 2},
        {"filename": "recording.mov", "content_type": "video/quicktime"},
    ):
        response = _request(site, created, **changes)
        assert response.status_code == 409 and "already open" in response.json()["detail"]
    assert _events(site, created, "recording_upload_granted") == 1
    video = site.app.state.service.store.get_video(created["submission_id"])
    assert video.object_key.endswith("/source.mp4") and video.size_bytes == len(BODY)
    _put(site, again)
    assert _confirm(site, created, first["version"]).status_code == 200
    storage = site.app.state.service.storage
    keys = [
        stat.key
        for stat in storage.list_objects(f"submissions/{created['submission_id']}/")
    ]
    assert keys == [f"submissions/{created['submission_id']}/source.mp4"]


def test_concurrent_grants_open_one_attempt(site) -> None:
    created = _ready_job(site)
    version = _job(site, created)["version"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(lambda _: _request(site, created, expected_version=version), range(4))
        )
    assert [response.status_code for response in responses] == [200] * 4
    attempts = {response.json()["attempt"]["attempt"] for response in responses}
    urls = {response.json()["upload"]["url"] for response in responses}
    assert len(attempts) == 1 and len(urls) == 1
    assert _events(site, created, "recording_upload_granted") == 1


def test_verified_upload_is_never_replaced(site) -> None:
    created = _ready_job(site)
    grant = _granted(site, created)
    _put(site, grant)
    assert _confirm(site, created, grant["version"]).status_code == 200
    assert _put_status(site, grant, b"x" * len(BODY)) == 412
    assert _request(site, created).status_code == 409
    _expire_grants(site, created)
    assert _request(site, created).status_code == 409
    key = f"submissions/{created['submission_id']}/source.mp4"
    assert site.app.state.service.storage.read_sha256(key)[0] == len(BODY)
    video = site.app.state.service.store.get_video(created["submission_id"])
    assert site.app.state.service.storage.read_sha256(key)[1] == video.sha256


def test_check_after_reload_verifies_without_a_new_grant(site) -> None:
    created = _ready_job(site)
    grant = _granted(site, created)
    _put(site, grant)
    detail = site.get(_endpoint(created)).json()
    assert detail["upload_attempt"]["status"] == "live"
    assert detail["upload_attempt"]["attempt"] == grant["attempt"]["attempt"]
    assert _confirm(site, created, detail["job"]["version"]).status_code == 200
    assert _events(site, created, "recording_upload_granted") == 1
    assert _gate(site, created).status == "queued"


def test_retry_after_an_ambiguous_transfer_reuses_the_link_and_confirms_by_head(site) -> None:
    created = _ready_job(site)
    first = _granted(site, created)
    _put(site, first)
    retry = _granted(site, created)
    assert retry["upload"] == first["upload"]
    assert retry["attempt"]["attempt"] == first["attempt"]["attempt"]
    assert _put_status(site, retry) == 412
    assert _events(site, created, "recording_upload_granted") == 1
    assert _confirm(site, created, retry["version"]).status_code == 200
    assert _job(site, created)["state"] == "uploading"
    assert _events(site, created, "recording_upload_verified") == 1


def test_an_expired_link_gets_no_new_grant_but_can_still_be_checked(site) -> None:
    created = _ready_job(site)
    grant = _granted(site, created)
    _age_grants(site, created, 900 + 60)
    attempt = site.get(_endpoint(created)).json()["upload_attempt"]
    assert attempt["status"] == "expired"
    refused = _request(site, created)
    assert refused.status_code == 409 and "can start after" in refused.json()["detail"]
    assert _events(site, created, "recording_upload_granted") == 1
    assert _confirm(site, created, grant["version"]).status_code == 400
    assert _events(site, created, "recording_upload_rejected") == 0
    assert site.get(_endpoint(created)).json()["upload_attempt"]["status"] == "expired"


def test_cancel_returns_to_awaiting_and_waits_out_the_old_link(site) -> None:
    created = _ready_job(site)
    first = _granted(site, created)
    assert _cancel(site, created).status_code == 200
    assert _job(site, created)["state"] == "awaiting_recording"
    assert _events(site, created, "recording_upload_abandoned") == 1
    assert _cancel(site, created).status_code == 409
    assert _confirm(site, created, _job(site, created)["version"]).status_code == 409
    refused = _request(site, created)
    assert refused.status_code == 409 and "can start after" in refused.json()["detail"]
    _age_grants(site, created, 900 + 900 - 60)
    assert _request(site, created).status_code == 409
    _age_grants(site, created, 900 + 900 + 1)
    second = _granted(site, created)
    assert second["attempt"]["attempt"] != first["attempt"]["attempt"]
    assert second["upload"]["url"] != first["upload"]["url"]
    assert _events(site, created, "recording_upload_granted") == 2


def _abandoned_then_replaced(site: TestClient) -> tuple[dict, dict, dict]:
    created = _ready_job(site)
    old = _granted(site, created)
    assert _cancel(site, created).status_code == 200
    _expire_grants(site, created)
    new = _granted(site, created)
    return created, old, new


def test_late_put_from_an_abandoned_attempt_cannot_overwrite_a_later_upload(site) -> None:
    created, old, new = _abandoned_then_replaced(site)
    _put(site, new)
    assert _put_status(site, old, b"L" * len(BODY)) == 412
    assert _confirm(site, created, new["version"]).status_code == 200
    key = f"submissions/{created['submission_id']}/source.mp4"
    video = site.app.state.service.store.get_video(created["submission_id"])
    assert site.app.state.service.storage.read_sha256(key)[1] == video.sha256
    assert site.app.state.service.storage.head(key).attempt == new["attempt"]["attempt"]


def test_late_put_from_an_abandoned_attempt_is_not_mistaken_for_the_new_one(site) -> None:
    created, old, new = _abandoned_then_replaced(site)
    _put(site, old, b"L" * len(BODY))
    assert _put_status(site, new) == 412
    response = _confirm(site, created, new["version"])
    assert response.status_code == 409
    assert "earlier upload attempt" in response.json()["detail"]
    _assert_unverified(site, created)
    assert _events(site, created, "recording_upload_verified") == 0


def _in_queue(site: TestClient, created: dict) -> bool:
    jobs = site.get("/api/admin/recordings").json()["jobs"]
    return created["submission_id"] in {job["submission_id"] for job in jobs}


def test_queue_keeps_waiting_and_failed_work_and_drops_processed_jobs(site) -> None:
    created = _ready_job(site)
    assert _in_queue(site, created)
    grant = _granted(site, created)
    assert _in_queue(site, created)
    _put(site, grant)
    assert _confirm(site, created, grant["version"]).status_code == 200
    assert _job(site, created)["state"] == "uploading"
    assert not _in_queue(site, created)
    done = _run_worker(site, created)
    assert done.status == "complete" and _row(site, created)["state"] == "ready"
    assert not _in_queue(site, created)


def test_queue_shows_a_recording_again_when_its_analysis_fails(site) -> None:
    site.pipeline.analyze_code = 1
    created = _verified(site)
    assert not _in_queue(site, created)
    assert _run_worker(site, created).status == "failed"
    assert _row(site, created)["state"] == "uploading"
    assert _in_queue(site, created)
    assert _flag_failed(site, created).status_code == 200
    assert _job(site, created)["state"] == "recording_failed"
    assert _in_queue(site, created)


def test_verified_upload_records_facts_and_queues_once(site) -> None:
    created = _verified(site)
    svc = site.app.state.service
    video = svc.store.get_video(created["submission_id"])
    assert (video.duration_seconds, video.width, video.height) == (180.0, 1920, 1080)
    assert video.upload_state == "stored"
    assert _job(site, created)["state"] == "uploading"
    assert _gate(site, created).status == "queued" and _gate(site, created).uploaded
    assert svc.store.get(created["submission_id"]).status == "queued"
    assert _view(site, created)["status"] == "queued"
    assert _events(site, created, "recording_upload_verified") == 1
    assert _events(site, created, "recording_analysis_queued") == 1
    assert svc.recordings.database.get(created["submission_id"])["review_code"] == CODE


def test_repeated_and_concurrent_confirmations_queue_once(site) -> None:
    created = _ready_job(site)
    grant = _granted(site, created)
    _put(site, grant)
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(
            pool.map(
                lambda _: _confirm(site, created, grant["version"]).status_code, range(4)
            )
        )
    assert codes == [200] * 4
    assert _confirm(site, created, grant["version"]).status_code == 200
    assert _events(site, created, "recording_upload_verified") == 1
    assert _events(site, created, "recording_analysis_queued") == 1
    assert _request(site, created).status_code == 409


def test_missing_object_is_not_eligible_and_keeps_the_attempt_open(site) -> None:
    created = _ready_job(site)
    grant = _granted(site, created)
    assert _confirm(site, created, grant["version"]).status_code == 400
    _assert_unverified(site, created)
    assert _events(site, created, "recording_upload_rejected") == 0
    _put(site, grant)
    assert _confirm(site, created, grant["version"]).status_code == 200


def test_wrong_size_is_rejected_and_not_eligible(site) -> None:
    created = _ready_job(site)
    grant = _granted(site, created)
    _put(site, grant, BODY[:-1])
    assert _confirm(site, created, grant["version"]).status_code == 400
    _assert_unverified(site, created)


def test_invalid_probe_is_rejected_deleted_and_can_be_reuploaded(site) -> None:
    created = _ready_job(site)
    grant = _granted(site, created)
    _put(site, grant)
    site.pipeline.reject = "This file could not be read as a video."
    response = _confirm(site, created, grant["version"])
    assert response.status_code == 400
    _assert_unverified(site, created)
    svc = site.app.state.service
    assert svc.storage.head(f"submissions/{created['submission_id']}/source.mp4") is None
    assert _events(site, created, "recording_upload_rejected") == 1
    site.pipeline.reject = None
    assert _request(site, created).status_code == 409
    _expire_grants(site, created)
    retry = _granted(site, created)
    _put(site, retry)
    assert _confirm(site, created, retry["version"]).status_code == 200
    assert _gate(site, created).status == "queued"


def _assert_unverified(site: TestClient, created: dict) -> None:
    svc = site.app.state.service
    assert _job(site, created)["state"] == "recording"
    assert _gate(site, created).status == "received"
    assert svc.store.get(created["submission_id"]).status == "received"
    assert not svc.recordings.database.queue_for_analysis(created["submission_id"])
    assert _claim(site) is None


def test_queue_transition_refuses_unpaid_and_unverified(site) -> None:
    created = _paid(site)
    database = site.app.state.service.recordings.database
    assert not database.queue_for_analysis(created["submission_id"])
    _login(site, admin=True)
    with database._conn() as conn:
        conn.execute(
            "UPDATE submission_gates SET payment_state='unpaid' WHERE submission_id=%s",
            (created["submission_id"],),
        )
        conn.execute(
            "UPDATE review_code_acquisitions SET state='uploading' "
            "WHERE submission_id=%s",
            (created["submission_id"],),
        )
    assert not database.queue_for_analysis(created["submission_id"])
    assert _confirm(site, created, _job(site, created)["version"]).status_code == 409
    assert _gate(site, created).status == "received"


def test_confirm_finishes_a_queue_projection_left_behind(site) -> None:
    created = _ready_job(site)
    grant = _granted(site, created)
    _put(site, grant)
    svc = site.app.state.service
    original = svc._queue
    svc._queue = lambda *args: (_ for _ in ()).throw(OSError("crash"))
    with pytest.raises(OSError):
        _confirm(site, created, grant["version"])
    svc._queue = original
    assert _gate(site, created).status == "queued"
    assert svc.store.get(created["submission_id"]).status == "received"
    assert _claim(site) is None
    assert _confirm(site, created, grant["version"]).status_code == 200
    assert svc.store.get(created["submission_id"]).status == "queued"
    assert _events(site, created, "recording_analysis_queued") == 1


def test_review_code_claimed_only_when_verified_and_queued(site) -> None:
    created = _paid(site)
    svc = site.app.state.service
    item = svc.store.get(created["submission_id"])
    item.status = "queued"
    svc.store.save_submission(item)
    assert _claim(site) is None
    assert svc.store.claim_oldest_queued("test-worker", "2026-11-10T00:00:00Z") is None
    item.status = "received"
    svc.store.save_submission(item)
    _login(site, admin=True)
    grant = _granted(site, created)
    _put(site, grant)
    assert _confirm(site, created, grant["version"]).status_code == 200
    assert svc.store.claim_oldest_queued("test-worker", "2026-11-10T00:00:00Z") is None
    claimed = _claim(site)
    assert claimed is not None and claimed.id == created["submission_id"]
    assert claimed.status == "processing"
    assert _claim(site) is None


def test_forged_local_records_without_database_verification_are_not_claimed(site) -> None:
    created = _ready_job(site)
    _put(site, _granted(site, created))
    svc = site.app.state.service
    video = svc.store.get_video(created["submission_id"])
    video.duration_seconds, video.width, video.height = 180.0, 1920, 1080
    video.upload_state = "stored"
    svc.store.save_video(video)
    item = svc.store.get(created["submission_id"])
    item.status = "queued"
    svc.store.save_submission(item)
    assert _claim(site) is None
    with svc.recordings.database._conn() as conn:
        conn.execute(
            "UPDATE submission_gates SET status='queued' WHERE submission_id=%s",
            (created["submission_id"],),
        )
    assert _claim(site) is None


def _flag_failed(site: TestClient, created: dict):
    return site.post(
        _endpoint(created) + "/actions",
        json={
            "action": "recording_failed",
            "expected_version": _job(site, created)["version"],
            "note": "Wrong match",
        },
    )


def test_verified_job_flagged_failed_before_claim_is_not_processed(site) -> None:
    created = _verified(site)
    svc = site.app.state.service
    assert _flag_failed(site, created).status_code == 200
    for _ in range(2):
        assert _job(site, created)["state"] == "recording_failed"
        assert _gate(site, created).status == "received"
        assert svc.store.get(created["submission_id"]).status == "received"
        assert _view(site, created)["status"] == "received"
        assert _claim(site) is None
        assert not site.app.state.worker.tick()
        svc.maintain(datetime.now(UTC))
    assert not site.pipeline.calls
    assert _row(site, created)["review_code"] == CODE


def test_failed_flag_is_refused_once_the_worker_has_claimed(site) -> None:
    created = _verified(site)
    assert _claim(site) is not None
    response = _flag_failed(site, created)
    assert response.status_code == 409
    assert _job(site, created)["state"] == "uploading"
    assert _gate(site, created).status == "queued"
    current = site.app.state.service.store.get(created["submission_id"])
    assert current.status == "processing"


def test_cancellation_left_behind_locally_is_repaired_by_reconcile(site) -> None:
    created = _verified(site)
    svc = site.app.state.service
    original = svc.recordings.refresh_projection
    svc.recordings.refresh_projection = lambda *args: None
    assert _flag_failed(site, created).status_code == 200
    svc.recordings.refresh_projection = original
    assert svc.store.get(created["submission_id"]).status == "queued"
    assert _claim(site) is None
    svc.maintain(datetime.now(UTC))
    assert svc.store.get(created["submission_id"]).status == "received"
    assert _view(site, created)["status"] == "received"


def test_worker_completes_verified_recording_through_existing_pipeline(
    site, monkeypatch
) -> None:
    created = _verified(site)
    log: list[str] = []
    _count_deliveries(site, monkeypatch, log)
    done = _run_worker(site, created)
    assert done.status == "complete" and done.outcome == "no_deaths"
    analyze = next(argv for argv in site.pipeline.calls if "analyze" in argv)
    assert analyze[analyze.index("analyze") + 1].endswith("/source.mp4")
    assert all(CODE not in " ".join(argv) for argv in site.pipeline.calls)
    assert not site.app.state.worker.tick()
    _assert_handed_off(site, created, done)
    assert _row(site, created)["handoff_reference"] == handoff_reference(done)
    assert log == ["notice:no_moments"]
    assert "no_moments" in done.notices_sent
    assert _view(site, created)["status"] == "complete"


def test_worker_refuses_a_swapped_object(site, tmp_path: Path) -> None:
    created = _verified(site)
    svc = site.app.state.service
    video = svc.store.get_video(created["submission_id"])
    swapped = tmp_path / "swap.mp4"
    swapped.write_bytes(b"not-the-verified-recording")
    svc.storage.put_file(video.object_key, swapped, "video/mp4")
    submission = svc.store.get(created["submission_id"])
    assert svc.recordings.intake.admits(submission) is False
    site.app.state.worker._execute(submission)
    failed = svc.store.get(created["submission_id"])
    assert failed.status == "failed"
    assert failed.error == MISMATCH
    assert site.pipeline.calls == []


def test_coached_completion_hands_off_then_announces_ready(site, monkeypatch) -> None:
    site.pipeline.coached = True
    created = _verified(site)
    log: list[str] = []
    _count_deliveries(site, monkeypatch, log)
    database = site.app.state.service.recordings.database
    confirm = database.confirm_recording_handoff

    def ordered(submission_id: str, reference: str) -> bool:
        log.append("handoff")
        return confirm(submission_id, reference)

    monkeypatch.setattr(database, "confirm_recording_handoff", ordered)
    done = _run_worker(site, created)
    assert done.status == "complete" and done.outcome == "coached"
    _assert_handed_off(site, created, done)
    assert log == ["handoff", "notice:ready"]
    assert "ready" in done.notices_sent


def test_analysis_failure_keeps_the_code_and_never_hands_off(site, monkeypatch) -> None:
    site.pipeline.analyze_code = 1
    created = _verified(site)
    log: list[str] = []
    _count_deliveries(site, monkeypatch, log)
    done = _run_worker(site, created)
    assert done.status == "failed"
    site.app.state.service.maintain(datetime.now(UTC))
    row = _row(site, created)
    assert row["state"] == "uploading" and row["review_code"] == CODE
    assert row["recording_handoff_at"] is None
    assert _events(site, created, "recording_handoff_verified") == 0
    assert "notice:ready" not in log and "notice:no_moments" not in log


def test_failed_handoff_holds_the_notice_until_reconcile_retries(
    site, monkeypatch
) -> None:
    created = _verified(site)
    svc = site.app.state.service
    log: list[str] = []
    _count_deliveries(site, monkeypatch, log)
    database = svc.recordings.database
    confirm = database.confirm_recording_handoff
    references: list[str] = []

    def unavailable(submission_id: str, reference: str) -> bool:
        references.append(reference)
        raise psycopg.OperationalError("database unavailable")

    monkeypatch.setattr(database, "confirm_recording_handoff", unavailable)
    done = _run_worker(site, created)
    assert done.status == "complete"
    row = _row(site, created)
    assert row["state"] == "uploading" and row["review_code"] == CODE
    assert row["recording_handoff_at"] is None
    assert log == [] and "no_moments" not in done.notices_sent
    svc.maintain(datetime.now(UTC))
    assert log == [] and _row(site, created)["review_code"] == CODE

    def recorded(submission_id: str, reference: str) -> bool:
        references.append(reference)
        return confirm(submission_id, reference)

    monkeypatch.setattr(database, "confirm_recording_handoff", recorded)
    svc.maintain(datetime.now(UTC))
    _assert_handed_off(site, created, done)
    assert set(references) == {handoff_reference(done)}
    assert log == ["notice:no_moments"]
    svc.maintain(datetime.now(UTC))
    assert log == ["notice:no_moments"]
    assert _events(site, created, "recording_handoff_verified") == 1


def test_duplicate_completion_creates_one_handoff_and_one_notice(
    site, monkeypatch
) -> None:
    created = _verified(site)
    svc = site.app.state.service
    log: list[str] = []
    _count_deliveries(site, monkeypatch, log)
    done = _run_worker(site, created)
    assert svc.recordings.hand_off(done)
    svc.note_terminal(done, "no_moments")
    for _ in range(2):
        svc.maintain(datetime.now(UTC))
    stale = done.model_copy(update={"processing_started_at": "2000-01-01T00:00:00Z"})
    assert not svc.recordings.hand_off(stale)
    _assert_handed_off(site, created, done)
    assert log == ["notice:no_moments"]


def test_late_handoff_receives_the_full_report_retention(site) -> None:
    created = _verified(site)
    svc = site.app.state.service
    long_ago = "2020-01-01T00:00:00Z"

    def backdate(item: Submission) -> None:
        item.created_at, item.expires_at = long_ago, long_ago

    svc.store.update(created["submission_id"], backdate)
    done = _run_worker(site, created)
    row = _row(site, created)
    expires = report_expires_at(row)
    handoff = row["recording_handoff_at"].astimezone(UTC)
    assert expires == (handoff + timedelta(days=row["retention_days"])).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert svc.store.get(created["submission_id"]).expires_at == expires
    assert _view(site, created)["expires_at"] == expires
    expiry = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    svc.maintain(datetime.now(UTC))
    svc.maintain(expiry - timedelta(seconds=1))
    assert _row(site, created)["report_expired_at"] is None
    assert svc.store.get(created["submission_id"]).status == "complete"
    assert Path(done.analysis_dir).exists()
    svc.maintain(expiry + timedelta(seconds=1))
    assert _row(site, created)["report_expired_at"] is not None
    assert svc.store.get(created["submission_id"]).status == "expired"


def test_normal_video_completion_never_touches_review_code_handoff(
    site, monkeypatch
) -> None:
    created = _paid(site)
    svc = site.app.state.service
    item = svc.store.get(created["submission_id"])
    video = item.model_copy(
        update={
            "id": "f" * 32,
            "input_type": "video",
            "source": "upload",
            "access_token_hash": "e" * 64,
            "status": "processing",
            "processing_started_at": "2026-11-10T00:00:00Z",
        }
    )
    svc.store.save_submission(video)
    svc.notices.seal(video.id, "video-token")

    def forbidden(*_args) -> bool:
        raise AssertionError("review-code handoff used for a normal video")

    monkeypatch.setattr(svc.recordings, "hand_off", forbidden)
    monkeypatch.setattr(svc.recordings, "report_available", forbidden)
    site.app.state.worker._complete(video, "no_deaths", FAKE_DETECTED_LANGUAGE)
    done = svc.store.get(video.id)
    assert done.status == "complete" and "no_moments" in done.notices_sent


def test_recovery_fails_interrupted_review_code_and_never_reclaims_it(site) -> None:
    created = _verified(site)
    assert _claim(site) is not None
    failed = site.app.state.worker.recover()
    assert [item.id for item in failed] == [created["submission_id"]]
    assert site.app.state.service.store.get(created["submission_id"]).status == "failed"
    assert _claim(site) is None
    site.app.state.service.maintain(datetime.now(UTC))
    row = site.app.state.service.recordings.database.get(created["submission_id"])
    assert row["review_code"] == CODE and row["recording_handoff_at"] is None
    assert row["state"] == "uploading"


def test_recovery_resumes_a_verified_publication_without_pipeline_ready(site) -> None:
    from public_site.report_publications import PublicationStore, ReportPublication

    created = _verified(site)
    claimed = _claim(site)
    assert claimed is not None
    store = site.app.state.service.store
    store.update(
        created["submission_id"],
        lambda item: (
            setattr(item, "status", "processing"),
            setattr(item, "step", "ready"),
            setattr(item, "outcome", "coached"),
            setattr(item, "analysis_dir", None),
            setattr(item, "error", None),
        ),
    )
    PublicationStore(site.app.state.settings.root).save(
        ReportPublication(
            submission_id=created["submission_id"],
            state="verified",
            run_id="20261117T000000Z-deadbeef",
            attempt_generation=1,
            published_at="2026-11-17T00:00:00Z",
            object_count=1,
            total_bytes=10,
            bundle_sha256="a" * 64,
        )
    )
    failed = site.app.state.worker.recover()
    assert failed == []
    done = store.get(created["submission_id"])
    assert done.status == "complete" and done.error is None
    assert done.outcome == "coached"
    _assert_handed_off(site, created, done)
    site.app.state.service.maintain(datetime.now(UTC))
    assert _events(site, created, "recording_handoff_verified") == 1


def test_maintain_restores_false_failed_when_publication_is_verified(site) -> None:
    from public_site.errors import REVIEW_CODE_INTERRUPTED
    from public_site.report_publications import PublicationStore, ReportPublication

    created = _verified(site)
    claimed = _claim(site)
    assert claimed is not None
    store = site.app.state.service.store
    store.update(
        created["submission_id"],
        lambda item: (
            setattr(item, "status", "failed"),
            setattr(item, "step", "ready"),
            setattr(item, "outcome", "coached"),
            setattr(item, "error", REVIEW_CODE_INTERRUPTED),
            setattr(item, "analysis_dir", None),
            setattr(item, "notices_sent", ["received", "failed"]),
        ),
    )
    PublicationStore(site.app.state.settings.root).save(
        ReportPublication(
            submission_id=created["submission_id"],
            state="verified",
            run_id="20261117T000000Z-cafebabe",
            attempt_generation=1,
            published_at="2026-11-17T00:00:00Z",
            object_count=1,
            total_bytes=10,
            bundle_sha256="b" * 64,
        )
    )
    site.app.state.service.maintain(datetime.now(UTC))
    done = store.get(created["submission_id"])
    assert done.status == "complete" and done.error is None
    _assert_handed_off(site, created, done)
    site.app.state.service.maintain(datetime.now(UTC))
    assert _events(site, created, "recording_handoff_verified") == 1


def test_rerecording_after_failed_analysis_is_refused_not_silently_ignored(site) -> None:
    created = _verified(site)
    assert _claim(site) is not None
    site.app.state.worker.recover()
    job = _job(site, created)
    endpoint = _endpoint(created) + "/actions"
    failed = site.post(
        endpoint,
        json={
            "action": "recording_failed",
            "expected_version": job["version"],
            "note": "Analysis interrupted; re-record",
        },
    )
    job = failed.json()["job"]
    corrected = site.post(
        endpoint,
        json={
            "action": "correct_code",
            "expected_version": job["version"],
            "note": "Same code",
            "review_code": CODE,
        },
    )
    assert corrected.status_code == 200
    _expire_grants(site, created)
    grant = _granted(site, created)
    _put(site, grant)
    response = _confirm(site, created, grant["version"])
    assert response.status_code == 409
    assert site.app.state.service.store.get(created["submission_id"]).status == "failed"
    assert _claim(site) is None


def test_normal_video_is_still_claimed_beside_review_code_records(site) -> None:
    created = _paid(site)
    svc = site.app.state.service
    item = svc.store.get(created["submission_id"])
    item.status = "queued"
    svc.store.save_submission(item)
    video = item.model_copy(
        update={
            "id": "f" * 32,
            "input_type": "video",
            "source": "upload",
            "access_token_hash": "e" * 64,
        }
    )
    svc.store.save_submission(video)
    assert svc.store.claim_oldest_queued("w", "2026-11-10T00:00:00Z").id == "f" * 32
