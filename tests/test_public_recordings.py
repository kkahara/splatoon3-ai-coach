"""P1-A acquisition, payment, contact, recovery and authorization contracts."""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from public_site.accounts import AccountStore
from public_site.app import create_app
from public_site.models import Submission
from public_site.payments import PaymentStore
from public_site.recording_models import ReviewCodeDraft, normalize_code
from public_site.recording_store import report_expires_at
from public_site.settings import PublicSettings
from public_site.tokens import hash_token
from test_public_payments import FakeCheckout, _event, _webhook

from splatoon3_ai_coach.config.paths import default_config_path


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
    )
    checkout = FakeCheckout()
    app = create_app(
        settings,
        accounts=accounts,
        payments=payments,
        checkout=checkout,
        verifier=lambda token: token == "ok",
        start_worker=False,
        run_command=lambda args: pytest.fail(f"Unexpected pipeline call: {args}"),
    )
    with TestClient(app) as client:
        client.fake_checkout = checkout
        yield client
    payments.clear()
    accounts.clear()


def _draft(**changes) -> dict:
    return {
        "turnstile_token": "ok",
        "review_code": "abcd 1234 efgh 5678",
        "email": "player@example.com",
        **changes,
    }


def _create(site: TestClient, **changes) -> dict:
    response = site.post("/api/review-code-submissions", json=_draft(**changes))
    assert response.status_code == 200, response.text
    return response.json()


def _verify_guest(site: TestClient) -> None:
    challenge = _latest_challenge(site)
    response = site.post("/api/notices/confirm", json={"token": challenge})
    assert response.status_code == 200, response.text


def _latest_challenge(site: TestClient) -> str:
    root = site.app.state.settings.root / "outbox"
    path = max(root.glob("confirm.*.txt"), key=lambda item: item.stat().st_mtime_ns)
    text = path.read_text(encoding="utf-8")
    return re.search(r"/confirm-notices/(\S+)", text).group(1)


def _age_challenges(site: TestClient, submission_id: str) -> None:
    svc = site.app.state.service
    with svc.recordings.database._conn() as conn:
        conn.execute(
            """UPDATE guest_email_challenges
               SET created_at = now() - interval '16 minutes'
               WHERE submission_id = %s""",
            (submission_id,),
        )


def _confirm(site: TestClient, token: str) -> int:
    return site.post("/api/notices/confirm", json={"token": token}).status_code


def _checkout(site: TestClient, created: dict):
    response = site.post(f"/api/submissions/{created['token']}/checkout")
    assert response.status_code == 200, response.text
    return site.fake_checkout.created[-1]


def _paid(site: TestClient) -> dict:
    created = _create(site)
    _verify_guest(site)
    session = _checkout(site, created)
    assert _webhook(site, _event(session)).status_code == 200
    return created


def _view(site: TestClient, created: dict) -> dict:
    return site.get(f"/api/submissions/{created['token']}").json()


def _login(site: TestClient, *, admin: bool = False) -> str:
    accounts = site.app.state.accounts
    now = datetime.now(UTC)
    challenge = accounts.register(
        name="Different account name",
        email="owner@example.com",
        password="correct-horse",
        now=now,
    )
    account, token = accounts.verify(challenge, now)
    if admin:
        accounts.set_role(account.email, "admin")
    site.cookies.set("s3_session", token)
    return account.id


@pytest.mark.parametrize(
    "raw", ["abcd1234efgh5678", "abcd-1234-efgh-5678", "ABCD 1234 EFGH 5678"]
)
def test_only_harmless_code_formatting_is_normalized(raw: str) -> None:
    assert normalize_code(raw) == "ABCD-1234-EFGH-5678"


@pytest.mark.parametrize(
    "raw",
    [
        "ABCD/1234/EFGH/5678",
        "ABCD1234EFGH5678!",
        "ＡBCD1234EFGH5678",
        "ABCD1234EFGH56789",
        "",
    ],
)
def test_bad_code_is_rejected_without_silent_repair(raw: str) -> None:
    with pytest.raises(ValueError):
        normalize_code(raw)


def test_draft_takes_no_player_name_language_or_slot() -> None:
    with pytest.raises(ValueError):
        ReviewCodeDraft(**_draft(target_player_name="Ika"))
    with pytest.raises(ValueError):
        ReviewCodeDraft(**_draft(target_slot=0))
    with pytest.raises(ValueError):
        ReviewCodeDraft(**_draft(language="en"))


def test_pending_submission_is_durable_but_cannot_checkout_or_queue(site) -> None:
    created = _create(site)
    svc = site.app.state.service
    row = svc.recordings.database.get(created["submission_id"])
    assert row["review_code"] == "ABCD-1234-EFGH-5678"
    assert row["submission_snapshot"]["source"] == "nintendo_review_code"
    assert row["contact_verified_at"] is None
    assert svc.store.get_video(created["submission_id"]) is None
    assert "upload" not in created
    assert site.post(f"/api/submissions/{created['token']}/checkout").status_code == 409
    assert _view(site, created)["acquisition"]["state"] == "pending_payment"
    assert "ABCD-1234" not in str(_view(site, created))
    assert not svc.recordings.database.list_jobs()


