"""Voucher validation, redemption, and the unchanged Stripe path."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from loguru import logger
from public_site.errors import PROBE_DURATION
from public_site.media_probe import ProbeRejected
from public_site.payments import PaymentStore
from public_site.service import _ready
from public_site.vouchers import (
    MATCH_ANALYSIS_SERVICE_ID,
    VoucherIssue,
    VoucherStore,
    code_hash,
    normalize_voucher_code,
)
from test_public_payments import _client, _create, _database_url, _probe_ok, _put

_SECRET = "test-voucher-secret"
_NOW = datetime(2026, 6, 1, tzinfo=UTC)
_EXPIRES = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)


def test_normalization_is_stable_and_rejects_internal_space() -> None:
    assert normalize_voucher_code("  squidlab50 ") == "SQUIDLAB50"
    assert normalize_voucher_code("skt-7k4m-p9qx") == "SKT-7K4M-P9QX"
    assert code_hash("SQUIDLAB50", _SECRET) == code_hash(
        normalize_voucher_code(" squidlab50"),
        _SECRET,
    )
    with pytest.raises(ValueError):
        normalize_voucher_code("SQUID LAB")
    with pytest.raises(ValueError):
        normalize_voucher_code("   ")


@pytest.fixture
def payments() -> PaymentStore:
    store = PaymentStore(_database_url())
    store.ensure_schema()
    store.clear()
    yield store
    store.clear()


@pytest.fixture
def vouchers(payments: PaymentStore) -> VoucherStore:
    store = VoucherStore(payments.database_url, _SECRET)
    store.ensure_schema()
    payments.clear()
    store.clear()
    payments.set_price(500)
    yield store
    payments.clear()
    store.clear()


def test_skilltown_code_is_hashed_and_duplicate_rejected(vouchers: VoucherStore) -> None:
    issued = vouchers.issue(_spec("skilltown"), _NOW)
    assert issued.code.startswith("SKT-")
    _assert_plaintext_absent(vouchers, issued.code)
    with pytest.raises(ValueError, match="already exists"):
        vouchers.issue(_spec("youtube", code=issued.code, max_redemptions=2), _NOW)


def test_expiration_boundary_and_revocation(vouchers: VoucherStore) -> None:
    issued = vouchers.issue(_spec("youtube", code="squidlab50", max_redemptions=3), _NOW)
    vouchers.preview(" SQUIDLAB50 ", _EXPIRES - timedelta(microseconds=1))
    with pytest.raises(Exception, match="expired") as caught:
        vouchers.preview(issued.code, _EXPIRES)
    assert caught.value.reason == "expired"
    assert vouchers.revoke(issued.voucher_id) is True
    with pytest.raises(Exception, match="revoked") as revoked:
        vouchers.preview(issued.code, _NOW)
    assert revoked.value.reason == "revoked"


def test_wrong_service_is_rejected(vouchers: VoucherStore) -> None:
    issued = vouchers.issue(_spec("skilltown"), _NOW)
    with vouchers._conn() as conn:
        conn.execute(
            "UPDATE vouchers SET eligible_service_id = %s WHERE id = %s",
            ("future_premium", issued.voucher_id),
        )
    with pytest.raises(Exception, match="not valid for this service") as caught:
        vouchers.preview(issued.code, _NOW)
    assert caught.value.reason == "wrong_service"


def test_pending_attachment_does_not_authorize_or_consume(
    tmp_path, payments: PaymentStore, vouchers: VoucherStore
) -> None:
    issued = vouchers.issue(_spec("skilltown"), _NOW)
    client = _voucher_client(tmp_path, payments)
    created = _create_with(client, issued.code)
    assert created["payment_required"] is True
    assert created["voucher_state"] == "pending"
    assert vouchers.redemption_count(issued.voucher_id) == 0
    _submission = _loaded(client, created["token"])[1]
    payments.mark_uploaded(_submission.id)
    gate = payments.get(_submission.id)
    assert gate is not None and gate.uploaded is True
    assert gate.payment_state == "unpaid"
    assert gate.voucher_state == "pending"
    assert _ready(gate) is False
    assert payments.try_queue(_submission.id) is False
    _assert_disk_has_no_code(tmp_path, issued.code)


def test_validate_does_not_redeem_and_final_confirm_does(
    tmp_path, payments: PaymentStore, vouchers: VoucherStore
) -> None:
    issued = vouchers.issue(_spec("skilltown"), _NOW)
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)))
    try:
        client = _voucher_client(tmp_path, payments)
        checked = client.post("/api/vouchers/validate", json={"code": f" {issued.code} "})
        assert checked.status_code == 200, checked.text
        assert checked.json()["accepted"] is True
        assert checked.json()["amount_due_cents"] == 0
        assert "code" not in checked.json()
        assert vouchers.redemption_count(issued.voucher_id) == 0
        created = _submit(client, issued.code)
    finally:
        logger.remove(sink)
    assert issued.code not in "".join(messages)
    view = created
    assert view["payment_state"] == "not_required"
    assert view["voucher_state"] == "redeemed"
    assert view["status"] == "queued"
    assert vouchers.redemption_count(issued.voucher_id) == 1
    again = client.post(
        "/api/submissions",
        json=_body(issued.code),
    )
    assert again.status_code == 400
    assert again.json()["reason"] == "exhausted"


def test_same_submission_redeem_is_idempotent(
    payments: PaymentStore, vouchers: VoucherStore
) -> None:
    issued = vouchers.issue(_spec("skilltown"), _NOW)
    submission_id = _open_pending(payments, issued.voucher_id)
    moment = datetime.now(UTC)
    first = vouchers.finalize(submission_id, moment)
    second = vouchers.finalize(submission_id, moment)
    assert first.outcome == "redeemed"
    assert second.outcome == "redeemed"
    assert vouchers.redemption_count(issued.voucher_id) == 1
    gate = payments.get(submission_id)
    assert gate is not None
    assert gate.payment_state == "not_required"
    assert gate.status == "queued"


def test_unexpected_redeemed_state_is_not_repaired(
    payments: PaymentStore, vouchers: VoucherStore
) -> None:
    issued = vouchers.issue(_spec("skilltown"), _NOW)
    submission_id = _open_pending(payments, issued.voucher_id)
    assert vouchers.finalize(submission_id, datetime.now(UTC)).outcome == "redeemed"
    with payments._conn() as conn:
        conn.execute(
            """
            UPDATE submission_gates
            SET payment_state = 'unpaid', voucher_state = 'pending', status = 'received'
            WHERE submission_id = %s
            """,
            (submission_id,),
        )
    decision = vouchers.finalize(submission_id, datetime.now(UTC))
    assert decision.outcome == "conflict"
    assert vouchers.redemption_count(issued.voucher_id) == 1
    gate = payments.get(submission_id)
    assert gate is not None
    assert gate.payment_state == "unpaid"


def test_voucher_can_expire_or_be_revoked_before_redemption(
    tmp_path, payments: PaymentStore, vouchers: VoucherStore
) -> None:
    expired = vouchers.issue(_spec("skilltown"), _NOW)
    revoked = vouchers.issue(_spec("skilltown"), _NOW)
    client = _voucher_client(tmp_path, payments)
    first = _create_with(client, expired.code)
    second = _create_with(client, revoked.code)
    _expire(vouchers, expired.voucher_id)
    assert vouchers.revoke(revoked.voucher_id) is True
    _put(client, first)
    failed = client.post(f"/api/submissions/{first['token']}/uploaded")
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "received"
    assert failed.json()["voucher_state"] == "failed"
    assert failed.json()["voucher_failure_reason"] == "expired"
    assert failed.json()["payment_state"] == "unpaid"
    assert vouchers.redemption_count(expired.voucher_id) == 0
    checkout = client.post(f"/api/submissions/{first['token']}/checkout")
    assert checkout.status_code == 200, checkout.text
    assert checkout.json()["url"]
    _put(client, second)
    blocked = client.post(f"/api/submissions/{second['token']}/uploaded")
    assert blocked.json()["voucher_failure_reason"] == "revoked"
    assert blocked.json()["status"] == "received"
    assert vouchers.redemption_count(revoked.voucher_id) == 0


def test_another_submission_can_take_the_last_use(
    payments: PaymentStore, vouchers: VoucherStore
) -> None:
    issued = vouchers.issue(_spec("youtube", code="SQUIDLAB50", max_redemptions=1), _NOW)
    first = _open_pending(payments, issued.voucher_id)
    second = _open_pending(payments, issued.voucher_id)
    assert vouchers.finalize(first, datetime.now(UTC)).outcome == "redeemed"
    loser = vouchers.finalize(second, datetime.now(UTC))
    assert loser.outcome == "failed"
    assert loser.reason == "exhausted"
    gate = payments.get(second)
    assert gate is not None
    assert gate.payment_state == "unpaid"
    assert gate.status == "received"
    assert vouchers.redemption_count(issued.voucher_id) == 1
    assert payments.claim_checkout(second, None) == 1


def test_youtube_limit_and_concurrent_last_use(
    payments: PaymentStore, vouchers: VoucherStore
) -> None:
    issued = vouchers.issue(_spec("youtube", code="CREATOR20", max_redemptions=2), _NOW)
    winners = [_open_pending(payments, issued.voucher_id) for _ in range(2)]
    for submission_id in winners:
        assert vouchers.finalize(submission_id, datetime.now(UTC)).outcome == "redeemed"
    assert vouchers.redemption_count(issued.voucher_id) == 2
    extra = _open_pending(payments, issued.voucher_id)
    assert vouchers.finalize(extra, datetime.now(UTC)).reason == "exhausted"

    raced = vouchers.issue(_spec("youtube", code="LASTSEAT", max_redemptions=1), _NOW)
    left = _open_pending(payments, raced.voucher_id)
    right = _open_pending(payments, raced.voucher_id)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def redeem(submission_id: str) -> None:
        barrier.wait()
        outcomes.append(vouchers.finalize(submission_id, datetime.now(UTC)).outcome)

    threads = [threading.Thread(target=redeem, args=(item,)) for item in (left, right)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["failed", "redeemed"]
    assert vouchers.redemption_count(raced.voucher_id) == 1


def test_checkout_cannot_race_a_pending_redemption(
    payments: PaymentStore, vouchers: VoucherStore
) -> None:
    issued = vouchers.issue(_spec("skilltown"), _NOW)
    submission_id = _open_pending(payments, issued.voucher_id)
    barrier = threading.Barrier(2)
    found: dict[str, object] = {}

    def redeem() -> None:
        barrier.wait()
        found["decision"] = vouchers.finalize(submission_id, datetime.now(UTC))

    def checkout() -> None:
        barrier.wait()
        found["attempt"] = payments.claim_checkout(submission_id, None)

    threads = [threading.Thread(target=redeem), threading.Thread(target=checkout)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    gate = payments.get(submission_id)
    assert gate is not None
    assert found["attempt"] is None
    assert gate.checkout_session_id is None
    assert gate.payment_state == "not_required"
    assert found["decision"].outcome == "redeemed"


def _reject_probe(_path):
    raise ProbeRejected(PROBE_DURATION)


def test_probe_failure_does_not_burn_the_voucher(
    tmp_path, payments: PaymentStore, vouchers: VoucherStore
) -> None:
    issued = vouchers.issue(_spec("skilltown"), _NOW)
    client = _voucher_client(tmp_path / "bad", payments, probe=_reject_probe)
    created = _create_with(client, issued.code)
    _put(client, created)
    failed = client.post(f"/api/submissions/{created['token']}/uploaded")
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "failed"
    assert vouchers.redemption_count(issued.voucher_id) == 0
    healthy = _voucher_client(tmp_path / "good", payments)
    view = _submit(healthy, issued.code)
    assert view["voucher_state"] == "redeemed"
    assert vouchers.redemption_count(issued.voucher_id) == 1


def test_voucher_keeps_file_and_submission_limits(
    tmp_path, payments: PaymentStore, vouchers: VoucherStore
) -> None:
    issued = vouchers.issue(_spec("youtube", code="LIMITED50", max_redemptions=5), _NOW)
    checking = _voucher_client(tmp_path / "files", payments)
    bad = checking.post(
        "/api/submissions",
        json=_body(issued.code, filename="notes.txt", content_type="text/plain"),
    )
    assert bad.status_code == 400
    assert vouchers.redemption_count(issued.voucher_id) == 0
    limited = _voucher_client(tmp_path / "limit", payments, submissions_per_hour=1)
    _submit(limited, issued.code)
    second = limited.post("/api/submissions", json=_body(issued.code))
    assert second.status_code == 429
    guessing = _voucher_client(tmp_path / "guess", payments)
    for _ in range(20):
        response = guessing.post("/api/vouchers/validate", json={"code": "NOPE"})
        assert response.status_code == 400
    blocked = guessing.post("/api/vouchers/validate", json={"code": "NOPE"})
    assert blocked.status_code == 429


def test_missing_secret_fails_closed_without_changing_stripe(
    tmp_path, payments: PaymentStore, vouchers: VoucherStore
) -> None:
    issued = vouchers.issue(_spec("skilltown"), _NOW)
    locked = _voucher_client(tmp_path, payments, secret="")
    refused = locked.post("/api/vouchers/validate", json={"code": issued.code})
    assert refused.status_code == 503
    assert refused.json()["reason"] == "unavailable"
    created = _create(locked)
    assert created["payment_required"] is True
    assert created["voucher_state"] is None
    _put(locked, created)
    checkout = locked.post(f"/api/submissions/{created['token']}/checkout")
    assert checkout.status_code == 200, checkout.text
    assert checkout.json()["url"]
    assert vouchers.redemption_count(issued.voucher_id) == 0


def test_invalid_code_is_rejected(
    tmp_path, payments: PaymentStore, vouchers: VoucherStore
) -> None:
    client = _voucher_client(tmp_path, payments)
    response = client.post("/api/vouchers/validate", json={"code": "NOT-A-REAL-CODE"})
    assert response.status_code == 400
    assert response.json()["reason"] == "invalid"
    assert "NOT-A-REAL-CODE" not in response.text


def _spec(source: str, code: str | None = None, max_redemptions: int = 1) -> VoucherIssue:
    benefit = "externally_prepaid" if source == "skilltown" else "complimentary"
    return VoucherIssue(
        source=source,
        campaign=f"{source}_2026",
        eligible_service_id=MATCH_ANALYSIS_SERVICE_ID,
        benefit_type=benefit,
        max_redemptions=max_redemptions,
        expires_at=_EXPIRES,
        code=code,
    )


def _voucher_client(tmp_path, payments: PaymentStore, **overrides) -> TestClient:
    secret = overrides.pop("secret", _SECRET)
    client = _client(
        tmp_path,
        payments,
        _Checkout(),
        probe=overrides.pop("probe", _probe_ok),
    )
    service = client.app.state.service
    service.vouchers.secret = secret
    per_hour = overrides.get("submissions_per_hour")
    if per_hour is not None:
        service.settings = replace(service.settings, submissions_per_hour=per_hour)
    return client


class _Checkout:
    """Enough Checkout to prove the no-voucher path still opens a session."""

    def __init__(self) -> None:
        self.created = []

    def create_session(self, **kwargs):
        from public_site.stripe_checkout import CheckoutSession

        session = CheckoutSession(
            id=f"cs_{len(self.created) + 1}",
            url="https://pay.example/voucher-test",
            status="open",
            payment_status="unpaid",
            amount_subtotal=kwargs["price_cents"],
            amount_total=kwargs["price_cents"],
            currency=kwargs["currency"],
            payment_intent_id=None,
            client_reference_id=kwargs["submission_id"],
        )
        self.created.append(session)
        return session

    def retrieve_session(self, session_id: str):
        return next(item for item in self.created if item.id == session_id)


def _create_with(client: TestClient, code: str) -> dict:
    response = client.post("/api/submissions", json=_body(code))
    assert response.status_code == 200, response.text
    return response.json()


def _submit(client: TestClient, code: str) -> dict:
    created = _create_with(client, code)
    _put(client, created)
    response = client.post(f"/api/submissions/{created['token']}/uploaded")
    assert response.status_code == 200, response.text
    return response.json()


def _body(
    code: str,
    filename: str = "match.mp4",
    content_type: str = "video/mp4",
) -> dict:
    return {
        "turnstile_token": "ok",
        "filename": filename,
        "size_bytes": 8,
        "content_type": content_type,
        "language": "en",
        "voucher_code": code,
    }


def _loaded(client: TestClient, token: str):
    from public_site.tokens import hash_token

    submission = client.app.state.store.get_by_hash(hash_token(token))
    assert submission is not None
    gate = client.app.state.payments.get(submission.id)
    assert gate is not None
    return gate, submission


def _open_pending(payments: PaymentStore, voucher_id: str) -> str:
    import uuid

    submission_id = uuid.uuid4().hex
    quote = payments.quote(admin=False)
    payments.open_gate(
        submission_id,
        quote,
        datetime.now(UTC) + timedelta(hours=1),
        voucher_id,
    )
    assert payments.mark_uploaded(submission_id) is True
    return submission_id


def _expire(vouchers: VoucherStore, voucher_id: str) -> None:
    with vouchers._conn() as conn:
        conn.execute(
            "UPDATE vouchers SET expires_at = %s WHERE id = %s",
            (datetime.now(UTC) - timedelta(seconds=1), voucher_id),
        )


def _assert_plaintext_absent(vouchers: VoucherStore, code: str) -> None:
    with vouchers._conn() as conn:
        rows = conn.execute("SELECT * FROM vouchers").fetchall()
    blob = " ".join(str(value) for row in rows for value in row.values())
    assert code not in blob


def _assert_disk_has_no_code(tmp_path, code: str) -> None:
    root = tmp_path / "public"
    for path in root.rglob("*.json"):
        assert code not in path.read_text()
