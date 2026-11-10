"""Operator upload, verification, queueing and worker admission for review codes."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from game_language_helpers import FAKE_DETECTED_LANGUAGE, analyze_detects
from public_site.accounts import AccountStore
from public_site.app import create_app
from public_site.media_probe import ProbeFacts, ProbeRejected
from public_site.payments import PaymentStore
from public_site.settings import PublicSettings
from public_site.worker import NO_COACHING_UNITS_EXIT_CODE
from test_public_payments import FakeCheckout
from test_public_recordings import _login, _paid, _view

from splatoon3_ai_coach.config.paths import default_config_path

CODE = "ABCD-1234-EFGH-5678"
BODY = b"0123456789abcdef"


class Pipeline:
    """Controllable probe and pipeline fakes; records every pipeline call."""

    def __init__(self) -> None:
        self.reject: str | None = None
        self.calls: list[list[str]] = []

    def probe(self, _path: Path) -> ProbeFacts:
        if self.reject:
            raise ProbeRejected(self.reject)
        return ProbeFacts(180.0, 1920, 1080, "mov,mp4,m4a,3gp,3g2,mj2")

    def run(self, argv: list[str]) -> int:
        self.calls.append(argv)
        return NO_COACHING_UNITS_EXIT_CODE if "coach-inputs" in argv else 0


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


def _confirm_target(site: TestClient, created: dict) -> dict:
    job = _job(site, created)
    response = site.post(
        _endpoint(created) + "/actions",
        json={
            "action": "confirm_target",
            "expected_version": job["version"],
            "note": "Observed on replay",
            "confirmed_player_name": "Observed player",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["job"]


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
    upload = grant["upload"]
    response = site.put(upload["url"], content=content, headers=upload["headers"])
    assert response.status_code == 200, response.text


def _confirm(site: TestClient, created: dict, version: int):
    return site.post(
        _endpoint(created) + "/upload/confirm", json={"expected_version": version}
    )


def _ready_job(site: TestClient) -> dict:
    created = _paid(site)
    _login(site, admin=True)
    _confirm_target(site, created)
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


def test_upload_endpoints_require_an_admin(site) -> None:
    created = _paid(site)
    body = {
        "expected_version": 0,
        "filename": "a.mp4",
        "content_type": "video/mp4",
        "size_bytes": 8,
    }
    assert site.post(_endpoint(created) + "/upload", json=body).status_code == 401
    assert _confirm(site, created, 0).status_code == 401
    _login(site)
    assert site.post(_endpoint(created) + "/upload", json=body).status_code == 403
    assert _confirm(site, created, 0).status_code == 403
    assert site.app.state.service.store.get_video(created["submission_id"]) is None


def test_admin_grant_uses_the_fixed_key_and_never_the_operator_filename(site) -> None:
    created = _ready_job(site)
    grant = _granted(site, created, filename=f"{CODE} replay.MP4")
    video = site.app.state.service.store.get_video(created["submission_id"])
    assert video.object_key == f"submissions/{created['submission_id']}/source.mp4"
    assert video.original_filename == "source.mp4"
    assert video.content_type == "video/mp4" and video.size_bytes == len(BODY)
    assert CODE not in str(grant) and "ABCD" not in grant["upload"]["url"]
    assert grant["upload"]["headers"]["Content-Type"] == "video/mp4"
    assert _job(site, created)["state"] == "recording"
    assert _gate(site, created).status == "received"


def test_upload_needs_paid_job_and_confirmed_target(site) -> None:
    created = _paid(site)
    _login(site, admin=True)
    assert _request(site, created).status_code == 409
    other = site.post(
        "/api/review-code-submissions",
        json={
            "turnstile_token": "ok",
            "review_code": "1111 2222 3333 4444",
            "target_player_name": "Someone",
            "email": "other@example.com",
        },
    ).json()
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
        {"filename": "recording.mov", "content_type": "video/quicktime"},
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


def test_repeated_grants_are_versioned_and_keep_one_object(site) -> None:
    created = _ready_job(site)
    first = _granted(site, created)
    stale = _request(site, created, expected_version=first["version"] - 1)
    assert stale.status_code == 409
    second = _granted(site, created, size_bytes=len(BODY) + 2)
    assert second["version"] == first["version"] + 1
    _put(site, second, BODY + b"!!")
    assert _confirm(site, created, first["version"]).status_code == 409
    assert _confirm(site, created, second["version"]).status_code == 200
    storage = site.app.state.service.storage
    keys = [
        stat.key
        for stat in storage.list_objects(f"submissions/{created['submission_id']}/")
    ]
    assert keys == [f"submissions/{created['submission_id']}/source.mp4"]
    assert _events(site, created, "recording_upload_granted") == 2


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


def test_missing_object_is_rejected_and_not_eligible(site) -> None:
    created = _ready_job(site)
    grant = _granted(site, created)
    assert _confirm(site, created, grant["version"]).status_code == 400
    _assert_unverified(site, created)


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


def test_queue_transition_refuses_unpaid_unconfirmed_and_unverified(site) -> None:
    created = _paid(site)
    database = site.app.state.service.recordings.database
    assert not database.queue_for_analysis(created["submission_id"])
    _login(site, admin=True)
    _confirm_target(site, created)
    assert not database.queue_for_analysis(created["submission_id"])
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
    _confirm_target(site, created)
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


def test_verified_job_flagged_failed_before_claim_is_not_processed(site) -> None:
    created = _verified(site)
    job = _job(site, created)
    response = site.post(
        _endpoint(created) + "/actions",
        json={
            "action": "recording_failed",
            "expected_version": job["version"],
            "note": "Wrong match",
        },
    )
    assert response.status_code == 200
    assert _claim(site) is None


def test_worker_completes_verified_recording_through_existing_pipeline(site) -> None:
    created = _verified(site)
    worker = site.app.state.worker
    assert worker.tick()
    worker.join(timeout=30)
    svc = site.app.state.service
    done = svc.store.get(created["submission_id"])
    assert done.status == "complete" and done.outcome == "no_deaths"
    analyze = next(argv for argv in site.pipeline.calls if "analyze" in argv)
    assert analyze[analyze.index("analyze") + 1].endswith("/source.mp4")
    assert all(CODE not in " ".join(argv) for argv in site.pipeline.calls)
    assert not worker.tick()
    row = svc.recordings.database.get(created["submission_id"])
    assert row["state"] == "uploading" and row["review_code"] == CODE


def test_recovery_fails_interrupted_review_code_and_never_reclaims_it(site) -> None:
    created = _verified(site)
    assert _claim(site) is not None
    failed = site.app.state.worker.recover()
    assert [item.id for item in failed] == [created["submission_id"]]
    assert site.app.state.service.store.get(created["submission_id"]).status == "failed"
    assert _claim(site) is None
    row = site.app.state.service.recordings.database.get(created["submission_id"])
    assert row["review_code"] == CODE and row["recording_handoff_at"] is None


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
    _confirm_target(site, created)
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
