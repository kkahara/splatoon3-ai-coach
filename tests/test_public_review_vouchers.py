"""Voucher redemption scope on both submission paths, and review-code voucher use.

The scope always comes from the server route or the stored gate. These tests go
through the real submission endpoints and the real redemption transaction.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from public_site import vouchers as voucher_module
from public_site.recording_store import AUTHORIZED, is_authorized
from public_site.vouchers import VoucherStore, _gate_scope
from test_public_payments import _event, _webhook
from test_public_recording_intake import (
    _claim,
    _confirm,
    _granted,
    _put,
    site,  # noqa: F401
)
from test_public_recordings import _create, _draft, _login, _verify_guest, _view
from test_public_vouchers import (
    _SECRET,
    _body,
    _open_pending,
    _spec,
    _submit,
    _voucher_client,
)

REVIEW_ROUTE = "/api/review-code-submissions"
REVIEW_CHECK = "/api/review-code-submissions/validate-voucher"
VIDEO_CHECK = "/api/vouchers/validate"


@pytest.fixture
def review(site: TestClient) -> TestClient:  # noqa: F811
    """The review-code site with a test voucher secret and an empty voucher table."""
    store = site.app.state.service.vouchers
    store.secret = _SECRET
    store.clear()
    yield site
    site.app.state.payments.clear()
    store.clear()


def _store(client: TestClient) -> VoucherStore:
    return client.app.state.service.vouchers


def _issue(client: TestClient, scope: str, *, uses: int = 1, code: str | None = None):
    source = "youtube" if code else "skilltown"
    spec = replace(_spec(source, code=code, max_redemptions=uses), redemption_scope=scope)
    return _store(client).issue(spec, datetime.now(UTC))


def _video(tmp_path: Path, client: TestClient) -> TestClient:
    """A video-upload client on the same database, so both paths share one counter."""
    return _voucher_client(tmp_path / "video", client.app.state.payments)


def _review_post(client: TestClient, code: str, **changes):
    return client.post(REVIEW_ROUTE, json=_draft(voucher_code=code, **changes))


def _gate(client: TestClient, submission_id: str):
    return client.app.state.payments.get(submission_id)


def _acquisition(client: TestClient, submission_id: str) -> dict:
    return client.app.state.service.recordings.database.get(submission_id)


def _events(client: TestClient, submission_id: str) -> list[str]:
    detail = client.app.state.service.recordings.database.detail(submission_id)
    return [event["kind"] for event in detail["events"]]


def _rows(client: TestClient) -> tuple[list, list]:
    with _store(client)._conn() as conn:
        return (
            conn.execute("SELECT * FROM voucher_redemptions ORDER BY id").fetchall(),
            conn.execute(
                "SELECT * FROM submission_gates ORDER BY submission_id"
            ).fetchall(),
        )


def _pending_guest(client: TestClient, code: str) -> dict:
    """A guest review-code request with an attached, not yet used voucher."""
    response = _review_post(client, code)
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["voucher_state"] == "pending"
    return created


def _verify_quietly(client: TestClient, created: dict) -> None:
    """Record the mailbox as verified without running any voucher redemption."""
    database = client.app.state.service.recordings.database
    with database._conn() as conn:
        conn.execute(
            """UPDATE review_code_acquisitions SET contact_verified_at = now()
               WHERE submission_id = %s""",
            (created["submission_id"],),
        )


def _rejected(response, status: int, reason: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["reason"] == reason


# Scope eligibility (spec 1-5), through the real submission endpoints


def test_video_upload_voucher_is_accepted_for_a_video_upload(review, tmp_path) -> None:
    issued = _issue(review, "video_upload")
    video = _video(tmp_path, review)
    assert video.post(VIDEO_CHECK, json={"code": issued.code}).json()["accepted"]
    view = _submit(video, issued.code)
    assert view["voucher_state"] == "redeemed"
    assert view["payment_state"] == "not_required"
    assert view["status"] == "queued"
    assert _store(review).redemption_count(issued.voucher_id) == 1


def test_video_upload_voucher_is_rejected_for_a_review_code(review) -> None:
    issued = _issue(review, "video_upload")
    _rejected(review.post(REVIEW_CHECK, json={"code": issued.code}), 400, "wrong_scope")
    response = _review_post(review, issued.code)
    _rejected(response, 400, "wrong_scope")
    assert "このクーポンコード" not in response.json()["detail"]
    assert response.json()["detail"] == (
        "This voucher cannot be used for this type of submission."
    )
    assert not review.app.state.service.recordings.database.snapshots()
    assert _store(review).redemption_count(issued.voucher_id) == 0


def test_review_code_voucher_is_accepted_for_a_signed_in_review_code(review) -> None:
    issued = _issue(review, "review_code")
    assert review.post(REVIEW_CHECK, json={"code": issued.code}).json() == {
        "accepted": True,
        "benefit_type": "externally_prepaid",
        "amount_due_cents": 0,
        "currency": "usd",
    }
    _login(review)
    response = _review_post(review, issued.code)
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["voucher_state"] == "redeemed"
    gate = _gate(review, created["submission_id"])
    assert (gate.payment_state, gate.voucher_state) == ("not_required", "redeemed")
    assert _acquisition(review, created["submission_id"])["state"] == "awaiting_recording"
    assert _events(review, created["submission_id"]) == ["paid_recording_queued"]
    assert not review.fake_checkout.created
    assert _store(review).redemption_count(issued.voucher_id) == 1


def test_review_code_voucher_is_rejected_for_a_video_upload(review, tmp_path) -> None:
    issued = _issue(review, "review_code")
    video = _video(tmp_path, review)
    _rejected(video.post(VIDEO_CHECK, json={"code": issued.code}), 400, "wrong_scope")
    _rejected(video.post("/api/submissions", json=_body(issued.code)), 400, "wrong_scope")
    assert video.app.state.store.list_submissions() == []
    assert _store(review).redemption_count(issued.voucher_id) == 0


def test_both_voucher_is_accepted_on_either_path(review, tmp_path) -> None:
    issued = _issue(review, "both", uses=2, code="BOTHPATHS")
    video = _video(tmp_path, review)
    assert video.post(VIDEO_CHECK, json={"code": "bothpaths"}).json()["accepted"]
    assert review.post(REVIEW_CHECK, json={"code": "bothpaths"}).json()["accepted"]
    assert _submit(video, "bothpaths")["voucher_state"] == "redeemed"
    _login(review)
    assert _review_post(review, "bothpaths").json()["voucher_state"] == "redeemed"
    assert _store(review).redemption_count(issued.voucher_id) == 2


# Redemption accounting (spec 6-9)


def test_wrong_scope_attempts_consume_nothing(review, tmp_path) -> None:
    video_only = _issue(review, "video_upload")
    review_only = _issue(review, "review_code")
    video = _video(tmp_path, review)
    for _ in range(3):
        _rejected(_review_post(review, video_only.code), 400, "wrong_scope")
        response = video.post("/api/submissions", json=_body(review_only.code))
        _rejected(response, 400, "wrong_scope")
    store = _store(review)
    assert store.redemption_count(video_only.voucher_id) == 0
    assert store.redemption_count(review_only.voucher_id) == 0
    assert _submit(video, video_only.code)["voucher_state"] == "redeemed"
    _login(review)
    assert _review_post(review, review_only.code).json()["voucher_state"] == "redeemed"


def test_both_voucher_shares_one_counter_and_its_limit(review, tmp_path) -> None:
    issued = _issue(review, "both", uses=2, code="SHARED2")
    video = _video(tmp_path, review)
    assert _submit(video, "SHARED2")["voucher_state"] == "redeemed"
    _login(review)
    assert _review_post(review, "SHARED2").json()["voucher_state"] == "redeemed"
    assert _store(review).redemption_count(issued.voucher_id) == 2
    _rejected(video.post(VIDEO_CHECK, json={"code": "SHARED2"}), 400, "exhausted")
    _rejected(review.post(REVIEW_CHECK, json={"code": "SHARED2"}), 400, "exhausted")
    _rejected(video.post("/api/submissions", json=_body("SHARED2")), 400, "exhausted")
    _rejected(_review_post(review, "SHARED2"), 400, "exhausted")
    assert _store(review).redemption_count(issued.voucher_id) == 2


def test_concurrent_redemptions_across_paths_cannot_exceed_the_limit(review) -> None:
    issued = _issue(review, "both", uses=1)
    created = _pending_guest(review, issued.code)
    _verify_quietly(review, created)
    video_id = _open_pending(review.app.state.payments, issued.voucher_id)
    store = _store(review)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def redeem(submission_id: str) -> None:
        barrier.wait()
        outcomes.append(store.finalize(submission_id, datetime.now(UTC)).outcome)

    threads = [
        threading.Thread(target=redeem, args=(submission_id,))
        for submission_id in (created["submission_id"], video_id)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["failed", "redeemed"]
    assert store.redemption_count(issued.voucher_id) == 1
    states = {
        _gate(review, created["submission_id"]).voucher_state,
        _gate(review, video_id).voucher_state,
    }
    assert states == {"redeemed", "failed"}


def test_concurrent_review_redemptions_of_one_use_redeem_once(review) -> None:
    issued = _issue(review, "review_code", uses=1)
    first = _pending_guest(review, issued.code)
    second = _pending_guest(review, issued.code)
    for created in (first, second):
        _verify_quietly(review, created)
    store = _store(review)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def redeem(submission_id: str) -> None:
        barrier.wait()
        outcomes.append(store.finalize(submission_id, datetime.now(UTC)).outcome)

    threads = [
        threading.Thread(target=redeem, args=(item["submission_id"],))
        for item in (first, second)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["failed", "redeemed"]
    assert store.redemption_count(issued.voucher_id) == 1


# Backward compatibility (spec 10-12)


def test_vouchers_from_before_scope_default_to_video_upload(review, tmp_path) -> None:
    issued = _issue(review, "video_upload", uses=3, code="LEGACY3")
    video = _video(tmp_path, review)
    assert _submit(video, "LEGACY3")["voucher_state"] == "redeemed"
    store = _store(review)
    with store._conn() as conn:
        conn.execute("ALTER TABLE vouchers DROP COLUMN redemption_scope")
        before = conn.execute(
            "SELECT id, code_hash, max_redemptions, status FROM vouchers"
        ).fetchall()
    redemptions_before, _ = _rows(review)
    store.ensure_schema()
    store.ensure_schema()
    with store._conn() as conn:
        after = conn.execute(
            """SELECT id, code_hash, max_redemptions, status, redemption_scope
               FROM vouchers"""
        ).fetchall()
    kept = [{k: v for k, v in row.items() if k != "redemption_scope"} for row in after]
    assert kept == before
    assert {row["redemption_scope"] for row in after} == {"video_upload"}
    assert _rows(review)[0] == redemptions_before
    assert store.redemption_count(issued.voucher_id) == 1
    assert store.status(issued.voucher_id).redemption_scope == "video_upload"
    assert video.post(VIDEO_CHECK, json={"code": "legacy3"}).json()["accepted"]
    _rejected(review.post(REVIEW_CHECK, json={"code": "legacy3"}), 400, "wrong_scope")
    assert _submit(video, "LEGACY3")["voucher_state"] == "redeemed"
    assert store.redemption_count(issued.voucher_id) == 2


def test_gate_rows_without_a_source_are_video_uploads() -> None:
    assert _gate_scope({}) == "video_upload"
    assert _gate_scope({"source": "upload"}) == "video_upload"
    assert _gate_scope({"source": "nintendo_review_code"}) == "review_code"


# Existing validation on the review-code path (spec 14-17)


def test_expired_exhausted_invalid_and_wrong_service_stay_rejected(review) -> None:
    expired = _issue(review, "both")
    exhausted = _issue(review, "both")
    wrong_service = _issue(review, "both")
    store = _store(review)
    with store._conn() as conn:
        conn.execute(
            "UPDATE vouchers SET expires_at = %s WHERE id = %s",
            (datetime.now(UTC) - timedelta(seconds=1), expired.voucher_id),
        )
        conn.execute(
            "UPDATE vouchers SET eligible_service_id = 'future_premium' WHERE id = %s",
            (wrong_service.voucher_id,),
        )
    _login(review)
    assert _review_post(review, exhausted.code).json()["voucher_state"] == "redeemed"
    cases = [
        (expired.code, "expired"),
        (exhausted.code, "exhausted"),
        (wrong_service.code, "wrong_service"),
        ("SKT-ZZZZ-ZZZZ", "invalid"),
        ("not a code", "invalid"),
    ]
    for code, reason in cases:
        _rejected(review.post(REVIEW_CHECK, json={"code": code}), 400, reason)
        _rejected(_review_post(review, code), 400, reason)
    store.secret = "a-different-secret"
    _rejected(review.post(REVIEW_CHECK, json={"code": exhausted.code}), 400, "invalid")
    assert store.redemption_count(expired.voucher_id) == 0
    assert store.redemption_count(wrong_service.voucher_id) == 0


def test_benefit_mismatch_is_still_refused_at_issue(review) -> None:
    spec = replace(
        _spec("skilltown"), benefit_type="complimentary", redemption_scope="both"
    )
    with pytest.raises(ValueError, match="benefit"):
        _store(review).issue(spec, datetime.now(UTC))
    with pytest.raises(ValueError, match="scope"):
        _store(review).issue(
            replace(_spec("skilltown"), redemption_scope="all"), datetime.now(UTC)
        )


# Section H: guests, interruption and recovery


def test_unverified_guest_consumes_nothing(review) -> None:
    issued = _issue(review, "review_code")
    created = _pending_guest(review, issued.code)
    svc = review.app.state.service
    svc.maintain(datetime.now(UTC))
    decision = _store(review).finalize(created["submission_id"], datetime.now(UTC))
    assert (decision.outcome, decision.reason) == ("conflict", "not_ready")
    assert _store(review).redemption_count(issued.voucher_id) == 0
    gate = _gate(review, created["submission_id"])
    assert (gate.payment_state, gate.voucher_state) == ("unpaid", "pending")
    assert review.post(f"/api/submissions/{created['token']}/checkout").status_code == 409


def test_guest_verification_redeems_on_the_original_request(review) -> None:
    issued = _issue(review, "review_code")
    created = _pending_guest(review, issued.code)
    view = _view(review, created)
    assert view["voucher_state"] == "pending"
    _verify_guest(review)
    gate = _gate(review, created["submission_id"])
    assert (gate.payment_state, gate.voucher_state) == ("not_required", "redeemed")
    assert _acquisition(review, created["submission_id"])["state"] == "awaiting_recording"
    view = _view(review, created)
    assert view["payment_state"] == "not_required"
    assert view["acquisition"]["contact_verified"]
    svc = review.app.state.service
    assert svc.recordings.redeem_pending_vouchers() == 0
    svc.maintain(datetime.now(UTC))
    assert _store(review).redemption_count(issued.voucher_id) == 1
    assert _events(review, created["submission_id"]) == ["paid_recording_queued"]
    assert review.post(f"/api/submissions/{created['token']}/checkout").status_code == 400
    assert len(review.app.state.service.recordings.database.snapshots()) == 1


def _voucher_action(client: TestClient, submission_id: str, action: str, version: int, **extra):
    return client.post(
        f"/api/admin/recordings/{submission_id}/actions",
        json={"action": action, "expected_version": version, "note": "Replay unavailable", **extra},
    )


def test_a_flagged_voucher_job_closes_without_refund_and_keeps_the_voucher(review) -> None:
    issued = _issue(review, "review_code")
    created = _pending_guest(review, issued.code)
    _verify_guest(review)
    submission_id = created["submission_id"]
    _login(review, admin=True)
    version = review.get(f"/api/admin/recordings/{submission_id}").json()["job"]["version"]
    assert _voucher_action(review, submission_id, "close_without_refund", version).status_code == 409
    flagged = _voucher_action(review, submission_id, "recording_failed", version)
    version = flagged.json()["job"]["version"]
    refund = _voucher_action(
        review, submission_id, "record_refund", version, refund_reference="re_x"
    )
    assert refund.status_code == 400 and "nothing to refund" in refund.json()["detail"]
    blank = _voucher_action(review, submission_id, "close_without_refund", version, note=" ")
    assert blank.status_code == 400
    stale = _voucher_action(review, submission_id, "close_without_refund", version - 1)
    assert stale.status_code == 409
    closed = _voucher_action(review, submission_id, "close_without_refund", version)
    assert closed.status_code == 200, closed.text
    assert closed.json()["job"]["state"] == "closed"
    kinds = [event["kind"] for event in closed.json()["events"]]
    assert kinds.count("close_without_refund") == 1
    acquisition = _acquisition(review, submission_id)
    for field in ("review_code", "target_player_name", "requested_email", "contact_verified_at"):
        assert acquisition[field] is None, field
    assert acquisition["sensitive_data_purged_at"] is not None
    snapshot = acquisition["submission_snapshot"]
    assert (snapshot["email"], snapshot["display_name"], snapshot["user_id"]) == (None, None, None)
    assert snapshot["notify"] is False
    gate = _gate(review, submission_id)
    assert (gate.payment_state, gate.voucher_state) == ("not_required", "redeemed")
    assert _store(review).redemption_count(issued.voucher_id) == 1
    again = _voucher_action(review, submission_id, "close_without_refund", version + 1)
    assert again.status_code == 409


def test_interrupted_redemption_is_recovered_by_reconcile(review, monkeypatch) -> None:
    issued = _issue(review, "review_code")
    created = _pending_guest(review, issued.code)
    _verify_quietly(review, created)
    svc = review.app.state.service
    database = svc.recordings.database

    def crash() -> None:
        raise RuntimeError("process stopped after the redemption committed")

    monkeypatch.setattr(database, "reconcile", crash)
    with pytest.raises(RuntimeError):
        svc.recordings.redeem_pending_vouchers()
    monkeypatch.undo()
    submission_id = created["submission_id"]
    gate = _gate(review, submission_id)
    assert (gate.payment_state, gate.voucher_state) == ("not_required", "redeemed")
    assert _acquisition(review, submission_id)["state"] == "pending_payment"
    assert _acquisition(review, submission_id)["queued_at"] is None
    assert _store(review).redemption_count(issued.voucher_id) == 1
    for _ in range(3):
        svc.maintain(datetime.now(UTC))
        assert _acquisition(review, submission_id)["state"] == "awaiting_recording"
        assert _events(review, submission_id) == ["paid_recording_queued"]
    again = _store(review).finalize(submission_id, datetime.now(UTC))
    assert again.outcome == "redeemed"
    assert _store(review).redemption_count(issued.voucher_id) == 1


def test_verified_request_whose_trigger_never_ran_is_redeemed_by_maintain(review) -> None:
    issued = _issue(review, "review_code")
    created = _pending_guest(review, issued.code)
    _verify_quietly(review, created)
    svc = review.app.state.service
    svc.maintain(datetime.now(UTC))
    svc.maintain(datetime.now(UTC))
    assert _store(review).redemption_count(issued.voucher_id) == 1
    assert _acquisition(review, created["submission_id"])["state"] == "awaiting_recording"
    assert _events(review, created["submission_id"]) == ["paid_recording_queued"]


def test_expired_voucher_at_verification_falls_back_to_payment(review) -> None:
    issued = _issue(review, "review_code")
    created = _pending_guest(review, issued.code)
    with _store(review)._conn() as conn:
        conn.execute(
            "UPDATE vouchers SET expires_at = %s WHERE id = %s",
            (datetime.now(UTC) - timedelta(seconds=1), issued.voucher_id),
        )
    _verify_guest(review)
    view = _view(review, created)
    assert view["voucher_state"] == "failed"
    assert view["voucher_failure_reason"] == "expired"
    assert view["payment_state"] == "unpaid"
    response = review.post(f"/api/submissions/{created['token']}/checkout")
    assert response.status_code == 200, response.text
    session = review.fake_checkout.created[-1]
    assert _webhook(review, _event(session)).status_code == 200
    assert _gate(review, created["submission_id"]).payment_state == "paid"
    assert _acquisition(review, created["submission_id"])["state"] == "awaiting_recording"
    assert _store(review).redemption_count(issued.voucher_id) == 0


def test_payment_window_passed_before_verification_consumes_nothing(review) -> None:
    issued = _issue(review, "review_code")
    created = _pending_guest(review, issued.code)
    _verify_quietly(review, created)
    later = datetime.now(UTC) + timedelta(days=2)
    decision = _store(review).finalize(created["submission_id"], later)
    assert (decision.outcome, decision.reason) == ("conflict", "window")
    assert _store(review).redemption_count(issued.voucher_id) == 0


def test_unknown_gate_source_fails_closed(review, monkeypatch) -> None:
    issued = _issue(review, "both")
    created = _pending_guest(review, issued.code)
    _verify_quietly(review, created)
    before = _rows(review)
    monkeypatch.setattr(voucher_module, "_GATE_SCOPES", {"upload": "video_upload"})
    decision = _store(review).finalize(created["submission_id"], datetime.now(UTC))
    assert decision.outcome == "conflict"
    assert _rows(review) == before
    assert _store(review).redemption_count(issued.voucher_id) == 0


def test_client_supplied_scope_is_ignored_or_refused(review, tmp_path) -> None:
    review_only = _issue(review, "review_code")
    video_only = _issue(review, "video_upload")
    video = _video(tmp_path, review)
    forged = {"code": review_only.code, "scope": "review_code"}
    _rejected(video.post(VIDEO_CHECK, json=forged), 400, "wrong_scope")
    forged = {"code": video_only.code, "scope": "video_upload"}
    _rejected(review.post(REVIEW_CHECK, json=forged), 400, "wrong_scope")
    body = {**_body(review_only.code), "scope": "review_code"}
    _rejected(video.post("/api/submissions", json=body), 400, "wrong_scope")
    body = {**_draft(voucher_code=video_only.code), "scope": "video_upload"}
    assert review.post(REVIEW_ROUTE, json=body).status_code == 422
    assert not review.app.state.service.recordings.database.snapshots()
    assert _store(review).redemption_count(review_only.voucher_id) == 0
    assert _store(review).redemption_count(video_only.voucher_id) == 0


def test_scope_edited_after_attachment_is_refused_at_redemption(review) -> None:
    issued = _issue(review, "review_code")
    created = _pending_guest(review, issued.code)
    with _store(review)._conn() as conn:
        conn.execute(
            "UPDATE vouchers SET redemption_scope = 'video_upload' WHERE id = %s",
            (issued.voucher_id,),
        )
    _verify_quietly(review, created)
    before = _rows(review)
    decision = _store(review).finalize(created["submission_id"], datetime.now(UTC))
    assert (decision.outcome, decision.reason) == ("conflict", "wrong_scope")
    assert _rows(review) == before


def test_create_uses_only_the_submitted_code(review) -> None:
    accepted = _issue(review, "review_code")
    assert review.post(REVIEW_CHECK, json={"code": accepted.code}).json()["accepted"]
    _rejected(_review_post(review, "SKT-ZZZZ-ZZZZ"), 400, "invalid")
    assert not review.app.state.service.recordings.database.snapshots()
    assert _store(review).redemption_count(accepted.voucher_id) == 0


# Review-code activation and worker eligibility


def test_voucher_job_is_listed_recorded_queued_and_admitted(review) -> None:
    issued = _issue(review, "review_code")
    created = _pending_guest(review, issued.code)
    _verify_guest(review)
    jobs = review.app.state.service.recordings.database.list_jobs()
    assert [job.submission_id for job in jobs] == [created["submission_id"]]
    _login(review, admin=True)
    endpoint = f"/api/admin/recordings/{created['submission_id']}"
    assert review.get(endpoint).json()["authorization"] == "voucher"
    grant = _granted(review, created)
    _put(review, grant)
    assert _confirm(review, created, grant["version"]).status_code == 200
    claimed = _claim(review)
    assert claimed is not None and claimed.id == created["submission_id"]
    assert _store(review).redemption_count(issued.voucher_id) == 1


def test_refund_is_refused_for_a_voucher_job(review) -> None:
    issued = _issue(review, "review_code")
    created = _pending_guest(review, issued.code)
    _verify_guest(review)
    _login(review, admin=True)
    endpoint = f"/api/admin/recordings/{created['submission_id']}"
    job = review.get(endpoint).json()["job"]
    failed = review.post(
        endpoint + "/actions",
        json={
            "action": "recording_failed",
            "expected_version": job["version"],
            "note": "Replay unavailable",
        },
    )
    job = failed.json()["job"]
    response = review.post(
        endpoint + "/actions",
        json={
            "action": "record_refund",
            "expected_version": job["version"],
            "note": "Attempted refund",
            "refund_reference": "re_test_external",
        },
    )
    assert response.status_code == 400
    assert "nothing to refund" in response.json()["detail"]
    assert review.get(endpoint).json()["job"]["state"] == "recording_failed"


def test_stray_webhook_after_voucher_redemption_is_stale(review) -> None:
    issued = _issue(review, "review_code")
    _login(review)
    created = _review_post(review, issued.code).json()
    assert created["voucher_state"] == "redeemed"
    submission_id = created["submission_id"]
    stray = SimpleNamespace(
        id="cs_test_stray",
        url="https://checkout.example/stray",
        amount_subtotal=500,
        amount_total=500,
        client_reference_id=submission_id,
    )
    response = _webhook(review, _event(stray))
    assert response.status_code == 400
    assert response.json()["detail"] == "association"
    result = review.app.state.payments.apply_paid(
        submission_id=submission_id,
        session_id="cs_test_stray",
        amount_subtotal=500,
        amount_total=500,
        currency="usd",
        payment_intent_id="pi_test_stray",
    )
    assert result == "stale"
    gate = _gate(review, submission_id)
    assert (gate.payment_state, gate.voucher_state) == ("not_required", "redeemed")
    assert gate.payment_intent_id is None
    assert _events(review, submission_id) == ["paid_recording_queued"]
    assert _store(review).redemption_count(issued.voucher_id) == 1


def test_duplicate_review_code_webhook_activates_once(review) -> None:
    created = _create(review)
    _verify_guest(review)
    response = review.post(f"/api/submissions/{created['token']}/checkout")
    assert response.status_code == 200, response.text
    session = review.fake_checkout.created[-1]
    for _ in range(3):
        assert _webhook(review, _event(session)).status_code == 200
    review.app.state.service.maintain(datetime.now(UTC))
    assert _events(review, created["submission_id"]) == ["paid_recording_queued"]
    assert len(review.app.state.service.recordings.database.list_jobs()) == 1


def test_bad_webhook_signature_changes_nothing(review) -> None:
    created = _create(review)
    _verify_guest(review)
    assert review.post(f"/api/submissions/{created['token']}/checkout").status_code == 200
    session = review.fake_checkout.created[-1]
    before = _rows(review)
    response = _webhook(review, _event(session), signature="bad")
    assert response.status_code == 400
    assert _rows(review) == before
    assert _gate(review, created["submission_id"]).payment_state == "checkout_created"


def test_sql_and_python_authorization_rules_agree(review) -> None:
    states = [
        ("paid", None),
        ("paid", "failed"),
        ("not_required", "redeemed"),
        ("not_required", None),
        ("not_required", "pending"),
        ("unpaid", "pending"),
        ("unpaid", "failed"),
        ("checkout_created", None),
    ]
    with _store(review)._conn() as conn:
        for payment_state, voucher_state in states:
            row = conn.execute(
                f"SELECT {AUTHORIZED} AS ok FROM (SELECT %s::text AS payment_state, "
                "%s::text AS voucher_state) g",
                (payment_state, voucher_state),
            ).fetchone()
            python = is_authorized(
                {"payment_state": payment_state, "voucher_state": voucher_state}
            )
            assert bool(row["ok"]) is python, (payment_state, voucher_state)