def test_verified_guest_paid_retries_activate_one_job_and_never_analyze(site) -> None:
    created = _paid(site)
    session = site.fake_checkout.created[-1]
    for _ in range(3):
        assert _webhook(site, _event(session)).status_code == 200
    svc = site.app.state.service
    svc.maintain(datetime.now(UTC))
    view = _view(site, created)
    assert view["payment_state"] == "paid"
    assert view["status"] == "received"
    assert view["acquisition"]["state"] == "awaiting_recording"
    assert view["acquisition"]["contact_verified"]
    assert "target_confirmed" not in view["acquisition"]
    assert not svc.payments.mark_uploaded(created["submission_id"])
    assert not svc.payments.try_queue(created["submission_id"])
    assert site.post(f"/api/submissions/{created['token']}/uploaded").status_code == 409
    assert not site.app.state.worker.tick()
    events = svc.recordings.database.detail(created["submission_id"])["events"]
    assert [e["kind"] for e in events] == ["paid_recording_queued"]
    svc.recordings.database.reconcile()
    assert len(svc.recordings.database.list_jobs()) == 1


def test_review_code_request_never_stores_a_player_name(site) -> None:
    created = _create(site)
    row = site.app.state.service.recordings.database.get(created["submission_id"])
    assert row["target_player_name"] is None
    assert row["review_code"] == "ABCD-1234-EFGH-5678"
    assert "target_player_name" not in _view(site, created)["acquisition"]
    named = site.post(
        "/api/review-code-submissions", json=_draft(target_player_name="Ika")
    )
    assert named.status_code == 422


def test_verified_account_email_is_forced_for_review_without_changing_preferences(
    site,
) -> None:
    account_id = _login(site)
    site.app.state.accounts.set_notice_preference(account_id, False)
    created = _create(site, email="forged@example.com")
    svc = site.app.state.service
    submission = svc.store.get(created["submission_id"])
    assert submission.email == "owner@example.com" and not submission.notify
    assert submission.user_id == account_id
    assert _view(site, created)["acquisition"]["contact_verified"]
    assert site.get("/api/me").json()["notify_by_email"] is False
    assert len(site.get("/api/me/submissions").json()) == 1


def test_payment_before_contact_stays_blocked_until_verified(site) -> None:
    created = _create(site)
    svc = site.app.state.service
    with svc.recordings.database._conn() as conn:
        conn.execute(
            "UPDATE submission_gates SET payment_state='paid' WHERE submission_id=%s",
            (created["submission_id"],),
        )
    svc.recordings.reconcile()
    assert not svc.recordings.database.list_jobs()
    _verify_guest(site)
    assert len(svc.recordings.database.list_jobs()) == 1


def test_duplicate_guest_verification_consumes_challenge_once(site) -> None:
    _create(site)
    token = _latest_challenge(site)
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: _confirm(site, token), range(2)))
    assert sorted(statuses) == [200, 400]


@pytest.mark.parametrize("browser_state", ["original", "missing", "changed", "expired"])
def test_review_contact_resend_verifies_the_bound_submission(
    site, browser_state: str
) -> None:
    created = _create(site)
    original_challenge = _latest_challenge(site)
    _age_challenges(site, created["submission_id"])

    if browser_state == "original":
        resender = site
    elif browser_state == "missing":
        resender = TestClient(site.app)
    else:
        resender = site
        cookie = resender.cookies.get("notice_browser")
        if browser_state == "changed":
            resender.cookies.set("notice_browser", "changed-browser-token")
        else:
            svc = site.app.state.service
            with svc.recordings.database._conn() as conn:
                conn.execute(
                    """UPDATE guest_notice_browsers
                       SET expires_at = now() - interval '1 day'
                       WHERE browser_token_hash = %s""",
                    (hash_token(cookie),),
                )

    response = resender.post(f"/api/submissions/{created['token']}/verify-contact")
    assert response.status_code == 200
    new_challenge = _latest_challenge(site)
    assert new_challenge != original_challenge
    assert _confirm(resender, new_challenge) == 200

    svc = site.app.state.service
    acquisition = svc.recordings.database.get(created["submission_id"])
    assert acquisition["contact_verified_at"] is not None
    assert (
        resender.post(f"/api/submissions/{created['token']}/checkout").status_code == 200
    )


def test_expired_challenge_is_rejected_without_verifying_contact(site) -> None:
    created = _create(site)
    challenge = _latest_challenge(site)
    svc = site.app.state.service
    with svc.recordings.database._conn() as conn:
        conn.execute(
            """UPDATE guest_email_challenges SET expires_at = now() - interval '1 second'
               WHERE token_hash = %s""",
            (hash_token(challenge),),
        )
    assert _confirm(site, challenge) == 400
    assert (
        svc.recordings.database.get(created["submission_id"])["contact_verified_at"]
        is None
    )


def test_wrong_address_binding_does_not_consume_challenge(site) -> None:
    created = _create(site)
    challenge = _latest_challenge(site)
    svc = site.app.state.service
    with svc.recordings.database._conn() as conn:
        conn.execute(
            """UPDATE review_code_acquisitions SET requested_email = %s
               WHERE submission_id = %s""",
            ("other@example.com", created["submission_id"]),
        )
    assert _confirm(site, challenge) == 400
    with svc.recordings.database._conn() as conn:
        row = conn.execute(
            "SELECT used_at FROM guest_email_challenges WHERE token_hash = %s",
            (hash_token(challenge),),
        ).fetchone()
    assert row["used_at"] is None
    assert (
        svc.recordings.database.get(created["submission_id"])["contact_verified_at"]
        is None
    )


