"""Customer support reference: format, uniqueness, migration, lifecycle and lookup.

The reference identifies one submission for support. It never grants access.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from public_site import payments as payments_module
from public_site.payments import PaymentStore, PriceQuote
from public_site.public_reference import ALPHABET, new_reference, normalize_reference
from test_public_payments import (
    FakeCheckout,
    _client,
    _create,
    _event,
    _put,
    _webhook,
    payments,  # noqa: F401
)
from test_public_recordings import (
    _checkout,
    _latest_challenge,
    _login,
    _verify_guest,
    _view,
    site,  # noqa: F401
)
from test_public_recordings import _create as _create_review
from test_public_review_vouchers import _issue, _pending_guest, review  # noqa: F401

REFERENCE = re.compile(r"^S3-[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{8}$")
QUOTE = PriceQuote(199, "usd", 1, "unpaid")


def _open(payments: PaymentStore, submission_id: str) -> str:  # noqa: F811
    payments.open_gate(submission_id, QUOTE, datetime.now(UTC) + timedelta(hours=1))
    return payments.get(submission_id).public_reference


def _references(payments: PaymentStore) -> dict[str, str | None]:  # noqa: F811
    with payments._conn() as conn:
        rows = conn.execute(
            "SELECT submission_id, public_reference FROM submission_gates"
        ).fetchall()
    return {row["submission_id"]: row["public_reference"] for row in rows}


def _video_reference(client: TestClient, token: str) -> str:
    return client.get(f"/api/submissions/{token}").json()["public_reference"]


# Format


def test_references_use_the_unambiguous_alphabet() -> None:
    assert not set("01ILO") & set(ALPHABET)
    drawn = {new_reference() for _ in range(500)}
    assert all(REFERENCE.fullmatch(reference) for reference in drawn)
    assert len(drawn) == 500


def test_operator_input_is_normalized_or_refused() -> None:
    assert normalize_reference(" s3-k7m4p9q2 ") == "S3-K7M4P9Q2"
    assert normalize_reference("K7M4P9Q2") == "S3-K7M4P9Q2"
    for text in ("S3-K7M4P9Q0", "S3-K7M4P9Q", "rin@example.com", "", "0f3c9a5e" * 4):
        assert normalize_reference(text) is None


# Database: uniqueness, collision, backfill


def test_each_gate_gets_its_own_reference_once(payments: PaymentStore) -> None:  # noqa: F811
    first, second = _open(payments, "a" * 32), _open(payments, "b" * 32)
    assert REFERENCE.fullmatch(first) and REFERENCE.fullmatch(second)
    assert first != second
    assert payments.get_by_reference(first).submission_id == "a" * 32
    assert payments.get("a" * 32).public_reference == first


def test_a_reference_clash_draws_again(
    payments: PaymentStore, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    drawn = iter(["S3-AAAAAAAA", "S3-AAAAAAAA", "S3-AAAAAAAA", "S3-BBBBBBBB"])
    monkeypatch.setattr(payments_module, "new_reference", lambda: next(drawn))
    assert _open(payments, "a" * 32) == "S3-AAAAAAAA"
    assert _open(payments, "b" * 32) == "S3-BBBBBBBB"
    assert next(drawn, None) is None


def test_endless_clashes_fail_and_a_duplicate_id_still_raises(
    payments: PaymentStore, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    monkeypatch.setattr(payments_module, "new_reference", lambda: "S3-AAAAAAAA")
    _open(payments, "a" * 32)
    with pytest.raises(RuntimeError, match="support reference"):
        _open(payments, "b" * 32)
    assert payments.get("b" * 32) is None
    monkeypatch.setattr(payments_module, "new_reference", lambda: "S3-CCCCCCCC")
    with pytest.raises(psycopg.errors.UniqueViolation):
        _open(payments, "a" * 32)


def test_the_database_enforces_presence_format_and_uniqueness(
    payments: PaymentStore,  # noqa: F811
) -> None:
    first = _open(payments, "a" * 32)
    _open(payments, "b" * 32)
    for value, error in (
        (first, psycopg.errors.UniqueViolation),
        ("S3-0000000O", psycopg.errors.CheckViolation),
        (None, psycopg.errors.NotNullViolation),
    ):
        with pytest.raises(error), payments._conn() as conn:
            conn.execute(
                """UPDATE submission_gates SET public_reference = %s
                   WHERE submission_id = %s""",
                (value, "b" * 32),
            )


def test_migration_backfills_missing_references_and_keeps_existing_ones(
    payments: PaymentStore,  # noqa: F811
) -> None:
    kept = _open(payments, "a" * 32)
    for name in "bcd":
        _open(payments, name * 32)
    with payments._conn() as conn:
        conn.execute(
            "ALTER TABLE submission_gates ALTER COLUMN public_reference DROP NOT NULL"
        )
        conn.execute(
            """UPDATE submission_gates SET public_reference = NULL
               WHERE submission_id <> %s""",
            ("a" * 32,),
        )
    payments.ensure_schema()
    backfilled = _references(payments)
    assert backfilled["a" * 32] == kept
    assert all(REFERENCE.fullmatch(value) for value in backfilled.values())
    assert len(set(backfilled.values())) == 4
    payments.ensure_schema()
    assert _references(payments) == backfilled


# Lifecycle: video upload


def test_video_reference_survives_payment_and_queueing(
    tmp_path: Path, payments: PaymentStore  # noqa: F811
) -> None:
    payments.set_price(199)
    checkout = FakeCheckout()
    client = _client(tmp_path, payments, checkout)
    created = _create(client)
    token = created["token"]
    reference = _video_reference(client, token)
    assert REFERENCE.fullmatch(reference)
    assert _video_reference(client, token) == reference
    _put(client, created)
    client.post(f"/api/submissions/{token}/uploaded")
    assert client.post(f"/api/submissions/{token}/checkout").status_code == 200
    assert _video_reference(client, token) == reference
    assert _webhook(client, _event(checkout.created[0])).status_code == 200
    view = client.get(f"/api/submissions/{token}").json()
    assert view["status"] == "queued"
    assert view["public_reference"] == reference


def test_video_reference_survives_unpaid_expiry(
    tmp_path: Path, payments: PaymentStore  # noqa: F811
) -> None:
    payments.set_price(199)
    client = _client(tmp_path, payments, FakeCheckout())
    created = _create(client)
    reference = _video_reference(client, created["token"])
    submission = client.app.state.store.list_submissions()[0]
    later = datetime.now(UTC) + timedelta(days=3)
    assert payments.claim_unpaid_expiry(submission.id, later)
    assert payments.get(submission.id).status == "expired"
    assert payments.get(submission.id).public_reference == reference


def test_notices_carry_the_reference_and_not_the_internal_id(
    tmp_path: Path, payments: PaymentStore  # noqa: F811
) -> None:
    payments.set_price(0)
    client = _client(tmp_path, payments, FakeCheckout())
    created = _create(client)
    service = client.app.state.service
    submission = service.store.list_submissions()[0]
    submission.notify, submission.email = True, "rin@example.com"
    service.store.save_submission(submission)
    service.notices.seal(submission.id, created["token"])
    reference = payments.get(submission.id).public_reference
    outbox = client.app.state.settings.root / "outbox"
    for kind in ("received", "failed"):
        service.deliver_notice(submission, kind)
        text = (outbox / f"{submission.id}.{kind}.txt").read_text(encoding="utf-8")
        assert f"Support reference: {reference}" in text
        assert submission.id not in text


# Lifecycle: review code


def _review_reference(site: TestClient, created: dict) -> str:  # noqa: F811
    view = _view(site, created)
    assert "job_reference" not in view["acquisition"]
    assert created["submission_id"] not in json.dumps(view)
    return view["public_reference"]


def test_review_code_reference_survives_verification_payment_and_failure(
    site: TestClient,  # noqa: F811
) -> None:
    created = _create_review(site)
    reference = _review_reference(site, created)
    assert REFERENCE.fullmatch(reference)
    assert reference not in _latest_challenge_text(site)
    site.post(f"/api/submissions/{created['token']}/verify-contact")
    _verify_guest(site)
    assert _review_reference(site, created) == reference
    session = _checkout(site, created)
    assert _webhook(site, _event(session)).status_code == 200
    assert _view(site, created)["acquisition"]["state"] == "awaiting_recording"
    assert _review_reference(site, created) == reference
    _login(site, admin=True)
    endpoint = f"/api/admin/recordings/{created['submission_id']}"
    job = site.get(endpoint).json()["job"]
    assert job["public_reference"] == reference
    failed = site.post(
        endpoint + "/actions",
        json={
            "action": "recording_failed",
            "expected_version": job["version"],
            "note": "n",
        },
    )
    assert failed.status_code == 200, failed.text
    assert _review_reference(site, created) == reference
    listed = site.get("/api/admin/recordings").json()["jobs"]
    assert [item["public_reference"] for item in listed] == [reference]


def _latest_challenge_text(site: TestClient) -> str:  # noqa: F811
    token = _latest_challenge(site)
    root = site.app.state.settings.root / "outbox"
    for path in root.glob("confirm.*.txt"):
        text = path.read_text(encoding="utf-8")
        if token in text:
            return text
    raise AssertionError("confirmation email not found")


def test_review_code_reference_survives_voucher_redemption(
    review: TestClient,  # noqa: F811
) -> None:
    issued = _issue(review, "review_code")
    created = _pending_guest(review, issued.code)
    reference = _review_reference(review, created)
    _verify_guest(review)
    view = _view(review, created)
    assert view["voucher_state"] == "redeemed"
    assert view["public_reference"] == reference


# Admin lookup: identification only


def test_admin_finds_a_submission_by_reference(site: TestClient) -> None:  # noqa: F811
    created = _create_review(site)
    reference = _view(site, created)["public_reference"]
    assert site.get(f"/api/admin/reference/{reference}").status_code in {401, 403}
    _login(site, admin=True)
    for text in (reference, reference.lower(), reference.removeprefix("S3-")):
        found = site.get(f"/api/admin/reference/{text}")
        assert found.status_code == 200, found.text
        body = found.json()
        assert body["submission_id"] == created["submission_id"]
        assert body["public_reference"] == reference
        assert body["source"] == "nintendo_review_code"
        assert body["acquisition_state"] == "pending_payment"
    assert site.get("/api/admin/reference/S3-22222222").status_code == 404
    assert site.get("/api/admin/reference/not-a-reference").status_code == 404


def test_a_reference_never_opens_a_submission(site: TestClient) -> None:  # noqa: F811
    created = _create_review(site)
    reference = _view(site, created)["public_reference"]
    for path in (
        f"/api/submissions/{reference}",
        f"/api/me/submissions/{reference}",
        f"/api/submissions/{reference.removeprefix('S3-')}",
    ):
        response = site.get(path)
        assert response.status_code in {401, 403, 404}, path
        assert created["submission_id"] not in response.text
