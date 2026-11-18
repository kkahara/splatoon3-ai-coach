"""Confirmation-email cooldown reporting, masked contact, and address correction.

The server's rate limits are unchanged. Only this browser's own cooldown is
reported; the per-address limit stays silent.
"""

from __future__ import annotations

import re
from pathlib import Path
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from public_site.recording_service import mask_email
from test_public_recordings import _create, _view, site  # noqa: F401

RESEND = "/api/submissions/{token}/verify-contact"
CHANGE = "/api/submissions/{token}/contact-email"


def _outbox(site: TestClient) -> list[Path]:  # noqa: F811
    root = site.app.state.settings.root / "outbox"
    return sorted(root.glob("confirm.*.txt"), key=lambda path: path.stat().st_mtime_ns)


def _sent_to(site: TestClient) -> list[str]:  # noqa: F811
    return [
        re.search(r"^To: (\S+)", path.read_text(encoding="utf-8")).group(1)
        for path in _outbox(site)
    ]


def _link(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return re.search(r"/confirm-notices/(\S+)", text).group(1)


def _age_all_challenges(site: TestClient) -> None:  # noqa: F811
    with site.app.state.service.recordings.database._conn() as conn:
        conn.execute(
            """UPDATE guest_email_challenges
               SET created_at = created_at - interval '16 minutes'"""
        )


def _challenge_count(site: TestClient) -> int:  # noqa: F811
    with site.app.state.service.recordings.database._conn() as conn:
        row = conn.execute("SELECT count(*) AS n FROM guest_email_challenges").fetchone()
    return row["n"]


def _resend(site: TestClient, created: dict):  # noqa: F811
    return site.post(RESEND.format(token=created["token"]))


def _change(site: TestClient, created: dict, email: str):  # noqa: F811
    return site.post(CHANGE.format(token=created["token"]), json={"email": email})


@pytest.mark.parametrize(
    ("email", "masked"),
    [
        ("kenji.kahara@gmail.com", "ke•••a@gmail.com"),
        ("abcd@example.com", "ab•••d@example.com"),
        ("abc@example.com", "a•••@example.com"),
        ("a@example.com", "a•••@example.com"),
    ],
)
def test_masked_address_keeps_the_domain(email: str, masked: str) -> None:
    assert mask_email(email) == masked


def test_browser_cooldown_is_reported_and_refused(site: TestClient) -> None:  # noqa: F811
    first = _create(site, email="first@example.com")
    assert first["confirm_mail_outcome"] == "sent"
    assert _view(site, first)["acquisition"]["confirm_mail_dispatched"] is True

    second = _create(site, email="second@example.com")
    assert second["confirm_mail_outcome"] == "cooldown"
    assert 0 < second["confirm_mail_retry_after_seconds"] <= 900
    assert _sent_to(site) == ["first@example.com"]
    acquisition = _view(site, second)["acquisition"]
    assert acquisition["contact_email_masked"] == "se•••d@example.com"
    assert acquisition["confirm_mail_dispatched"] is False
    assert 0 < acquisition["resend_retry_after_seconds"] <= 900

    refused = _resend(site, second)
    assert refused.status_code == 429
    assert refused.json()["reason"] == "confirm_cooldown"
    assert refused.json()["confirm_mail_outcome"] == "cooldown"
    assert 0 < refused.json()["retry_after_seconds"] <= 900
    assert refused.headers["Retry-After"] == str(refused.json()["retry_after_seconds"])
    assert _sent_to(site) == ["first@example.com"]

    _age_all_challenges(site)
    assert _view(site, second)["acquisition"]["resend_retry_after_seconds"] is None
    site.app.state.service.maintain(datetime.now(UTC))
    assert _sent_to(site) == ["first@example.com", "second@example.com"]
    assert _view(site, second)["acquisition"]["confirm_mail_dispatched"] is True
    assert _view(site, second)["acquisition"]["resend_retry_after_seconds"] > 890


def test_address_limit_stays_silent(site: TestClient) -> None:  # noqa: F811
    _create(site, email="shared@example.com")
    other = TestClient(site.app)
    created = _create(other, email="shared@example.com")
    assert _view(other, created)["acquisition"]["resend_retry_after_seconds"] is None
    response = _resend(other, created)
    assert response.status_code == 200, response.text
    assert response.json()["notice_pending"] is True
    assert response.json()["confirm_mail_outcome"] == "cooldown"
    assert response.json().get("retry_after_seconds") is None
    assert _sent_to(site) == ["shared@example.com"]
    assert _view(other, created)["acquisition"]["confirm_mail_dispatched"] is False


def test_corrected_address_keeps_the_request_and_retires_old_links(
    site: TestClient,  # noqa: F811
) -> None:
    created = _create(site, email="typo@exmaple.com")
    before = _view(site, created)
    old_link = _link(_outbox(site)[-1])
    _age_all_challenges(site)
    challenges = _challenge_count(site)

    changed = _change(site, created, " Fixed@Example.com ")
    assert changed.status_code == 200, changed.text
    assert changed.json() == {
        "notice_pending": True,
        "confirm_mail_outcome": "sent",
        "retry_after_seconds": None,
    }
    assert _sent_to(site) == ["typo@exmaple.com", "fixed@example.com"]
    assert _challenge_count(site) == challenges + 1
    after = _view(site, created)
    assert after["acquisition"]["contact_email_masked"] == "fi•••d@example.com"
    assert after["acquisition"]["confirm_mail_dispatched"] is True
    assert after["public_reference"] == before["public_reference"]
    assert after["payment_state"] == before["payment_state"] == "unpaid"

    stale = site.post("/api/notices/confirm", json={"token": old_link})
    assert stale.status_code == 400
    assert _view(site, created)["acquisition"]["contact_verified"] is False
    fresh = site.post("/api/notices/confirm", json={"token": _link(_outbox(site)[-1])})
    assert fresh.status_code == 200, fresh.text
    verified = _view(site, created)
    assert verified["acquisition"]["contact_verified"] is True
    assert verified["acquisition"]["contact_email_masked"] is None
    assert verified["public_reference"] == before["public_reference"]

    database = site.app.state.service.recordings.database
    stored = database.get(created["submission_id"])
    assert stored["requested_email"] == "fixed@example.com"
    with database._conn() as conn:
        events = conn.execute(
            """SELECT kind, details FROM review_code_acquisition_events
               WHERE submission_id = %s AND kind = 'contact_email_changed'""",
            (created["submission_id"],),
        ).fetchall()
    assert events == [{"kind": "contact_email_changed", "details": {}}]
    assert _change(site, created, "other@example.com").status_code == 409


def test_correction_during_cooldown_sends_nothing(site: TestClient) -> None:  # noqa: F811
    created = _create(site, email="typo@exmaple.com")
    changed = _change(site, created, "fixed@example.com")
    assert changed.status_code == 200, changed.text
    assert changed.json()["confirm_mail_outcome"] == "cooldown"
    assert 0 < changed.json()["retry_after_seconds"] <= 900
    assert _sent_to(site) == ["typo@exmaple.com"]
    acquisition = _view(site, created)["acquisition"]
    assert acquisition["contact_email_masked"] == "fi•••d@example.com"
    assert acquisition["confirm_mail_dispatched"] is False
    assert _resend(site, created).status_code == 429


def test_invalid_correction_is_refused(site: TestClient) -> None:  # noqa: F811
    created = _create(site, email="typo@exmaple.com")
    assert _change(site, created, "not-an-address").status_code == 400
    unknown = site.post(CHANGE.format(token="unknown"), json={"email": "a@b.co"})
    assert unknown.status_code == 404
    acquisition = _view(site, created)["acquisition"]
    assert acquisition["contact_email_masked"] == "ty•••o@exmaple.com"