def test_one_challenge_verifies_only_same_browser_same_email_requests(site) -> None:
    first = _create(site)
    second = _create(site)
    outsider = TestClient(site.app)
    third = _create(outsider)
    challenge = _latest_challenge(site)
    assert _confirm(site, challenge) == 200
    database = site.app.state.service.recordings.database
    assert database.get(first["submission_id"])["contact_verified_at"] is not None
    assert database.get(second["submission_id"])["contact_verified_at"] is not None
    assert database.get(third["submission_id"])["contact_verified_at"] is None


def test_replayed_challenge_is_rejected_and_verification_is_idempotent(site) -> None:
    created = _create(site)
    challenge = _latest_challenge(site)
    assert _confirm(site, challenge) == 200
    verified_at = site.app.state.service.recordings.database.get(
        created["submission_id"]
    )["contact_verified_at"]
    assert _confirm(site, challenge) == 400
    assert (
        site.app.state.service.recordings.database.get(created["submission_id"])[
            "contact_verified_at"
        ]
        == verified_at
    )


def test_repeated_resend_after_verification_succeeds_without_another_email(site) -> None:
    created = _create(site)
    _confirm(site, _latest_challenge(site))
    response = site.post(f"/api/submissions/{created['token']}/verify-contact")
    assert response.status_code == 200
    assert response.json()["notice_pending"] is False
    assert len(list((site.app.state.settings.root / "outbox").glob("confirm.*.txt"))) == 1


def test_crash_after_payment_before_queue_and_missing_json_recovers(
    site, monkeypatch
) -> None:
    created = _create(site)
    _verify_guest(site)
    session = _checkout(site, created)
    svc = site.app.state.service
    original = svc.attempt_queue
    monkeypatch.setattr(
        svc, "attempt_queue", lambda *args: (_ for _ in ()).throw(RuntimeError("crash"))
    )
    with pytest.raises(RuntimeError, match="crash"):
        _webhook(site, _event(session))
    assert svc.payments.get(created["submission_id"]).payment_state == "paid"
    assert (
        svc.recordings.database.get(created["submission_id"])["state"]
        == "pending_payment"
    )
    (svc.settings.root / "submissions" / f"{created['submission_id']}.json").unlink()
    (svc.settings.root / "by_token" / hash_token(created["token"])).unlink()
    monkeypatch.setattr(svc, "attempt_queue", original)
    svc.maintain(datetime.now(UTC))
    assert _view(site, created)["acquisition"]["state"] == "awaiting_recording"
    restored = svc.store.get(created["submission_id"])
    assert restored.email == "player@example.com" and restored.notify
    assert len(svc.recordings.database.list_jobs()) == 1


def test_submission_and_gate_creation_rollback_together(site, monkeypatch) -> None:
    svc = site.app.state.service
    original = svc.payments.open_gate

    def crash(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("crash before acquisition commit")

    monkeypatch.setattr(svc.payments, "open_gate", crash)
    with pytest.raises(RuntimeError):
        _create(site)
    with svc.recordings.database._conn() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM submission_gates").fetchone()["n"]
            == 0
        )
        assert (
            conn.execute("SELECT count(*) AS n FROM review_code_acquisitions").fetchone()[
                "n"
            ]
            == 0
        )
    assert not svc.store.list_submissions()


def test_paid_job_outlives_checkout_upload_and_submission_deadlines(site) -> None:
    created = _paid(site)
    svc = site.app.state.service
    with svc.recordings.database._conn() as conn:
        conn.execute(
            "UPDATE submission_gates SET expires_at=now()-interval '2 days' "
            "WHERE submission_id=%s",
            (created["submission_id"],),
        )
    item = svc.store.get(created["submission_id"])
    item.created_at = "2020-01-01T00:00:00Z"
    item.expires_at = "2020-01-02T00:00:00Z"
    svc.store.save_submission(item)
    svc.maintain(datetime.now(UTC))
    assert _view(site, created)["acquisition"]["state"] == "awaiting_recording"
    assert svc.store.get(item.id).status == "received"
    assert len(svc.recordings.database.list_jobs()) == 1


def test_unpaid_checkout_expiration_still_applies(site) -> None:
    created = _create(site)
    svc = site.app.state.service
    with svc.recordings.database._conn() as conn:
        conn.execute(
            "UPDATE submission_gates SET expires_at=now()-interval '1 hour' "
            "WHERE submission_id=%s",
            (created["submission_id"],),
        )
    svc.maintain(datetime.now(UTC))
    assert _view(site, created)["status"] == "expired"
    assert _view(site, created)["acquisition"]["state"] == "expired"
    assert not svc.recordings.database.list_jobs()


def test_browser_return_cancel_or_unpaid_webhook_cannot_authorize_recording(site) -> None:
    created = _create(site)
    _verify_guest(site)
    session = _checkout(site, created)
    assert _view(site, created)["acquisition"]["state"] == "pending_payment"
    assert _webhook(site, _event(session, payment_status="unpaid")).status_code == 200
    assert _webhook(site, _event(session), signature="forged").status_code == 400
    assert _webhook(site, _event(session, amount_subtotal=1)).status_code == 400
    site.fake_checkout.expire(session.id)
    next_session = _checkout(site, created)
    assert next_session.id != session.id
    assert _webhook(site, _event(session)).status_code == 400
    assert not site.app.state.service.recordings.database.list_jobs()


