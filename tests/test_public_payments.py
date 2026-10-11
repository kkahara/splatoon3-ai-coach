"""Per-analysis price, Checkout gate, and funnel counts."""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from fastapi.testclient import TestClient
from public_site.accounts import AccountStore
from public_site.app import SESSION_COOKIE, create_app
from public_site.errors import PAYMENT_UNAVAILABLE, PROBE_DURATION
from public_site.media_probe import ProbeFacts, ProbeRejected
from public_site.payments import PaymentStore
from public_site.settings import PublicSettings
from public_site.stripe_checkout import (
    CheckoutEvent,
    CheckoutSession,
    CheckoutSignatureError,
)
from public_site.tokens import hash_token

from splatoon3_ai_coach.config.paths import default_config_path

_PASSWORD = "correct-horse"


def _database_url() -> str:
    url = os.environ.get("PUBLIC_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("PUBLIC_DATABASE_URL is not set")
    parts = urlsplit(url)
    admin = urlunsplit((parts.scheme, parts.netloc, "/postgres", parts.query, ""))
    test_url = urlunsplit((parts.scheme, parts.netloc, "/coach_test", parts.query, ""))
    with psycopg.connect(admin, autocommit=True) as conn:
        found = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            ("coach_test",),
        ).fetchone()
        if found is None:
            conn.execute("CREATE DATABASE coach_test")
    return test_url


@pytest.fixture
def payments() -> PaymentStore:
    store = PaymentStore(_database_url())
    store.ensure_schema()
    store.clear()
    yield store
    store.clear()


class FakeCheckout:
    """In-memory Checkout. One idempotency key always returns the same session."""

    def __init__(self) -> None:
        self.sessions: dict[str, CheckoutSession] = {}
        self.by_key: dict[str, CheckoutSession] = {}
        self.created: list[CheckoutSession] = []
        self.expires_at: datetime | None = None
        self.success_url = ""

    def create_session(
        self,
        *,
        submission_id: str,
        price_cents: int,
        currency: str,
        expires_at: datetime,
        success_url: str,
        cancel_url: str,
        idempotency_key: str,
    ) -> CheckoutSession:
        existing = self.by_key.get(idempotency_key)
        if existing is not None:
            return existing
        session = CheckoutSession(
            id=f"cs_{len(self.created) + 1}",
            url=f"https://pay.example/{idempotency_key}",
            status="open",
            payment_status="unpaid",
            amount_total=price_cents,
            currency=currency,
            payment_intent_id=None,
            client_reference_id=submission_id,
        )
        self.sessions[session.id] = session
        self.by_key[idempotency_key] = session
        self.created.append(session)
        self.expires_at = expires_at
        self.success_url = success_url
        self.cancel_url = cancel_url
        return session

    def retrieve_session(self, session_id: str) -> CheckoutSession:
        return self.sessions[session_id]

    def construct_event(
        self, payload: bytes, signature: str, secret: str
    ) -> CheckoutEvent:
        if signature != "signed" or secret != "whsec":
            raise CheckoutSignatureError("invalid signature")
        body = json.loads(payload)
        session = CheckoutSession(**body["session"])
        return CheckoutEvent(type=body["type"], session=session)

    def expire(self, session_id: str) -> None:
        self.sessions[session_id] = replace(self.sessions[session_id], status="expired")


def _probe_ok(_path: Path) -> ProbeFacts:
    return ProbeFacts(12.0, 1920, 1080, "mp4")


def _client(
    tmp_path: Path,
    payments: PaymentStore,
    checkout: FakeCheckout,
    *,
    accounts: AccountStore | None = None,
    probe=_probe_ok,
    run_command=None,
) -> TestClient:
    root = tmp_path / "public"
    root.mkdir(parents=True, exist_ok=True)
    settings = PublicSettings(
        root=root,
        config_path=default_config_path(),
        static_dir=tmp_path / "no-dist",
        dev_mode=True,
        worker_id="worker-test",
        public_base_url="http://coach.example",
        max_video_bytes=1000,
        stripe_webhook_secret="whsec",
        payment_hold_seconds=24 * 60 * 60,
    )
    app = create_app(
        settings,
        accounts=accounts,
        payments=payments,
        checkout=checkout,
        verifier=lambda token: token == "ok",
        probe=probe,
        run_command=run_command or (lambda _argv: 0),
        start_worker=False,
    )
    return TestClient(app)


