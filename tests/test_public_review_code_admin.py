"""An administrator's review-code request needs no payment; everyone else still pays."""

from __future__ import annotations

from public_site.recording_store import is_authorized
from test_public_recordings import _create, _login, _view, site  # noqa: F401
from test_public_review_vouchers import _issue, _review_post, review  # noqa: F401


def _gate(site, created: dict):  # noqa: F811
    return site.app.state.payments.get(created["submission_id"])


def _endpoint(created: dict) -> str:
    return f"/api/admin/recordings/{created['submission_id']}"


def test_an_admin_request_skips_checkout_and_waits_for_recording(site) -> None:  # noqa: F811
    _login(site, admin=True)
    created = _create(site)
    assert created["payment_required"] is False
    gate = _gate(site, created)
    assert (gate.payment_state, gate.voucher_state) == ("not_required", None)
    row = site.app.state.service.recordings.database.get(created["submission_id"])
    assert row["state"] == "awaiting_recording" and row["queued_at"] is not None
    jobs = site.app.state.service.recordings.database.list_jobs()
    assert [job.submission_id for job in jobs] == [created["submission_id"]]
    assert site.get(_endpoint(created)).json()["authorization"] == "admin"
    view = _view(site, created)
    assert view["payment_state"] == "not_required"
    assert view["acquisition"]["state"] == "awaiting_recording"
    checkout = site.post(f"/api/submissions/{created['token']}/checkout")
    assert checkout.status_code == 400
    assert site.fake_checkout.created == []


def test_a_signed_in_non_admin_still_pays(site) -> None:  # noqa: F811
    _login(site)
    created = _create(site)
    assert created["payment_required"] is True and created["price_cents"] == 500
    assert _gate(site, created).payment_state == "unpaid"
    assert site.app.state.service.recordings.database.list_jobs() == []


def test_a_guest_still_pays(site) -> None:  # noqa: F811
    created = _create(site)
    assert created["payment_required"] is True
    assert _gate(site, created).payment_state == "unpaid"


def test_an_admin_with_a_voucher_keeps_the_voucher_flow(review) -> None:  # noqa: F811
    issued = _issue(review, "review_code")
    _login(review, admin=True)
    response = _review_post(review, issued.code)
    assert response.status_code == 200, response.text
    created = response.json()
    assert created["voucher_state"] == "redeemed"
    gate = _gate(review, created)
    assert (gate.payment_state, gate.voucher_state) == ("not_required", "redeemed")
    assert review.get(_endpoint(created)).json()["authorization"] == "voucher"


def test_an_admin_request_can_be_flagged_and_closed_without_refund(site) -> None:  # noqa: F811
    _login(site, admin=True)
    created = _create(site)

    def act(action: str, **extra):
        version = site.get(_endpoint(created)).json()["job"]["version"]
        body = {"action": action, "expected_version": version, "note": "test", **extra}
        return site.post(_endpoint(created) + "/actions", json=body)

    assert act("recording_failed").status_code == 200
    assert act("record_refund", refund_reference="re_x").status_code == 400
    closed = act("close_without_refund")
    assert closed.status_code == 200, closed.text
    assert closed.json()["job"]["state"] == "closed"


def test_a_row_without_voucher_state_is_never_an_admin_request() -> None:
    free = {"payment_state": "not_required"}
    assert not is_authorized(free)
    assert is_authorized({**free, "voucher_state": None})
    assert not is_authorized({**free, "voucher_state": "failed"})
    assert not is_authorized({**free, "voucher_state": "pending"})
