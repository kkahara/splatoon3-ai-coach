"""Contact messages go to email or the outbox, never to a submission record."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from loguru import logger
from public_site.app import create_app
from public_site.errors import (
    BAD_ACCOUNT_EMAIL,
    BAD_ACCOUNT_NAME,
    CONTACT_RATE_LIMIT,
    CONTACT_UNAVAILABLE,
    CONTACT_VERIFY,
)
from public_site.settings import PublicSettings

from splatoon3_ai_coach.config.paths import default_config_path


def _client(tmp_path: Path, **overrides) -> TestClient:
    root = tmp_path / "public"
    root.mkdir(parents=True, exist_ok=True)
    values = {
        "root": root,
        "config_path": default_config_path(),
        "static_dir": tmp_path / "no-dist",
        "dev_mode": True,
        "worker_id": "contact-test",
        "public_base_url": "http://coach.example",
        "contact_messages_per_hour": 5,
    }
    values.update(overrides)
    settings = PublicSettings(**values)
    app = create_app(settings, start_worker=False)
    client = TestClient(app)
    client.app = app
    return client


def _body(**extra) -> dict:
    body = {
        "turnstile_token": "dev",
        "reason": "collaboration",
        "name": "Rin",
        "email": "rin@example.com",
        "message": "I would like to help with the evidence checks.",
        "reference": "",
        "leave_blank": "",
    }
    body.update(extra)
    return body


def _outbox(client: TestClient) -> list[Path]:
    folder = client.app.state.settings.root / "outbox"
    if not folder.is_dir():
        return []
    return sorted(folder.glob("contact.*.txt"))


def test_a_valid_message_is_written_to_the_outbox(tmp_path: Path) -> None:
    client = _client(tmp_path, contact_to="owner@example.com")
    lines: list[str] = []
    handle = logger.add(lines.append, format="{message}")
    try:
        sent = client.post(
            "/api/contact",
            json=_body(
                reason="technical",
                reference="https://coach.example/review/abc",
                message="The review page failed.",
            ),
        )
    finally:
        logger.remove(handle)
    assert sent.status_code == 200, sent.text
    assert sent.json() == {"ok": True}
    files = _outbox(client)
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "To: owner@example.com" in text
    assert "Reply-To: rin@example.com" in text
    assert "Technical problem" in text
    assert "https://coach.example/review/abc" in text
    assert "The review page failed." in text
    logged = "\n".join(lines)
    assert "contact message accepted" in logged
    assert "rin@example.com" not in logged
    assert "The review page failed." not in logged
    assert client.app.state.store.list_submissions() == []


def test_a_honeypot_returns_success_and_stores_nothing(tmp_path: Path) -> None:
    client = _client(tmp_path)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    client.app.state.store.write_rate({"video-key": [stamp]})
    sent = client.post("/api/contact", json=_body(leave_blank="filled by a bot"))
    assert sent.status_code == 200, sent.text
    assert _outbox(client) == []
    assert client.app.state.store.read_rate() == {"video-key": [stamp]}


def test_contact_limits_do_not_consume_the_video_key(tmp_path: Path) -> None:
    client = _client(tmp_path)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    client.app.state.store.write_rate({"video-key": [stamp, stamp]})
    sent = client.post("/api/contact", json=_body())
    assert sent.status_code == 200, sent.text
    rates = client.app.state.store.read_rate()
    assert rates["video-key"] == [stamp, stamp]
    assert len([key for key in rates if key.startswith("contact:")]) == 1


def test_a_bad_email_and_a_failed_check_write_nothing(tmp_path: Path) -> None:
    client = _client(tmp_path)
    rejected = client.post("/api/contact", json=_body(email="not-an-email"))
    assert rejected.status_code == 400
    assert rejected.json()["detail"] == BAD_ACCOUNT_EMAIL
    blank = client.post("/api/contact", json=_body(name="   "))
    assert blank.status_code == 400
    assert blank.json()["detail"] == BAD_ACCOUNT_NAME
    blocked = create_app(
        client.app.state.settings,
        store=client.app.state.store,
        verifier=lambda _token: False,
        start_worker=False,
    )
    failed = TestClient(blocked).post("/api/contact", json=_body())
    assert failed.status_code == 400
    assert failed.json()["detail"] == CONTACT_VERIFY
    assert _outbox(client) == []


def test_the_sixth_message_in_an_hour_is_rate_limited(tmp_path: Path) -> None:
    client = _client(tmp_path, contact_messages_per_hour=5)
    for _ in range(5):
        sent = client.post("/api/contact", json=_body())
        assert sent.status_code == 200, sent.text
    limited = client.post("/api/contact", json=_body())
    assert limited.status_code == 429
    assert limited.json()["detail"] == CONTACT_RATE_LIMIT
    assert len(_outbox(client)) == 5


def test_smtp_without_a_recipient_never_connects(tmp_path: Path, monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("SMTP was contacted")

    monkeypatch.setattr("public_site.contact.smtplib.SMTP", refuse)
    client = _client(tmp_path, smtp_host="smtp.example", contact_to="")
    sent = client.post("/api/contact", json=_body())
    assert sent.status_code == 503
    assert sent.json()["detail"] == CONTACT_UNAVAILABLE
    assert _outbox(client) == []