def _create(client: TestClient) -> dict:
    response = client.post(
        "/api/submissions",
        json={
            "turnstile_token": "ok",
            "filename": "match.mp4",
            "size_bytes": 8,
            "content_type": "video/mp4",
            "language": "en",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _put(client: TestClient, created: dict) -> str:
    upload = created["upload"]
    response = client.put(upload["url"], content=b"12345678", headers=upload["headers"])
    assert response.status_code == 200, response.text
    return created["token"]


def _gate(client: TestClient, token: str):
    submission = client.app.state.store.get_by_hash(hash_token(token))
    assert submission is not None
    return client.app.state.payments.get(submission.id), submission


def _event(
    session: CheckoutSession,
    *,
    payment_status: str = "paid",
    amount_total: int | None = None,
    client_reference_id: str | None = None,
    session_id: str | None = None,
) -> bytes:
    payload = {
        "type": "checkout.session.completed",
        "session": {
            "id": session_id or session.id,
            "url": session.url,
            "status": "complete",
            "payment_status": payment_status,
            "amount_total": (
                session.amount_total if amount_total is None else amount_total
            ),
            "currency": "usd",
            "payment_intent_id": "pi_test",
            "client_reference_id": client_reference_id or session.client_reference_id,
        },
    }
    return json.dumps(payload).encode()


def _webhook(client: TestClient, payload: bytes, signature: str = "signed"):
    return client.post(
        "/api/stripe/webhook",
        content=payload,
        headers={"stripe-signature": signature},
    )


def _admin_session(accounts: AccountStore) -> str:
    now = datetime.now(UTC)
    verify = accounts.register(
        name="Ada", email="ada@example.com", password=_PASSWORD, now=now
    )
    _account, session = accounts.verify(verify, now)
    accounts.set_role("ada@example.com", "admin")
    return session


def test_guest_is_refused_until_a_price_exists(
    tmp_path: Path, payments: PaymentStore
) -> None:
    client = _client(tmp_path, payments, FakeCheckout())
    refused = client.post(
        "/api/submissions",
        json={
            "turnstile_token": "ok",
            "filename": "match.mp4",
            "size_bytes": 8,
            "content_type": "video/mp4",
            "language": "en",
        },
    )
    assert refused.status_code == 503
    assert refused.json()["detail"] == PAYMENT_UNAVAILABLE
    assert client.app.state.store.list_submissions() == []
    config = client.get("/api/config").json()
    assert config["payment_required"] is True
    assert config["price_cents"] is None


def test_zero_price_queues_on_upload_without_checkout(
    tmp_path: Path, payments: PaymentStore
) -> None:
    payments.set_price(0)
    client = _client(tmp_path, payments, FakeCheckout())
    created = _create(client)
    assert created["payment_required"] is False
    token = _put(client, created)
    queued = client.post(f"/api/submissions/{token}/uploaded")
    assert queued.status_code == 200
    assert queued.json()["status"] == "queued"
    assert queued.json()["payment_state"] == "not_required"


def test_snapshot_survives_a_later_price_change(
    tmp_path: Path, payments: PaymentStore
) -> None:
    payments.set_price(99)
    with psycopg.connect(payments.database_url) as conn:
        conn.execute("UPDATE analysis_price SET pricing_version = 3, price_cents = 99")
    checkout = FakeCheckout()
    client = _client(tmp_path, payments, checkout)
    created = _create(client)
    assert created["price_cents"] == 99
    assert created["payment_required"] is True
    gate, submission = _gate(client, created["token"])
    assert gate.pricing_version == 3
    assert gate.price_cents == 99
    changed = payments.set_price(199)
    assert changed.pricing_version == 4
    assert changed.price_cents == 199

    token = _put(client, created)
    waiting = client.post(f"/api/submissions/{token}/uploaded")
    assert waiting.json()["status"] == "received"
    assert waiting.json()["payment_state"] == "unpaid"
    opened = client.post(f"/api/submissions/{token}/checkout")
    assert opened.status_code == 200, opened.text
    assert checkout.created[0].amount_total == 99
    assert checkout.expires_at == payments.get(submission.id).expires_at
    assert checkout.success_url == f"http://coach.example/review/{token}"

    mismatch = _webhook(client, _event(checkout.created[0], amount_total=199))
    assert mismatch.status_code == 400
    assert mismatch.json()["detail"] == "amount"
    assert payments.get(submission.id).payment_state == "checkout_created"

    paid = _webhook(client, _event(checkout.created[0]))
    assert paid.status_code == 200
    assert paid.json()["result"] == "paid"
    assert client.app.state.store.get(submission.id).status == "queued"
    fresh = payments.get(submission.id)
    assert fresh.payment_state == "paid"
    assert fresh.price_cents == 99
    assert fresh.pricing_version == 3
    assert fresh.payment_intent_id == "pi_test"


def test_checkout_reuses_an_open_session_and_replaces_an_expired_one(
    tmp_path: Path, payments: PaymentStore
) -> None:
    payments.set_price(199)
    checkout = FakeCheckout()
    client = _client(tmp_path, payments, checkout)
    created = _create(client)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    first = client.post(f"/api/submissions/{token}/checkout")
    second = client.post(f"/api/submissions/{token}/checkout")
    assert first.json()["url"] == second.json()["url"]
    assert len(checkout.created) == 1

    checkout.expire(checkout.created[0].id)
    replaced = client.post(f"/api/submissions/{token}/checkout")
    assert replaced.status_code == 200, replaced.text
    assert len(checkout.created) == 2
    assert replaced.json()["url"] != first.json()["url"]
    gate, submission = _gate(client, token)
    assert gate.checkout_attempt == 2
    assert gate.checkout_session_id == checkout.created[1].id

    stale = payments.apply_paid(
        submission_id=submission.id,
        session_id=checkout.created[0].id,
        amount_total=199,
        currency="usd",
        payment_intent_id="pi_old",
    )
    assert stale == "stale"
    current = payments.get(submission.id)
    assert current.payment_state == "checkout_created"
    assert current.checkout_session_id == checkout.created[1].id


def test_webhook_ignores_unpaid_status_and_a_foreign_session(
    tmp_path: Path, payments: PaymentStore
) -> None:
    payments.set_price(199)
    checkout = FakeCheckout()
    client = _client(tmp_path, payments, checkout)
    first = _create(client)
    second = _create(client)
    token = _put(client, first)
    client.post(f"/api/submissions/{token}/uploaded")
    client.post(f"/api/submissions/{token}/checkout")
    session = checkout.created[0]
    _other_gate, other = _gate(client, second["token"])

    unpaid = _webhook(client, _event(session, payment_status="unpaid"))
    assert unpaid.status_code == 200
    assert unpaid.json()["result"] == "ignored"
    gate, submission = _gate(client, token)
    assert gate.payment_state == "checkout_created"

    foreign = _webhook(client, _event(session, client_reference_id=other.id))
    assert foreign.status_code == 400
    assert foreign.json()["detail"] == "association"
    assert payments.get(submission.id).payment_state == "checkout_created"
    assert payments.get(other.id).payment_state == "unpaid"

    bad = _webhook(client, _event(session), signature="nope")
    assert bad.status_code == 400


def test_repeat_paid_webhook_does_not_queue_twice(
    tmp_path: Path, payments: PaymentStore
) -> None:
    payments.set_price(199)
    checkout = FakeCheckout()
    client = _client(tmp_path, payments, checkout)
    created = _create(client)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    client.post(f"/api/submissions/{token}/checkout")
    payload = _event(checkout.created[0])
    assert _webhook(client, payload).status_code == 200
    again = _webhook(client, payload)
    assert again.status_code == 200
    assert client.app.state.store.count_status("queued") == 1


def test_upload_and_payment_queue_exactly_once(payments: PaymentStore) -> None:
    payments.set_price(199)
    quote = payments.quote(admin=False)
    submission_id = uuid.uuid4().hex
    payments.open_gate(submission_id, quote, datetime.now(UTC) + timedelta(hours=24))
    attempt = payments.claim_checkout(submission_id, None)
    assert attempt is not None
    assert payments.attach_session(submission_id, attempt, "cs_race", "https://pay.example/race")
    barrier = threading.Barrier(2)
    wins: list[bool] = []

    def upload() -> None:
        barrier.wait(timeout=5)
        payments.mark_uploaded(submission_id)
        wins.append(payments.try_queue(submission_id))

    def pay() -> None:
        barrier.wait(timeout=5)
        payments.apply_paid(
            submission_id=submission_id,
            session_id="cs_race",
            amount_total=199,
            currency="usd",
            payment_intent_id="pi_race",
        )
        wins.append(payments.try_queue(submission_id))

    threads = [threading.Thread(target=upload), threading.Thread(target=pay)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    gate = payments.get(submission_id)
    assert gate is not None
    assert gate.status == "queued"
    assert gate.uploaded is True
    assert gate.payment_state == "paid"
    assert wins.count(True) == 1


def test_confirmed_payment_wins_over_unpaid_cleanup(
    tmp_path: Path, payments: PaymentStore
) -> None:
    payments.set_price(199)
    quote = payments.quote(admin=False)
    past = datetime.now(UTC) - timedelta(minutes=1)
    submission_id = uuid.uuid4().hex
    payments.open_gate(submission_id, quote, past)
    payments.mark_uploaded(submission_id)
    attempt = payments.claim_checkout(submission_id, None)
    assert attempt is not None
    payments.attach_session(submission_id, attempt, "cs_tie", "https://pay.example/tie")
    video = tmp_path / "source.mp4"
    video.write_bytes(b"12345678")

    def pay() -> None:
        payments.apply_paid(
            submission_id=submission_id,
            session_id="cs_tie",
            amount_total=199,
            currency="usd",
            payment_intent_id="pi_tie",
        )

    def expire() -> None:
        if not payments.claim_unpaid_expiry(submission_id, datetime.now(UTC)):
            return
        if payments.remains_unpaid_expired(submission_id) and video.is_file():
            video.unlink()

    for _ in range(12):
        if not video.is_file():
            video.write_bytes(b"12345678")
        with psycopg.connect(payments.database_url) as conn:
            conn.execute(
                """
                UPDATE submission_gates
                SET status = 'received', payment_state = 'checkout_created',
                    uploaded = true, expires_at = %s
                WHERE submission_id = %s
                """,
                (past, submission_id),
            )
        barrier = threading.Barrier(2)

        def run_pay(start: threading.Barrier = barrier) -> None:
            start.wait(timeout=5)
            pay()

        def run_expire(start: threading.Barrier = barrier) -> None:
            start.wait(timeout=5)
            expire()

        threads = [threading.Thread(target=run_pay), threading.Thread(target=run_expire)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        gate = payments.get(submission_id)
        assert gate is not None
        if gate.payment_state == "paid":
            assert video.is_file()
            assert gate.status != "expired"
        else:
            assert gate.status == "expired"
            assert gate.payment_state != "paid"


def test_unpaid_upload_is_not_queued_and_expires_with_its_video(
    tmp_path: Path, payments: PaymentStore
) -> None:
    payments.set_price(199)
    client = _client(tmp_path, payments, FakeCheckout())
    created = _create(client)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    submission = client.app.state.store.list_submissions()[0]
    video = client.app.state.store.get_video(submission.id)
    assert video is not None
    assert client.app.state.service.storage.head(video.object_key) is not None
    aged = datetime.now(UTC) - timedelta(hours=2)
    submission.created_at = aged.strftime("%Y-%m-%dT%H:%M:%SZ")
    client.app.state.store.save_submission(submission)
    client.app.state.service.maintain(datetime.now(UTC))
    held = client.app.state.store.get(submission.id)
    assert held is not None
    assert held.status == "received"
    assert client.app.state.service.storage.head(video.object_key) is not None

    with psycopg.connect(payments.database_url) as conn:
        conn.execute(
            "UPDATE submission_gates SET expires_at = %s WHERE submission_id = %s",
            (datetime.now(UTC) - timedelta(minutes=1), submission.id),
        )
    client.app.state.service.maintain(datetime.now(UTC))
    expired = client.app.state.store.get(submission.id)
    assert expired is not None
    assert expired.status == "expired"
    assert client.app.state.service.storage.head(video.object_key) is None
    assert any(row.kind == "analysis_failed" for row in payments.event_counts())


def test_paid_cleanup_leaves_the_video(tmp_path: Path, payments: PaymentStore) -> None:
    payments.set_price(199)
    checkout = FakeCheckout()
    client = _client(tmp_path, payments, checkout)
    created = _create(client)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    client.post(f"/api/submissions/{token}/checkout")
    _webhook(client, _event(checkout.created[0]))
    submission = client.app.state.store.list_submissions()[0]
    video = client.app.state.store.get_video(submission.id)
    assert video is not None
    with psycopg.connect(payments.database_url) as conn:
        conn.execute(
            """
            UPDATE submission_gates
            SET status = 'received', expires_at = %s
            WHERE submission_id = %s
            """,
            (datetime.now(UTC) - timedelta(minutes=1), submission.id),
        )
    assert payments.claim_unpaid_expiry(submission.id, datetime.now(UTC)) is False
    client.app.state.service.maintain(datetime.now(UTC))
    assert client.app.state.service.storage.head(video.object_key) is not None
    assert payments.get(submission.id).payment_state == "paid"


def test_funnel_counts_failures_without_a_conversion_rate(
    tmp_path: Path, payments: PaymentStore
) -> None:
    payments.set_price(199)
    accounts = AccountStore(payments.database_url)
    accounts.ensure_schema()
    accounts.clear()

    def fail(_path: Path) -> ProbeFacts:
        raise ProbeRejected(PROBE_DURATION)

    checkout = FakeCheckout()
    client = _client(tmp_path, payments, checkout, accounts=accounts, probe=fail)
    assert client.post("/api/funnel/page-view").status_code == 200
    created = _create(client)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    client.post(f"/api/submissions/{token}/checkout")
    _webhook(client, _event(checkout.created[0]))
    counts = {row.kind: row.count for row in payments.event_counts()}
    assert counts["page_viewed"] == 1
    assert counts["upload_started"] == 1
    assert counts["upload_completed"] == 1
    assert counts["checkout_opened"] == 1
    assert counts["payment_completed"] == 1
    assert counts["analysis_failed"] == 1

    client.cookies.set(SESSION_COOKIE, _admin_session(accounts))
    admin = client.get("/api/admin/price")
    assert admin.status_code == 200
    body = admin.json()
    assert body["price_cents"] == 199
    assert body["pricing_version"] == 1
    assert "conversion" not in admin.text
    assert any(row["kind"] == "analysis_failed" for row in body["events"])
    saved = client.put("/api/admin/price", json={"price_cents": 299})
    assert saved.status_code == 200
    assert saved.json()["pricing_version"] == 2
    assert saved.json()["price_cents"] == 299
    accounts.clear()


def test_admin_skips_payment_when_no_price_is_saved(
    tmp_path: Path, payments: PaymentStore
) -> None:
    accounts = AccountStore(payments.database_url)
    accounts.ensure_schema()
    accounts.clear()
    client = _client(tmp_path, payments, FakeCheckout(), accounts=accounts)
    client.cookies.set(SESSION_COOKIE, _admin_session(accounts))
    created = _create(client)
    assert created["payment_required"] is False
    token = _put(client, created)
    queued = client.post(f"/api/submissions/{token}/uploaded")
    assert queued.json()["status"] == "queued"
    accounts.clear()


def test_processing_failure_is_an_allowlisted_funnel_event(
    tmp_path: Path, payments: PaymentStore
) -> None:
    payments.set_price(199)
    checkout = FakeCheckout()
    client = _client(tmp_path, payments, checkout, run_command=lambda _argv: 1)
    created = _create(client)
    token = _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    client.post(f"/api/submissions/{token}/checkout")
    _webhook(client, _event(checkout.created[0]))
    assert client.app.state.worker.pump() is True
    failed = [row for row in payments.event_counts() if row.kind == "analysis_failed"]
    assert len(failed) == 1
    assert failed[0].price_cents == 199
    with psycopg.connect(payments.database_url) as conn:
        row = conn.execute(
            "SELECT failure_category FROM price_events WHERE kind = 'analysis_failed'"
        ).fetchone()
    assert row is not None
    assert row[0] == "processing"


def test_same_price_does_not_bump_the_version(payments: PaymentStore) -> None:
    first = payments.set_price(199)
    second = payments.set_price(199)
    third = payments.set_price(299)
    assert first.pricing_version == 1
    assert second.pricing_version == 1
    assert third.pricing_version == 2