def test_admin_exception_and_correction_are_audited_without_target_action(site) -> None:
    created = _paid(site)
    endpoint = f"/api/admin/recordings/{created['submission_id']}"
    assert site.get("/api/admin/recordings").status_code == 401
    assert site.get(endpoint).status_code == 401
    actor = _login(site)
    assert site.get(endpoint).status_code == 403
    site.app.state.accounts.set_role("owner@example.com", "admin")
    job = site.get(endpoint).json()["job"]

    def act(action, **extra):
        return site.post(
            endpoint + "/actions",
            json={
                "action": action,
                "expected_version": job["version"],
                "note": "Observed and checked on replay",
                **extra,
            },
        )

    assert act("confirm_target").status_code == 422
    assert act("recording_failed", confirmed_player_name="Ika").status_code == 422
    failed = act(
        "recording_failed", customer_message="Please contact us to confirm your code."
    )
    assert failed.status_code == 200
    assert act("recording_failed").status_code == 409
    job = failed.json()["job"]
    corrected = act("correct_code", review_code="1111-2222-3333-4444")
    assert corrected.status_code == 200
    job = corrected.json()["job"]
    assert job["state"] == "awaiting_recording"
    assert "target_confirmed_at" not in job and "confirmed_player_name" not in job
    assert not site.app.state.worker.tick()
    assert any(str(e["actor_id"]) == actor for e in corrected.json()["events"])
    corrected_event = next(
        e for e in corrected.json()["events"] if e["kind"] == "correct_code"
    )
    assert "1111-2222-3333-4444" not in str(corrected_event["details"])


def test_external_refund_is_only_an_audited_operator_resolution(site) -> None:
    created = _paid(site)
    _login(site, admin=True)
    endpoint = f"/api/admin/recordings/{created['submission_id']}"
    job = site.get(endpoint).json()["job"]
    response = site.post(
        endpoint + "/actions",
        json={
            "action": "recording_failed",
            "expected_version": job["version"],
            "note": "Replay unavailable",
        },
    )
    job = response.json()["job"]
    body = {
        "action": "record_refund",
        "expected_version": job["version"],
        "note": "Refund completed outside app",
    }
    assert site.post(endpoint + "/actions", json=body).status_code == 400
    body["refund_reference"] = "re_test_external"
    assert site.post(endpoint + "/actions", json=body).json()["job"]["state"] == "closed"
    assert site.app.state.payments.get(created["submission_id"]).payment_state == "paid"
    acquisition = site.app.state.service.recordings.database.get(created["submission_id"])
    assert acquisition["review_code"] is None
    assert acquisition["requested_email"] is None
    assert acquisition["sensitive_data_purged_at"] is not None


def _flagged(site: TestClient, created: dict, **extra) -> dict:
    endpoint = f"/api/admin/recordings/{created['submission_id']}"
    job = site.get(endpoint).json()["job"]
    response = site.post(
        endpoint + "/actions",
        json={
            "action": "recording_failed",
            "expected_version": job["version"],
            "note": "Replay unavailable",
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["job"]


def _act(site: TestClient, created: dict, action: str, version: int, **extra):
    return site.post(
        f"/api/admin/recordings/{created['submission_id']}/actions",
        json={"action": action, "expected_version": version, "note": "Checked", **extra},
    )


def test_record_again_returns_a_flagged_job_with_the_same_code(site) -> None:
    created = _paid(site)
    _login(site, admin=True)
    before = site.app.state.service.recordings.database.get(created["submission_id"])
    queued = site.get(f"/api/admin/recordings/{created['submission_id']}").json()["job"]
    assert _act(site, created, "retry_recording", queued["version"]).status_code == 409
    job = _flagged(site, created, customer_message="We could not open the replay.")
    assert _act(site, created, "retry_recording", job["version"], note=" ").status_code == 400
    assert _act(site, created, "retry_recording", job["version"] - 1).status_code == 409
    response = _act(site, created, "retry_recording", job["version"])
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    assert job["state"] == "awaiting_recording"
    assert job["review_code"] == before["review_code"]
    assert job["customer_message"] is None
    kinds = [event["kind"] for event in response.json()["events"]]
    assert kinds.count("retry_recording") == 1
    assert _act(site, created, "retry_recording", job["version"]).status_code == 409
    grant = site.post(
        f"/api/admin/recordings/{created['submission_id']}/upload",
        json={
            "expected_version": job["version"],
            "filename": "take.mp4",
            "content_type": "video/mp4",
            "size_bytes": 1000,
        },
    )
    assert grant.status_code == 200, grant.text


def test_a_card_paid_job_cannot_close_without_a_refund(site) -> None:
    created = _paid(site)
    _login(site, admin=True)
    assert site.get("/api/admin/recordings").json()["station_takes"] == {}
    job = _flagged(site, created)
    refused = _act(site, created, "close_without_refund", job["version"])
    assert refused.status_code == 400
    assert "paid by card" in refused.json()["detail"]
    acquisition = site.app.state.service.recordings.database.get(created["submission_id"])
    assert acquisition["state"] == "recording_failed"
    assert acquisition["requested_email"] is not None


def test_parallel_reconciliation_creates_one_recording_event(site) -> None:
    created = _create(site)
    _verify_guest(site)
    svc = site.app.state.service
    with svc.recordings.database._conn() as conn:
        conn.execute(
            "UPDATE submission_gates SET payment_state='paid' WHERE submission_id=%s",
            (created["submission_id"],),
        )
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: svc.recordings.database.reconcile(), range(8)))
    events = svc.recordings.database.detail(created["submission_id"])["events"]
    assert len(events) == 1


