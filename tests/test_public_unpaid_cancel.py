"""Operator cancellation of unpaid review-code requests: Stripe first, then audit and email."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from test_public_payments import _event, _webhook
from test_public_recordings import (  # noqa: F401 - ``site`` is the shared fixture
    _checkout,
    _create,
    _login,
    _verify_guest,
    _view,
    site,
)

REASON = "Customer asked us to stop"


def _endpoint(created: dict) -> str:
    return f"/api/admin/recordings/{created['submission_id']}/close-unpaid"


def _unpaid(site: TestClient, created: dict) -> dict | None:
    listed = site.get("/api/admin/recordings").json()["unpaid"]
    return next((i for i in listed if i["submission_id"] == created["submission_id"]), None)


def _close(site: TestClient, created: dict, reason: str = REASON, version: int | None = None):
    if version is None:
        version = _unpaid(site, created)["version"]
    return site.post(_endpoint(created), json={"expected_version": version, "reason": reason})


def _events(site: TestClient, created: dict, kind: str) -> list[dict]:
    with site.app.state.service.recordings.database._conn() as conn:
        rows = conn.execute(
            """SELECT actor_id, details, created_at FROM review_code_acquisition_events
               WHERE submission_id = %s AND kind = %s ORDER BY id""",
            (created["submission_id"], kind),
        ).fetchall()
    return list(rows)


def _mail(site: TestClient, created: dict) -> list[Path]:
    outbox = site.app.state.settings.root / "outbox"
    return list(outbox.glob(f"{created['submission_id']}.cancelled.txt"))


def _gate(site: TestClient, created: dict):
    return site.app.state.payments.get(created["submission_id"])


def _state(site: TestClient, created: dict) -> str:
    return site.app.state.service.recordings.database.get(created["submission_id"])["state"]


def _verified_with_checkout(site: TestClient) -> tuple[dict, object]:
    created = _create(site)
    _verify_guest(site)
    session = _checkout(site, created)
    return created, session


def test_open_checkout_is_expired_then_closed_audited_and_emailed(site) -> None:
    created, session = _verified_with_checkout(site)
    actor = _login(site, admin=True)
    closed = _close(site, created)
    assert closed.status_code == 200, closed.text
    assert closed.json() == {
        "state": "expired",
        "cancellation_email": "pending",
        "email_sent": True,
    }
    assert site.fake_checkout.sessions[session.id].status == "expired"
    assert site.fake_checkout.expire_calls == 1
    [event] = _events(site, created, "unpaid_closed")
    assert str(event["actor_id"]) == actor and event["created_at"] is not None
    details = event["details"]
    assert details["reason"] == REASON
    assert details["previous"] == {
        "acquisition_state": "pending_payment",
        "gate_status": "received",
        "payment_state": "checkout_created",
    }
    assert details["final_state"] == "expired"
    assert details["stripe"]["observed"] == "expired_by_operator"
    assert details["stripe"]["session_status"] == "expired"
    assert details["cancellation_email"] == "pending"
    assert len(_events(site, created, "cancellation_email_sent")) == 1
    [mail] = _mail(site, created)
    text = mail.read_text(encoding="utf-8")
    assert "To: player@example.com" in text
    assert "cancelled because it was not paid" in text and "No payment was taken" in text
    assert f"/review/{created['token']}" in text
    assert _gate(site, created).public_reference in text


def test_guest_still_opens_the_expired_status_page_after_the_email(site) -> None:
    created, _session = _verified_with_checkout(site)
    _login(site, admin=True)
    assert _close(site, created).status_code == 200
    sealed = site.app.state.settings.root / "notice_secrets" / created["submission_id"]
    assert not sealed.exists()
    site.cookies.clear()
    view = _view(site, created)
    assert view["status"] == "expired"
    assert view["acquisition"]["state"] == "expired"
    assert view["acquisition"]["cancelled"] is True
    checkout = site.post(f"/api/submissions/{created['token']}/checkout")
    assert checkout.status_code == 400


def test_a_repeated_or_concurrent_cancel_sends_one_email(site) -> None:
    created, _session = _verified_with_checkout(site)
    _login(site, admin=True)
    version = _unpaid(site, created)["version"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        codes = sorted(
            pool.map(lambda _: _close(site, created, version=version).status_code, range(3))
        )
    assert codes[0] == 200 and all(code in {409, 503} for code in codes[1:])
    assert _close(site, created, version=version).status_code == 409
    assert len(_events(site, created, "unpaid_closed")) == 1
    assert len(_mail(site, created)) == 1
    site.app.state.service.maintain(datetime.now(UTC))
    assert len(_events(site, created, "cancellation_email_sent")) == 1


def test_a_payment_completed_at_stripe_is_recorded_and_not_cancelled(site) -> None:
    created, session = _verified_with_checkout(site)
    site.fake_checkout.pay(session.id)
    _login(site, admin=True)
    refused = _close(site, created)
    assert refused.status_code == 409
    assert refused.json()["reason"] == "paid_at_stripe"
    assert _gate(site, created).payment_state == "paid"
    assert _state(site, created) == "awaiting_recording"
    assert _events(site, created, "unpaid_closed") == []
    assert _mail(site, created) == []
    paid = site.fake_checkout.sessions[session.id]
    assert _webhook(site, _event(paid)).status_code == 200
    assert _gate(site, created).payment_state == "paid"
    with site.app.state.payments._conn() as conn:
        count = conn.execute(
            """SELECT count(*) AS n FROM price_events
               WHERE submission_id = %s AND kind = 'payment_completed'""",
            (created["submission_id"],),
        ).fetchone()["n"]
    assert count == 1


def test_payment_racing_the_expire_call_wins(site, monkeypatch) -> None:
    created, session = _verified_with_checkout(site)
    _login(site, admin=True)
    checkout = site.fake_checkout

    def paid_first(session_id: str):
        checkout.pay(session_id)
        raise RuntimeError("session is not open")

    monkeypatch.setattr(checkout, "expire_session", paid_first)
    assert _close(site, created).status_code == 409
    assert _gate(site, created).payment_state == "paid"
    assert _events(site, created, "unpaid_closed") == []


def test_unreachable_or_undecided_stripe_changes_nothing(site, monkeypatch) -> None:
    created, session = _verified_with_checkout(site)
    _login(site, admin=True)
    checkout = site.fake_checkout
    version = _unpaid(site, created)["version"]

    def down(_session_id: str):
        raise OSError("network")

    monkeypatch.setattr(checkout, "retrieve_session", down)
    assert _close(site, created, version=version).status_code == 503
    monkeypatch.undo()

    def stays_open(_session_id: str):
        raise RuntimeError("stripe busy")

    monkeypatch.setattr(checkout, "expire_session", stays_open)
    assert _close(site, created, version=version).status_code == 503
    assert checkout.sessions[session.id].status == "open"
    assert _gate(site, created).payment_state == "checkout_created"
    assert _gate(site, created).status == "received"
    assert _state(site, created) == "pending_payment"
    assert _unpaid(site, created)["version"] == version
    assert _events(site, created, "unpaid_closed") == []
    assert _mail(site, created) == []


def test_an_already_expired_session_is_not_expired_again(site) -> None:
    created, session = _verified_with_checkout(site)
    site.fake_checkout.expire(session.id)
    _login(site, admin=True)
    assert _close(site, created).status_code == 200
    assert getattr(site.fake_checkout, "expire_calls", 0) == 0
    [event] = _events(site, created, "unpaid_closed")
    assert event["details"]["stripe"]["observed"] == "already_expired"


def test_a_request_without_checkout_is_closed_without_calling_stripe(site) -> None:
    created = _create(site)
    _verify_guest(site)
    _login(site, admin=True)
    assert _close(site, created).status_code == 200
    [event] = _events(site, created, "unpaid_closed")
    assert event["details"]["stripe"] == {"observed": "no_session"}
    assert len(_mail(site, created)) == 1


def test_an_unverified_address_is_cancelled_without_email(site) -> None:
    created = _create(site)
    _login(site, admin=True)
    closed = _close(site, created)
    assert closed.status_code == 200
    assert closed.json()["cancellation_email"] == "skipped_unverified"
    assert closed.json()["email_sent"] is False
    [event] = _events(site, created, "unpaid_closed")
    assert event["details"]["cancellation_email"] == "skipped_unverified"
    site.app.state.service.maintain(datetime.now(UTC))
    assert _mail(site, created) == []
    assert _events(site, created, "cancellation_email_sent") == []
    assert _view(site, created)["acquisition"]["state"] == "expired"


def test_a_reason_is_required_and_review_codes_are_redacted(site) -> None:
    created = _create(site)
    _verify_guest(site)
    _login(site, admin=True)
    assert _close(site, created, reason="").status_code == 422
    assert _close(site, created, reason="   ").status_code == 422
    assert _close(site, created, reason="code ABCD-1234-EFGH-5678 was wrong").status_code == 200
    [event] = _events(site, created, "unpaid_closed")
    assert "ABCD" not in event["details"]["reason"]
    assert "[redacted]" in event["details"]["reason"]


def test_mail_failure_keeps_the_cancel_and_maintenance_retries(site, monkeypatch) -> None:
    created, _session = _verified_with_checkout(site)
    _login(site, admin=True)
    svc = site.app.state.service
    deliver = svc.notices.deliver

    def refused(*_args, **_kwargs):
        raise OSError("smtp down")

    monkeypatch.setattr(svc.notices, "deliver", refused)
    closed = _close(site, created)
    assert closed.status_code == 200 and closed.json()["email_sent"] is False
    assert _state(site, created) == "expired"
    monkeypatch.setattr(svc.notices, "deliver", deliver)

    def stale(item) -> None:
        item.notice_claims["cancelled"] = "2000-01-01T00:00:00Z"

    svc.store.update(created["submission_id"], stale)
    svc.maintain(datetime.now(UTC))
    assert len(_mail(site, created)) == 1
    assert len(_events(site, created, "cancellation_email_sent")) == 1
    svc.maintain(datetime.now(UTC))
    assert len(_events(site, created, "cancellation_email_sent")) == 1


def test_a_crash_between_commit_and_send_is_finished_by_maintenance(site, monkeypatch) -> None:
    created, _session = _verified_with_checkout(site)
    _login(site, admin=True)
    recordings = site.app.state.service.recordings
    send = recordings.send_cancellation
    monkeypatch.setattr(recordings, "send_cancellation", lambda _id: None)
    assert _close(site, created).status_code == 200
    assert _mail(site, created) == []
    monkeypatch.setattr(recordings, "send_cancellation", send)
    site.app.state.service.maintain(datetime.now(UTC))
    assert len(_mail(site, created)) == 1
    assert len(_events(site, created, "cancellation_email_sent")) == 1


def test_automatic_unpaid_expiry_sends_no_cancellation_email(site) -> None:
    created, _session = _verified_with_checkout(site)
    svc = site.app.state.service
    with svc.recordings.database._conn() as conn:
        conn.execute(
            "UPDATE submission_gates SET expires_at = now() - interval '1 minute' "
            "WHERE submission_id = %s",
            (created["submission_id"],),
        )
    svc.maintain(datetime.now(UTC))
    svc.maintain(datetime.now(UTC) + timedelta(seconds=1))
    assert _gate(site, created).status == "expired"
    assert _mail(site, created) == []
    assert _events(site, created, "unpaid_closed") == []
    assert _view(site, created)["acquisition"].get("cancelled") is False


def test_account_history_marks_a_cancelled_request_expired(site) -> None:
    _login(site)
    created = _create(site)
    _checkout(site, created)
    site.app.state.accounts.set_role("owner@example.com", "admin")
    assert _close(site, created).status_code == 200
    history = site.get("/api/me/submissions").json()
    item = next(entry for entry in history if entry["id"] == created["submission_id"])
    assert item["status"] == "expired"
    assert item["display_status"] == "cancelled"
    assert len(_mail(site, created)) == 1
    text = _mail(site, created)[0].read_text(encoding="utf-8")
    assert "To: owner@example.com" in text