def test_zero_price_and_invalid_verification_do_not_create_requests(site) -> None:
    assert (
        site.post(
            "/api/review-code-submissions", json=_draft(turnstile_token="bad")
        ).status_code
        == 400
    )
    site.app.state.payments.set_price(0)
    assert site.post("/api/review-code-submissions", json=_draft()).status_code == 503
    assert not site.app.state.service.recordings.database.snapshots()


def test_migration_is_repeatable_and_historical_models_keep_video_defaults(site) -> None:
    svc = site.app.state.service
    svc.recordings.database.ensure_schema(
        svc.settings.retention_days, svc.settings.account_retention_days
    )
    svc.recordings.database.ensure_schema(
        svc.settings.retention_days, svc.settings.account_retention_days
    )
    legacy = Submission(
        id="a" * 32,
        access_token_hash="b" * 64,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        expires_at="2027-01-01T00:00:00Z",
    )
    assert legacy.input_type == "video" and legacy.source == "upload"
    svc.store.save_submission(legacy)
    assert svc.project(legacy).acquisition is None


def test_review_code_never_claimed_even_if_filesystem_status_is_queued(site) -> None:
    """Unverified is never claimed; ``test_public_recording_intake`` claims verified."""
    created = _paid(site)
    svc = site.app.state.service
    item = svc.store.get(created["submission_id"])
    item.status = "queued"
    svc.store.save_submission(item)
    worker = site.app.state.worker
    assert svc.store.claim_oldest_queued("test-worker", "2026-11-08T00:00:00Z") is None
    assert (
        svc.store.claim_oldest_queued(
            "test-worker", "2026-11-08T00:00:00Z", admit=worker._admit_review_code
        )
        is None
    )
    assert not worker.tick()


def test_late_verified_payment_recovers_even_after_unpaid_expiry_sweep(site) -> None:
    created = _create(site)
    _verify_guest(site)
    session = _checkout(site, created)
    svc = site.app.state.service
    with svc.recordings.database._conn() as conn:
        conn.execute(
            "UPDATE submission_gates SET expires_at=now()-interval '1 day' "
            "WHERE submission_id=%s",
            (created["submission_id"],),
        )
    svc.maintain(datetime.now(UTC))
    assert _view(site, created)["status"] == "expired"
    assert _webhook(site, _event(session)).status_code == 200
    assert _view(site, created)["status"] == "received"
    assert _view(site, created)["acquisition"]["state"] == "awaiting_recording"
    assert len(svc.recordings.database.list_jobs()) == 1


def test_review_backlog_does_not_consume_existing_video_capacity(site) -> None:
    _create(site)
    assert site.app.state.store.unfinished_count() == 0
    assert site.app.state.service.recordings.database.open_count() == 1


@pytest.mark.parametrize(
    "account,retention_setting",
    [(False, "retention_days"), (True, "account_retention_days")],
)
def test_unpaid_sensitive_data_cleanup_uses_configured_boundary(
    site, account: bool, retention_setting: str
) -> None:
    if account:
        _login(site)
    created = _create(site)
    svc = site.app.state.service
    assert getattr(svc.settings, retention_setting) > 0
    expected_email = "owner@example.com" if account else "player@example.com"
    with svc.recordings.database._conn() as conn:
        gate = conn.execute(
            "UPDATE submission_gates g SET status='expired', "
            "expires_at=now()-interval '2 days' "
            "FROM review_code_acquisitions a "
            "WHERE g.submission_id=a.submission_id AND g.submission_id=%s "
            "RETURNING g.expires_at + a.retention_days * interval '1 day' AS cutoff",
            (created["submission_id"],),
        ).fetchone()
        conn.execute(
            "UPDATE review_code_acquisitions SET state='expired' WHERE submission_id=%s",
            (created["submission_id"],),
        )
    cutoff = gate["cutoff"]
    assert (
        svc.recordings.database.purge_expired_unpaid(cutoff - timedelta(microseconds=1))
        == []
    )
    before = svc.recordings.database.get(created["submission_id"])
    assert before["review_code"] == "ABCD-1234-EFGH-5678"
    assert before["requested_email"] == expected_email
    assert svc.recordings.database.purge_expired_unpaid(cutoff) == [
        created["submission_id"]
    ]
    assert svc.recordings.database.purge_expired_unpaid(cutoff + timedelta(days=1)) == []
    after = svc.recordings.database.get(created["submission_id"])
    assert after["review_code"] is None
    assert after["target_player_name"] is None
    assert after["requested_email"] is None
    assert after["contact_verified_at"] is None
    assert after["sensitive_data_purged_at"] is not None
    assert after["submission_snapshot"]["access_token_hash"]
    assert after["submission_snapshot"]["email"] is None
    assert after["submission_snapshot"]["display_name"] is None
    assert after["submission_snapshot"]["user_id"] is None
    assert svc.payments.get(created["submission_id"]).payment_state == "unpaid"
    with svc.recordings.database._conn() as conn:
        purge_events = conn.execute(
            "SELECT count(*) AS n FROM review_code_acquisition_events "
            "WHERE submission_id=%s AND kind='sensitive_data_purged'",
            (created["submission_id"],),
        ).fetchone()["n"]
    assert purge_events == 1


@pytest.mark.parametrize("state", ["awaiting_recording", "recording_failed"])
def test_paid_recording_states_keep_code_past_checkout_and_report_expiry(
    site, state: str
) -> None:
    created = _paid(site)
    svc = site.app.state.service
    with svc.recordings.database._conn() as conn:
        conn.execute(
            "UPDATE submission_gates SET expires_at=now()-interval '400 days' "
            "WHERE submission_id=%s",
            (created["submission_id"],),
        )
        conn.execute(
            "UPDATE review_code_acquisitions SET state=%s WHERE submission_id=%s",
            (state, created["submission_id"]),
        )
    svc.maintain(datetime.now(UTC) + timedelta(days=500))
    row = svc.recordings.database.get(created["submission_id"])
    assert row["review_code"] == "ABCD-1234-EFGH-5678"
    assert row["sensitive_data_purged_at"] is None
    assert row["recording_handoff_at"] is None


def test_schema_drops_target_confirmation_and_handoff_needs_none(site) -> None:
    svc = site.app.state.service
    database = svc.recordings.database
    with database._conn() as conn:
        conn.execute(
            "ALTER TABLE review_code_acquisitions ADD CONSTRAINT legacy_target_check "
            "CHECK (state NOT IN ('recording', 'uploading', 'ready') "
            "OR target_confirmed_at IS NOT NULL) NOT VALID"
        )
    settings = svc.settings
    for _ in range(2):
        database.ensure_schema(settings.retention_days, settings.account_retention_days)
    with database._conn() as conn:
        remaining = conn.execute(
            """SELECT count(*) AS n FROM pg_constraint con
               JOIN pg_class rel ON rel.oid = con.conrelid
               WHERE rel.relname = 'review_code_acquisitions'
                 AND pg_get_constraintdef(con.oid) ILIKE '%target_confirmed_at%'"""
        ).fetchone()["n"]
    assert remaining == 0
    created = _paid(site)
    submission_id = created["submission_id"]
    for state in ("recording", "uploading"):
        with database._conn() as conn:
            conn.execute(
                "UPDATE review_code_acquisitions SET state = %s WHERE submission_id = %s",
                (state, submission_id),
            )
    assert database.confirm_recording_handoff(submission_id, "handoff-no-target")
    row = database.get(submission_id)
    assert row["state"] == "ready" and row["target_confirmed_at"] is None


def test_verified_handoff_clears_code_then_existing_report_retention_applies(
    site,
) -> None:
    created = _paid(site)
    svc = site.app.state.service
    submission = svc.store.get(created["submission_id"])
    analysis_dir = svc.settings.root / "analysis" / created["submission_id"]
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "report.txt").write_text("report", encoding="utf-8")
    submission.analysis_dir = str(analysis_dir)
    svc.store.save_submission(submission)
    with svc.recordings.database._conn() as conn:
        conn.execute(
            "UPDATE review_code_acquisitions SET state='uploading' "
            "WHERE submission_id=%s",
            (created["submission_id"],),
        )
    assert svc.recordings.database.confirm_recording_handoff(
        created["submission_id"], "handoff-1"
    )
    assert svc.recordings.database.confirm_recording_handoff(
        created["submission_id"], "handoff-1"
    )
    assert not svc.recordings.database.confirm_recording_handoff(
        created["submission_id"], "other"
    )
    row = svc.recordings.database.get(created["submission_id"])
    assert row["state"] == "ready" and row["review_code"] is None
    assert (
        row["recording_handoff_at"] is not None
        and row["handoff_reference"] == "handoff-1"
    )
    assert (analysis_dir / "report.txt").exists()
    expired_at = datetime.fromisoformat(
        report_expires_at(row).replace("Z", "+00:00")
    )
    svc.maintain(expired_at - timedelta(seconds=1))
    assert analysis_dir.exists()
    row = svc.recordings.database.get(created["submission_id"])
    assert row["report_expired_at"] is None
    svc.maintain(expired_at + timedelta(seconds=1))
    row = svc.recordings.database.get(created["submission_id"])
    assert row["report_expired_at"] is not None
    assert not analysis_dir.exists()
    svc.maintain(expired_at + timedelta(days=1))
    assert (
        svc.recordings.database.get(created["submission_id"])["report_expired_at"]
        == row["report_expired_at"]
    )


def test_report_directory_deletion_failure_retries_after_durable_expiry(
    site, monkeypatch
) -> None:
    created = _paid(site)
    svc = site.app.state.service
    submission = svc.store.get(created["submission_id"])
    analysis_dir = svc.settings.root / "analysis" / created["submission_id"]
    analysis_dir.mkdir(parents=True)
    report_file = analysis_dir / "report.txt"
    report_file.write_text("private report", encoding="utf-8")
    submission.analysis_dir = str(analysis_dir)
    submission.status = "complete"
    submission.outcome = "coached"
    svc.store.save_submission(submission)
    with svc.recordings.database._conn() as conn:
        conn.execute(
            "UPDATE review_code_acquisitions SET state='uploading' "
            "WHERE submission_id=%s",
            (created["submission_id"],),
        )
    assert svc.recordings.database.confirm_recording_handoff(
        created["submission_id"], "handoff-retry"
    )
    with svc.recordings.database._conn() as conn:
        conn.execute(
            "UPDATE review_code_acquisitions SET recording_handoff_at = "
            "now() - make_interval(days => retention_days, secs => 1) "
            "WHERE submission_id=%s",
            (created["submission_id"],),
        )

    from public_site import service as service_module

    remove_tree = service_module._remove_tree
    attempts = 0

    def fail_once(path: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("injected deletion failure")
        remove_tree(path)

    monkeypatch.setattr(service_module, "_remove_tree", fail_once)
    first_now = datetime.now(UTC)
    svc.maintain(first_now)
    row = svc.recordings.database.get(created["submission_id"])
    after_first = svc.store.get(created["submission_id"])
    assert row["report_expired_at"] is not None
    assert after_first.status == "expired"
    assert after_first.analysis_dir == str(analysis_dir)
    assert analysis_dir.exists() and report_file.exists()
    assert svc.notices.read_sealed(created["submission_id"]) is None
    assert not _guest_rows(site, "guest_notice_requests", created["submission_id"])
    assert _view(site, created)["result"] is None
    assert (
        site.get(f"/api/submissions/{created['token']}/moments/0/frame").status_code
        == 404
    )

    report_file.unlink()
    assert analysis_dir.exists()
    svc.maintain(first_now + timedelta(seconds=1))
    after_retry = svc.store.get(created["submission_id"])
    retried_row = svc.recordings.database.get(created["submission_id"])
    assert not analysis_dir.exists()
    assert after_retry.analysis_dir is None and after_retry.status == "expired"
    assert svc.notices.read_sealed(created["submission_id"]) is None
    assert retried_row["report_expired_at"] == row["report_expired_at"]
    with svc.recordings.database._conn() as conn:
        event_count = conn.execute(
            "SELECT count(*) AS n FROM review_code_acquisition_events "
            "WHERE submission_id=%s AND kind='report_expired'",
            (created["submission_id"],),
        ).fetchone()["n"]
    assert event_count == 1
    svc.maintain(first_now + timedelta(seconds=2))
    assert svc.store.get(created["submission_id"]).analysis_dir is None


def _expire_unpaid_review(site: TestClient, created: dict, now: datetime) -> None:
    svc = site.app.state.service
    with svc.recordings.database._conn() as conn:
        cutoff = conn.execute(
            "UPDATE submission_gates g SET status='received', "
            "expires_at=now()-a.retention_days * interval '1 day'-interval '1 second' "
            "FROM review_code_acquisitions a "
            "WHERE g.submission_id=a.submission_id AND g.submission_id=%s "
            "RETURNING g.expires_at + a.retention_days * interval '1 day' AS cutoff",
            (created["submission_id"],),
        ).fetchone()["cutoff"]
    svc.maintain(cutoff + timedelta(microseconds=1))


def _guest_rows(site: TestClient, table: str, submission_id: str) -> list[dict]:
    with site.app.state.service.recordings.database._conn() as conn:
        return conn.execute(
            f"SELECT * FROM {table} WHERE submission_id=%s",
            (submission_id,),
        ).fetchall()


def test_maintenance_purges_shared_guest_browser_only_after_last_reference(site) -> None:
    first = _create(site)
    _age_challenges(site, first["submission_id"])
    second = _create(site)
    database = site.app.state.service.recordings.database
    challenge = _latest_challenge(site)
    assert _confirm(site, challenge) == 200
    with database._conn() as conn:
        first_browser = conn.execute(
            "SELECT browser_id FROM guest_notice_requests WHERE submission_id=%s",
            (first["submission_id"],),
        ).fetchone()["browser_id"]
        assert conn.execute(
            "SELECT 1 FROM guest_verified_emails WHERE browser_id=%s "
            "AND normalized_email=%s",
            (first_browser, "player@example.com"),
        ).fetchone()

    first_now = datetime.now(UTC)
    _expire_unpaid_review(site, first, first_now)
    assert not _guest_rows(site, "guest_notice_requests", first["submission_id"])
    assert not _guest_rows(site, "guest_email_challenges", first["submission_id"])
    assert _guest_rows(site, "guest_notice_requests", second["submission_id"])
    assert _guest_rows(site, "guest_email_challenges", second["submission_id"])
    with database._conn() as conn:
        assert conn.execute(
            "SELECT 1 FROM guest_notice_browsers WHERE id=%s", (first_browser,)
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM guest_verified_emails WHERE browser_id=%s "
            "AND normalized_email=%s",
            (first_browser, "player@example.com"),
        ).fetchone()

    _expire_unpaid_review(site, second, first_now + timedelta(days=400))
    assert not _guest_rows(site, "guest_notice_requests", second["submission_id"])
    assert not _guest_rows(site, "guest_email_challenges", second["submission_id"])
    with database._conn() as conn:
        assert not conn.execute(
            "SELECT 1 FROM guest_verified_emails WHERE browser_id=%s "
            "AND normalized_email=%s",
            (first_browser, "player@example.com"),
        ).fetchone()
        assert not conn.execute(
            "SELECT 1 FROM guest_notice_browsers WHERE id=%s", (first_browser,)
        ).fetchone()
    _expire_unpaid_review(site, second, first_now + timedelta(days=401))


def test_maintenance_retries_guest_cleanup_after_durable_purge(site, monkeypatch) -> None:
    created = _create(site)
    svc = site.app.state.service
    guest = svc.guest
    original = guest.purge_submission
    failed = False

    def fail_once(submission_id: str) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected guest cleanup failure")
        original(submission_id)

    monkeypatch.setattr(guest, "purge_submission", fail_once)
    now = datetime.now(UTC)
    _expire_unpaid_review(site, created, now)
    assert (
        svc.recordings.database.get(created["submission_id"])["sensitive_data_purged_at"]
        is not None
    )
    assert _guest_rows(site, "guest_notice_requests", created["submission_id"])
    svc.maintain(now + timedelta(seconds=1))
    assert not _guest_rows(site, "guest_notice_requests", created["submission_id"])
    assert not _guest_rows(site, "guest_email_challenges", created["submission_id"])


def test_browser_cleanup_serializes_concurrent_request_creation(site) -> None:
    first = _create(site)
    database = site.app.state.service.recordings.database
    guest = site.app.state.service.guest
    with database._conn() as conn:
        browser_id = str(
            conn.execute(
                "SELECT browser_id FROM guest_notice_requests WHERE submission_id=%s",
                (first["submission_id"],),
            ).fetchone()["browser_id"]
        )
    barrier = Barrier(2)
    second_id = "concurrent-guest-request"
    now = datetime.now(UTC)

    def purge() -> None:
        barrier.wait()
        guest.purge_submission(first["submission_id"])

    def add_request() -> tuple[str, str | None]:
        barrier.wait()
        return guest.add_request(browser_id, second_id, "player@example.com", now)

    with ThreadPoolExecutor(max_workers=2) as pool:
        purge_future = pool.submit(purge)
        request_future = pool.submit(add_request)
        purge_future.result()
        new_browser, fresh_cookie = request_future.result()

    assert (
        guest.begin_challenge(
            new_browser, "player@example.com", second_id, now + timedelta(minutes=20)
        )
        is not None
    )
    with database._conn() as conn:
        assert conn.execute(
            "SELECT 1 FROM guest_notice_requests WHERE submission_id=%s",
            (second_id,),
        ).fetchone()
        assert conn.execute(
            "SELECT 1 FROM guest_notice_browsers WHERE id=%s", (new_browser,)
        ).fetchone()
    assert (new_browser == browser_id) == (fresh_cookie is None)


def _connections_per_maintain(site: TestClient, monkeypatch, now: datetime) -> int:
    import psycopg

    real = psycopg.connect
    calls = 0

    def counting(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(psycopg, "connect", counting)
    site.app.state.service.maintain(now)
    monkeypatch.setattr(psycopg, "connect", real)
    return calls


def test_settled_rows_do_not_add_per_pass_database_work(site, monkeypatch) -> None:
    now = datetime.now(UTC)
    _expire_unpaid_review(site, _create(site), now)
    later = now + timedelta(days=500)
    site.app.state.service.maintain(later)
    baseline = _connections_per_maintain(site, monkeypatch, later)
    for index in range(3):
        _expire_unpaid_review(site, _create(site, email=f"p{index}@example.com"), now)
    site.app.state.service.maintain(later)
    assert _connections_per_maintain(site, monkeypatch, later) == baseline


def test_admin_can_close_an_unpaid_request(site) -> None:
    created = _create(site)
    _verify_guest(site)
    _login(site, admin=True)
    listed = site.get("/api/admin/recordings").json()["unpaid"]
    job = next(
        item for item in listed if item["submission_id"] == created["submission_id"]
    )
    closed = site.post(
        f"/api/admin/recordings/{created['submission_id']}/close-unpaid",
        json={"expected_version": job["version"], "reason": "Customer asked to stop"},
    )
    assert closed.status_code == 200, closed.text
    again = site.get("/api/admin/recordings").json()["unpaid"]
    assert created["submission_id"] not in {item["submission_id"] for item in again}
    assert _view(site, created)["acquisition"]["state"] == "expired"


def test_a_paid_request_cannot_be_closed_as_unpaid(site) -> None:
    created = _paid(site)
    _login(site, admin=True)
    job = site.get(f"/api/admin/recordings/{created['submission_id']}").json()["job"]
    refused = site.post(
        f"/api/admin/recordings/{created['submission_id']}/close-unpaid",
        json={"expected_version": job["version"], "reason": "Duplicate"},
    )
    assert refused.status_code == 409


def test_owned_checkout_returns_to_the_account_page(site) -> None:
    _login(site)
    created = _create(site)
    response = site.post(f"/api/me/submissions/{created['submission_id']}/checkout")
    assert response.status_code == 200, response.text
    target = f"/coaching/{created['submission_id']}"
    assert site.fake_checkout.success_url.endswith(target)
    assert site.fake_checkout.cancel_url == site.fake_checkout.success_url
